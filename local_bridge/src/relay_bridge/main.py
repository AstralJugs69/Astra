"""Read-only CUPS observation entry point for the WSL bridge."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from .cups_observer import CupsRequiredJobNotFound, ReadOnlyCupsObserver
from .journal import ObservationJournal
from .observation_builder import build_observation, canonical_bytes


class QueueObserver(Protocol):
    def queue_snapshot(self, *, required_job_id: int | None = None) -> dict[str, Any]: ...


class DemoArmAlreadyRunning(RuntimeError):
    """A second observer loop must never interleave a canonical hash chain."""


class _DemoArmLock:
    """Tiny process lock for one bounded human-armed observation session."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise DemoArmAlreadyRunning("an observation loop is already armed") from exc
        os.write(self._descriptor, f"{os.getpid()}\n".encode("ascii"))

    def release(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None
            self.path.unlink(missing_ok=True)


def observe_once(
    *,
    observer: QueueObserver,
    journal: ObservationJournal,
    site_id: str,
    bridge_id: str,
    queue_name: str,
    required_job_id: int | None = None,
) -> dict[str, object]:
    """Read, normalize, chain, and durably enqueue one site observation."""
    sequence, previous_id = journal.next_position()
    payload = build_observation(
        site_id=site_id,
        bridge_id=bridge_id,
        queue_name=queue_name,
        sequence=sequence,
        queue_snapshot=observer.queue_snapshot(required_job_id=required_job_id),
        previous_sha256=previous_id,
    )
    observation_id = payload.get("observation_id")
    if not isinstance(observation_id, str):
        raise TypeError("observation builder returned no observation ID")
    if not journal.append(sequence, observation_id, payload):
        raise RuntimeError("new observation unexpectedly replayed an existing sequence")
    return payload


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """Write a canonical observation without exposing a partial file to a publisher."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(payload) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def observe_loop(
    *,
    observer: QueueObserver,
    journal: ObservationJournal,
    site_id: str,
    bridge_id: str,
    queue_name: str,
    required_job_id: int,
    interval_seconds: float,
    max_runtime_seconds: float,
    status_path: Path,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Append fresh observations for one bounded, human-armed session.

    This is only a CUPS Get-operation loop. It never reads a spool/capture
    directory, substitutes a different queue job, sends telemetry, or changes
    a queue/device. A separate human-owned publisher may acknowledge the
    canonical outbox only after private cloud admission accepts each exact
    observation.
    """

    if required_job_id <= 0:
        raise ValueError("required scheduler job ID must be positive")
    if not 1.0 <= interval_seconds <= 15.0:
        raise ValueError("observation interval must be between 1 and 15 seconds")
    if not 30.0 <= max_runtime_seconds <= 1_800.0:
        raise ValueError("observation runtime must be between 30 and 1800 seconds")
    lock = _DemoArmLock(status_path.with_suffix(status_path.suffix + ".lock"))
    lock.acquire()
    started = monotonic_clock()
    last_observation_id: str | None = None
    last_sequence: int | None = None
    last_observed_at: str | None = None
    try:
        write_json_atomic(
            status_path,
            {
                "schema_version": "demo-observer-status.v1",
                "status": "ARMED",
                "required_scheduler_job_id": required_job_id,
                "interval_seconds": interval_seconds,
                "max_runtime_seconds": max_runtime_seconds,
            },
        )
        while monotonic_clock() - started < max_runtime_seconds:
            try:
                payload = observe_once(
                    observer=observer,
                    journal=journal,
                    site_id=site_id,
                    bridge_id=bridge_id,
                    queue_name=queue_name,
                    required_job_id=required_job_id,
                )
            except CupsRequiredJobNotFound:
                write_json_atomic(
                    status_path,
                    {
                        "schema_version": "demo-observer-status.v1",
                        "status": "BLOCKED",
                        "blocking_reason": "MISSING_LINEAGE",
                        "required_scheduler_job_id": required_job_id,
                    },
                )
                return 3
            observation_id = payload.get("observation_id")
            sequence = payload.get("sequence")
            observed_at = payload.get("observed_at")
            if (
                not isinstance(observation_id, str)
                or not isinstance(sequence, int)
                or not isinstance(observed_at, str)
            ):
                raise TypeError("observation loop received an invalid canonical payload")
            last_observation_id = observation_id
            last_sequence = sequence
            last_observed_at = observed_at
            write_json_atomic(
                status_path,
                {
                    "schema_version": "demo-observer-status.v1",
                    "status": "OBSERVING",
                    "required_scheduler_job_id": required_job_id,
                    "last_observation_id": observation_id,
                    "last_sequence": sequence,
                    "last_observed_at": observed_at,
                },
            )
            remaining = max_runtime_seconds - (monotonic_clock() - started)
            if remaining <= 0:
                break
            sleep(min(interval_seconds, remaining))
        completed: dict[str, object] = {
            "schema_version": "demo-observer-status.v1",
            "status": "COMPLETED",
            "required_scheduler_job_id": required_job_id,
        }
        if (
            last_observation_id is not None
            and last_sequence is not None
            and last_observed_at is not None
        ):
            completed.update(
                {
                    "last_observation_id": last_observation_id,
                    "last_sequence": last_sequence,
                    "last_observed_at": last_observed_at,
                }
            )
        write_json_atomic(status_path, completed)
        return 0
    except KeyboardInterrupt:
        write_json_atomic(
            status_path,
            {
                "schema_version": "demo-observer-status.v1",
                "status": "STOPPED_BY_HUMAN",
                "required_scheduler_job_id": required_job_id,
                "last_observation_id": last_observation_id,
                "last_sequence": last_sequence,
                "last_observed_at": last_observed_at,
            },
        )
        return 130
    except Exception:
        write_json_atomic(
            status_path,
            {
                "schema_version": "demo-observer-status.v1",
                "status": "BLOCKED",
                "blocking_reason": "OBSERVATION_READ_FAILED",
                "required_scheduler_job_id": required_job_id,
                "last_observation_id": last_observation_id,
                "last_sequence": last_sequence,
                "last_observed_at": last_observed_at,
            },
        )
        raise
    finally:
        lock.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relay-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    observe = commands.add_parser(
        "observe-once",
        help="Read one normalized queue snapshot and append it to the local outbox.",
    )
    observe.add_argument("--server", default=os.environ.get("CUPS_SERVER", "localhost:631"))
    observe.add_argument("--queue", default=os.environ.get("QUEUE_NAME", "Braille-Embosser-Sim"))
    observe.add_argument("--site-id", default=os.environ.get("SITE_ID", "demo-site"))
    observe.add_argument(
        "--bridge-id",
        default=os.environ.get("BRIDGE_ID", "single-pc-bridge"),
    )

    loop = commands.add_parser(
        "observe-loop",
        help="Run one bounded, read-only, exact-job observation session for a human-armed demo.",
    )
    loop.add_argument("--server", default=os.environ.get("CUPS_SERVER", "localhost:631"))
    loop.add_argument("--queue", default=os.environ.get("QUEUE_NAME", "Braille-Embosser-Sim"))
    loop.add_argument("--site-id", default=os.environ.get("SITE_ID", "demo-site"))
    loop.add_argument("--bridge-id", default=os.environ.get("BRIDGE_ID", "single-pc-bridge"))
    loop.add_argument("--user", default="relay-observer")
    loop.add_argument(
        "--journal",
        type=Path,
        default=Path(
            os.environ.get(
                "BRIDGE_JOURNAL",
                "/var/lib/braille-relay/observer/journal.sqlite3",
            )
        ),
    )
    loop.add_argument("--require-job-id", type=int, required=True)
    loop.add_argument("--interval-seconds", type=float, default=5.0)
    loop.add_argument("--max-runtime-seconds", type=float, default=900.0)
    loop.add_argument(
        "--status-path",
        type=Path,
        default=Path("work/demo-arm/observer-status.json"),
    )
    observe.add_argument("--user", default="relay-observer")
    observe.add_argument(
        "--password-stdin",
        action="store_true",
        help="read one CUPS password line from stdin instead of prompting",
    )
    observe.add_argument(
        "--journal",
        type=Path,
        default=Path(
            os.environ.get(
                "BRIDGE_JOURNAL",
                "/var/lib/braille-relay/observer/journal.sqlite3",
            )
        ),
    )

    verify = commands.add_parser(
        "verify-access",
        help="Verify read-only access to one configured CUPS queue without writing evidence.",
    )
    verify.add_argument("--server", default=os.environ.get("CUPS_SERVER", "localhost:631"))
    verify.add_argument("--queue", default=os.environ.get("QUEUE_NAME", "Braille-Embosser-Sim"))
    verify.add_argument("--user", default="relay-observer")
    verify.add_argument(
        "--password-stdin",
        action="store_true",
        help="read one CUPS password line from stdin instead of prompting",
    )
    observe.add_argument("--output", type=Path, required=True)
    observe.add_argument(
        "--require-job-id",
        type=int,
        help=(
            "require this exact scheduler job through read-only Get-Job-Attributes; "
            "never substitutes another job"
        ),
    )

    acknowledge = commands.add_parser(
        "acknowledge-published",
        help="Acknowledge one outbox item only after cloud telemetry admission succeeds.",
    )
    acknowledge.add_argument("--journal", type=Path, required=True)
    acknowledge.add_argument("--observation-id", required=True)

    pending = commands.add_parser(
        "pending-outbox",
        help="Emit durable unpublished observations for an external telemetry publisher.",
    )
    pending.add_argument("--journal", type=Path, required=True)
    return parser


def _configure_cups_identity(
    username: str,
    *,
    password_stdin: bool = False,
    single_use: bool = False,
) -> None:
    try:
        import cups  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pycups is required in the WSL bridge") from exc
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("CUPS password stdin was empty")
    else:
        password = getpass.getpass(f"Password for {username}: ")
    cups.setUser(username)
    supplied = False

    def password_callback(_prompt: str) -> str:
        nonlocal supplied
        if single_use and supplied:
            return ""
        supplied = True
        return password

    cups.setPasswordCB(password_callback)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-access":
        _configure_cups_identity(
            args.user,
            password_stdin=args.password_stdin,
            single_use=True,
        )
        observer = ReadOnlyCupsObserver(server=args.server, queue_name=args.queue)
        try:
            observer.queue_snapshot()
        except Exception as exc:
            if exc.args and exc.args[0] == 4096:
                print(
                    json.dumps(
                        {"status": "ACCESS_DENIED", "queue": args.queue},
                        sort_keys=True,
                    )
                )
                return 4
            raise
        print(json.dumps({"status": "ACCESS_VERIFIED", "queue": args.queue}, sort_keys=True))
        return 0
    if args.command == "observe-once":
        if args.require_job_id is not None and args.require_job_id <= 0:
            raise ValueError("required scheduler job ID must be positive")
        _configure_cups_identity(args.user, password_stdin=args.password_stdin)
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        journal = ObservationJournal(args.journal)
        try:
            try:
                payload = observe_once(
                    observer=ReadOnlyCupsObserver(server=args.server, queue_name=args.queue),
                    journal=journal,
                    site_id=args.site_id,
                    bridge_id=args.bridge_id,
                    queue_name=args.queue,
                    required_job_id=args.require_job_id,
                )
            except CupsRequiredJobNotFound:
                print(
                    json.dumps(
                        {
                            "status": "BLOCKED",
                            "blocking_reason": "MISSING_LINEAGE",
                            "required_scheduler_job_id": args.require_job_id,
                        },
                        sort_keys=True,
                    )
                )
                return 3
            write_json_atomic(args.output, payload)
        finally:
            journal.close()
        observations = payload.get("observations")
        if not isinstance(observations, list):
            raise TypeError("observation payload contains no normalized job list")
        print(
            json.dumps(
                {
                    "status": "OBSERVED_AND_QUEUED",
                    "observation_id": payload["observation_id"],
                    "sequence": payload["sequence"],
                    "job_count": len(observations),
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "observe-loop":
        _configure_cups_identity(args.user)
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        journal = ObservationJournal(args.journal)
        try:
            return observe_loop(
                observer=ReadOnlyCupsObserver(server=args.server, queue_name=args.queue),
                journal=journal,
                site_id=args.site_id,
                bridge_id=args.bridge_id,
                queue_name=args.queue,
                required_job_id=args.require_job_id,
                interval_seconds=args.interval_seconds,
                max_runtime_seconds=args.max_runtime_seconds,
                status_path=args.status_path,
            )
        finally:
            journal.close()
    if args.command == "acknowledge-published":
        journal = ObservationJournal(args.journal)
        try:
            acknowledged = journal.mark_outbox_published(args.observation_id)
        finally:
            journal.close()
        if not acknowledged:
            raise RuntimeError("observation is absent or already acknowledged")
        print(json.dumps({"status": "PUBLISHED", "observation_id": args.observation_id}))
        return 0
    if args.command == "pending-outbox":
        journal = ObservationJournal(args.journal)
        try:
            pending = journal.pending_outbox()
        finally:
            journal.close()
        print(
            json.dumps(
                {
                    "schema_version": "bridge-pending-observations.v1",
                    "observations": [
                        {
                            "observation_id": entry["observation_id"],
                            "payload": entry["payload"],
                        }
                        for entry in pending
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
