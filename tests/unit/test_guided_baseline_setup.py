from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from braille_errata_relay.api import main as api_main
from braille_errata_relay.api.main import create_app
from braille_errata_relay.api.security import IdentityVerifier, VerifiedIdentity
from braille_errata_relay.application.baseline_registration import BaselineRegistrationWorkflow
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.domain.models import (
    BaselineStatus,
    SourceLocator,
    SourceMetadata,
    SourceRevision,
    SourceSnapshot,
)


class FakeVerifier:
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity:
        return VerifiedIdentity(email=token, subject="test-subject", audience=audience)


class ConfiguredProvider:
    service = object()
    expected_file_id = "1ConfiguredSourceIdentity12345"
    supported_mime_type = "application/vnd.google-apps.document"
    max_bytes = 1_048_576


class FakeDriveWorkflow:
    provider = ConfiguredProvider()

    async def initialize(self) -> SimpleNamespace:
        return SimpleNamespace(source_revision_ids=("drive:configured:7:source",))


class CandidateProvider:
    def __init__(
        self,
        *,
        service: object,
        expected_file_id: str,
        supported_mime_type: str,
        max_bytes: int,
    ) -> None:
        assert service is ConfiguredProvider.service
        self.expected_file_id = expected_file_id
        self.supported_mime_type = supported_mime_type
        self.max_bytes = max_bytes

    async def fetch_revision(self, locator: SourceLocator) -> SourceSnapshot:
        metadata = SourceMetadata(
            locator=locator,
            provider_version="7",
            modified_at=datetime(2026, 8, 31, tzinfo=UTC),
            byte_length=35,
        )
        return SourceSnapshot(
            revision=SourceRevision(
                revision_id=f"drive:{self.expected_file_id}:7:source",
                metadata=metadata,
                source_sha256="a" * 64,
                fetched_at=datetime(2026, 8, 31, tzinfo=UTC),
            ),
            source_bytes=b"# Biology\n\nMitochondria make ATP.\n",
        )


class FakeBaselineWorkflow:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def register_demo_fixture(self, **values: object) -> SimpleNamespace:
        self.calls.append(values)
        baseline = SimpleNamespace(
            baseline_id="b" * 64,
            production_id=values["production_id"],
            source_revision_id=values["source_revision_id"],
            source_sha256="a" * 64,
            approved_brf_sha256="c" * 64,
            translation_profile_sha256="d" * 64,
            site_id=values["site_id"],
            queue_name=values["queue_name"],
            status=BaselineStatus.AWAITING_PRODUCTION_LINK,
            state_version=0,
        )
        record = SimpleNamespace(
            baseline=baseline,
            created_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
        return SimpleNamespace(record=record, duplicate=False)


def _settings() -> CloudSettings:
    return CloudSettings(
        project_id="project-12345",
        cloud_run_region="europe-west3",
        google_cloud_location="europe-west3",
        gemini_model="gemini-test",
        drive_file_id=ConfiguredProvider.expected_file_id,
        drive_source_mime_type=ConfiguredProvider.supported_mime_type,
        artifact_bucket="artifact-bucket",
        runtime_service_account_email=("relay-runtime@project-12345.iam.gserviceaccount.com"),
        internal_oidc_audience="https://relay.example.test",
        demonstrator_principal_email="demonstrator@example.com",
        site_id="demo-site",
        cups_queue_name="Braille-Embosser-Sim",
    )


def _client(workflow: FakeBaselineWorkflow | None = None) -> TestClient:
    return TestClient(
        create_app(
            cloud_settings=_settings(),
            drive_workflow=cast(DriveGate0Workflow, FakeDriveWorkflow()),
            baseline_workflow=cast(
                BaselineRegistrationWorkflow,
                workflow or FakeBaselineWorkflow(),
            ),
            identity_verifier=cast(IdentityVerifier, FakeVerifier()),
        )
    )


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer demonstrator@example.com"}


def test_setup_status_is_private_and_never_returns_raw_drive_identity() -> None:
    client = _client()

    unauthenticated = client.get("/api/v1/setup/source")
    response = client.get("/api/v1/setup/source", headers=_headers())

    assert unauthenticated.status_code == 401
    assert response.status_code == 200
    assert response.json()["runtime_service_account_email"].startswith("relay-runtime@")
    assert response.json()["source_mime_type"] == ConfiguredProvider.supported_mime_type
    assert ConfiguredProvider.expected_file_id not in response.text
    assert len(response.json()["source_file_id_sha256"]) == 64


def test_source_verification_is_read_only_sanitized_and_detects_configured_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "DriveBlobProvider", CandidateProvider)
    client = _client()

    response = client.post(
        "/api/v1/setup/source-verifications",
        headers=_headers(),
        json={
            "file_id": ConfiguredProvider.expected_file_id,
            "mime_type": ConfiguredProvider.supported_mime_type,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "VERIFIED"
    assert response.json()["matches_configured_source"] is True
    assert response.json()["block_count"] == 2
    assert ConfiguredProvider.expected_file_id not in response.text


def test_guided_registration_derives_configured_revision_and_performs_no_production_action() -> (
    None
):
    workflow = FakeBaselineWorkflow()
    client = _client(workflow)

    response = client.post(
        "/api/v1/setup/baselines",
        headers=_headers(),
        json={
            "production_id": "BIOLOGY-DEMO",
            "site_id": "demo-site",
            "queue_name": "Braille-Embosser-Sim",
            "idempotency_key": "guided-registration-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["baseline"]["baseline_id"] == "b" * 64
    assert response.json()["production_action"] == "NOT_PERFORMED"
    assert ConfiguredProvider.expected_file_id not in response.text
    assert workflow.calls == [
        {
            "production_id": "BIOLOGY-DEMO",
            "source_revision_id": "drive:configured:7:source",
            "expected_file_id": ConfiguredProvider.expected_file_id,
            "approval_label": "DEMO_FIXTURE_APPROVED",
            "site_id": "demo-site",
            "queue_name": "Braille-Embosser-Sim",
            "idempotency_key": "guided-registration-1",
        }
    ]
