"""Unit tests for pure rule-based signal detectors."""

from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.domain.signals import (
    SignalType,
    compute_failure_signature,
    detect_premature_termination,
    detect_repeated_verification_failures,
    detect_same_file_repeated_edits,
    run_all_rule_detectors,
)
from astra.domain.trajectory import create_initial_trajectory, reduce_trajectory


def test_detect_repeated_verification_failures():
    state = create_initial_trajectory("session-1", timestamp_ms=1000)

    # 1 failure
    event_fail1 = AstraEvent(
        event_id="e1",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        tool=ToolCallSummary(name="run_command", arguments_summary="pytest", had_error=True),
        received_at=1100,
        correlation_id="c1",
    )
    state = reduce_trajectory(state, event_fail1)
    sig = detect_repeated_verification_failures(state, event_fail1, threshold=2)
    assert sig is None

    # 2nd consecutive failure -> triggers signal
    event_fail2 = AstraEvent(
        event_id="e2",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        tool=ToolCallSummary(name="run_command", arguments_summary="pytest", had_error=True),
        received_at=1200,
        correlation_id="c2",
    )
    state = reduce_trajectory(state, event_fail2)
    sig = detect_repeated_verification_failures(state, event_fail2, threshold=2)
    assert sig is not None
    assert sig.type == SignalType.REPEATED_VERIFICATION_FAILURE
    assert sig.confidence >= 0.9


def test_detect_same_file_repeated_edits():
    state = create_initial_trajectory("session-1", timestamp_ms=1000)

    for i in range(3):
        edit = AstraEvent(
            event_id=f"e{i}",
            session_id="session-1",
            event_type=EventType.POST_TOOL_USE,
            tool=ToolCallSummary(name="replace_file_content", arguments_summary="src/app.py"),
            received_at=1100 + i * 100,
            correlation_id=f"c{i}",
        )
        state = reduce_trajectory(state, edit)

    # Check signal on 3rd edit
    sig = detect_same_file_repeated_edits(state, edit, threshold=3)
    assert sig is not None
    assert sig.type == SignalType.SAME_FILE_REPEATED_EDITS


def test_detect_premature_termination():
    state = create_initial_trajectory("session-1", timestamp_ms=1000)

    # Edit code
    edit = AstraEvent(
        event_id="e1",
        session_id="session-1",
        event_type=EventType.POST_TOOL_USE,
        tool=ToolCallSummary(name="replace_file_content", arguments_summary="src/app.py"),
        received_at=1100,
        correlation_id="c1",
    )
    state = reduce_trajectory(state, edit)

    # Stop without verification
    stop_event = AstraEvent(
        event_id="e_stop",
        session_id="session-1",
        event_type=EventType.STOP,
        received_at=1200,
        correlation_id="c_stop",
    )
    sig = detect_premature_termination(state, stop_event)
    assert sig is not None
    assert sig.type == SignalType.UNSUPPORTED_SUCCESS_CLAIM
    assert sig.suggested_mode == "INTERVENE"
