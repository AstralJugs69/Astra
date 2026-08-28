from __future__ import annotations

from fastapi.testclient import TestClient

from braille_errata_relay.api.main import create_app


def test_health_is_available_without_braille_engine() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 503
