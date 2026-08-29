"""One-shot, read-only CUPS observation entry point for the WSL bridge."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .cups_observer import CupsRequiredJobNotFound, ReadOnlyCupsObserver
from .journal import ObservationJournal
from .observation_builder import build_observation, canonical_bytes


class QueueObserver(Protocol):
    def queue_snapshot(self, *, required_job_id: int | None = None) -> dict[str, Any]: ...


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
    observe.add_argument("--user", default="relay-observer")
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


def _configure_cups_identity(username: str) -> None:
    try:
        import cups  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pycups is required in the WSL bridge") from exc
    password = getpass.getpass(f"Password for {username}: ")
    cups.setUser(username)
    cups.setPasswordCB(lambda _prompt: password)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "observe-once":
        if args.require_job_id is not None and args.require_job_id <= 0:
            raise ValueError("required scheduler job ID must be positive")
        _configure_cups_identity(args.user)
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
