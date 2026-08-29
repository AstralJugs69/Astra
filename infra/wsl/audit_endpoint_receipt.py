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
ACCEPTANCE_SCHEMA_PATH = ROOT / "schemas" / "capture-acceptance.v1.json"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
SITE_ID = "demo-site"
QUEUE_NAME = "Braille-Embosser-Sim"
ENDPOINT_ID = "relay-capture://demo-embosser"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_STATES = {"COMPLETED", "TERMINATED", "FAILED"}
ACCEPTANCE_FILENAME = "capture-acceptance.json"


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


def _schema_object(path: Path, schema_path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=str,
    )
    if errors:
        raise ValueError(f"{label} does not conform to its immutable schema")
    return value, raw


def _manifest(path: Path) -> tuple[dict[str, object], bytes]:
    return _schema_object(path, SCHEMA_PATH, label="capture manifest")


def _acceptance(path: Path) -> tuple[dict[str, object], bytes]:
    return _schema_object(path, ACCEPTANCE_SCHEMA_PATH, label="capture acceptance record")


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


def _first_event(path: Path, backend: ModuleType) -> dict[str, object]:
    """Validate only the immutable acceptance prefix of an active capture.

    A simulator may append page and terminal events after the acceptance record
    is durable. Those mutable suffixes cannot change what bytes the endpoint
    accepted, so they are deliberately outside this active-evidence check.
    """

    with path.open("r", encoding="utf-8") as stream:
        line = stream.readline()
    if not line.strip():
        raise ValueError("capture event chain has no first acceptance event")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("first capture event must be an object")
    event_hash = value.get("event_sha256")
    if not isinstance(event_hash, str) or event_hash != backend._event_digest(value):
        raise ValueError("first capture acceptance event hash is invalid")
    return value


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
    events_path = job_dir / "events.jsonl"
    manifest_path = job_dir / "manifest.json"
    acceptance_path = job_dir / ACCEPTANCE_FILENAME
    for path in (input_path, events_path):
        if not path.is_file():
            raise ValueError("required endpoint evidence is missing")
    backend = _load_backend()
    input_hash = _sha256_file(input_path)
    if input_hash != approved_brf_sha256:
        raise ValueError("endpoint-received bytes do not match the approved baseline BRF")

    if manifest_path.is_file():
        manifest, manifest_bytes = _manifest(manifest_path)
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
            or manifest.get("received_sha256") != input_hash
        ):
            raise ValueError("capture manifest conflicts with the terminal event chain")
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
            "idempotency_key": _idempotency_key(
                baseline_id=baseline_id,
                production_link_id=production_link_id,
                expected_state_version=expected_state_version,
            ),
        }

    if not acceptance_path.is_file():
        raise ValueError("capture has neither immutable acceptance nor terminal manifest evidence")
    acceptance, acceptance_bytes = _acceptance(acceptance_path)
    accepted = _first_event(events_path, backend)
    details = accepted.get("details")
    if (
        acceptance.get("scheduler_job_id") != job_id
        or acceptance.get("job_title") != expected_title
        or acceptance.get("received_sha256") != input_hash
        or acceptance.get("byte_length_received") != input_path.stat().st_size
        or acceptance.get("simulated_endpoint_id") != ENDPOINT_ID
        or acceptance.get("truth_basis") != "SIMULATED_DEMO"
        or accepted.get("event_type") != "ACCEPTED"
        or accepted.get("event_sha256") != acceptance.get("accepted_event_sha256")
        or accepted.get("previous_event_sha256") != acceptance.get("previous_event_sha256")
        or accepted.get("previous_event_sha256") is not None
        or not isinstance(details, dict)
        or details.get("scheduler_job_id") != job_id
        or details.get("job_title") != expected_title
        or details.get("received_sha256") != input_hash
        or details.get("byte_length_received") != input_path.stat().st_size
        or details.get("simulated_endpoint_id") != ENDPOINT_ID
        or details.get("truth_basis") != "SIMULATED_DEMO"
        or accepted.get("recorded_at") != acceptance.get("accepted_at")
    ):
        raise ValueError("active capture acceptance conflicts with immutable lineage")
    accepted_event_hash = acceptance.get("accepted_event_sha256")
    accepted_at = acceptance.get("accepted_at")
    previous_event_hash = acceptance.get("previous_event_sha256")
    if not isinstance(accepted_event_hash, str) or not isinstance(accepted_at, str):
        raise TypeError("active capture acceptance is incomplete")

    return {
        "schema_version": "endpoint-evidence-submission.v2",
        "baseline_id": baseline_id,
        "production_link_id": production_link_id,
        "scheduler_job_id": job_id,
        "scheduler_job_title": expected_title,
        "site_id": SITE_ID,
        "queue_name": QUEUE_NAME,
        "simulated_endpoint_id": ENDPOINT_ID,
        "approved_baseline_brf_sha256": approved_brf_sha256,
        "endpoint_received_sha256": input_hash,
        "capture_manifest_sha256": None,
        "terminal_event_sha256": None,
        "capture_acceptance_sha256": _sha256_bytes(acceptance_bytes),
        "accepted_event_sha256": accepted_event_hash,
        "previous_event_sha256": previous_event_hash,
        "capture_state": "RECEIVED",
        "evidence_timestamp": accepted_at,
        "truth_basis": "SIMULATED_DEMO",
        "expected_baseline_state_version": expected_state_version,
        "idempotency_key": _idempotency_key(
            baseline_id=baseline_id,
            production_link_id=production_link_id,
            expected_state_version=expected_state_version,
        ),
    }


def _idempotency_key(
    *, baseline_id: str, production_link_id: str, expected_state_version: int
) -> str:
    return _sha256_bytes(
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
