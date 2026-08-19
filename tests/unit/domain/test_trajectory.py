"""Unit tests for pure trajectory state reducers."""

from astra.domain.events import AstraEvent, EventType, ToolCallSummary, VerificationOutcome
from astra.domain.trajectory import (
    TrajectoryState,
    create_initial_trajectory,
    reduce_trajectory,
)


def test_initial_trajectory_creation():
    state = create_initial_trajectory("session-1", timestamp_ms=1000)
    assert state.session_id == "session-1"
    assert state.state_version == 1
    assert state.failure_count == 0
    assert len(state.actions_taken) == 0


def test_reduce_trajectory_records_action_and_verification():
    state = create_initial_trajectory("session-1", timestamp_ms=1000)

    # First event: code edit
    edit_event = AstraEvent(
        event_id="evt-1",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        step_index=1,
        tool=ToolCallSummary(name="replace_file_content", arguments_summary="src/main.py"),
        received_at=1100,
        correlation_id="corr-1",
    )
    state = reduce_trajectory(state, edit_event)
    assert state.state_version == 2
    assert len(state.actions_taken) == 1
    assert state.actions_taken[0].tool_name == "replace_file_content"
    assert len(state.verification_history) == 0

    # Second event: failed test run
    test_fail_event = AstraEvent(
        event_id="evt-2",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        step_index=2,
        tool=ToolCallSummary(
            name="run_command",
            arguments_summary="pytest tests/unit/",
            had_error=True,
            output_summary="1 failed",
        ),
        received_at=1200,
        correlation_id="corr-2",
    )
    state = reduce_trajectory(state, test_fail_event)
    assert state.state_version == 3
    assert len(state.verification_history) == 1
    assert state.verification_history[0].outcome == VerificationOutcome.FAILED
    assert state.failure_count == 1

    # Third event: passed test run
    test_pass_event = AstraEvent(
        event_id="evt-3",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        step_index=3,
        tool=ToolCallSummary(
            name="run_command",
            arguments_summary="pytest tests/unit/",
            had_error=False,
            output_summary="1 passed",
        ),
        received_at=1300,
        correlation_id="corr-3",
    )
    state = reduce_trajectory(state, test_pass_event)
    assert len(state.verification_history) == 2
    assert state.verification_history[1].outcome == VerificationOutcome.PASSED
    assert state.failure_count == 0  # Reset consecutive failure count
