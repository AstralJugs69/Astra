"""Unit tests for Fast Tier Assessor."""

import pytest
from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.domain.model_ports import CostMetadata
from astra.domain.signals import SignalType
from astra.domain.trajectory import create_initial_trajectory, reduce_trajectory
from astra.tiers.fast.assessor import FastTierAssessor


class FakeModelProvider:
    def __init__(self, response_obj):
        self.response_obj = response_obj

    async def generate_structured(self, prompt, response_schema, **kwargs):
        return self.response_obj, CostMetadata(tier_invoked="fast", model_calls=1, tokens_in=50, tokens_out=20, latency_ms=150)


@pytest.mark.asyncio
async def test_fast_assessor_detects_rules_without_model():
    assessor = FastTierAssessor(model_provider=None)
    state = create_initial_trajectory("session-1")

    # Add 2 failed verification events to state
    for i in range(2):
        event = AstraEvent(
            event_id=f"e{i}",
            session_id="session-1",
            event_type=EventType.POST_TOOL_USE,
            tool=ToolCallSummary(name="run_command", arguments_summary="pytest", had_error=True),
            received_at=1000 + i * 100,
            correlation_id=f"c{i}",
        )
        state = reduce_trajectory(state, event)

    current_event = AstraEvent(
        event_id="e_curr",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        tool=ToolCallSummary(name="run_command", arguments_summary="pytest", had_error=True),
        received_at=1300,
        correlation_id="c_curr",
    )

    result = await assessor.assess(current_event, state)
    assert len(result.signals) > 0
    assert result.signals[0].type == SignalType.REPEATED_VERIFICATION_FAILURE
    assert result.cost.model_calls == 0  # 0 LLM calls made
