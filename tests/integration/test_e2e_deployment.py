"""End-to-end smoke tests for production configuration and API."""

import pytest
from fastapi.testclient import TestClient

from astra.api.main import create_app
from astra.settings import Settings


def test_production_app_smoke(monkeypatch):
    monkeypatch.setenv("ASTRA_ENV", "prod")
    monkeypatch.setenv("ASTRA_AUTH_TOKEN", "prod-secret-token-xyz")
    monkeypatch.setenv("ASTRA_PERSISTENCE_BACKEND", "IN_MEMORY")

    app = create_app()
    client = TestClient(app)

    # Health check
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["environment"] == "prod"

    # Event check with auth
    envelope = {
        "event_type": "PostToolUse",
        "correlation_id": "e2e-smoke-corr",
        "payload": {
            "conversationId": "session-e2e-smoke",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "pytest"}},
            "toolResult": {"exitCode": 0, "output": "1 passed in 0.1s"},
        },
    }

    res_event = client.post(
        "/event",
        json=envelope,
        headers={"Authorization": "Bearer prod-secret-token-xyz"},
    )
    assert res_event.status_code == 200
    assert res_event.json() == {}
