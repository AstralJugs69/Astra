"""Integration test for decision pipeline in Shadow mode."""

import pytest
from astra.application.pipeline import DecisionPipeline
from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.infrastructure.persistence.memory_store import InMemoryTrajectoryStore
from astra.tiers.fast.assessor import FastTierAssessor


@pytest.mark.asyncio
async def test_pipeline_shadow_mode_stores_state_and_allows_continuation():
    store = InMemoryTrajectoryStore()
    assessor = FastTierAssessor(model_provider=None)
    pipeline = DecisionPipeline(state_store=store, fast_assessor=assessor)

    event = AstraEvent(
        event_id="e-shadow-1",
        session_id="session-shadow",
        event_type=EventType.POST_TOOL_USE,
        step_index=1,
        tool=ToolCallSummary(name="view_file", arguments_summary="src/app.py"),
        received_at=1000,
        correlation_id="c-shadow-1",
    )

    response = await pipeline.process_event(event)

    # Assert response
    assert response.decision == "continue"
    assert response.mode == "SHADOW"
    assert response.intervention_id is None

    # Assert trajectory state is saved in store
    saved_state = await store.load("session-shadow")
    assert saved_state is not None
    assert len(saved_state.actions_taken) == 1
    assert saved_state.actions_taken[0].tool_name == "view_file"
