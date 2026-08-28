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
from datetime import datetime, timezone
from pathlib import Path


DEVICE_URI = "relay-capture://demo-embosser"
BRF_ASCII = set(
    " abcdefghijklmnopqrstuvwxyz0123456789'@\",*/-^.;<%:[>+_$? !#&()]=\\_"
)
TERMINATE = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _on_sigterm(_signum: int, _frame: object) -> None:
    global TERMINATE
    TERMINATE = True


def validate_brf(data: bytes, *, cells_per_line: int, lines_per_page: int) -> tuple[bytes, ...]:
    if not data or any(byte not in {ord(char) for char in BRF_ASCII} | {10, 12, 13} for byte in data):
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class CaptureJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous_hash: str | None = None

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
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.previous_hash = digest
        return digest


def _read_input(path: str | None, max_bytes: int) -> bytes:
    stream = open(path, "rb") if path else sys.stdin.buffer
    close_stream = path is not None
    try:
        data = stream.read(max_bytes + 1)
    finally:
        if close_stream:
            stream.close()
    if len(data) > max_bytes:
        raise ValueError("BRF exceeds the configured byte limit")
    return data


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
    journal = CaptureJournal(job_dir / "events.jsonl")
    input_part = job_dir / "input.brf.part"
    input_file = job_dir / "input.brf"
    data = _read_input(input_path, max_bytes)
    input_part.write_bytes(data)
    with input_part.open("r+b") as stream:
        os.fsync(stream.fileno())
    input_part.replace(input_file)
    pages = validate_brf(data, cells_per_line=cells_per_line, lines_per_page=lines_per_page)
    journal.append(
        "ACCEPTED",
        {"scheduler_job_id": job_id, "job_title": title, "received_sha256": sha256_bytes(data)},
    )
    completed: list[bytes] = []
    state = "COMPLETED"
    try:
        for page_number, page in enumerate(pages, start=1):
            page_part = job_dir / f"page-{page_number:04d}.brf.part"
            page_file = job_dir / f"page-{page_number:04d}.brf"
            page_part.write_bytes(page)
            with page_part.open("r+b") as stream:
                os.fsync(stream.fileno())
            page_part.replace(page_file)
            completed.append(page)
            print(f"PAGE: {page_number} 1", file=sys.stderr, flush=True)
            journal.append(
                "PAGE_COMPLETED",
                {"scheduler_job_id": job_id, "page": page_number, "page_sha256": sha256_bytes(page)},
            )
            time.sleep(page_delay_seconds)
            if TERMINATE:
                state = "TERMINATED"
                journal.append(
                    "TERMINATED",
                    {"scheduler_job_id": job_id, "pages_completed": len(completed)},
                )
                break
    except Exception as exc:
        state = "FAILED"
        journal.append("FAILED", {"scheduler_job_id": job_id, "reason": type(exc).__name__})
        raise
    output_hash = sha256_bytes(b"\x0c".join(completed)) if state == "COMPLETED" else None
    if state == "COMPLETED":
        output_part = job_dir / "output.brf.part"
        output_part.write_bytes(data)
        with output_part.open("r+b") as stream:
            os.fsync(stream.fileno())
        output_part.replace(job_dir / "output.brf")
        journal.append("COMPLETED", {"scheduler_job_id": job_id, "output_sha256": output_hash})
    manifest = {
        "schema_version": "capture-manifest.v1",
        "scheduler_job_id": job_id,
        "job_title": title,
        "state": state,
        "received_sha256": sha256_bytes(data),
        "completed_output_sha256": output_hash,
        "byte_length_received": len(data),
        "pages_total": len(pages),
        "pages_completed": len(completed),
        "simulated_endpoint": True,
        "events_sha256": journal.previous_hash,
        "finished_at": utc_now(),
    }
    (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
            capture_root=Path(os.environ.get("RELAY_CAPTURE_ROOT", "/var/lib/braille-relay/captures")),
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

