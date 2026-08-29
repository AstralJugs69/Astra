from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from braille_errata_relay.adapters.adk_assessor import AdkSemanticAssessor, AssessmentTrace
from braille_errata_relay.adapters.firestore_ledger import FirestoreGate0Ledger
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
    async def assess_with_trace(self, _evidence: object) -> AssessmentTrace:
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

    async def record_assessment_execution(self, record: dict[str, object]) -> bool:
        self.records.append(record)
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


def _client(ledger: FakeLedger | None = None) -> TestClient:
    return TestClient(
        create_app(
            cloud_settings=_settings(),
            assessor=cast(AdkSemanticAssessor, FakeAssessor()),
            ledger=cast(FirestoreGate0Ledger, ledger),
            identity_verifier=cast(IdentityVerifier, FakeVerifier()),
        )
    )


def test_health_remains_available_inside_private_service_without_route_token() -> None:
    assert _client().get("/healthz").status_code == 200


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
