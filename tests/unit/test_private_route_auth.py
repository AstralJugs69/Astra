from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from braille_errata_relay.adapters.adk_assessor import AdkSemanticAssessor, AssessmentTrace
from braille_errata_relay.adapters.firestore_ledger import (
    FirestoreGate0Ledger,
    SemanticClaimStatus,
    SemanticExecutionClaim,
)
from braille_errata_relay.api.main import create_app
from braille_errata_relay.api.security import (
    IdentityVerifier,
    OidcVerificationError,
    VerifiedIdentity,
)
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import SemanticAssessment


class FakeVerifier:
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity:
        if token == "invalid":
            raise OidcVerificationError("invalid")
        return VerifiedIdentity(email=token, subject="safe-subject", audience=audience)


class FakeAssessor:
    model_id = "gemini-test"
    prompt_version = "semantic-assessment.v1"

    def __init__(self) -> None:
        self.calls = 0

    async def assess_with_trace(
        self,
        _evidence: object,
        *,
        analysis_revision: int = 1,
    ) -> AssessmentTrace:
        assert analysis_revision == 1
        self.calls += 1
        assessment = SemanticAssessment(
            assessment_id="a" * 64,
            analysis_revision=1,
            model_id="gemini-test",
            prompt_version="semantic-assessment.v1",
            materiality="MATERIAL",
            change_kind="FACTUAL_CORRECTION",
            summary="The scientific referent changed.",
            rationale=("The terms identify different organelles.",),
            evidence_span_ids=("old:block-17", "new:block-17"),
            uncertainties=(),
            confidence="MEDIUM",
            requires_professional_review=True,
        )
        return AssessmentTrace(
            assessment=assessment,
            latency_ms=12,
            attempts=1,
            outcome_sha256=canonical_sha256(assessment.model_dump(mode="json")),
        )


class FakeLedger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self.assessment: SemanticAssessment | None = None
        self.active_lease: str | None = None

    async def claim_semantic_execution(self, **values: object) -> SemanticExecutionClaim:
        execution_key = str(values["execution_key"])
        if self.assessment is not None:
            return SemanticExecutionClaim(
                execution_key=execution_key,
                status=SemanticClaimStatus.READY,
                assessment=self.assessment,
            )
        self.active_lease = str(values["lease_token"])
        return SemanticExecutionClaim(
            execution_key=execution_key,
            status=SemanticClaimStatus.ACQUIRED,
            lease_token=self.active_lease,
        )

    async def complete_semantic_execution(self, **values: object) -> bool:
        assert values["lease_token"] == self.active_lease
        self.assessment = cast(SemanticAssessment, values["assessment"])
        return False

    async def record_semantic_attempt(self, **values: object) -> bool:
        self.records.append(cast(dict[str, object], values["sanitized_record"]))
        return False


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


def _payload() -> dict[str, object]:
    return {
        "schema_version": "cloud-gate0-source-job.v1",
        "job_kind": "SEMANTIC_GATE0_SMOKE",
        "evidence": {
            "schema_version": "semantic-assessment-input.v1",
            "evidence_spans": [
                {
                    "span_id": "old:block-17",
                    "side": "old",
                    "block_kind": "paragraph",
                    "text": "Mitochondria store genetic instructions.",
                },
                {
                    "span_id": "new:block-17",
                    "side": "new",
                    "block_kind": "paragraph",
                    "text": "The nucleus stores genetic instructions.",
                },
            ],
            "impact_summary": {
                "pages_changed": True,
                "baseline_page_count": 1,
                "candidate_page_count": 1,
            },
        },
    }


def _client(
    ledger: FakeLedger | None = None,
    assessor: FakeAssessor | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            cloud_settings=_settings(),
            assessor=cast(AdkSemanticAssessor, assessor or FakeAssessor()),
            ledger=cast(FirestoreGate0Ledger, ledger),
            identity_verifier=cast(IdentityVerifier, FakeVerifier()),
        )
    )


def test_health_remains_available_inside_private_service_without_route_token() -> None:
    client = _client()

    assert client.get("/health").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_internal_route_requires_verified_oidc_bearer() -> None:
    response = _client().post("/internal/source-jobs", json=_payload())
    assert response.status_code == 401
    assert "token" not in response.text.lower()


def test_valid_invoker_is_rejected_on_the_wrong_internal_route() -> None:
    response = _client().post(
        "/internal/source-jobs",
        json=_payload(),
        headers={"Authorization": "Bearer scheduler@example.iam.gserviceaccount.com"},
    )
    assert response.status_code == 403


def test_source_principal_receives_schema_valid_assessment_and_persists_trace() -> None:
    ledger = FakeLedger()
    response = _client(ledger).post(
        "/internal/source-jobs",
        json=_payload(),
        headers={"Authorization": "Bearer source@example.iam.gserviceaccount.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assessment"]["schema_version"] == "semantic-assessment.v1"
    assert body["execution"]["model_id"] == "gemini-test"
    assert body["duplicate_execution"] is False
    assert len(ledger.records) == 1
    assert set(ledger.records[0]).isdisjoint({"source", "source_text", "token"})


def test_duplicate_semantic_request_reuses_first_result_without_model_call() -> None:
    ledger = FakeLedger()
    assessor = FakeAssessor()
    client = _client(ledger, assessor)
    headers = {"Authorization": "Bearer source@example.iam.gserviceaccount.com"}

    first = client.post("/internal/source-jobs", json=_payload(), headers=headers)
    second = client.post("/internal/source-jobs", json=_payload(), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert assessor.calls == 1
    assert first.json()["assessment"] == second.json()["assessment"]
    assert second.json()["duplicate_execution"] is True
    assert second.json()["execution"]["outcome"] == "REUSED_FIRST_VALID"


def test_source_principal_cannot_use_scheduler_route() -> None:
    response = _client().post(
        "/internal/drive-reconcile",
        headers={"Authorization": "Bearer source@example.iam.gserviceaccount.com"},
    )
    assert response.status_code == 403


def test_invalid_identity_is_rejected_without_claim_details() -> None:
    response = _client().post(
        "/internal/source-jobs",
        json=_payload(),
        headers={"X-Serverless-Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "OIDC verification failed"}


def test_demonstrator_is_the_only_principal_admitted_to_baseline_api() -> None:
    payload = {
        "production_id": "WO-DEMO-001",
        "production_id_origin": "EXTERNAL_REFERENCE",
        "source": {
            "provider": "google_drive",
            "file_id": "file",
            "revision_id": "drive:file:62:" + "a" * 64,
        },
        "artifact_origin": "DEMO_GENERATED_FIXTURE",
        "approved_brf_sha256": None,
        "approval_label": "DEMO_FIXTURE_APPROVED",
        "translation_profile_id": "demo-ueb-40x25-v1",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
        "idempotency_key": "baseline-demo-v1",
    }
    admitted = _client().post(
        "/api/v1/baselines",
        json=payload,
        headers={"Authorization": "Bearer demonstrator@example.com"},
    )
    denied = _client().post(
        "/api/v1/baselines",
        json=payload,
        headers={"Authorization": "Bearer source@example.iam.gserviceaccount.com"},
    )

    assert admitted.status_code == 503
    assert admitted.json()["detail"] == "baseline workflow is not configured"
    assert denied.status_code == 403


def test_telemetry_and_scheduler_principals_are_route_scoped() -> None:
    telemetry = _client().post(
        "/internal/site-observations",
        json={},
        headers={"Authorization": "Bearer telemetry@example.iam.gserviceaccount.com"},
    )
    outbox = _client().post(
        "/internal/outbox-drain",
        json={"schema_version": "outbox-drain-request.v1", "limit": 1},
        headers={"Authorization": "Bearer scheduler@example.iam.gserviceaccount.com"},
    )
    wrong_telemetry = _client().post(
        "/internal/site-observations",
        json={},
        headers={"Authorization": "Bearer source@example.iam.gserviceaccount.com"},
    )
    wrong_scheduler = _client().post(
        "/internal/outbox-drain",
        json={"schema_version": "outbox-drain-request.v1", "limit": 1},
        headers={"Authorization": "Bearer source@example.iam.gserviceaccount.com"},
    )

    assert telemetry.status_code == 503
    assert outbox.status_code == 503
    assert wrong_telemetry.status_code == 403
    assert wrong_scheduler.status_code == 403


def test_production_link_route_is_demonstrator_only() -> None:
    payload = {
        "schema_version": "baseline-production-link-request.v1",
        "scheduler_job_id": 42,
        "expected_state_version": 0,
        "idempotency_key": "a" * 64,
    }
    path = "/api/v1/baselines/" + "b" * 64 + "/production-links"

    admitted = _client().post(
        path,
        json=payload,
        headers={"Authorization": "Bearer demonstrator@example.com"},
    )
    denied = _client().post(
        path,
        json=payload,
        headers={"Authorization": "Bearer telemetry@example.iam.gserviceaccount.com"},
    )

    assert admitted.status_code == 503
    assert admitted.json()["detail"] == "production link is not configured"
    assert denied.status_code == 403


def test_endpoint_evidence_routes_use_only_the_dedicated_verified_principal() -> None:
    receipt = {
        "schema_version": "endpoint-evidence-submission.v1",
        "baseline_id": "a" * 64,
        "production_link_id": "b" * 64,
        "scheduler_job_id": 19,
        "scheduler_job_title": "BER|WO-DEMO-001|cccccccccccc|BASELINE",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
        "simulated_endpoint_id": "relay-capture://demo-embosser",
        "approved_baseline_brf_sha256": "c" * 64,
        "endpoint_received_sha256": "c" * 64,
        "capture_manifest_sha256": "d" * 64,
        "terminal_event_sha256": "e" * 64,
        "capture_state": "COMPLETED",
        "evidence_timestamp": "2026-08-29T20:00:00+00:00",
        "truth_basis": "SIMULATED_DEMO",
        "expected_baseline_state_version": 1,
        "idempotency_key": "f" * 64,
    }
    correction = {
        "schema_version": "baseline-link-correction-request.v1",
        "baseline_id": "a" * 64,
        "production_link_id": "b" * 64,
        "expected_state_version": 1,
        "prior_report_id": "c" * 64,
        "idempotency_key": "d" * 64,
    }

    for path, payload in (
        ("/internal/endpoint-receipts", receipt),
        ("/internal/baseline-link-corrections", correction),
    ):
        admitted = _client().post(
            path,
            json=payload,
            headers={"Authorization": "Bearer endpoint@example.iam.gserviceaccount.com"},
        )
        observer = _client().post(
            path,
            json=payload,
            headers={"Authorization": "Bearer telemetry@example.iam.gserviceaccount.com"},
        )
        demonstrator = _client().post(
            path,
            json=payload,
            headers={"Authorization": "Bearer demonstrator@example.com"},
        )

        assert admitted.status_code == 503
        assert observer.status_code == 403
        assert demonstrator.status_code == 403
