"""End-to-End lifecycle tests for Astra companion agent.

Marked with @pytest.mark.e2e. Simulates complete multi-step Antigravity CLI sessions
through the full HTTP pipeline, verification cycles, and anti-loop safety boundaries.
"""

import pytest
from fastapi.testclient import TestClient

from astra.api.main import app
from astra.domain.trajectory import EpistemicPhase
from astra.settings import get_settings


@pytest.mark.e2e
def test_complete_debugging_session_lifecycle_e2e():
    """E2E test simulating a full coding agent session:
    1. Agent writes code (PostToolUse) -> Astra tracks modification in Shadow mode.
    2. Agent attempts premature termination (Stop) -> Astra blocks termination with verification demand.
    3. Agent executes passing pytest suite (PostToolUse) -> Astra confirms fix.
    4. Agent terminates (Stop) -> Astra allows termination on fast path.
    """
    settings = get_settings()
    auth_header = {"Authorization": f"Bearer {settings.auth_token}"}
    session_id = "e2e_session_lifecycle_001"

    with TestClient(app) as client:
        # Step 1: Agent edits source file
        post_tool_payload = {
            "event_type": "PostToolUse",
            "correlation_id": "c-101",
            "payload": {
                "conversationId": session_id,
                "stepIdx": 1,
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": "src/calculator.py", "CodeContent": "def add(a, b): return a + b"},
                },
                "toolResult": {"output": "File created."},
            },
            "client_timestamp_ms": 1000,
        }
        res1 = client.post("/event", json=post_tool_payload, headers=auth_header)
        assert res1.status_code == 200
        assert res1.json() == {}

        # Step 2: Agent attempts premature Stop without running tests
        stop_payload_premature = {
            "event_type": "Stop",
            "correlation_id": "c-102",
            "payload": {
                "conversationId": session_id,
                "stepIdx": 2,
                "terminationReason": "I fixed the addition bug in calculator.py",
            },
            "client_timestamp_ms": 2000,
        }
        res2 = client.post("/event", json=stop_payload_premature, headers=auth_header)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2.get("decision") == "continue"
        assert "Astra Stop Intervene" in data2.get("reason", "") or "verification" in data2.get("reason", "").lower()

        # Step 3: Agent executes verification command (pytest)
        post_tool_verification = {
            "event_type": "PostToolUse",
            "correlation_id": "c-103",
            "payload": {
                "conversationId": session_id,
                "stepIdx": 3,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "pytest tests/test_calculator.py"},
                },
                "toolResult": {
                    "output": "1 passed in 0.05s",
                    "exitCode": 0,
                },
            },
            "client_timestamp_ms": 3000,
        }
        res3 = client.post("/event", json=post_tool_verification, headers=auth_header)
        assert res3.status_code == 200
        assert res3.json() == {}

        # Step 4: Agent attempts Stop again after verified fix
        stop_payload_verified = {
            "event_type": "Stop",
            "correlation_id": "c-104",
            "payload": {
                "conversationId": session_id,
                "stepIdx": 4,
                "terminationReason": "Tests pass, task complete.",
            },
            "client_timestamp_ms": 4000,
        }
        res4 = client.post("/event", json=stop_payload_verified, headers=auth_header)
        assert res4.status_code == 200
        data4 = res4.json()
        assert data4.get("decision") == "allow"


@pytest.mark.e2e
def test_anti_loop_safety_exhaustion_e2e():
    """E2E test verifying that repeated identical failure loops are bounded by anti-loop policy."""
    settings = get_settings()
    auth_header = {"Authorization": f"Bearer {settings.auth_token}"}
    session_id = "e2e_session_anti_loop_002"

    with TestClient(app) as client:
        # Edit code
        client.post(
            "/event",
            json={
                "event_type": "PostToolUse",
                "correlation_id": "c-201",
                "payload": {
                    "conversationId": session_id,
                    "toolCall": {
                        "name": "write_to_file",
                        "args": {"TargetFile": "bug.py"},
                    },
                    "toolResult": {"output": "Saved"},
                },
                "client_timestamp_ms": 1000,
            },
            headers=auth_header,
        )

        # Stop attempt 1 -> Forced continuation
        r1 = client.post(
            "/event",
            json={
                "event_type": "Stop",
                "correlation_id": "c-202",
                "payload": {"conversationId": session_id, "terminationReason": "Done"},
                "client_timestamp_ms": 2000,
            },
            headers=auth_header,
        )
        assert r1.json().get("decision") == "continue"

        # Stop attempt 2 -> Forced continuation (cap = 2 reached)
        r2 = client.post(
            "/event",
            json={
                "event_type": "Stop",
                "correlation_id": "c-203",
                "payload": {"conversationId": session_id, "terminationReason": "Done"},
                "client_timestamp_ms": 3000,
            },
            headers=auth_header,
        )
        assert r2.json().get("decision") == "continue"

        # Stop attempt 3 -> Anti-loop exhaustion triggers; Astra surfaces and allows termination
        r3 = client.post(
            "/event",
            json={
                "event_type": "Stop",
                "correlation_id": "c-204",
                "payload": {"conversationId": session_id, "terminationReason": "Done"},
                "client_timestamp_ms": 4000,
            },
            headers=auth_header,
        )
        assert r3.json().get("decision") == "allow"
