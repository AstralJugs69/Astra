"""Unit tests for pure anti-loop safety policy."""

from astra.domain.intervention import (
    evaluate_anti_loop_policy,
    record_intervention,
)
from astra.domain.trajectory import create_initial_trajectory


def test_anti_loop_permits_initial_intervention():
    state = create_initial_trajectory("session-1")
    sig_hash = "sig-abc12345"

    decision = evaluate_anti_loop_policy(
        state,
        failure_signature_hash=sig_hash,
        max_forced_continuations_per_sig=2,
    )
    assert decision.allow_forced_continuation
    assert decision.action == "continue_block"
    assert decision.current_count == 0


def test_anti_loop_exhaustion_triggers_surface_to_user():
    state = create_initial_trajectory("session-1")
    sig_hash = "sig-abc12345"

    # Simulate 2 recorded forced continuations
    state = record_intervention(
        state=state,
        intervention_id="int-1",
        mode="INTERVENE",
        trigger_signal="PREMATURE_TERMINATION",
        message="Block 1",
        was_forced_continuation=True,
        failure_signature_hash=sig_hash,
        timestamp_ms=1000,
    )
    state = record_intervention(
        state=state,
        intervention_id="int-2",
        mode="INTERVENE",
        trigger_signal="PREMATURE_TERMINATION",
        message="Block 2",
        was_forced_continuation=True,
        failure_signature_hash=sig_hash,
        timestamp_ms=2000,
    )

    assert state.failure_signatures[sig_hash] == 2

    # 3rd attempt: must trigger surface_to_user and disallow forced loop
    decision = evaluate_anti_loop_policy(
        state,
        failure_signature_hash=sig_hash,
        max_forced_continuations_per_sig=2,
    )
    assert not decision.allow_forced_continuation
    assert decision.action == "surface_to_user"
    assert decision.user_surfaced_message is not None
    assert "Anti-Loop Guard" in decision.user_surfaced_message


def test_anti_loop_cooldown_enforcement():
    state = create_initial_trajectory("session-1")
    sig_hash = "sig-abc12345"

    state = record_intervention(
        state=state,
        intervention_id="int-1",
        mode="INTERVENE",
        trigger_signal="PREMATURE_TERMINATION",
        message="Block 1",
        was_forced_continuation=True,
        failure_signature_hash=sig_hash,
        timestamp_ms=10000,
    )

    # Attempt forced continuation just 5 seconds later (cooldown is 30s)
    decision = evaluate_anti_loop_policy(
        state,
        failure_signature_hash=sig_hash,
        max_forced_continuations_per_sig=2,
        cooldown_seconds=30.0,
        current_time_ms=15000,  # 5s elapsed
    )
    assert not decision.allow_forced_continuation
    assert decision.action == "allow_normal_stop"
    assert "cooldown active" in decision.reason
