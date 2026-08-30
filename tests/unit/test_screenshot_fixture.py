from __future__ import annotations

import inspect
import re

from fastapi.testclient import TestClient

from braille_errata_relay.api.main import create_app
from braille_errata_relay.presentation import screenshot_fixture


def test_screenshot_fixture_is_offline_sanitized_get_only_and_not_mounted_by_cloud_api() -> None:
    app = screenshot_fixture.create_screenshot_fixture_app()
    client = TestClient(app, base_url="http://127.0.0.1:8877")

    overview = client.get("/")
    proof_ready = client.get(f"/incidents/{screenshot_fixture.PROOF_READY_ID}")
    observed = client.get(f"/incidents/{screenshot_fixture.REPLACEMENT_OBSERVED_ID}")
    mutation = client.post(f"/incidents/{screenshot_fixture.PROOF_READY_ID}/proof-records")
    candidate = client.get(f"/incidents/{screenshot_fixture.PROOF_READY_ID}/approved-candidate")

    assert overview.status_code == 200
    assert proof_ready.status_code == 200
    assert observed.status_code == 200
    assert "SANITIZED DEMO FIXTURE" in overview.text
    assert "SANITIZED DEMO FIXTURE" in proof_ready.text
    for state in ("REPORT_READY", "NEEDS_REVIEW", "AWAITING_REPLACEMENT", "REPLACEMENT_OBSERVED"):
        assert state in overview.text
    assert "fixture_mode" not in proof_ready.text
    assert '<form method="post"' not in proof_ready.text
    assert "replacement linking is intentionally disabled" in proof_ready.text
    assert "REPLACEMENT_OBSERVATION_LINK" in observed.text
    assert mutation.status_code in {404, 405}
    assert candidate.status_code == 404
    assert client.get("/api/v1/incidents").status_code == 404
    assert all(route.path != "/screenshot-fixture" for route in create_app().routes)


def test_screenshot_fixture_contains_no_private_identifier_or_external_runtime_client() -> None:
    source = inspect.getsource(screenshot_fixture).lower()
    client = TestClient(screenshot_fixture.create_screenshot_fixture_app())
    rendered = client.get(f"/incidents/{screenshot_fixture.PROOF_READY_ID}").text.lower()

    for forbidden in (
        "import cups",
        "cups.connection",
        "import subprocess",
        "google.auth",
        "httpx",
    ):
        assert forbidden not in source
    for forbidden in ("gs://", "drive.google.com", "project-", "c:\\\\", "access_token"):
        assert forbidden not in rendered
    assert re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+", rendered) is None
    assert "sanitized demo fixture" in rendered
