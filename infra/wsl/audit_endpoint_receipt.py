#!/usr/bin/env python3
"""Emit one fail-closed endpoint receipt from the fixed demo capture root.

This utility is a narrowly scoped local audit reader. It accepts only a numeric
scheduler job identity and expected immutable lineage values; it never accepts
a capture root or any other filesystem path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT = Path("/var/lib/braille-relay/captures")
SCHEMA_PATH = ROOT / "schemas" / "capture-manifest.v1.json"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
SITE_ID = "demo-site"
QUEUE_NAME = "Braille-Embosser-Sim"
ENDPOINT_ID = "relay-capture://demo-embosser"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_STATES = {"COMPLETED", "TERMINATED", "FAILED"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_backend() -> ModuleType:
    spec = importlib.util.spec_from_file_location("relay_endpoint_audit_backend", BACKEND_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the capture hash-chain verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _job_directory(job_id: int) -> Path:
    root = CAPTURE_ROOT.resolve(strict=True)
    job_dir = (root / str(job_id)).resolve(strict=True)
    if root not in job_dir.parents or job_dir.name != str(job_id):
        raise ValueError("capture path escaped the fixed endpoint evidence root")
    return job_dir


def _manifest(path: Path) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("capture manifest must be an object")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=str,
    )
    if errors:
        raise ValueError("capture manifest does not conform to capture-manifest.v1")
    return value, raw


def _events(path: Path, backend: ModuleType) -> tuple[list[dict[str, object]], str]:
    _first_previous, terminal_hash = backend.verify_event_chain(path)
    if not isinstance(terminal_hash, str):
        raise TypeError("capture event chain has no terminal event")
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError("capture event must be an object")
        records.append(value)
    if not records:
        raise ValueError("capture event chain is empty")
    return records, terminal_hash


def audit(
    *,
    baseline_id: str,
    production_link_id: str,
    job_id: int,
    expected_title: str,
    approved_brf_sha256: str,
    expected_state_version: int,
) -> dict[str, object]:
    for label, value in (
        ("baseline ID", baseline_id),
        ("production link ID", production_link_id),
        ("approved BRF SHA-256", approved_brf_sha256),
    ):
        if SHA256.fullmatch(value) is None:
            raise ValueError(f"{label} is invalid")
    if job_id <= 0 or expected_state_version < 1:
        raise ValueError("job ID and expected state version must be positive")
    if not expected_title or len(expected_title) > 512:
        raise ValueError("expected canonical title is invalid")

    job_dir = _job_directory(job_id)
    input_path = job_dir / "input.brf"
    manifest_path = job_dir / "manifest.json"
    events_path = job_dir / "events.jsonl"
    for path in (input_path, manifest_path, events_path):
        if not path.is_file():
            raise ValueError("required endpoint evidence is missing")
    manifest, manifest_bytes = _manifest(manifest_path)
    backend = _load_backend()
    events, terminal_hash = _events(events_path, backend)

    state = manifest.get("state")
    if state not in TERMINAL_STATES:
        raise ValueError("capture evidence is not terminal")
    if manifest.get("scheduler_job_id") != job_id:
        raise ValueError("capture scheduler job ID does not match")
    if manifest.get("job_title") != expected_title:
        raise ValueError("capture canonical title does not match")
    if manifest.get("simulated_endpoint") is not True:
        raise ValueError("capture is not labeled as a simulated demo endpoint")
    if (
        manifest.get("terminal_event_sha256") != terminal_hash
        or manifest.get("events_sha256") != terminal_hash
    ):
        raise ValueError("capture manifest conflicts with the terminal event chain")
    input_hash = _sha256_file(input_path)
    if input_hash != manifest.get("received_sha256") or input_hash != approved_brf_sha256:
        raise ValueError("endpoint-received bytes do not match the approved baseline BRF")

    accepted = events[0]
    terminal = events[-1]
    details = accepted.get("details")
    if (
        accepted.get("event_type") != "ACCEPTED"
        or not isinstance(details, dict)
        or details.get("scheduler_job_id") != job_id
        or details.get("job_title") != expected_title
        or details.get("received_sha256") != input_hash
    ):
        raise ValueError("capture acceptance event conflicts with immutable lineage")
    if terminal.get("event_type") != state or terminal.get("event_sha256") != terminal_hash:
        raise ValueError("capture terminal event conflicts with manifest state")
    evidence_timestamp = manifest.get("finished_at")
    if not isinstance(evidence_timestamp, str):
        raise TypeError("capture evidence timestamp is missing")

    idempotency_key = _sha256_bytes(
        json.dumps(
            {
                "scope": "endpoint-receipt",
                "baseline_id": baseline_id,
                "production_link_id": production_link_id,
                "expected_state_version": expected_state_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return {
        "schema_version": "endpoint-evidence-submission.v1",
        "baseline_id": baseline_id,
        "production_link_id": production_link_id,
        "scheduler_job_id": job_id,
        "scheduler_job_title": expected_title,
        "site_id": SITE_ID,
        "queue_name": QUEUE_NAME,
        "simulated_endpoint_id": ENDPOINT_ID,
        "approved_baseline_brf_sha256": approved_brf_sha256,
        "endpoint_received_sha256": input_hash,
        "capture_manifest_sha256": _sha256_bytes(manifest_bytes),
        "terminal_event_sha256": terminal_hash,
        "capture_state": state,
        "evidence_timestamp": evidence_timestamp,
        "truth_basis": "SIMULATED_DEMO",
        "expected_baseline_state_version": expected_state_version,
        "idempotency_key": idempotency_key,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--production-link-id", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--expected-title", required=True)
    parser.add_argument("--approved-brf-sha256", required=True)
    parser.add_argument("--expected-state-version", type=int, required=True)
    args = parser.parse_args()
    try:
        result = audit(
            baseline_id=args.baseline_id,
            production_link_id=args.production_link_id,
            job_id=args.job_id,
            expected_title=args.expected_title,
            approved_brf_sha256=args.approved_brf_sha256,
            expected_state_version=args.expected_state_version,
        )
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {type(exc).__name__}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
