"""Unit tests for Turns-to-Fix metric calculation from transcripts."""

from astra.evaluation.metrics import calculate_transcript_metrics
from astra.evaluation.models import TrialOutcome


def test_calculate_transcript_metrics_resolved():
    steps = [
        {"type": "USER_INPUT", "content": "Fix bug in window.py"},
        {
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_window.py"}}],
            "status": "ERROR",
            "content": "1 failed in 0.2s",
        },
        {"type": "USER_INPUT", "content": "Edit code"},
        {
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_window.py"}}],
            "status": "DONE",
            "content": "1 passed in 0.2s",
        },
    ]

    turns, outcome, secondary = calculate_transcript_metrics(
        steps=steps,
        verification_command="pytest tests/test_window.py",
        max_turns=10,
    )
    assert outcome == TrialOutcome.RESOLVED
    assert turns == 4
    assert secondary.failed_verification_attempts == 1


def test_calculate_transcript_metrics_unresolved_on_failure():
    steps = [
        {"type": "USER_INPUT", "content": "Fix bug"},
        {
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_window.py"}}],
            "status": "ERROR",
            "content": "1 failed in 0.2s",
        },
    ]

    turns, outcome, secondary = calculate_transcript_metrics(
        steps=steps,
        verification_command="pytest tests/test_window.py",
        max_turns=10,
    )
    assert outcome == TrialOutcome.UNRESOLVED
    assert turns is None
    assert secondary.failed_verification_attempts == 1
