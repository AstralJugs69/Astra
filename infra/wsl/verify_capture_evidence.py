#!/usr/bin/env python3
"""Read-only exact-byte and manifest verification for one simulator capture."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT = Path("/var/lib/braille-relay/captures")
SCHEMA_PATH = ROOT / "schemas" / "capture-manifest.v1.json"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_backend() -> ModuleType:
    spec = importlib.util.spec_from_file_location("relay_capture_backend_evidence", BACKEND_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the simulator capture verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("capture manifest must be a JSON object")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=str
    )
    if errors:
        raise ValueError("capture manifest does not conform to capture-manifest.v1")
    return value


def _capture_directory(capture_root: Path, job_id: int) -> Path:
    root = capture_root.resolve()
    job_dir = (root / str(job_id)).resolve()
    if root not in job_dir.parents:
        raise ValueError("capture path escaped the fixed evidence root")
    return job_dir


def verify(
    *,
    candidate: Path,
    job_id: int,
    capture_root: Path,
    expected_state: str,
) -> dict[str, object]:
    if not candidate.is_file():
        raise ValueError("candidate BRF does not exist")
    job_dir = _capture_directory(capture_root, job_id)
    input_path = job_dir / "input.brf"
    events_path = job_dir / "events.jsonl"
    manifest_path = job_dir / "manifest.json"
    for path in (input_path, events_path, manifest_path):
        if not path.is_file():
            raise ValueError("required capture evidence is missing")

    manifest = _validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    if manifest["scheduler_job_id"] != job_id:
        raise ValueError("capture manifest job ID differs from the requested job")
    if manifest["state"] != expected_state:
        raise ValueError("capture manifest terminal state differs from the expected state")

    backend = _load_backend()
    _first_event, terminal_event = backend.verify_event_chain(events_path)
    if terminal_event is None:
        raise ValueError("capture journal has no terminal event")
    if manifest["terminal_event_sha256"] != terminal_event:
        raise ValueError("manifest terminal hash differs from the capture journal")
    if manifest["events_sha256"] != terminal_event:
        raise ValueError("manifest event-chain hash differs from the capture journal")

    candidate_hash = _sha256(candidate)
    input_hash = _sha256(input_path)
    if candidate_hash != input_hash or manifest["received_sha256"] != candidate_hash:
        raise ValueError("submitted candidate bytes differ from backend-received bytes")

    output_path = job_dir / "output.brf"
    output_hash: str | None = None
    if expected_state == "COMPLETED":
        if not output_path.is_file():
            raise ValueError("completed capture output is missing")
        output_hash = _sha256(output_path)
        if output_hash != candidate_hash or manifest["completed_output_sha256"] != candidate_hash:
            raise ValueError("captured output bytes differ from the submitted candidate")
        if manifest["completed_at"] != manifest["finished_at"]:
            raise ValueError("completed capture timestamp is incomplete")
    else:
        if output_path.exists() or manifest["completed_output_sha256"] is not None:
            raise ValueError("terminated capture unexpectedly has completed output")
        if manifest["completed_at"] is not None:
            raise ValueError("terminated capture unexpectedly has a completion timestamp")

    pages_total = manifest["pages_total"]
    pages_completed = manifest["pages_completed"]
    if not isinstance(pages_total, int) or not isinstance(pages_completed, int):
        raise TypeError("capture page counters must be integers")
    if pages_completed > pages_total:
        raise ValueError("capture completed more pages than it received")
    return {
        "schema_version": "capture-evidence-check.v1",
        "scheduler_job_id": job_id,
        "state": expected_state,
        "candidate_sha256": candidate_hash,
        "backend_received_sha256": input_hash,
        "captured_output_sha256": output_hash,
        "terminal_event_sha256": terminal_event,
        "pages_total": pages_total,
        "pages_completed": pages_completed,
        "manifest_schema_valid": True,
        "event_chain_valid": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, default=CAPTURE_ROOT)
    parser.add_argument("--expected-state", choices=("COMPLETED", "TERMINATED"), required=True)
    args = parser.parse_args()
    if args.job_id <= 0:
        parser.error("--job-id must be positive")
    try:
        result = verify(
            candidate=args.candidate,
            job_id=args.job_id,
            capture_root=args.capture_root,
            expected_state=args.expected_state,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {type(exc).__name__}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
