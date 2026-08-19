"""Unit tests for normalized domain events."""

from astra.domain.events import AstraEvent, EventType, ToolCallSummary


def test_astra_event_creation():
    event = AstraEvent(
        event_id="evt-1",
        session_id="session-100",
        event_type=EventType.POST_TOOL_USE,
        step_index=1,
        tool=ToolCallSummary(name="run_command", arguments_summary="pytest", had_error=False),
        received_at=1000,
        correlation_id="corr-1",
    )
    assert event.event_id == "evt-1"
    assert event.event_type == EventType.POST_TOOL_USE
    assert not event.is_tool_failure


def test_astra_event_tool_failure_detection():
    event_error_str = AstraEvent(
        event_id="evt-2",
        session_id="session-100",
        event_type=EventType.POST_TOOL_USE,
        error="exit status 1",
        received_at=1000,
        correlation_id="corr-2",
    )
    assert event_error_str.is_tool_failure

    event_tool_err = AstraEvent(
        event_id="evt-3",
        session_id="session-100",
        event_type=EventType.POST_TOOL_USE,
        tool=ToolCallSummary(name="run_command", had_error=True),
        received_at=1000,
        correlation_id="corr-3",
    )
    assert event_tool_err.is_tool_failure
