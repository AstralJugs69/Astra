from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient

from braille_errata_relay.api.main import _safe_candidate_manifest_evidence, create_app
from braille_errata_relay.api.security import IdentityVerifier, VerifiedIdentity
from braille_errata_relay.application.containment_proof import (
    ContainmentConfirmationResult,
    ContainmentProofConflict,
    ContainmentProofWorkflow,
    ProofRecordResult,
)
from braille_errata_relay.application.replacement_observation import (
    ApprovedCandidateDownload,
    ReplacementObservationLinkResult,
    ReplacementObservationRejected,
)
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    BlockingReason,
    BoundTranslationTable,
    ContainmentConfirmation,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    IncidentWorkflowStage,
    JobState,
    ProofDecision,
    ProofRecord,
    ReplacementObservationLink,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
INCIDENT_ID = "a" * 64


class _Verifier:
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity:
        return VerifiedIdentity(email=token, subject="test-subject", audience=audience)


class _Ledger:
    def __init__(self) -> None:
        self.checkpoint = IncidentCheckpoint(
            incident_id=INCIDENT_ID,
            baseline_id="b" * 64,
            new_source_revision_id="drive:file:63:" + "c" * 64,
            new_source_sha256="c" * 64,
            production_job_lineage_id="d" * 64,
            stage=IncidentWorkflowStage.REPORT_READY,
            state_version=3,
            candidate_brf=ArtifactRef(
                sha256="e" * 64,
                kind=ArtifactKind.FULL_CANDIDATE_BRF,
                byte_length=12,
                uri="gs://relay-test/candidate.brf",
            ),
            report=ArtifactRef(
                sha256="f" * 64,
                kind=ArtifactKind.REPORT,
                byte_length=12,
                uri="gs://relay-test/report.json",
            ),
            disposition_packet=ArtifactRef(
                sha256="1" * 64,
                kind=ArtifactKind.HUMAN_DISPOSITION_PACKET,
                byte_length=12,
                uri="gs://relay-test/packet.json",
            ),
            report_created_at=NOW,
            report_ready_at=NOW,
            updated_at=NOW,
        )

    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None:
        return self.checkpoint if incident_id == INCIDENT_ID else None

    async def list_incident_checkpoints(self) -> tuple[IncidentCheckpoint, ...]:
        return (self.checkpoint,)


class _IncidentWorkflow:
    bridge_id = "single-pc-bridge"


class _ContainmentProofWorkflow:
    def __init__(self) -> None:
        self.containment_call: dict[str, object] | None = None
        self.proof_call: dict[str, object] | None = None
        self.conflict = False

    @staticmethod
    def _state(state: IncidentState, version: int) -> IncidentReviewState:
        return IncidentReviewState(
            incident_id=INCIDENT_ID,
            baseline_id="b" * 64,
            state=state,
            state_version=version,
            report_ready_at=NOW,
            current_candidate_sha256="e" * 64,
            updated_at=NOW,
        )

    async def record_containment_confirmation(
        self,
        **values: object,
    ) -> ContainmentConfirmationResult:
        if self.conflict:
            raise ContainmentProofConflict()
        self.containment_call = values
        record = ContainmentConfirmation(
            record_id="2" * 64,
            incident_id=INCIDENT_ID,
            halt_disposition_record_id="3" * 64,
            site_observation_id="4" * 64,
            queue_name="Braille-Embosser-Sim",
            scheduler_job_id=42,
            observed_job_state=JobState.CANCELED,
            observed_at=NOW,
            physical_output_isolation_attestation_id="5" * 64,
            selected_role="production_coordinator",
            expected_state_version=2,
            idempotency_key="containment-api-1",
            actor_principal="demonstrator@example.test",
            recorded_at=NOW,
        )
        return ContainmentConfirmationResult(
            self._state(IncidentState.AWAITING_PROOF, 4), record, False
        )

    async def record_proof(self, **values: object) -> ProofRecordResult:
        if self.conflict:
            raise ContainmentProofConflict()
        self.proof_call = values
        record = ProofRecord(
            record_id="6" * 64,
            incident_id=INCIDENT_ID,
            candidate_sha256="e" * 64,
            manifest_sha256="7" * 64,
            source_revision_id="drive:file:63:" + "c" * 64,
            source_sha256="c" * 64,
            translation_profile_id="demo-ueb-40x25-v1",
            translation_profile_sha256="8" * 64,
            liblouis_version="3.38.0",
            translation_tables=(BoundTranslationTable(name="en-ueb-g2.ctb", sha256="9" * 64),),
            formatter_version="relay-formatter.v1",
            decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
            review_basis="DEMO_FIXTURE_REVIEW",
            selected_role="proofreader",
            expected_state_version=4,
            idempotency_key="proof-api-1",
            actor_principal="demonstrator@example.test",
            recorded_at=NOW,
        )
        return ProofRecordResult(self._state(IncidentState.AWAITING_REPLACEMENT, 6), record, False)


class _ReplacementObservationWorkflow:
    def __init__(self) -> None:
        self.download_blocked = False
        self.link_call: dict[str, object] | None = None

    @staticmethod
    def _state() -> IncidentReviewState:
        return IncidentReviewState(
            incident_id=INCIDENT_ID,
            baseline_id="b" * 64,
            state=IncidentState.REPLACEMENT_OBSERVED,
            state_version=7,
            report_ready_at=NOW,
            current_candidate_sha256="e" * 64,
            updated_at=NOW,
        )

    async def download_current_candidate(self, *, incident_id: str) -> ApprovedCandidateDownload:
        assert incident_id == INCIDENT_ID
        if self.download_blocked:
            raise ReplacementObservationRejected(
                BlockingReason.PROOF_NOT_ELIGIBLE,
                "proof approval is required",
            )
        return ApprovedCandidateDownload(
            content=b"immutable-candidate-brf\r\n",
            candidate_sha256="e" * 64,
            manifest_sha256="7" * 64,
            proof_record_id="6" * 64,
            filename=f"braille-errata-relay-{INCIDENT_ID[:12]}-{'e' * 12}.brf",
        )

    async def record_observation_link(self, **values: object) -> ReplacementObservationLinkResult:
        self.link_call = values
        link = ReplacementObservationLink(
            record_id="8" * 64,
            incident_id=INCIDENT_ID,
            approved_candidate_sha256="e" * 64,
            candidate_manifest_sha256="7" * 64,
            proof_record_id="6" * 64,
            original_scheduler_job_id=42,
            scheduler_job_id=43,
            observed_job_title=f"BER|{INCIDENT_ID}|{'e' * 12}|REPLACEMENT",
            site_id="demo-site",
            bridge_id="single-pc-bridge",
            queue_name="Braille-Embosser-Sim",
            site_observation_id="9" * 64,
            observed_job_state=JobState.PENDING_HELD,
            observed_at=NOW,
            selected_role="machine_operator",
            expected_state_version=6,
            idempotency_key="replacement-api-1",
            actor_principal="demonstrator@example.test",
            recorded_at=NOW,
        )
        return ReplacementObservationLinkResult(self._state(), link, False)


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
        demonstrator_principal_email="demonstrator@example.test",
        endpoint_evidence_principal_email="endpoint@example.iam.gserviceaccount.com",
    )


def _client(
    replacement: _ReplacementObservationWorkflow | None = None,
) -> tuple[TestClient, _ContainmentProofWorkflow]:
    workflow = _ContainmentProofWorkflow()
    return (
        TestClient(
            create_app(
                cloud_settings=_settings(),
                ledger=cast(object, _Ledger()),
                incident_workflow=cast(object, _IncidentWorkflow()),
                containment_proof_workflow=cast(ContainmentProofWorkflow, workflow),
                replacement_observation_workflow=cast(object, replacement),
                identity_verifier=cast(IdentityVerifier, _Verifier()),
            )
        ),
        workflow,
    )


def _headers(principal: str = "demonstrator@example.test") -> dict[str, str]:
    return {"Authorization": f"Bearer {principal}"}


def _containment_payload() -> dict[str, object]:
    return {
        "halt_disposition_record_id": "3" * 64,
        "site_observation_id": "4" * 64,
        "physical_output_isolation_attestation_id": "5" * 64,
        "selected_role": "production_coordinator",
        "expected_state_version": 2,
        "note": "Coordinator confirms evidence; no device action occurs here.",
        "idempotency_key": "containment-api-1",
    }


def _proof_payload() -> dict[str, object]:
    return {
        "candidate_sha256": "e" * 64,
        "manifest_sha256": "7" * 64,
        "decision": "APPROVED_FOR_HUMAN_SUBMISSION",
        "review_basis": "DEMO_FIXTURE_REVIEW",
        "selected_role": "proofreader",
        "expected_state_version": 4,
        "note": "Fixture proof is recorded for the exact candidate only.",
        "findings": ["Exact immutable lineage reviewed."],
        "visual_only_uncertainty": False,
        "idempotency_key": "proof-api-1",
    }


def _replacement_payload() -> dict[str, object]:
    return {
        "candidate_sha256": "e" * 64,
        "candidate_manifest_sha256": "7" * 64,
        "proof_record_id": "6" * 64,
        "scheduler_job_id": 43,
        "site_observation_id": "9" * 64,
        "selected_role": "machine_operator",
        "expected_state_version": 6,
        "note": "Operator links an independently submitted job observation only.",
        "idempotency_key": "replacement-api-1",
    }


def test_containment_and_proof_routes_are_demonstrator_only_and_derive_actor() -> None:
    client, workflow = _client()

    containment = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/containment-confirmations",
        json=_containment_payload(),
        headers=_headers(),
    )
    proof = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/proof-records",
        json=_proof_payload(),
        headers=_headers(),
    )
    denied = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/proof-records",
        json=_proof_payload(),
        headers=_headers("source@example.test"),
    )

    assert containment.status_code == 201
    assert containment.json()["status"] == "AWAITING_PROOF"
    assert proof.status_code == 201
    assert proof.json()["status"] == "AWAITING_REPLACEMENT"
    assert proof.json()["next_human_stage"] == "AWAITING_HUMAN_SUBMISSION"
    assert denied.status_code == 403
    assert workflow.containment_call is not None
    assert workflow.proof_call is not None
    assert workflow.containment_call["actor_principal"] == "demonstrator@example.test"
    assert workflow.proof_call["actor_principal"] == "demonstrator@example.test"


def test_containment_and_proof_routes_reject_spoofed_roles_control_fields_and_stale_forms() -> None:
    client, workflow = _client()
    containment_path = f"/api/v1/incidents/{INCIDENT_ID}/containment-confirmations"
    proof_path = f"/api/v1/incidents/{INCIDENT_ID}/proof-records"

    wrong_role = client.post(
        containment_path,
        json={**_containment_payload(), "selected_role": "machine_operator"},
        headers=_headers(),
    )
    control_field = client.post(
        proof_path,
        json={**_proof_payload(), "cancel": True},
        headers=_headers(),
    )
    workflow.conflict = True
    stale = client.post(proof_path, json=_proof_payload(), headers=_headers())

    assert wrong_role.status_code == 422
    assert control_field.status_code == 422
    assert stale.status_code == 409
    assert stale.json()["blocking_reason"] == "STALE_STATE_VERSION"


def test_candidate_manifest_browser_evidence_omits_private_artifact_locations() -> None:
    evidence = _safe_candidate_manifest_evidence(
        {
            "schema_version": "artifact-manifest.v1",
            "artifact_sha256": "a" * 64,
            "source_revision_id": "drive:file:63:revision",
            "source_map_uri": "gs://private-relay-bucket/source-maps/secret.json",
            "unrelated_private_field": "must not cross the boundary",
        }
    )

    assert evidence == {
        "schema_version": "artifact-manifest.v1",
        "artifact_sha256": "a" * 64,
        "source_revision_id": "drive:file:63:revision",
    }


def test_current_candidate_download_is_private_rehashed_and_has_no_artifact_path_input() -> None:
    replacement = _ReplacementObservationWorkflow()
    client, _ = _client(replacement)
    path = f"/api/v1/incidents/{INCIDENT_ID}/approved-candidate"

    admitted = client.get(path, headers=_headers())
    wrong_principal = client.get(path, headers=_headers("source@example.test"))
    arbitrary_query = client.get(path + "?uri=gs://private/example", headers=_headers())
    replacement.download_blocked = True
    blocked = client.get(path, headers=_headers())

    expected_filename = f"braille-errata-relay-{INCIDENT_ID[:12]}-{'e' * 12}.brf"
    assert admitted.status_code == 200
    assert admitted.content == b"immutable-candidate-brf\r\n"
    assert admitted.headers["cache-control"] == "no-store"
    assert admitted.headers["content-disposition"] == f'attachment; filename="{expected_filename}"'
    assert admitted.headers["x-content-type-options"] == "nosniff"
    assert wrong_principal.status_code == 403
    assert arbitrary_query.status_code == 200
    assert arbitrary_query.content == admitted.content
    assert blocked.status_code == 403
    assert blocked.content != admitted.content


def test_replacement_link_route_is_demonstrator_only_derives_actor_and_stops_at_observed() -> None:
    replacement = _ReplacementObservationWorkflow()
    client, _ = _client(replacement)
    path = f"/api/v1/incidents/{INCIDENT_ID}/replacement-observation-links"

    admitted = client.post(path, json=_replacement_payload(), headers=_headers())
    wrong_principal = client.post(
        path,
        json=_replacement_payload(),
        headers=_headers("source@example.test"),
    )
    bad_role = client.post(
        path,
        json={**_replacement_payload(), "selected_role": "proofreader"},
        headers=_headers(),
    )
    control_field = client.post(
        path,
        json={**_replacement_payload(), "cancel": True},
        headers=_headers(),
    )

    assert admitted.status_code == 201
    assert admitted.json()["status"] == "REPLACEMENT_OBSERVED"
    assert admitted.json()["next_human_stage"] == (
        "OBSERVED_REPLACEMENT_REQUIRES_SEPARATE_VERIFICATION"
    )
    assert "RESOLVED_BY_HUMAN" not in admitted.text
    assert wrong_principal.status_code == 403
    assert bad_role.status_code == 422
    assert control_field.status_code == 422
    assert replacement.link_call is not None
    assert replacement.link_call["actor_principal"] == "demonstrator@example.test"
