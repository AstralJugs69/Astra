"""Integration tests for POST /reason endpoint."""

import pytest
from fastapi.testclient import TestClient

from astra.api.main import create_app
from astra.settings import get_settings


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_reason_endpoint_executes_deep_investigation(client):
    settings = get_settings()
    payload = {
        "session_id": "test-direct-reason-session",
        "task": "Investigate regression in payment module",
        "trigger_type": "EXPLICIT_REASON_REQUEST",
    }

    response = client.post(
        "/reason",
        json=payload,
        headers={"Authorization": f"Bearer {settings.auth_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "engine_name" in data
    assert "verdict" in data
    assert "confidence" in data
