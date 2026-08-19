"""Integration tests for FastAPI /event and /health endpoints."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from astra.api.main import create_app
from astra.settings import get_settings

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "hook_payloads"


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_health_check_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "astra-backend"


def test_event_endpoint_requires_auth(client):
    payload = {
        "event_type": "PostToolUse",
        "correlation_id": "c-1",
        "payload": {},
    }
    # No auth header
    response = client.post("/event", json=payload)
    assert response.status_code == 401

    # Invalid auth header
    response = client.post(
        "/event",
        json=payload,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403


def test_event_endpoint_handles_post_tool_use_success(client):
    fixture_path = FIXTURES_DIR / "post_tool_use_success.json"
    raw_dict = json.loads(fixture_path.read_text(encoding="utf-8"))

    settings = get_settings()
    envelope = {
        "event_type": "PostToolUse",
        "correlation_id": "corr-api-test",
        "payload": raw_dict,
    }

    response = client.post(
        "/event",
        json=envelope,
        headers={"Authorization": f"Bearer {settings.auth_token}"},
    )
    assert response.status_code == 200
    # PostToolUse returns empty object {} in Antigravity stdout format
    assert response.json() == {}


def test_event_endpoint_handles_stop_event_normal(client):
    fixture_path = FIXTURES_DIR / "stop_after_passed_verification.json"
    raw_dict = json.loads(fixture_path.read_text(encoding="utf-8"))

    settings = get_settings()
    envelope = {
        "event_type": "Stop",
        "correlation_id": "corr-stop-test",
        "payload": raw_dict,
    }

    response = client.post(
        "/event",
        json=envelope,
        headers={"Authorization": f"Bearer {settings.auth_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] in ["allow", "continue"]
