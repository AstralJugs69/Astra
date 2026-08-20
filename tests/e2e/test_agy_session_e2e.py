"""End-to-End lifecycle tests for Astra companion agent.

Marked with @pytest.mark.e2e. Simulates complete multi-step Antigravity CLI sessions
through the full HTTP pipeline, verification cycles, and anti-loop safety boundaries.
"""

import httpx
import pytest

from astra.api.deps import get_model_provider
from astra.api.main import app
from astra.domain.model_ports import CostMetadata
from astra.domain.trajectory import EpistemicPhase
from astra.settings import get_settings


class FastMockProvider:
    """Fast deterministic mock provider for isolated E2E pipeline tests."""

    async def generate_structured(self, prompt, response_schema, **kwargs):
        if "calculator tests are passing" in prompt or "1 passed in 0.05s" in prompt:
            return (
                response_schema(
                    is_verified=True,
                    confidence=1.0,
                    evidence_soundness_reason="Passing test verification verified.",
                ),
                CostMetadata(tier_invoked="deep", latency_ms=5),
            )
        return (
            response_schema(
                is_verified=False,
                confidence=0.9,
                evidence_soundness_reason="Active unverified changes in workspace.",
                missing_verification="Run pytest suite to verify changes.",
            ),
            CostMetadata(tier_invoked="deep", latency_ms=5),
        )

    async def generate_text(self, prompt, **kwargs):
        return "Mock model output", CostMetadata(tier_invoked="fast", latency_ms=5)


@pytest.fixture(autouse=True)
def override_model_provider_for_e2e():
    """Overrides real Vertex AI provider during E2E lifecycle tests for speed and isolation."""
    mock = FastMockProvider()
    app.dependency_overrides[get_model_provider] = lambda: mock
    yield
    app.dependency_overrides.pop(get_model_provider, None)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_complete_debugging_session_lifecycle_e2e():
    """E2E test simulating a full coding agent session:
    1. Agent writes code (PostToolUse) -> Astra tracks modification in Shadow mode.
    2. Agent attempts premature termination (Stop) -> Astra blocks termination with verification demand.
    3. Agent executes passing pytest suite (PostToolUse) -> Astra confirms fix.
    4. Agent terminates (Stop) -> Astra allows termination on fast path.
    """
    settings = get_settings()
    auth_header = {"Authorization": f"Bearer {settings.auth_token}"}
    session_id = "e2e_session_lifecycle_001"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
        res1 = await client.post("/event", json=post_tool_payload, headers=auth_header)
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
        res2 = await client.post("/event", json=stop_payload_premature, headers=auth_header)
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
        res3 = await client.post("/event", json=post_tool_verification, headers=auth_header)
        assert res3.status_code == 200
        assert res3.json() == {}

        # Step 4: Agent terminates (Stop) -> Astra observes verified fix, allows stop
        stop_payload_verified = {
            "event_type": "Stop",
            "correlation_id": "c-104",
            "payload": {
                "conversationId": session_id,
                "stepIdx": 4,
                "terminationReason": "All calculator tests are passing",
            },
            "client_timestamp_ms": 4000,
        }
        res4 = await client.post("/event", json=stop_payload_verified, headers=auth_header)
        assert res4.status_code == 200
        data4 = res4.json()
        assert data4.get("decision") == "allow"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anti_loop_safety_exhaustion_e2e():
    """E2E test verifying that repeated identical failure loops are bounded by anti-loop policy."""
    settings = get_settings()
    auth_header = {"Authorization": f"Bearer {settings.auth_token}"}
    session_id = "e2e_session_anti_loop_002"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Edit code
        await client.post(
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

        # Execute forced continuations up to the configured cap
        cap = settings.max_forced_continuations_per_signature
        for attempt in range(1, cap + 1):
            r = await client.post(
                "/event",
                json={
                    "event_type": "Stop",
                    "correlation_id": f"c-20{attempt+1}",
                    "payload": {"conversationId": session_id, "terminationReason": "Done"},
                    "client_timestamp_ms": 2000 + attempt * 5000,
                },
                headers=auth_header,
            )
            assert r.json().get("decision") == "continue"

        # Final Stop attempt -> Anti-loop exhaustion triggers; Astra surfaces and allows termination
        r_final = await client.post(
            "/event",
            json={
                "event_type": "Stop",
                "correlation_id": f"c-20{cap+2}",
                "payload": {"conversationId": session_id, "terminationReason": "Done"},
                "client_timestamp_ms": 2000 + (cap + 1) * 5000,
            },
            headers=auth_header,
        )
        assert r_final.json().get("decision") == "allow"
