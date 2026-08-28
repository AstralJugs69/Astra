#!/usr/bin/env python3
"""CUPS backend that simulates only the physical endpoint.

The backend accepts one fixed URI, derives storage from the numeric scheduler
job ID, and preserves a hash-chained capture manifest. It never controls CUPS
and never treats a capture as proof of tactile output.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEVICE_URI = "relay-capture://demo-embosser"
BRF_ASCII = set(" abcdefghijklmnopqrstuvwxyz0123456789'@\",*/-^.;<%:[>+_$? !#&()]=\\_")
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


def _atomic_write_bytes(destination: Path, data: bytes) -> None:
    part = destination.with_name(destination.name + ".part")
    with part.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(destination)


def _atomic_write_json(destination: Path, value: dict[str, object]) -> None:
    _atomic_write_bytes(
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        first_previous, terminal = verify_event_chain(path)
        self.first_previous_hash = first_previous
        self.previous_hash = terminal

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
            0o600,
        )
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.previous_hash = digest
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


def run_backend(
    *,
    device_uri: str,
    job_id_text: str,
    title: str,
    input_path: str | None,
    capture_root: Path,
    max_bytes: int = 10 * 1024 * 1024,
    cells_per_line: int = 40,
    lines_per_page: int = 25,
    page_delay_seconds: float = 0.25,
) -> int:
    global TERMINATE
    TERMINATE = False
    if device_uri != DEVICE_URI:
        raise ValueError("unsupported device URI")
    if not job_id_text.isdigit() or int(job_id_text) <= 0:
        raise ValueError("CUPS scheduler job ID must be a positive integer")
    job_id = int(job_id_text)
    capture_root = capture_root.resolve()
    job_dir = (capture_root / str(job_id)).resolve()
    if capture_root not in job_dir.parents:
        raise ValueError("capture path escaped the configured root")
    job_dir.mkdir(parents=True, exist_ok=True)
    if (job_dir / "manifest.json").exists():
        raise ValueError("capture already has a terminal manifest")
    journal = CaptureJournal(job_dir / "events.jsonl")
    previous_event_hash = journal.previous_hash
    started_at = utc_now()
    data = _read_input(input_path, max_bytes)
    pages = validate_brf(data, cells_per_line=cells_per_line, lines_per_page=lines_per_page)
    _atomic_write_bytes(job_dir / "input.brf", data)
    journal.append(
        "ACCEPTED",
        {"scheduler_job_id": job_id, "job_title": title, "received_sha256": sha256_bytes(data)},
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
    if len(argv) < 6:
        print("device-uri job-id user title copies options [file]", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        return run_backend(
            device_uri=argv[0],
            job_id_text=argv[1],
            title=argv[3],
            input_path=argv[6] if len(argv) > 6 else None,
            capture_root=Path(
                os.environ.get("RELAY_CAPTURE_ROOT", "/var/lib/braille-relay/captures")
            ),
            max_bytes=int(os.environ.get("RELAY_CAPTURE_MAX_BYTES", str(10 * 1024 * 1024))),
            cells_per_line=int(os.environ.get("RELAY_CELLS_PER_LINE", "40")),
            lines_per_page=int(os.environ.get("RELAY_LINES_PER_PAGE", "25")),
            page_delay_seconds=float(os.environ.get("RELAY_PAGE_DELAY_SECONDS", "0.25")),
        )
    except (OSError, ValueError) as exc:
        print(f"relay-capture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
