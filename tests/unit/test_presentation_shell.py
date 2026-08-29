from __future__ import annotations

import inspect
import re
from collections.abc import Mapping

from fastapi.testclient import TestClient

from braille_errata_relay.presentation.app import (
    PresentationSettings,
    create_presentation_app,
    main,
)

INCIDENT_ID = "a" * 64


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
            api_base_url="https://private-relay.example.test",
            audience="https://private-relay.example.test",
            session_secret="s" * 32,
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
