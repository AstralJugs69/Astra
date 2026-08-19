"""Pure anti-loop safety policy and intervention budgeting.

Zero I/O, zero framework imports. Prevents infinite forced continuation loops.
"""

from typing import Optional
from pydantic import BaseModel

from astra.domain.trajectory import InterventionRecord, TrajectoryState


class AntiLoopDecision(BaseModel):
    """Result of an anti-loop safety check."""

    allow_forced_continuation: bool
    current_count: int
    max_allowed: int
    action: str  # "continue_block", "surface_to_user", "allow_normal_stop"
    reason: str
    user_surfaced_message: Optional[str] = None


def evaluate_anti_loop_policy(
    state: TrajectoryState,
    failure_signature_hash: Optional[str],
    max_forced_continuations_per_sig: int = 2,
    cooldown_seconds: float = 30.0,
    current_time_ms: Optional[int] = None,
) -> AntiLoopDecision:
    """Evaluates whether an intervention on Stop is safe or would risk an infinite loop."""
    if not failure_signature_hash:
        return AntiLoopDecision(
            allow_forced_continuation=True,
            current_count=0,
            max_allowed=max_forced_continuations_per_sig,
            action="continue_block",
            reason="No recurring failure signature; continuation permitted.",
        )

    current_count = state.failure_signatures.get(failure_signature_hash, 0)

    # Check if signature has hit max forced continuations cap
    if current_count >= max_forced_continuations_per_sig:
        return AntiLoopDecision(
            allow_forced_continuation=False,
            current_count=current_count,
            max_allowed=max_forced_continuations_per_sig,
            action="surface_to_user",
            reason=f"Failure signature {failure_signature_hash} reached forced continuation cap ({current_count}/{max_forced_continuations_per_sig}).",
            user_surfaced_message=(
                f"Astra Anti-Loop Guard: The agent has attempted to fix this recurring failure {current_count} "
                "times without resolution. Surfacing unresolved failure to user."
            ),
        )

    # Check cooldown if previous forced intervention was very recent
    if state.interventions and current_time_ms is not None:
        last_intervention = state.interventions[-1]
        if last_intervention.was_forced_continuation and last_intervention.failure_signature_hash == failure_signature_hash:
            elapsed_sec = (current_time_ms - last_intervention.timestamp) / 1000.0
            if elapsed_sec < cooldown_seconds:
                return AntiLoopDecision(
                    allow_forced_continuation=False,
                    current_count=current_count,
                    max_allowed=max_forced_continuations_per_sig,
                    action="allow_normal_stop",
                    reason=f"Intervention cooldown active ({elapsed_sec:.1f}s < {cooldown_seconds}s).",
                )

    return AntiLoopDecision(
        allow_forced_continuation=True,
        current_count=current_count,
        max_allowed=max_forced_continuations_per_sig,
        action="continue_block",
        reason=f"Forced continuation {current_count + 1} of {max_forced_continuations_per_sig} permitted.",
    )


def record_intervention(
    state: TrajectoryState,
    intervention_id: str,
    mode: str,
    trigger_signal: str,
    message: str,
    was_forced_continuation: bool,
    failure_signature_hash: Optional[str],
    timestamp_ms: int,
) -> TrajectoryState:
    """Pure state updater recording an intervention and updating failure signature counts."""
    new_state = state.model_copy(deep=True)
    new_state.state_version += 1
    new_state.updated_at = timestamp_ms

    record = InterventionRecord(
        intervention_id=intervention_id,
        mode=mode,
        trigger_signal=trigger_signal,
        message=message,
        timestamp=timestamp_ms,
        was_forced_continuation=was_forced_continuation,
        failure_signature_hash=failure_signature_hash,
    )
    new_state.interventions.append(record)

    if was_forced_continuation and failure_signature_hash:
        new_state.failure_signatures[failure_signature_hash] = (
            new_state.failure_signatures.get(failure_signature_hash, 0) + 1
        )

    return new_state
