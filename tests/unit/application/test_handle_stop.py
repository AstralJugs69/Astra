"""Unit tests for StopHookHandler."""

import pytest
from astra.application.handle_stop import StopHookHandler
from astra.domain.events import AstraEvent, EventType
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import CritiquePayload, CritiqueSeverity, CritiqueType, EngineResult, EngineVerdict
from astra.domain.trajectory import create_initial_trajectory
from astra.integration.antigravity.response_format import AssistPayload
from astra.tiers.deep.orchestrator import DeepInvestigationResult


class MockOrchestrator:
    def __init__(self, verdict: EngineVerdict):
        self.verdict = verdict

    async def investigate(self, event, state, triggering_signal=None):
        critique = None
        if self.verdict == EngineVerdict.NOT_VERIFIED:
            critique = CritiquePayload(
                type=CritiqueType.INSUFFICIENT_VERIFICATION,
                severity=CritiqueSeverity.HIGH,
                claim_under_review="Code fix completed",
                supporting_observation="Test suite was not run",
                why_problematic="Unverified change risks regression",
                missing_information="Run pytest suite",
                suggested_next_action="Run pytest",
            )
        engine_res = EngineResult(
            engine_name="bugfix_verifier",
            verdict=self.verdict,
            critique=critique,
            confidence=0.95,
        )
        return DeepInvestigationResult(
            engine_result=engine_res,
            assist_payload=AssistPayload(message="Audit complete", critique=critique),
            total_cost=CostMetadata(tier_invoked="deep", model_calls=1),
        )


@pytest.mark.asyncio
async def test_stop_handler_allows_verified_stop():
    orchestrator = MockOrchestrator(verdict=EngineVerdict.VERIFIED)
    handler = StopHookHandler(deep_orchestrator=orchestrator)

    event = AstraEvent(
        event_id="e-stop-1",
        session_id="s1",
        event_type=EventType.STOP,
        received_at=1000,
        correlation_id="c1",
    )
    state = create_initial_trajectory("s1")

    resp, new_state = await handler.handle(event, state)
    assert resp.decision == "allow"
    assert "verification passed" in resp.reason


@pytest.mark.asyncio
async def test_stop_handler_blocks_unverified_stop():
    orchestrator = MockOrchestrator(verdict=EngineVerdict.NOT_VERIFIED)
    handler = StopHookHandler(deep_orchestrator=orchestrator, max_forced_continuations_per_signature=2)

    event = AstraEvent(
        event_id="e-stop-2",
        session_id="s1",
        event_type=EventType.STOP,
        received_at=1000,
        correlation_id="c2",
    )
    state = create_initial_trajectory("s1")

    resp, new_state = await handler.handle(event, state)
    assert resp.decision == "block_stop"
    assert "Astra Stop Intervene" in resp.reason
    assert new_state.current_mode == "INTERVENE"
    assert len(new_state.interventions) == 1


@pytest.mark.asyncio
async def test_stop_handler_surfaces_to_user_when_cap_reached():
    orchestrator = MockOrchestrator(verdict=EngineVerdict.NOT_VERIFIED)
    handler = StopHookHandler(deep_orchestrator=orchestrator, max_forced_continuations_per_signature=2)

    state = create_initial_trajectory("s1")
    event = AstraEvent(
        event_id="e-stop-3",
        session_id="s1",
        event_type=EventType.STOP,
        received_at=1000,
        correlation_id="c3",
    )

    # 1st attempt -> blocked
    resp1, state = await handler.handle(event, state)
    assert resp1.decision == "block_stop"

    # 2nd attempt -> blocked
    event.received_at = 45000  # Past cooldown
    resp2, state = await handler.handle(event, state)
    assert resp2.decision == "block_stop"

    # 3rd attempt -> reached cap of 2 -> surfaces to user and allows stop
    event.received_at = 90000
    resp3, state = await handler.handle(event, state)
    assert resp3.decision == "continue"
    assert "Anti-Loop Guard" in resp3.reason
