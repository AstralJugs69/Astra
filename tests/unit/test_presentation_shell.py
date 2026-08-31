from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.oauth2.credentials import Credentials as UserAdcCredentials

from braille_errata_relay.presentation import app as presentation_app
from braille_errata_relay.presentation.app import (
    GoogleAudienceTokenProvider,
    PresentationAuthenticationError,
    PresentationSettings,
    PrivateReviewApiError,
    _settings_from_args,
    create_presentation_app,
    main,
)

INCIDENT_ID = "a" * 64
AUDIENCE = "https://private-relay.example.test"
DEMONSTRATOR_IDENTITY = "relay-demonstrator@project-12345.iam.gserviceaccount.com"


class FakePrivateReviewApi:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, object]]] = []
        self.downloads: list[str] = []

    async def get_json(self, path: str) -> dict[str, object]:
        if path == "/api/v1/setup/source":
            return {
                "status": "CONFIGURED",
                "runtime_service_account_email": "relay-runtime@project-12345.iam.gserviceaccount.com",
                "project_id": "project-12345",
                "cloud_run_region": "europe-west3",
                "source_mime_type": "application/vnd.google-apps.document",
                "site_id": "demo-site",
                "queue_name": "Braille-Embosser-Sim",
            }
        if path == f"/api/v1/setup/baselines/{'d' * 64}":
            return {
                "baseline_id": "d" * 64,
                "production_id": "BIOLOGY-DEMO",
                "approved_brf_sha256": "e" * 64,
                "site_id": "demo-site",
                "queue_name": "Braille-Embosser-Sim",
                "status": "AWAITING_PRODUCTION_LINK",
                "state_version": 0,
                "created_at": "2026-08-31T12:00:00+00:00",
            }
        if path == "/api/v1/incidents":
            return {
                "incidents": [
                    {
                        "incident_id": INCIDENT_ID,
                        "review_state": {"state": "NEEDS_REVIEW"},
                        "blocking_reason": "SEMANTIC_REVIEW_REQUIRED",
                    }
                ]
            }
        if path == "/api/v1/baselines":
            return {
                "baselines": [
                    {
                        "baseline_id": "d" * 64,
                        "production_id": "BIOLOGY-DEMO",
                        "status": "PRODUCTION_LINK_VERIFIED",
                        "state_version": 2,
                        "site_id": "demo-site",
                        "queue_name": "Braille-Embosser-Sim",
                        "approved_brf_sha256": "e" * 64,
                        "created_at": "2026-08-31T12:00:00+00:00",
                    }
                ]
            }
        if path == f"/api/v1/incidents/{INCIDENT_ID}":
            return {
                "review_state": {
                    "state": "NEEDS_REVIEW",
                    "state_version": 0,
                    "blocking_reason": "SEMANTIC_REVIEW_REQUIRED",
                },
                "source_correction": {"new": "A <script>must be escaped</script>."},
                "report": {
                    "semantic_assessment": {
                        "summary": "Semantic summary <img src=x onerror=alert(1)>",
                        "uncertainties": ["Confirm terminology."],
                    },
                    "braille_impact": {"old_page_range": [1, 1], "new_page_range": [1, 1]},
                },
                "human_disposition_packet": {
                    "baseline_brf_sha256": "b" * 64,
                    "candidate_brf": {"sha256": "c" * 64},
                    "observation_age_seconds": 2.0,
                },
                "current_site_observation": {"observed_at": "2026-08-30T12:00:00+00:00"},
            }
        if path == f"/api/v1/incidents/{INCIDENT_ID}/timeline":
            return {
                "events": [
                    {
                        "kind": "QUEUE_CANCELLATION_OBSERVED",
                        "truth_basis": "REAL_READ_ONLY_OBSERVATION",
                        "recorded_at": "2026-08-30T12:01:00+00:00",
                        "device_stop_confirmed": False,
                        "physical_output_isolated": False,
                    },
                    {
                        "kind": "OPERATOR_ATTESTATION",
                        "truth_basis": "HUMAN_ATTESTATION",
                        "recorded_at": "2026-08-30T12:02:00+00:00",
                    },
                ]
            }
        raise AssertionError(f"unexpected path: {path}")

    async def post_json(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        self.posts.append((path, dict(payload)))
        if path == "/api/v1/setup/source-verifications":
            return {
                "status": "VERIFIED",
                "matches_configured_source": True,
                "source_file_id_sha256": "f" * 64,
                "source_mime_type": payload["mime_type"],
                "source_sha256": "a" * 64,
                "byte_length": 128,
                "block_count": 3,
            }
        if path == "/api/v1/setup/baselines":
            return {
                "status": "REGISTERED",
                "baseline": {"baseline_id": "d" * 64},
            }
        return {"status": "HALT_REQUESTED"}

    async def get_bytes(self, path: str) -> tuple[bytes, str]:
        self.downloads.append(path)
        return (
            b"fixture-approved-candidate\r\n",
            f'attachment; filename="braille-errata-relay-{INCIDENT_ID[:12]}-{("c" * 12)}.brf"',
        )


class GateEligiblePrivateReviewApi(FakePrivateReviewApi):
    """Private API fixture with one authoritative human gate at a time."""

    def __init__(self, *, gate: str) -> None:
        super().__init__()
        self.gate = gate

    async def get_json(self, path: str) -> dict[str, object]:
        response = await super().get_json(path)
        if path != f"/api/v1/incidents/{INCIDENT_ID}":
            return response
        if self.gate == "containment":
            return {
                **response,
                "review_state": {"state": "CONTAINMENT_IN_PROGRESS", "state_version": 2},
                "review_actions": {
                    "containment_confirmation": {
                        "eligible": True,
                        "blocking_reason": None,
                        "halt_disposition_record_id": "b" * 64,
                        "site_observation_id": "c" * 64,
                        "physical_output_isolation_attestation_id": "d" * 64,
                    },
                    "proof": {"eligible": False, "blocking_reason": "PROOF_NOT_ELIGIBLE"},
                },
            }
        if self.gate == "replacement":
            return {
                **response,
                "review_state": {"state": "AWAITING_REPLACEMENT", "state_version": 6},
                "candidate_manifest": {"artifact_sha256": "c" * 64},
                "profile_identity": {"profile_id": "demo-ueb-40x25-v1"},
                "current_site_observation": {
                    "observation_id": "e" * 64,
                    "observed_at": "2026-08-30T12:00:00+00:00",
                },
                "review_actions": {
                    "containment_confirmation": {
                        "eligible": False,
                        "blocking_reason": "CONTAINMENT_CONFIRMATION_REQUIRED",
                    },
                    "proof": {"eligible": False, "blocking_reason": "PROOF_NOT_ELIGIBLE"},
                    "replacement_observation": {
                        "eligible": True,
                        "candidate_download_eligible": True,
                        "blocking_reason": None,
                        "provenance": {
                            "candidate_sha256": "c" * 64,
                            "manifest_sha256": "d" * 64,
                            "proof_record_id": "f" * 64,
                        },
                    },
                },
            }
        return {
            **response,
            "review_state": {"state": "AWAITING_PROOF", "state_version": 4},
            "candidate_manifest": {"artifact_sha256": "c" * 64},
            "profile_identity": {"profile_id": "demo-ueb-40x25-v1"},
            "candidate_evidence_preview": {
                "label": "TEXT EVIDENCE PREVIEW ONLY — NOT TACTILE PROOF",
                "text": "Fixture-only preview.",
            },
            "review_actions": {
                "containment_confirmation": {
                    "eligible": False,
                    "blocking_reason": "CONTAINMENT_CONFIRMATION_REQUIRED",
                },
                "proof": {
                    "eligible": True,
                    "blocking_reason": None,
                    "provenance": {
                        "candidate_sha256": "c" * 64,
                        "manifest_sha256": "d" * 64,
                    },
                },
            },
        }


class UnavailablePrivateReviewApi:
    async def get_json(self, _path: str) -> dict[str, object]:
        raise PresentationAuthenticationError("private data unavailable")

    async def post_json(self, _path: str, _payload: Mapping[str, object]) -> dict[str, object]:
        raise AssertionError("fallback review controls must not submit")


class RejectedSourcePrivateReviewApi(FakePrivateReviewApi):
    async def post_json(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        if path == "/api/v1/setup/source-verifications":
            raise PrivateReviewApiError(
                422,
                sanitized_detail="The exported document contains a paragraph longer than 512 characters.",
            )
        return await super().post_json(path, payload)


def _client() -> tuple[TestClient, FakePrivateReviewApi]:
    api = FakePrivateReviewApi()
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
        ),
        api_client=api,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765"), api


def _gate_client(gate: str) -> tuple[TestClient, GateEligiblePrivateReviewApi]:
    api = GateEligiblePrivateReviewApi(gate=gate)
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
        ),
        api_client=api,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765"), api


def test_hosted_public_dashboard_is_read_only_and_explains_both_access_paths() -> None:
    api = FakePrivateReviewApi()
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
            hosted_read_only=True,
            public_origin="https://astra-public.example.run.app",
            source_document_url="https://docs.google.com/document/d/safe-demo/edit",
        ),
        api_client=api,
    )
    client = TestClient(app, base_url="https://astra-public.example.run.app")

    overview = client.get("/")
    guide = client.get("/test-astra")
    incident = client.get(f"/incidents/{INCIDENT_ID}")
    rejected = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions", data={"decision": "HALT"}
    )

    assert overview.status_code == 200
    assert "Public read-only view" in overview.text
    assert "Registered baselines" in overview.text
    assert "Past reports and human outcomes" in overview.text
    assert "Register baseline" not in overview.text
    assert guide.status_code == 200
    assert "Path A" in guide.text
    assert "Path B" in guide.text
    assert "https://docs.google.com/document/d/safe-demo/edit" in guide.text
    assert incident.status_code == 200
    assert "Public read-only view" in incident.text
    assert "<form" not in incident.text
    assert rejected.status_code == 405
    assert rejected.text == "Hosted dashboard is read-only."
    assert api.posts == []


def test_hosted_public_dashboard_hides_eligible_human_forms_and_candidate_downloads() -> None:
    api = GateEligiblePrivateReviewApi(gate="proof")
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
            hosted_read_only=True,
            public_origin="https://astra-public.example.run.app",
        ),
        api_client=api,
    )
    client = TestClient(app, base_url="https://astra-public.example.run.app")

    detail = client.get(f"/incidents/{INCIDENT_ID}")
    candidate = client.get(f"/incidents/{INCIDENT_ID}/approved-candidate")

    assert detail.status_code == 200
    assert "Public read-only view" in detail.text
    assert '<form method="post"' not in detail.text
    assert "Download exact approved candidate BRF" not in detail.text
    assert candidate.status_code == 404
    assert api.posts == []


def _csrf(client: TestClient) -> str:
    response = client.get(f"/incidents/{INCIDENT_ID}")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _setup_csrf(client: TestClient) -> str:
    response = client.get("/setup/source")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def test_guided_source_setup_verifies_configured_google_doc_without_echoing_id() -> None:
    client, api = _client()
    csrf = _setup_csrf(client)
    raw_file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    response = client.post(
        "/setup/source/verify",
        data={
            "csrf_token": csrf,
            "source_reference": f"https://docs.google.com/document/d/{raw_file_id}/edit",
            "mime_type": "application/vnd.google-apps.document",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
    )

    assert response.status_code == 200
    assert "Source verified and configured" in response.text
    assert "Continue to baseline" in response.text
    assert raw_file_id not in response.text
    assert api.posts[-1] == (
        "/api/v1/setup/source-verifications",
        {
            "file_id": raw_file_id,
            "mime_type": "application/vnd.google-apps.document",
        },
    )


def test_guided_source_setup_shows_the_runtime_sanitized_rejection_detail() -> None:
    api = RejectedSourcePrivateReviewApi()
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
        ),
        api_client=api,
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    response = client.post(
        "/setup/source/verify",
        data={
            "csrf_token": _setup_csrf(client),
            "source_reference": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            "mime_type": "application/vnd.google-apps.document",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
    )

    assert response.status_code == 422
    assert "Runtime detail:" in response.text
    assert "paragraph longer than 512 characters" in response.text


def test_guided_baseline_registration_requires_verification_then_redirects_to_monitor() -> None:
    client, api = _client()
    unverified = client.get("/setup/baseline")
    assert "Verify the currently configured source" in unverified.text
    csrf = _setup_csrf(client)
    client.post(
        "/setup/source/verify",
        data={
            "csrf_token": csrf,
            "source_reference": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            "mime_type": "application/vnd.google-apps.document",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    baseline_page = client.get("/setup/baseline")
    match = re.search(r'name="csrf_token" value="([^"]+)"', baseline_page.text)
    assert match is not None

    registered = client.post(
        "/setup/baseline",
        data={
            "csrf_token": match.group(1),
            "production_id": "BIOLOGY-DEMO",
            "site_id": "demo-site",
            "queue_name": "Braille-Embosser-Sim",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert registered.status_code == 303
    assert registered.headers["location"] == f"/baselines/{'d' * 64}"
    payload = api.posts[-1]
    assert payload[0] == "/api/v1/setup/baselines"
    assert payload[1]["production_id"] == "BIOLOGY-DEMO"
    assert isinstance(payload[1]["idempotency_key"], str)
    monitor = client.get(registered.headers["location"])
    assert monitor.status_code == 200
    assert "Registration successful" in monitor.text
    assert "No CUPS/device action" in monitor.text


def _disposition_form(csrf: str) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "decision": "HALT_REQUESTED",
        "selected_role": "production_coordinator",
        "expected_state_version": "0",
        "note": "Request manual containment review.",
        "idempotency_key": "presentation-halt-1",
    }


def test_presentation_is_server_rendered_escaped_and_uses_strict_http_only_session_cookie() -> None:
    client, _ = _client()
    response = client.get(f"/incidents/{INCIDENT_ID}")

    assert response.status_code == 200
    assert "&lt;script&gt;must be escaped&lt;/script&gt;" in response.text
    assert "<script>must be escaped</script>" not in response.text
    assert "[REAL]" in response.text
    assert "[HUMAN ATTESTATION]" in response.text
    assert "Human workflow state:" in response.text
    assert "Choose a decision" in response.text
    assert 'select name="decision" required' in response.text
    assert "[SIMULATED ENDPOINT]" in response.text
    assert DEMONSTRATOR_IDENTITY not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_printable_incident_report_uses_only_existing_evidence_and_no_human_mutation() -> None:
    client, api = _client()

    response = client.get(f"/incidents/{INCIDENT_ID}/report")

    assert response.status_code == 200
    assert "Print / Save as PDF" in response.text
    assert 'src="/assets/report.js"' in response.text
    assert "Semantic summary &lt;img src=x onerror=alert(1)&gt;" in response.text
    assert "Candidate BRF is not an approved production master" in response.text
    assert '<form method="post"' not in response.text
    assert api.posts == []
    assert api.downloads == []
    assert response.headers["cache-control"] == "no-store"
    assert "form-action 'self'" in response.headers["content-security-policy"]


def test_presentation_hides_human_record_forms_when_private_data_is_unavailable() -> None:
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
        ),
        api_client=UnavailablePrivateReviewApi(),
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    response = client.get(f"/incidents/{INCIDENT_ID}")

    assert response.status_code == 200
    assert "Private review data is unavailable." in response.text
    assert "PRIVATE_REVIEW_DATA_UNAVAILABLE" in response.text
    assert "Professional disposition controls are unavailable" in response.text
    assert "Operator attestation controls are unavailable" in response.text
    assert "/professional-dispositions" not in response.text
    assert "/operator-attestations" not in response.text
    assert "Record professional disposition" not in response.text
    assert "Record operator attestation" not in response.text


def test_presentation_rejects_missing_or_cross_origin_and_bad_csrf_forms() -> None:
    client, _ = _client()
    csrf = _csrf(client)
    path = f"/incidents/{INCIDENT_ID}/professional-dispositions"

    missing_origin = client.post(path, data=_disposition_form(csrf), follow_redirects=False)
    wrong_origin = client.post(
        path,
        data=_disposition_form(csrf),
        headers={"Origin": "http://attacker.example.test"},
        follow_redirects=False,
    )
    bad_csrf = client.post(
        path,
        data=_disposition_form("wrong"),
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert missing_origin.status_code == 403
    assert wrong_origin.status_code == 403
    assert bad_csrf.status_code == 403


def test_presentation_accepts_normalized_exact_loopback_form_origin() -> None:
    client, api = _client()
    csrf = _csrf(client)

    response = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions",
        data=_disposition_form(csrf),
        headers={"Origin": "HTTP://127.0.0.1:8765/"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(api.posts) == 1


@pytest.mark.parametrize("origin", (None, "null"))
def test_presentation_accepts_browser_privacy_origin_only_with_same_origin_proof(
    origin: str | None,
) -> None:
    client, api = _client()
    csrf = _csrf(client)
    headers = {
        "Referer": f"http://127.0.0.1:8765/incidents/{INCIDENT_ID}/",
        "Sec-Fetch-Site": "same-origin",
    }
    if origin is not None:
        headers["Origin"] = origin

    response = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions",
        data=_disposition_form(csrf),
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(api.posts) == 1


@pytest.mark.parametrize(
    "origin",
    (
        "null",
        "http://localhost:8765",
        "http://127.0.0.1:8766",
        "http://127.0.0.1:8765.evil.example",
        "http://127.0.0.1:8765/?unexpected=true",
        "http://user@127.0.0.1:8765/",
    ),
)
def test_presentation_rejects_loopback_origin_lookalikes(origin: str) -> None:
    client, api = _client()
    csrf = _csrf(client)

    response = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions",
        data=_disposition_form(csrf),
        headers={"Origin": origin},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert api.posts == []


@pytest.mark.parametrize(
    "headers",
    (
        {"Sec-Fetch-Site": "same-origin"},
        {"Referer": f"http://127.0.0.1:8765/incidents/{INCIDENT_ID}/"},
        {
            "Referer": f"http://127.0.0.1:8765/incidents/{INCIDENT_ID}/",
            "Sec-Fetch-Site": "cross-site",
        },
        {
            "Referer": "http://127.0.0.1:8765/not-an-incident/",
            "Sec-Fetch-Site": "same-origin",
        },
        {
            "Origin": "http://attacker.example.test",
            "Referer": f"http://127.0.0.1:8765/incidents/{INCIDENT_ID}/",
            "Sec-Fetch-Site": "same-origin",
        },
    ),
)
def test_presentation_rejects_incomplete_or_cross_site_origin_fallback(
    headers: dict[str, str],
) -> None:
    client, api = _client()
    csrf = _csrf(client)

    response = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions",
        data=_disposition_form(csrf),
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert api.posts == []


def test_presentation_forwards_only_human_record_fields_after_valid_local_checks() -> None:
    client, api = _client()
    csrf = _csrf(client)

    response = client.post(
        f"/incidents/{INCIDENT_ID}/professional-dispositions",
        data=_disposition_form(csrf),
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert api.posts == [
        (
            f"/api/v1/incidents/{INCIDENT_ID}/professional-dispositions",
            {
                "decision": "HALT_REQUESTED",
                "selected_role": "production_coordinator",
                "expected_state_version": 0,
                "note": "Request manual containment review.",
                "idempotency_key": "presentation-halt-1",
            },
        )
    ]


def test_presentation_renders_only_authoritatively_eligible_containment_form_and_forwards_exact_evidence() -> (
    None
):
    client, api = _gate_client("containment")
    csrf = _csrf(client)
    detail = client.get(f"/incidents/{INCIDENT_ID}")

    assert "/containment-confirmations" in detail.text
    assert "/proof-records" not in detail.text
    assert "CUPS state alone never proves device stop" in detail.text
    response = client.post(
        f"/incidents/{INCIDENT_ID}/containment-confirmations",
        data={
            "csrf_token": csrf,
            "halt_disposition_record_id": "b" * 64,
            "site_observation_id": "c" * 64,
            "physical_output_isolation_attestation_id": "d" * 64,
            "selected_role": "production_coordinator",
            "expected_state_version": "2",
            "note": "Coordinator confirms the authoritative evidence set.",
            "idempotency_key": "presentation-containment-1",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert api.posts == [
        (
            f"/api/v1/incidents/{INCIDENT_ID}/containment-confirmations",
            {
                "halt_disposition_record_id": "b" * 64,
                "site_observation_id": "c" * 64,
                "physical_output_isolation_attestation_id": "d" * 64,
                "selected_role": "production_coordinator",
                "expected_state_version": 2,
                "note": "Coordinator confirms the authoritative evidence set.",
                "idempotency_key": "presentation-containment-1",
            },
        )
    ]


def test_presentation_proof_form_requires_loopback_csrf_and_only_forwards_exact_fixture_fields() -> (
    None
):
    client, api = _gate_client("proof")
    csrf = _csrf(client)
    detail = client.get(f"/incidents/{INCIDENT_ID}")

    assert "/proof-records" in detail.text
    assert "/containment-confirmations" not in detail.text
    assert "DEMO_FIXTURE_REVIEW" in detail.text
    assert "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER" in detail.text
    rejected = client.post(
        f"/incidents/{INCIDENT_ID}/proof-records",
        data={
            "csrf_token": "wrong",
            "candidate_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "decision": "APPROVED_FOR_HUMAN_SUBMISSION",
            "review_basis": "DEMO_FIXTURE_REVIEW",
            "selected_role": "proofreader",
            "expected_state_version": "4",
            "idempotency_key": "presentation-proof-1",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )
    accepted = client.post(
        f"/incidents/{INCIDENT_ID}/proof-records",
        data={
            "csrf_token": csrf,
            "candidate_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "decision": "APPROVED_FOR_HUMAN_SUBMISSION",
            "review_basis": "DEMO_FIXTURE_REVIEW",
            "selected_role": "proofreader",
            "expected_state_version": "4",
            "note": "Fixture proof record only.",
            "visual_only_uncertainty": "false",
            "idempotency_key": "presentation-proof-1",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 303
    assert api.posts == [
        (
            f"/api/v1/incidents/{INCIDENT_ID}/proof-records",
            {
                "candidate_sha256": "c" * 64,
                "manifest_sha256": "d" * 64,
                "decision": "APPROVED_FOR_HUMAN_SUBMISSION",
                "review_basis": "DEMO_FIXTURE_REVIEW",
                "selected_role": "proofreader",
                "expected_state_version": 4,
                "note": "Fixture proof record only.",
                "findings": [],
                "visual_only_uncertainty": False,
                "idempotency_key": "presentation-proof-1",
            },
        )
    ]


def test_presentation_proxies_only_the_current_candidate_and_replacement_observation_form() -> None:
    client, api = _gate_client("replacement")
    csrf = _csrf(client)
    detail = client.get(f"/incidents/{INCIDENT_ID}")
    download = client.get(f"/incidents/{INCIDENT_ID}/approved-candidate")

    assert "approved demo-fixture candidate for human-controlled submission" in detail.text
    assert "/replacement-observation-links" in detail.text
    assert "Relay does not control CUPS or the embosser" in detail.text
    assert download.status_code == 200
    assert download.content == b"fixture-approved-candidate\r\n"
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"].startswith("attachment; filename=")
    assert api.downloads == [f"/api/v1/incidents/{INCIDENT_ID}/approved-candidate"]

    response = client.post(
        f"/incidents/{INCIDENT_ID}/replacement-observation-links",
        data={
            "csrf_token": csrf,
            "candidate_sha256": "c" * 64,
            "candidate_manifest_sha256": "d" * 64,
            "proof_record_id": "f" * 64,
            "scheduler_job_id": "43",
            "site_observation_id": "e" * 64,
            "selected_role": "machine_operator",
            "expected_state_version": "6",
            "note": "Associates a fresh read-only external job observation only.",
            "idempotency_key": "presentation-replacement-1",
        },
        headers={"Origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert api.posts == [
        (
            f"/api/v1/incidents/{INCIDENT_ID}/replacement-observation-links",
            {
                "candidate_sha256": "c" * 64,
                "candidate_manifest_sha256": "d" * 64,
                "proof_record_id": "f" * 64,
                "scheduler_job_id": 43,
                "site_observation_id": "e" * 64,
                "selected_role": "machine_operator",
                "expected_state_version": 6,
                "note": "Associates a fresh read-only external job observation only.",
                "idempotency_key": "presentation-replacement-1",
            },
        )
    ]


def test_presentation_has_no_cors_or_cups_mutation_surface_and_launcher_is_loopback_only() -> None:
    client, _ = _client()
    paths = {route.path for route in client.app.routes}
    source = inspect.getsource(main).lower()

    assert not any("cups" in path or "cancel" in path or "submit" in path for path in paths)
    assert not any(
        middleware.cls.__name__ == "CORSMiddleware" for middleware in client.app.user_middleware
    )
    assert 'host="127.0.0.1"' in source


def test_presentation_mints_audience_bound_id_tokens_from_user_adc_by_impersonation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = UserAdcCredentials(token="ordinary-user-adc")
    impersonated_calls: list[dict[str, object]] = []
    token_calls: list[dict[str, object]] = []
    monotonic = [100.0]
    now = datetime(2026, 8, 29, tzinfo=UTC)

    class FakeImpersonatedCredentials:
        def __init__(self, **kwargs: object) -> None:
            impersonated_calls.append(kwargs)

    class FakeIdTokenCredentials:
        issued = 0

        def __init__(self, **kwargs: object) -> None:
            token_calls.append(kwargs)
            self.token: str | None = None
            self.expiry: datetime | None = None

        def refresh(self, _request: object) -> None:
            type(self).issued += 1
            self.token = f"short-lived-token-{type(self).issued}"
            self.expiry = now + timedelta(minutes=10)

    monkeypatch.setattr(
        presentation_app.google.auth,
        "default",
        lambda *, scopes: (source, "project-12345"),
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "Credentials",
        FakeImpersonatedCredentials,
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "IDTokenCredentials",
        FakeIdTokenCredentials,
    )
    provider = GoogleAudienceTokenProvider(
        target_principal=DEMONSTRATOR_IDENTITY,
        audience=AUDIENCE,
        monotonic_clock=lambda: monotonic[0],
        utc_clock=lambda: now,
    )

    async def mint_and_expire_cache() -> tuple[str, str, str]:
        first = await provider.token_for(AUDIENCE)
        second = await provider.token_for(AUDIENCE)
        monotonic[0] += 271.0
        third = await provider.token_for(AUDIENCE)
        return first, second, third

    first, second, third = asyncio.run(mint_and_expire_cache())

    assert (first, second, third) == (
        "short-lived-token-1",
        "short-lived-token-1",
        "short-lived-token-2",
    )
    assert impersonated_calls == [
        {
            "source_credentials": source,
            "target_principal": DEMONSTRATOR_IDENTITY,
            "target_scopes": ("https://www.googleapis.com/auth/cloud-platform",),
            "lifetime": 300,
        },
        {
            "source_credentials": source,
            "target_principal": DEMONSTRATOR_IDENTITY,
            "target_scopes": ("https://www.googleapis.com/auth/cloud-platform",),
            "lifetime": 300,
        },
    ]
    assert len(token_calls) == 2
    assert all(call["target_audience"] == AUDIENCE for call in token_calls)
    assert all(call["include_email"] is True for call in token_calls)
    assert all("target_credentials" in call for call in token_calls)


def test_presentation_rejects_missing_user_adc_and_unexpected_audiences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleAudienceTokenProvider(
        target_principal=DEMONSTRATOR_IDENTITY,
        audience=AUDIENCE,
    )
    with pytest.raises(ValueError, match="unexpected audience"):
        asyncio.run(provider.token_for("https://wrong-audience.example.test"))

    monkeypatch.setattr(
        presentation_app.google.auth,
        "default",
        lambda *, scopes: (_ for _ in ()).throw(DefaultCredentialsError("missing ADC")),
    )
    with pytest.raises(PresentationAuthenticationError, match="ordinary local user ADC"):
        asyncio.run(provider.token_for(AUDIENCE))


def test_presentation_rejects_missing_impersonation_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeImpersonatedCredentials:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class UnauthorizedIdTokenCredentials:
        token: str | None = None
        expiry: datetime | None = None

        def __init__(self, **_kwargs: object) -> None:
            pass

        def refresh(self, _request: object) -> None:
            raise RefreshError("permission denied")

    monkeypatch.setattr(
        presentation_app.google.auth,
        "default",
        lambda *, scopes: (UserAdcCredentials(token="ordinary-user-adc"), "project-12345"),
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "Credentials",
        FakeImpersonatedCredentials,
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "IDTokenCredentials",
        UnauthorizedIdTokenCredentials,
    )
    provider = GoogleAudienceTokenProvider(
        target_principal=DEMONSTRATOR_IDENTITY,
        audience=AUDIENCE,
    )

    with pytest.raises(PresentationAuthenticationError, match="not authorized"):
        asyncio.run(provider.token_for(AUDIENCE))


def test_presentation_retries_bounded_transient_token_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    refresh_attempts = 0
    retry_delays: list[float] = []

    class FakeImpersonatedCredentials:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class EventuallyAvailableIdTokenCredentials:
        token: str | None = None
        expiry: datetime | None = None

        def __init__(self, **_kwargs: object) -> None:
            pass

        def refresh(self, _request: object) -> None:
            nonlocal refresh_attempts
            refresh_attempts += 1
            if refresh_attempts < 3:
                raise TransportError("transient IAM transport failure")
            self.token = "short-lived-token"
            self.expiry = now + timedelta(minutes=5)

    monkeypatch.setattr(
        presentation_app.google.auth,
        "default",
        lambda *, scopes: (UserAdcCredentials(token="ordinary-user-adc"), "project-12345"),
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "Credentials",
        FakeImpersonatedCredentials,
    )
    monkeypatch.setattr(
        presentation_app.impersonated_credentials,
        "IDTokenCredentials",
        EventuallyAvailableIdTokenCredentials,
    )
    provider = GoogleAudienceTokenProvider(
        target_principal=DEMONSTRATOR_IDENTITY,
        audience=AUDIENCE,
        utc_clock=lambda: now,
        sleep=retry_delays.append,
        transport_retry_delays_seconds=(0.1, 0.2, 0.4),
    )

    token = asyncio.run(provider.token_for(AUDIENCE))

    assert token == "short-lived-token"
    assert refresh_attempts == 3
    assert retry_delays == [0.1, 0.2]


def test_presentation_settings_reject_missing_or_malformed_impersonation_targets() -> None:
    with pytest.raises(ValueError, match="service-account principal"):
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account="not-a-service-account",
        )


def test_presentation_settings_accept_explicit_impersonation_configuration() -> None:
    settings = _settings_from_args(
        [
            "--api-base-url",
            AUDIENCE,
            "--audience",
            AUDIENCE,
            "--session-secret",
            "s" * 32,
            "--impersonate-service-account",
            DEMONSTRATOR_IDENTITY,
        ]
    )

    assert settings.impersonate_service_account == DEMONSTRATOR_IDENTITY


def test_active_review_runbook_has_temporary_iam_cleanup_and_never_uses_key_file_fallback() -> None:
    runbook = (
        (Path(__file__).resolve().parents[2] / "infra" / "wsl")
        .joinpath("run_active_professional_review_demo.sh")
        .read_text(encoding="utf-8")
    )
    source = inspect.getsource(presentation_app).lower()

    assert "roles/iam.serviceaccounttokencreator" in runbook.lower()
    assert "add-iam-policy-binding" in runbook
    assert "remove-iam-policy-binding" in runbook
    assert "finally" in runbook
    assert "RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT" in runbook
    assert "fetch_id_token" not in source
    assert "google.oauth2 import service_account" not in source
    assert "impersonated_credentials.idtokencredentials" in source
