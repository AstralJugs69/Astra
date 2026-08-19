"""Unit tests for pure escalation and operating mode policy."""

from astra.domain.modes import Mode, decide_mode
from astra.domain.signals import Signal, SignalType
from astra.domain.trajectory import create_initial_trajectory


def test_decide_mode_shadow_when_no_signals():
    state = create_initial_trajectory("session-1")
    decision = decide_mode(Mode.SHADOW, signals=[], state=state)
    assert decision.new_mode == Mode.SHADOW
    assert not decision.should_escalate


def test_decide_mode_escalates_to_assist():
    state = create_initial_trajectory("session-1")
    assist_signal = Signal(
        type=SignalType.SAME_FILE_REPEATED_EDITS,
        confidence=0.8,
        suggested_mode="ASSIST",
        rationale="Thrashing on same file",
    )
    decision = decide_mode(Mode.SHADOW, signals=[assist_signal], state=state)
    assert decision.new_mode == Mode.ASSIST
    assert decision.should_escalate


def test_decide_mode_escalates_to_intervene():
    state = create_initial_trajectory("session-1")
    intervene_signal = Signal(
        type=SignalType.PREMATURE_TERMINATION,
        confidence=0.95,
        suggested_mode="INTERVENE",
        rationale="Terminating with failed test",
    )
    decision = decide_mode(Mode.SHADOW, signals=[intervene_signal], state=state)
    assert decision.new_mode == Mode.INTERVENE
    assert decision.should_escalate


def test_decide_mode_respects_intervention_budget():
    state = create_initial_trajectory("session-1")
    # Simulate 5 prior interventions
    for i in range(5):
        from astra.domain.trajectory import InterventionRecord
        state.interventions.append(
            InterventionRecord(
                intervention_id=f"int-{i}",
                mode="ASSIST",
                trigger_signal="SAME_FILE_REPEATED_EDITS",
                message="guidance",
                timestamp=1000 + i,
            )
        )
    intervene_signal = Signal(
        type=SignalType.PREMATURE_TERMINATION,
        confidence=0.95,
        suggested_mode="INTERVENE",
    )
    decision = decide_mode(Mode.SHADOW, signals=[intervene_signal], state=state, max_session_interventions=5)
    # Must degrade to SHADOW because budget is exhausted
    assert decision.new_mode == Mode.SHADOW
    assert not decision.should_escalate
