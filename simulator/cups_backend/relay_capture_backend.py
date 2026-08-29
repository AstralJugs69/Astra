#!/usr/bin/env python3
"""CUPS backend that simulates only the physical endpoint.

The backend accepts one fixed URI, derives storage from the numeric scheduler
job ID, and preserves a hash-chained capture manifest. It never controls CUPS
and never treats a capture as proof of tactile output.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEVICE_URI = "relay-capture://demo-embosser"
CAPTURE_ROOT = Path("/var/lib/braille-relay/captures")
CAPTURE_CONFIG_PATH = Path("/etc/cups/relay-capture.conf")
BRF_ASCII = set(" abcdefghijklmnopqrstuvwxyz0123456789'@\",*/-^.;<%:[>+_$? !#&()]=\\_")
CAPTURE_DIRECTORY_MODE = 0o2750
CAPTURE_FILE_MODE = 0o640
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_CELLS_PER_LINE = 40
DEFAULT_LINES_PER_PAGE = 25
DEFAULT_PAGE_DELAY_SECONDS = 5.0
MIN_PAGE_DELAY_SECONDS = 1.0
MAX_PAGE_DELAY_SECONDS = 60.0
ACCEPTANCE_FILENAME = "capture-acceptance.json"
SIMULATED_DEMO_TRUTH_BASIS = "SIMULATED_DEMO"
TERMINATE = False


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _on_sigterm(_signum: int, _frame: object) -> None:
    global TERMINATE
    TERMINATE = True


def validate_brf(data: bytes, *, cells_per_line: int, lines_per_page: int) -> tuple[bytes, ...]:
    if not data or any(
        byte not in {ord(char) for char in BRF_ASCII} | {10, 12, 13} for byte in data
    ):
        raise ValueError("input is not an allowlisted BRF byte stream")
    pages = data.split(b"\x0c")
    for number, page in enumerate(pages, start=1):
        rows = page.split(b"\r\n")
        if len(rows) != lines_per_page:
            raise ValueError(f"page {number} has {len(rows)} rows; expected {lines_per_page}")
        if any(len(row) != cells_per_line for row in rows):
            raise ValueError(f"page {number} has an invalid row width")
    return tuple(pages)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _set_private_file_mode(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, CAPTURE_FILE_MODE)


def _ensure_private_directory(path: Path) -> None:
    """Create a group-auditable directory despite CUPS' restrictive umask."""

    if os.name == "nt":
        path.mkdir(parents=True, exist_ok=True)
        return
    previous_umask = os.umask(0o027)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=CAPTURE_DIRECTORY_MODE)
    finally:
        os.umask(previous_umask)


def _set_private_descriptor_mode(descriptor: int) -> None:
    if os.name != "nt":
        os.fchmod(descriptor, CAPTURE_FILE_MODE)


def _fsync_directory(path: Path) -> None:
    """Persist a completed rename before exposing immutable capture evidence."""

    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_page_delay(config_path: Path, *, require_root_owner: bool) -> float:
    """Load the single root-controlled simulator timing setting."""

    if not config_path.is_file():
        raise ValueError("CUPS capture timing configuration is missing")
    metadata = config_path.stat()
    if require_root_owner and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
        raise ValueError("CUPS capture timing configuration must be root-owned and not writable")

    expected_key = "RELAY_PAGE_DELAY_SECONDS"
    values: dict[str, str] = {}
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("CUPS capture timing configuration is not UTF-8") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if separator != "=" or key != expected_key or not value or key in values:
            raise ValueError("CUPS capture timing configuration is invalid")
        values[key] = value
    if set(values) != {expected_key}:
        raise ValueError("CUPS capture timing configuration is incomplete")
    try:
        delay = float(values[expected_key])
    except ValueError as exc:
        raise ValueError("CUPS capture delay must be numeric") from exc
    if not math.isfinite(delay) or not MIN_PAGE_DELAY_SECONDS <= delay <= MAX_PAGE_DELAY_SECONDS:
        raise ValueError("CUPS capture delay is outside the permitted range")
    return delay


def _atomic_write_bytes(destination: Path, data: bytes) -> None:
    part = destination.with_name(destination.name + ".part")
    descriptor = os.open(
        part,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        CAPTURE_FILE_MODE,
    )
    try:
        _set_private_descriptor_mode(descriptor)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(part, destination)
    _set_private_file_mode(destination)
    _fsync_directory(destination.parent)


def _atomic_write_bytes_once(destination: Path, data: bytes) -> None:
    """Create immutable evidence without exposing a partially written target."""

    if destination.exists():
        raise ValueError("immutable capture evidence already exists")
    part = destination.with_name(f".{destination.name}.{os.getpid()}.{time.time_ns()}.part")
    descriptor = os.open(
        part,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        CAPTURE_FILE_MODE,
    )
    try:
        _set_private_descriptor_mode(descriptor)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    try:
        # Linking a fully synced temporary file is atomic: a reader observes
        # either no acceptance record or the complete immutable record.
        os.link(part, destination)
    except FileExistsError as exc:
        raise ValueError("immutable capture evidence already exists") from exc
    finally:
        if part.exists():
            part.unlink()
    _set_private_file_mode(destination)
    _fsync_directory(destination.parent)


def _atomic_write_json(destination: Path, value: dict[str, object]) -> None:
    _atomic_write_bytes(
        destination,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _atomic_write_json_once(destination: Path, value: dict[str, object]) -> None:
    _atomic_write_bytes_once(
        destination,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _event_digest(entry: dict[str, Any]) -> str:
    body = dict(entry)
    event_hash = body.pop("event_sha256", None)
    if not isinstance(event_hash, str):
        raise TypeError("capture event is missing event_sha256")
    return sha256_bytes(_canonical(body))


def verify_event_chain(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, None
    previous: str | None = None
    first_previous: str | None = None
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank capture event at line {line_number}")
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise TypeError(f"capture event at line {line_number} is not an object")
            event_hash = entry.get("event_sha256")
            if event_hash != _event_digest(entry):
                raise ValueError(f"capture event hash mismatch at line {line_number}")
            if line_number == 1:
                first_previous = entry.get("previous_event_sha256")
            if entry.get("previous_event_sha256") != previous:
                raise ValueError(f"capture event chain mismatch at line {line_number}")
            previous = event_hash
    return first_previous, previous


class CaptureJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        # setup_cups_gate0.sh owns the set-group-ID capture tree. The CUPS
        # backend runs as lp, which is intentionally not a member of the
        # human relay-audit group; chmod here would clear that inheritance.
        _ensure_private_directory(self.path.parent)
        if self.path.exists():
            _set_private_file_mode(self.path)
        first_previous, terminal = verify_event_chain(path)
        self.first_previous_hash = first_previous
        self.previous_hash = terminal
        self.last_entry: dict[str, object] | None = None

    def append(self, event_type: str, details: dict[str, object]) -> str:
        body = {
            "schema_version": "capture-event.v1",
            "event_type": event_type,
            "recorded_at": utc_now(),
            "previous_event_sha256": self.previous_hash,
            "details": details,
        }
        digest = sha256_bytes(_canonical(body))
        entry = {**body, "event_sha256": digest}
        encoded = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            CAPTURE_FILE_MODE,
        )
        try:
            _set_private_descriptor_mode(descriptor)
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.previous_hash = digest
        self.last_entry = entry
        if self.first_previous_hash is None:
            self.first_previous_hash = body["previous_event_sha256"]
        return digest


def _read_input(path: str | None, max_bytes: int) -> bytes:
    if path is None:
        data = sys.stdin.buffer.read(max_bytes + 1)
    else:
        with open(path, "rb") as stream:
            data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("BRF exceeds the configured byte limit")
    return data


def _manifest(
    *,
    job_id: int,
    title: str,
    state: str,
    data: bytes,
    pages_total: int,
    pages_completed: int,
    output_hash: str | None,
    previous_event_hash: str | None,
    terminal_event_hash: str,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    return {
        "schema_version": "capture-manifest.v1",
        "scheduler_job_id": job_id,
        "job_title": title or "untitled",
        "state": state,
        "received_sha256": sha256_bytes(data),
        "completed_output_sha256": output_hash,
        "byte_length_received": len(data),
        "pages_total": pages_total,
        "pages_completed": pages_completed,
        "simulated_endpoint": True,
        "previous_event_sha256": previous_event_hash,
        "events_sha256": terminal_event_hash,
        "terminal_event_sha256": terminal_event_hash,
        "started_at": started_at,
        "finished_at": finished_at,
        "completed_at": finished_at if state == "COMPLETED" else None,
    }


def _acceptance_record(
    *,
    job_id: int,
    title: str,
    data: bytes,
    accepted_at: str,
    accepted_event_hash: str,
    previous_event_hash: str | None,
) -> dict[str, object]:
    return {
        "schema_version": "capture-acceptance.v1",
        "scheduler_job_id": job_id,
        "job_title": title,
        "received_sha256": sha256_bytes(data),
        "byte_length_received": len(data),
        "simulated_endpoint_id": DEVICE_URI,
        "accepted_at": accepted_at,
        "accepted_event_sha256": accepted_event_hash,
        "previous_event_sha256": previous_event_hash,
        "truth_basis": SIMULATED_DEMO_TRUTH_BASIS,
    }


def run_backend(
    *,
    device_uri: str,
    job_id_text: str,
    title: str,
    input_path: str | None,
    capture_root: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    cells_per_line: int = DEFAULT_CELLS_PER_LINE,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
) -> int:
    global TERMINATE
    TERMINATE = False
    if device_uri != DEVICE_URI:
        raise ValueError("unsupported device URI")
    if not job_id_text.isdigit() or int(job_id_text) <= 0:
        raise ValueError("CUPS scheduler job ID must be a positive integer")
    job_id = int(job_id_text)
    if not title or len(title) > 512:
        raise ValueError("CUPS job title must be present and bounded")
    # Preserve the group and set-group-ID mode installed by the root-owned
    # setup script so human relay-audit readers can inspect capture evidence
    # without granting access to relay-observer.
    _ensure_private_directory(capture_root)
    capture_root = capture_root.resolve()
    job_dir = (capture_root / str(job_id)).resolve()
    if capture_root not in job_dir.parents:
        raise ValueError("capture path escaped the configured root")
    _ensure_private_directory(job_dir)
    evidence_paths = (
        job_dir / "input.brf",
        job_dir / "events.jsonl",
        job_dir / ACCEPTANCE_FILENAME,
        job_dir / "manifest.json",
    )
    if any(path.exists() for path in evidence_paths):
        raise ValueError("capture already has immutable evidence")
    journal = CaptureJournal(job_dir / "events.jsonl")
    previous_event_hash = journal.previous_hash
    started_at = utc_now()
    data = _read_input(input_path, max_bytes)
    pages = validate_brf(data, cells_per_line=cells_per_line, lines_per_page=lines_per_page)
    _atomic_write_bytes(job_dir / "input.brf", data)
    accepted_event_hash = journal.append(
        "ACCEPTED",
        {
            "scheduler_job_id": job_id,
            "job_title": title,
            "received_sha256": sha256_bytes(data),
            "byte_length_received": len(data),
            "simulated_endpoint_id": DEVICE_URI,
            "truth_basis": SIMULATED_DEMO_TRUTH_BASIS,
        },
    )
    accepted_at = None if journal.last_entry is None else journal.last_entry.get("recorded_at")
    if not isinstance(accepted_at, str):
        raise TypeError("capture acceptance event was not durably recorded")
    _atomic_write_json_once(
        job_dir / ACCEPTANCE_FILENAME,
        _acceptance_record(
            job_id=job_id,
            title=title,
            data=data,
            accepted_at=accepted_at,
            accepted_event_hash=accepted_event_hash,
            previous_event_hash=previous_event_hash,
        ),
    )
    completed: list[bytes] = []
    state = "COMPLETED"
    terminal_event_hash: str | None = None
    try:
        for page_number, page in enumerate(pages, start=1):
            _atomic_write_bytes(job_dir / f"page-{page_number:04d}.brf", page)
            completed.append(page)
            print(f"PAGE: {page_number} 1", file=sys.stderr, flush=True)
            journal.append(
                "PAGE_COMPLETED",
                {
                    "scheduler_job_id": job_id,
                    "page": page_number,
                    "page_sha256": sha256_bytes(page),
                },
            )
            time.sleep(page_delay_seconds)
            if TERMINATE:
                state = "TERMINATED"
                terminal_event_hash = journal.append(
                    "TERMINATED",
                    {"scheduler_job_id": job_id, "pages_completed": len(completed)},
                )
                break
    except Exception as exc:
        state = "FAILED"
        terminal_event_hash = journal.append(
            "FAILED",
            {"scheduler_job_id": job_id, "reason": type(exc).__name__},
        )
        finished_at = utc_now()
        _atomic_write_json(
            job_dir / "manifest.json",
            _manifest(
                job_id=job_id,
                title=title,
                state=state,
                data=data,
                pages_total=len(pages),
                pages_completed=len(completed),
                output_hash=None,
                previous_event_hash=previous_event_hash,
                terminal_event_hash=terminal_event_hash,
                started_at=started_at,
                finished_at=finished_at,
            ),
        )
        raise
    output_hash: str | None = None
    if state == "COMPLETED":
        output_hash = sha256_bytes(data)
        _atomic_write_bytes(job_dir / "output.brf", data)
        terminal_event_hash = journal.append(
            "COMPLETED",
            {"scheduler_job_id": job_id, "output_sha256": output_hash},
        )
    assert terminal_event_hash is not None
    finished_at = utc_now()
    _atomic_write_json(
        job_dir / "manifest.json",
        _manifest(
            job_id=job_id,
            title=title,
            state=state,
            data=data,
            pages_total=len(pages),
            pages_completed=len(completed),
            output_hash=output_hash,
            previous_event_hash=previous_event_hash,
            terminal_event_hash=terminal_event_hash,
            started_at=started_at,
            finished_at=finished_at,
        ),
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--validate-runtime-config":
        try:
            load_page_delay(Path(argv[1]), require_root_owner=False)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"relay-capture: {exc}", file=sys.stderr)
            return 1
        return 0
    if len(argv) < 5:
        print("job-id user title copies options [file]", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        return run_backend(
            # CUPS puts the queue name in the executable argv[0]. Linux
            # removes that value when it invokes this Python shebang script,
            # so argv starts with CUPS's numeric scheduler job ID. The
            # scheduler-provided DEVICE_URI is the only device URI accepted.
            device_uri=os.environ.get("DEVICE_URI", ""),
            job_id_text=argv[0],
            title=argv[2],
            input_path=argv[5] if len(argv) > 5 else None,
            capture_root=CAPTURE_ROOT,
            max_bytes=DEFAULT_MAX_BYTES,
            cells_per_line=DEFAULT_CELLS_PER_LINE,
            lines_per_page=DEFAULT_LINES_PER_PAGE,
            page_delay_seconds=load_page_delay(
                CAPTURE_CONFIG_PATH, require_root_owner=os.name != "nt"
            ),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"relay-capture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
