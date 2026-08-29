from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from braille_errata_relay.domain.models import SiteObservation

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "local_bridge" / "src" / "relay_bridge" / "observation_builder.py"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
LIVE_CLOSURE_EVIDENCE = ROOT / "demo" / "evidence" / "report-first-live-closure.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _normalized_snapshot() -> dict[str, object]:
    observed_at = "2026-08-28T17:00:00+00:00"
    return {
        "queue_name": "Braille-Embosser-Sim",
        "observed_at": observed_at,
        "jobs": [
            {
                "scheduler_job_id": 42,
                "owner": "relay-operator",
                "title": "BER|WO-DEMO-001|abc|BASELINE",
                "destination": "Braille-Embosser-Sim",
                "state": "PROCESSING",
                "state_reasons": ["processing-to-device"],
                "observed_at": observed_at,
                "job_created_at": "2026-08-28T16:59:00+00:00",
                "processing_at": observed_at,
                "completed_at": None,
                "impressions_completed": 1,
            }
        ],
        "printer": {
            "printer_state": "processing",
            "printer_state_reasons": ["processing"],
            "printer_accepting_jobs": True,
        },
    }


def test_generated_site_observation_matches_schema_and_domain_contract() -> None:
    builder = _load_module("observation_builder_contract", BRIDGE_PATH)
    payload = builder.build_observation(
        site_id="demo-site",
        bridge_id="bridge-1",
        queue_name="Braille-Embosser-Sim",
        sequence=1,
        queue_snapshot=_normalized_snapshot(),
        previous_sha256=None,
    )
    errors = sorted(_validator("site-observation.v1.json").iter_errors(payload), key=str)
    assert errors == []
    SiteObservation.model_validate(payload)
    assert "attributes" not in json.dumps(payload)


def test_site_observation_rejects_raw_cups_attributes() -> None:
    builder = _load_module("observation_builder_raw_contract", BRIDGE_PATH)
    snapshot = _normalized_snapshot()
    snapshot["jobs"] = [{**snapshot["jobs"][0], "attributes": {"job-state": 5}}]
    with pytest.raises((TypeError, ValueError), match="raw CUPS attributes"):
        builder.build_observation(
            site_id="demo-site",
            bridge_id="bridge-1",
            queue_name="Braille-Embosser-Sim",
            sequence=1,
            queue_snapshot=snapshot,
            previous_sha256=None,
        )


def test_capture_manifest_and_event_chain_match_schema(tmp_path: Path) -> None:
    backend = _load_module("capture_manifest_contract", BACKEND_PATH)
    row = b"a   "
    input_path = tmp_path / "candidate.brf"
    input_path.write_bytes(b"\r\n".join((row, b"    ")))
    capture_root = tmp_path / "captures"
    backend.run_backend(
        device_uri=backend.DEVICE_URI,
        job_id_text="42",
        title="BER|INCIDENT|abc|REPLACEMENT",
        input_path=str(input_path),
        capture_root=capture_root,
        cells_per_line=4,
        lines_per_page=2,
        page_delay_seconds=0,
    )
    job_dir = capture_root / "42"
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    errors = sorted(_validator("capture-manifest.v1.json").iter_errors(manifest), key=str)
    assert errors == []
    first_previous, terminal = backend.verify_event_chain(job_dir / "events.jsonl")
    assert first_previous is None
    assert terminal == manifest["terminal_event_sha256"]
    assert manifest["events_sha256"] == manifest["terminal_event_sha256"]
    assert manifest["completed_at"] == manifest["finished_at"]


def test_sanitized_cloud_gate0_evidence_matches_schema() -> None:
    payload = json.loads(
        (ROOT / "demo" / "evidence" / "cloud-gate0.json").read_text(encoding="utf-8")
    )
    errors = sorted(_validator("cloud-gate0-evidence.v1.json").iter_errors(payload), key=str)
    assert errors == []
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "raw_cursor",
        "source_content",
    ):
        assert forbidden not in serialized


def test_sanitized_report_first_evidence_matches_schema() -> None:
    payload = json.loads(
        (ROOT / "demo" / "evidence" / "report-first.json").read_text(encoding="utf-8")
    )
    errors = sorted(_validator("report-first-evidence.v1.json").iter_errors(payload), key=str)
    assert errors == []
    assert payload["status"] == "BLOCKED"
    assert payload["cloud"]["live_routes"] == "BLOCKED"
    assert payload["cloud"]["scheduler"] == "PAUSED_BLOCKED"
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "private_key",
        "client_email",
        "project_id",
    ):
        assert forbidden not in serialized


def test_live_closure_evidence_contract_rejects_private_or_incomplete_records() -> None:
    payload = {
        "schema_version": "report-first-live-closure-evidence.v1",
        "recorded_at": "2026-08-29T18:00:00+00:00",
        "branch": "codex/slice-2-1-live-closure",
        "base_commit": "a" * 40,
        "governing_documents": {
            "instruction.md": "b" * 64,
            "architecture.md": "c" * 64,
        },
        "service": {
            "region": "europe-west3",
            "revision": "braille-errata-relay-00008-rmg",
            "image_digest": "sha256:" + "d" * 64,
            "private": True,
            "project_id_sha256": "e" * 64,
        },
        "authenticated_routes": {
            "health_status": 200,
            "ready_status": 200,
            "ready": True,
            "reserved_healthz_status": 404,
        },
        "scheduler": {
            "request_body_sha256": "f" * 64,
            "prior_failed_http_status": 500,
            "scheduler_http_status": 200,
            "uvicorn_http_status": 200,
            "successful_execution_count": 1,
            "recovery_after_repair": True,
            "paused_after_evidence": True,
        },
        "baseline_link": {
            "baseline_id": "1" * 64,
            "approved_brf_sha256": "2" * 64,
            "scheduler_job_id": 17,
            "site_observation_id": "3" * 64,
            "production_link_id": "4" * 64,
            "status": "PRODUCTION_LINK_VERIFIED",
        },
        "source_reconciliation": {
            "v2_source_sha256": "5" * 64,
            "reused_durable_same_file_revision": True,
        },
        "workflow": {
            "incident_id": "6" * 64,
            "stage": "NEEDS_REVIEW",
            "incident_count": 1,
            "candidate_brf_sha256": "7" * 64,
            "candidate_count": 1,
            "semantic_assessment_id": "8" * 64,
            "semantic_execution_count": 1,
            "semantic_attempt_count": 1,
            "report_sha256": "9" * 64,
            "report_count": 1,
            "disposition_packet_sha256": "a" * 64,
            "disposition_packet_count": 1,
            "report_created_at_allocated": True,
            "report_ready_at_allocated": True,
        },
        "replay": {
            "same_source_converged": True,
            "duplicate_source_receipt": True,
            "same_outbox_converged": True,
            "outbox_replay_http_status": 200,
            "outbox_replay_leased": 0,
            "outbox_replay_completed": 0,
            "outbox_replay_retried": 0,
            "outbox_replay_message_count": 0,
        },
        "verification": {
            "windows_tests": "190 passed",
            "frozen_lock": "PASS",
            "ruff_lint": "PASS",
            "scoped_format": "PASS",
            "strict_mypy": "PASS",
            "schema_validation": "PASS",
            "local_container_build": "PASS",
            "remote_container_build": "PASS",
            "wsl_liblouis_goldens": "PASS",
            "container_smoke": "PASS",
            "wsl_container_brf_identity": "PASS",
            "historical_gate0_evidence_preserved": True,
        },
        "iam_cleanup": True,
        "notifications": "NOT_CLAIMED",
        "remaining_blockers": [],
    }

    errors = sorted(
        _validator("report-first-live-closure-evidence.v1.json").iter_errors(payload),
        key=str,
    )

    assert errors == []
    rendered = json.dumps(payload).lower()
    for forbidden in ("access_token", "id_token", "api_key", "credentials", 'project_id"'):
        assert forbidden not in rendered


def test_committed_live_closure_evidence_validates_and_is_sanitized() -> None:
    payload = json.loads(LIVE_CLOSURE_EVIDENCE.read_text(encoding="utf-8"))

    errors = sorted(
        _validator("report-first-live-closure-evidence.v1.json").iter_errors(payload),
        key=str,
    )

    assert errors == []
    assert payload["scheduler"]["successful_execution_count"] == 1
    assert payload["workflow"]["incident_count"] == 1
    assert payload["workflow"]["semantic_execution_count"] == 1
    assert payload["replay"]["outbox_replay_leased"] == 0
    assert payload["remaining_blockers"] == []
    rendered = json.dumps(payload).casefold()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "private_key",
        "client_email",
        'project_id"',
        "drive.google.com",
    ):
        assert forbidden not in rendered


def test_baseline_production_link_matches_immutable_schema() -> None:
    payload = {
        "schema_version": "baseline-production-link.v1",
        "link_id": "1" * 64,
        "baseline_id": "2" * 64,
        "scheduler_job_id": 42,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'3' * 12}|BASELINE",
        "site_observation_id": "4" * 64,
        "site_id": "demo-site",
        "bridge_id": "single-pc-bridge",
        "queue_name": "Braille-Embosser-Sim",
        "baseline_brf_sha256": "3" * 64,
        "baseline_state_version": 1,
        "idempotency_key_sha256": "5" * 64,
        "evidence_observed_at": "2026-08-29T17:00:00+00:00",
        "verified_at": "2026-08-29T17:00:01+00:00",
        "verification_basis": "READ_ONLY_EXACT_JOB_QUEUE_TITLE_AND_HASH_PREFIX",
    }

    errors = sorted(_validator("baseline-production-link.v1.json").iter_errors(payload), key=str)

    assert errors == []
