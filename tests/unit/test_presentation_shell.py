from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.oauth2.credentials import Credentials as UserAdcCredentials

from braille_errata_relay.presentation import app as presentation_app
from braille_errata_relay.presentation.app import (
    GoogleAudienceTokenProvider,
    PresentationAuthenticationError,
    PresentationSettings,
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

    async def get_json(self, path: str) -> dict[str, object]:
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
        return {"status": "HALT_REQUESTED"}


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


def _csrf(client: TestClient) -> str:
    response = client.get(f"/incidents/{INCIDENT_ID}")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


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
    assert "[SIMULATED ENDPOINT]" in response.text
    assert DEMONSTRATOR_IDENTITY not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


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
