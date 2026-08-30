from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from braille_errata_relay.domain.models import (
    AttestationType,
    CandidateApprovalInvalidation,
    ContainmentConfirmation,
    HumanTimelineEventKind,
    IncidentState,
    IncidentTimelineEvent,
    OperatorAttestation,
    ProfessionalDecision,
    ProfessionalDisposition,
    ProofDecision,
    ProofRecord,
    SiteObservation,
    TruthBasis,
)

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "local_bridge" / "src" / "relay_bridge" / "observation_builder.py"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
LIVE_CLOSURE_EVIDENCE = ROOT / "demo" / "evidence" / "report-first-live-closure.json"
ACTIVE_REVIEW_EVIDENCE = ROOT / "demo" / "evidence" / "active-professional-review.json"
CONTAINMENT_PROOF_EVIDENCE = ROOT / "demo" / "evidence" / "slice-2-3-containment-proof-gate.json"


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

    acceptance = json.loads((job_dir / "capture-acceptance.json").read_text(encoding="utf-8"))
    acceptance_errors = sorted(
        _validator("capture-acceptance.v1.json").iter_errors(acceptance), key=str
    )
    assert acceptance_errors == []
    assert acceptance["received_sha256"] == manifest["received_sha256"]
    assert acceptance["truth_basis"] == "SIMULATED_DEMO"


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


def test_advisory_production_link_and_endpoint_receipt_match_versioned_schemas() -> None:
    advisory = {
        "schema_version": "baseline-production-link.v2",
        "link_id": "1" * 64,
        "baseline_id": "2" * 64,
        "scheduler_job_id": 19,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'3' * 12}|BASELINE",
        "site_observation_id": "4" * 64,
        "site_id": "demo-site",
        "bridge_id": "single-pc-bridge",
        "queue_name": "Braille-Embosser-Sim",
        "baseline_brf_sha256": "3" * 64,
        "baseline_state_version": 1,
        "idempotency_key_sha256": "5" * 64,
        "evidence_observed_at": "2026-08-29T17:00:00+00:00",
        "linked_at": "2026-08-29T17:00:01+00:00",
        "verified_at": None,
        "verification_basis": "READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY",
    }
    receipt = {
        "schema_version": "endpoint-receipt.v1",
        "receipt_id": "6" * 64,
        "baseline_id": "2" * 64,
        "production_link_id": "1" * 64,
        "scheduler_job_id": 19,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'3' * 12}|BASELINE",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
        "simulated_endpoint_id": "relay-capture://demo-embosser",
        "approved_baseline_brf_sha256": "3" * 64,
        "endpoint_received_sha256": "3" * 64,
        "capture_manifest_sha256": "7" * 64,
        "terminal_event_sha256": "8" * 64,
        "capture_state": "COMPLETED",
        "evidence_timestamp": "2026-08-29T17:00:02+00:00",
        "verified_at": "2026-08-29T17:00:03+00:00",
        "truth_basis": "SIMULATED_DEMO",
        "submitting_principal": "endpoint@example.iam.gserviceaccount.com",
        "idempotency_key_sha256": "9" * 64,
        "expected_baseline_state_version": 1,
        "baseline_state_version": 2,
        "artifact_uri": "gs://test/endpoint-receipts/receipt.json",
    }

    assert (
        sorted(_validator("baseline-production-link.v2.json").iter_errors(advisory), key=str) == []
    )
    assert sorted(_validator("endpoint-receipt.v1.json").iter_errors(receipt), key=str) == []


def test_active_endpoint_receipt_submission_and_receipt_match_v2_schemas() -> None:
    submission = {
        "schema_version": "endpoint-evidence-submission.v2",
        "baseline_id": "1" * 64,
        "production_link_id": "2" * 64,
        "scheduler_job_id": 20,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'3' * 12}|BASELINE",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
        "simulated_endpoint_id": "relay-capture://demo-embosser",
        "approved_baseline_brf_sha256": "3" * 64,
        "endpoint_received_sha256": "3" * 64,
        "capture_manifest_sha256": None,
        "terminal_event_sha256": None,
        "capture_acceptance_sha256": "4" * 64,
        "accepted_event_sha256": "5" * 64,
        "previous_event_sha256": None,
        "capture_state": "RECEIVED",
        "evidence_timestamp": "2026-08-30T12:00:00+00:00",
        "truth_basis": "SIMULATED_DEMO",
        "expected_baseline_state_version": 2,
        "idempotency_key": "6" * 64,
    }
    receipt = {
        **submission,
        "schema_version": "endpoint-receipt.v2",
        "receipt_id": "7" * 64,
        "verified_at": "2026-08-30T12:00:01+00:00",
        "submitting_principal": "endpoint@example.iam.gserviceaccount.com",
        "idempotency_key_sha256": "8" * 64,
        "baseline_state_version": 3,
        "artifact_uri": "gs://test/endpoint-receipts/receipt.json",
    }
    del receipt["idempotency_key"]

    assert (
        sorted(_validator("endpoint-evidence-submission.v2.json").iter_errors(submission), key=str)
        == []
    )
    assert sorted(_validator("endpoint-receipt.v2.json").iter_errors(receipt), key=str) == []


def test_superseding_production_link_matches_append_only_v3_schema() -> None:
    payload = {
        "schema_version": "baseline-production-link.v3",
        "link_id": "1" * 64,
        "baseline_id": "2" * 64,
        "supersedes_production_link_id": "3" * 64,
        "scheduler_job_id": 43,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'4' * 12}|BASELINE",
        "site_observation_id": "5" * 64,
        "site_id": "demo-site",
        "bridge_id": "single-pc-bridge",
        "queue_name": "Braille-Embosser-Sim",
        "baseline_brf_sha256": "4" * 64,
        "baseline_state_version": 2,
        "idempotency_key_sha256": "6" * 64,
        "evidence_observed_at": "2026-08-30T12:00:00+00:00",
        "linked_at": "2026-08-30T12:00:01+00:00",
        "verified_at": None,
        "verification_basis": "READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY",
    }

    assert (
        sorted(_validator("baseline-production-link.v3.json").iter_errors(payload), key=str) == []
    )


def test_historical_link_correction_matches_append_only_schema() -> None:
    payload = {
        "schema_version": "baseline-link-correction.v1",
        "correction_id": "1" * 64,
        "baseline_id": "2" * 64,
        "production_link_id": "3" * 64,
        "expected_baseline_state_version": 1,
        "baseline_state_version": 2,
        "reason": "PRIOR_LINK_LACKED_ENDPOINT_BYTE_CONFIRMATION",
        "prior_report_id": "4" * 64,
        "prior_report_created_before_endpoint_confirmation": True,
        "corrected_at": "2026-08-29T20:00:00+00:00",
        "submitting_principal": "endpoint@example.iam.gserviceaccount.com",
        "idempotency_key_sha256": "5" * 64,
    }

    assert (
        sorted(_validator("baseline-link-correction.v1.json").iter_errors(payload), key=str) == []
    )


def test_slice_2_1_1_evidence_matches_sanitized_schema() -> None:
    payload = json.loads(
        (ROOT / "demo" / "evidence" / "byte-confirmed-link.json").read_text(encoding="utf-8")
    )

    assert (
        sorted(_validator("byte-confirmed-link-evidence.v1.json").iter_errors(payload), key=str)
        == []
    )


def test_human_review_records_match_append_only_contract_schemas() -> None:
    disposition = ProfessionalDisposition(
        record_id="1" * 64,
        incident_id="2" * 64,
        decision=ProfessionalDecision.HALT_REQUESTED,
        selected_role="production_coordinator",
        expected_state_version=0,
        idempotency_key="human-halt-1",
        note="Request manual containment review.",
        actor_principal="coordinator@example.test",
        recorded_at="2026-08-30T12:00:00+00:00",
    )
    attestation = OperatorAttestation(
        record_id="3" * 64,
        incident_id=disposition.incident_id,
        attestation_type=AttestationType.PHYSICAL_OUTPUT_ISOLATED,
        truth_basis=TruthBasis.SIMULATED_DEMO,
        selected_role="machine_operator",
        expected_state_version=1,
        idempotency_key="human-isolation-1",
        note="The simulated endpoint output was isolated.",
        actor_principal="operator@example.test",
        recorded_at="2026-08-30T12:01:00+00:00",
    )
    timeline = IncidentTimelineEvent(
        event_id="4" * 64,
        incident_id=disposition.incident_id,
        kind=HumanTimelineEventKind.OPERATOR_ATTESTATION,
        record_id=attestation.record_id,
        state_version=2,
        actor_principal=attestation.actor_principal,
        recorded_at=attestation.recorded_at,
    )

    assert (
        sorted(
            _validator("professional-disposition.v1.json").iter_errors(
                disposition.model_dump(mode="json")
            ),
            key=str,
        )
        == []
    )
    assert (
        sorted(
            _validator("operator-attestation.v1.json").iter_errors(
                attestation.model_dump(mode="json")
            ),
            key=str,
        )
        == []
    )
    assert (
        sorted(
            _validator("incident-timeline-event.v1.json").iter_errors(
                timeline.model_dump(mode="json")
            ),
            key=str,
        )
        == []
    )


def test_containment_and_exact_candidate_proof_records_match_contract_schemas() -> None:
    containment = ContainmentConfirmation(
        record_id="5" * 64,
        incident_id="2" * 64,
        halt_disposition_record_id="1" * 64,
        site_observation_id="3" * 64,
        queue_name="Braille-Embosser-Sim",
        scheduler_job_id=42,
        observed_job_state="CANCELED",
        observed_at="2026-08-30T12:02:00+00:00",
        physical_output_isolation_attestation_id="4" * 64,
        selected_role="production_coordinator",
        expected_state_version=2,
        idempotency_key="containment-1",
        note="Coordinator confirms the exact evidence set.",
        actor_principal="coordinator@example.test",
        recorded_at="2026-08-30T12:03:00+00:00",
    )
    proof = ProofRecord(
        record_id="6" * 64,
        incident_id=containment.incident_id,
        candidate_sha256="7" * 64,
        manifest_sha256="8" * 64,
        source_revision_id="drive:file:63:revision",
        source_sha256="9" * 64,
        translation_profile_id="demo-ueb-40x25-v1",
        translation_profile_sha256="a" * 64,
        liblouis_version="3.38.0",
        translation_tables=(
            {"name": "en-ueb-g2.ctb", "sha256": "b" * 64},
            {"name": "en-us-brf.dis", "sha256": "c" * 64},
        ),
        formatter_version="relay-formatter.v1",
        decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
        review_basis="DEMO_FIXTURE_REVIEW",
        selected_role="proofreader",
        expected_state_version=4,
        idempotency_key="proof-1",
        note="Fixture review only, not a production-master approval.",
        findings=("Exact candidate identity reviewed.",),
        actor_principal="proofreader@example.test",
        recorded_at="2026-08-30T12:04:00+00:00",
    )
    invalidation = CandidateApprovalInvalidation(
        record_id="d" * 64,
        incident_id=containment.incident_id,
        prior_candidate_sha256=proof.candidate_sha256,
        current_candidate_sha256="e" * 64,
        prior_state=IncidentState.AWAITING_REPLACEMENT,
        recorded_at="2026-08-30T12:05:00+00:00",
    )

    for schema, record in (
        ("containment-confirmation.v1.json", containment),
        ("proof-record.v1.json", proof),
        ("candidate-approval-invalidation.v1.json", invalidation),
    ):
        assert sorted(_validator(schema).iter_errors(record.model_dump(mode="json")), key=str) == []

    invalid_payload = proof.model_dump(mode="json")
    invalid_payload["review_basis"] = "HUMAN_REVIEW"
    assert list(_validator("proof-record.v1.json").iter_errors(invalid_payload))


def test_active_professional_review_evidence_is_sanitized_and_does_not_claim_a_live_run() -> None:
    payload = json.loads(ACTIVE_REVIEW_EVIDENCE.read_text(encoding="utf-8"))

    assert (
        sorted(
            _validator("active-professional-review-evidence.v1.json").iter_errors(payload), key=str
        )
        == []
    )
    assert payload["live_story"]["status"] == "NOT_RUN"
    rendered = json.dumps(payload).casefold()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "private_key",
        "client_email",
        "drive.google.com",
    ):
        assert forbidden not in rendered


def test_containment_proof_evidence_is_sanitized_and_preserves_human_authority() -> None:
    payload = json.loads(CONTAINMENT_PROOF_EVIDENCE.read_text(encoding="utf-8"))

    assert (
        sorted(
            _validator("slice-2-3-containment-proof-gate-evidence.v1.json").iter_errors(payload),
            key=str,
        )
        == []
    )
    assert payload["live_story"]["status"] == "NOT_TOUCHED"
    assert payload["live_story"]["candidate_approved"] == "NOT_CLAIMED"
    rendered = json.dumps(payload).casefold()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "private_key",
        "client_email",
        "drive.google.com",
        "gs://",
        "password",
    ):
        assert forbidden not in rendered
