from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from braille_errata_relay.api.main import create_app
from braille_errata_relay.api.security import IdentityVerifier, VerifiedIdentity
from braille_errata_relay.application.professional_review import (
    OperatorAttestationResult,
    ProfessionalDispositionResult,
    ProfessionalReviewConflict,
    ProfessionalReviewWorkflow,
)
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    BlockingReason,
    HumanTimelineEventKind,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    IncidentTimelineEvent,
    IncidentWorkflowStage,
    JobState,
    OperatorAttestation,
    ProfessionalDisposition,
    QueueObservation,
    SiteObservation,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
INCIDENT_ID = "a" * 64


class FakeVerifier:
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity:
        return VerifiedIdentity(email=token, subject="demonstrator-subject", audience=audience)


class FakeArtifactStore:
    def __init__(self) -> None:
        self.values = {
            "c" * 64: {
                "semantic_assessment": {
                    "summary": "A <script>source string</script> changed.",
                    "uncertainties": ["Check the referent."],
                },
                "braille_impact": {"old_page_range": [1, 1], "new_page_range": [1, 1]},
            },
            "d" * 64: {"authority_notice": ["Relay does not control CUPS."]},
        }

    async def read(self, ref: ArtifactRef) -> bytes:
        return json.dumps(self.values[ref.sha256]).encode("utf-8")


class FakeIncidentWorkflow:
    bridge_id = "single-pc-bridge"

    def __init__(self) -> None:
        self.artifact_store = FakeArtifactStore()


class FakeReviewLedger:
    def __init__(self, checkpoint: IncidentCheckpoint) -> None:
        self.checkpoint = checkpoint
        self.state: IncidentReviewState | None = None
        self.dispositions: dict[str, ProfessionalDisposition] = {}
        self.attestations: dict[str, OperatorAttestation] = {}
        self.events: list[IncidentTimelineEvent] = []
        self.baseline: FakeBaseline | None = None
        self.observation: SiteObservation | None = None

    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None:
        return self.checkpoint if incident_id == self.checkpoint.incident_id else None

    async def list_incident_checkpoints(self) -> tuple[IncidentCheckpoint, ...]:
        return (self.checkpoint,)

    async def get_baseline(self, _baseline_id: str) -> FakeBaseline | None:
        return self.baseline

    async def get_latest_site_observation(self, **_kwargs: object) -> SiteObservation | None:
        return self.observation

    async def get_production_link(self, _baseline_id: str) -> None:
        return None

    async def get_endpoint_receipt_for_link(self, **_kwargs: object) -> None:
        return None

    async def get_incident_review_state(self, _incident_id: str) -> IncidentReviewState | None:
        return self.state

    async def list_incident_timeline_events(
        self,
        _incident_id: str,
    ) -> tuple[IncidentTimelineEvent, ...]:
        return tuple(self.events)

    async def get_professional_disposition(
        self,
        record_id: str,
    ) -> ProfessionalDisposition | None:
        return self.dispositions.get(record_id)

    async def get_operator_attestation(self, record_id: str) -> OperatorAttestation | None:
        return self.attestations.get(record_id)


class FakeBaseline:
    class Record:
        site_id = "demo-site"
        queue_name = "Braille-Embosser-Sim"
        scheduler_job_id = 17

    baseline = Record()

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {"baseline": {"scheduler_job_id": self.baseline.scheduler_job_id}}


class FakeProfessionalReviewWorkflow:
    def __init__(self, ledger: FakeReviewLedger) -> None:
        self.ledger = ledger
        self.disposition_call: dict[str, object] | None = None
        self.raise_conflict = False

    async def record_disposition(self, **values: object) -> ProfessionalDispositionResult:
        if self.raise_conflict:
            raise ProfessionalReviewConflict("stale")
        self.disposition_call = values
        record = ProfessionalDisposition(
            record_id="e" * 64,
            incident_id=cast(str, values["incident_id"]),
            decision=values["decision"],
            selected_role=values["selected_role"],
            expected_state_version=values["expected_state_version"],
            idempotency_key=values["idempotency_key"],
            note=values["note"],
            actor_principal=values["actor_principal"],
            recorded_at=NOW,
        )
        state = IncidentReviewState(
            incident_id=record.incident_id,
            baseline_id=self.ledger.checkpoint.baseline_id,
            state=IncidentState(record.decision.value),
            state_version=1,
            report_ready_at=self.ledger.checkpoint.report_ready_at or NOW,
            current_candidate_sha256="b" * 64,
            blocking_reason=self.ledger.checkpoint.blocking_reason,
            last_attributable_evidence_id=record.record_id,
            updated_at=NOW,
        )
        self.ledger.state = state
        self.ledger.dispositions[record.record_id] = record
        self.ledger.events.append(
            IncidentTimelineEvent(
                event_id="f" * 64,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.PROFESSIONAL_DISPOSITION,
                record_id=record.record_id,
                state_version=1,
                actor_principal=record.actor_principal,
                recorded_at=NOW,
            )
        )
        return ProfessionalDispositionResult(state, record, False)

    async def record_operator_attestation(self, **values: object) -> OperatorAttestationResult:
        record = OperatorAttestation(
            record_id="1" * 64,
            incident_id=cast(str, values["incident_id"]),
            attestation_type=values["attestation_type"],
            truth_basis=values["truth_basis"],
            selected_role=values["selected_role"],
            expected_state_version=values["expected_state_version"],
            idempotency_key=values["idempotency_key"],
            note=values["note"],
            actor_principal=values["actor_principal"],
            recorded_at=NOW,
        )
        state = IncidentReviewState(
            incident_id=record.incident_id,
            baseline_id=self.ledger.checkpoint.baseline_id,
            state=IncidentState.CONTAINMENT_IN_PROGRESS,
            state_version=2,
            report_ready_at=self.ledger.checkpoint.report_ready_at or NOW,
            current_candidate_sha256="b" * 64,
            blocking_reason=self.ledger.checkpoint.blocking_reason,
            last_attributable_evidence_id=record.record_id,
            updated_at=NOW,
        )
        self.ledger.state = state
        self.ledger.attestations[record.record_id] = record
        self.ledger.events.append(
            IncidentTimelineEvent(
                event_id="2" * 64,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.OPERATOR_ATTESTATION,
                record_id=record.record_id,
                state_version=2,
                actor_principal=record.actor_principal,
                recorded_at=NOW,
            )
        )
        return OperatorAttestationResult(state, record, False)


def _checkpoint() -> IncidentCheckpoint:
    return IncidentCheckpoint(
        incident_id=INCIDENT_ID,
        baseline_id="9" * 64,
        new_source_revision_id="drive:file:63:" + "8" * 64,
        new_source_sha256="8" * 64,
        production_job_lineage_id="7" * 64,
        stage=IncidentWorkflowStage.NEEDS_REVIEW,
        state_version=3,
        candidate_brf=ArtifactRef(
            sha256="b" * 64,
            kind=ArtifactKind.FULL_CANDIDATE_BRF,
            byte_length=12,
            uri="gs://relay-test/candidate.brf",
        ),
        report=ArtifactRef(
            sha256="c" * 64,
            kind=ArtifactKind.REPORT,
            byte_length=12,
            uri="gs://relay-test/report.json",
        ),
        disposition_packet=ArtifactRef(
            sha256="d" * 64,
            kind=ArtifactKind.HUMAN_DISPOSITION_PACKET,
            byte_length=12,
            uri="gs://relay-test/packet.json",
        ),
        report_created_at=NOW,
        report_ready_at=NOW,
        blocking_reason=BlockingReason.SEMANTIC_REVIEW_REQUIRED,
        updated_at=NOW,
    )


def _settings() -> CloudSettings:
    return CloudSettings(
        project_id="test-project",
        cloud_run_region="europe-west3",
        google_cloud_location="europe-west3",
        gemini_model="gemini-test",
        internal_oidc_audience="https://relay.example.run.app",
        source_push_principal_email="source@example.iam.gserviceaccount.com",
        telemetry_push_principal_email="telemetry@example.iam.gserviceaccount.com",
        scheduler_principal_email="scheduler@example.iam.gserviceaccount.com",
        demonstrator_principal_email="demonstrator@example.com",
        endpoint_evidence_principal_email="endpoint@example.iam.gserviceaccount.com",
    )


def _client() -> tuple[TestClient, FakeProfessionalReviewWorkflow]:
    ledger = FakeReviewLedger(_checkpoint())
    workflow = FakeProfessionalReviewWorkflow(ledger)
    client = TestClient(
        create_app(
            cloud_settings=_settings(),
            ledger=cast(object, ledger),
            incident_workflow=cast(object, FakeIncidentWorkflow()),
            professional_review_workflow=cast(ProfessionalReviewWorkflow, workflow),
            identity_verifier=cast(IdentityVerifier, FakeVerifier()),
        )
    )
    return client, workflow


def _headers(principal: str = "demonstrator@example.com") -> dict[str, str]:
    return {"Authorization": f"Bearer {principal}"}


def _disposition_payload() -> dict[str, object]:
    return {
        "decision": "HALT_REQUESTED",
        "selected_role": "production_coordinator",
        "expected_state_version": 0,
        "note": "Request manual containment review.",
        "idempotency_key": "halt-request-1",
    }


def test_review_routes_are_demonstrator_only_and_server_derives_actor() -> None:
    client, workflow = _client()
    path = f"/api/v1/incidents/{INCIDENT_ID}/professional-dispositions"

    admitted = client.post(path, json=_disposition_payload(), headers=_headers())
    denied = client.post(path, json=_disposition_payload(), headers=_headers("source@example.test"))

    assert admitted.status_code == 201
    assert denied.status_code == 403
    assert workflow.disposition_call is not None
    assert workflow.disposition_call["actor_principal"] == "demonstrator@example.com"
    assert "actor_principal" not in _disposition_payload()


def test_human_payloads_reject_roles_actor_spoofing_and_control_fields() -> None:
    client, _ = _client()
    path = f"/api/v1/incidents/{INCIDENT_ID}/professional-dispositions"

    wrong_role = client.post(
        path,
        json={**_disposition_payload(), "selected_role": "machine_operator"},
        headers=_headers(),
    )
    spoofed_actor = client.post(
        path,
        json={**_disposition_payload(), "actor_principal": "attacker@example.test"},
        headers=_headers(),
    )
    control_attempt = client.post(
        path,
        json={**_disposition_payload(), "cancel": True},
        headers=_headers(),
    )

    assert wrong_role.status_code == 422
    assert spoofed_actor.status_code == 422
    assert control_attempt.status_code == 422


def test_incident_list_detail_and_timeline_keep_block_and_human_facts_separate() -> None:
    client, _ = _client()
    path = f"/api/v1/incidents/{INCIDENT_ID}/professional-dispositions"
    assert client.post(path, json=_disposition_payload(), headers=_headers()).status_code == 201

    listing = client.get("/api/v1/incidents", headers=_headers())
    detail = client.get(f"/api/v1/incidents/{INCIDENT_ID}", headers=_headers())
    timeline = client.get(f"/api/v1/incidents/{INCIDENT_ID}/timeline", headers=_headers())

    assert listing.status_code == 200
    assert listing.json()["incidents"][0]["blocking_reason"] == "SEMANTIC_REVIEW_REQUIRED"
    assert detail.status_code == 200
    assert detail.json()["review_state"]["state"] == "HALT_REQUESTED"
    assert timeline.status_code == 200
    events = timeline.json()["events"]
    assert events[0]["kind"] == "REPORT_READY"
    assert events[1]["kind"] == "PROFESSIONAL_DISPOSITION"
    assert events[1]["truth_basis"] == "HUMAN_ATTESTATION"


def test_review_conflict_returns_a_stale_form_response() -> None:
    client, workflow = _client()
    workflow.raise_conflict = True

    response = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/professional-dispositions",
        json=_disposition_payload(),
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["blocking_reason"] == "STALE_STATE_VERSION"


def test_canceled_queue_observation_is_explicitly_not_a_device_or_isolation_attestation() -> None:
    client, workflow = _client()
    workflow.ledger.baseline = FakeBaseline()
    workflow.ledger.observation = SiteObservation(
        observation_id="6" * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        sequence=1,
        observed_at=NOW + timedelta(minutes=1),
        observations=(
            QueueObservation(
                scheduler_job_id=17,
                owner="relay-operator",
                title="BER|WO-DEMO-001|cccccccccccc|BASELINE",
                destination="Braille-Embosser-Sim",
                state=JobState.CANCELED,
                observed_at=NOW + timedelta(minutes=1),
                completed_at=NOW + timedelta(seconds=30),
            ),
        ),
    )

    response = client.get(f"/api/v1/incidents/{INCIDENT_ID}/timeline", headers=_headers())

    assert response.status_code == 200
    cancellation = next(
        event
        for event in response.json()["events"]
        if event["kind"] == "QUEUE_CANCELLATION_OBSERVED"
    )
    assert cancellation["truth_basis"] == "REAL_READ_ONLY_OBSERVATION"
    assert cancellation["device_stop_confirmed"] is False
    assert cancellation["physical_output_isolated"] is False


def test_api_source_contains_no_cups_or_subprocess_control_surface() -> None:
    source = Path(create_app.__code__.co_filename).read_text(encoding="utf-8").lower()

    assert "import subprocess" not in source
    assert "cups.connection" not in source
    assert '@app.post("/api/v1/cups' not in source
