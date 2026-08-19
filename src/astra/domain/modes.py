"""Pure escalation and operating mode policy.

Zero I/O, zero framework imports. Maps signals and trajectory state into Mode decisions.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel

from astra.domain.signals import Signal
from astra.domain.trajectory import TrajectoryState


class Mode(str, Enum):
    SHADOW = "SHADOW"
    ASSIST = "ASSIST"
    INTERVENE = "INTERVENE"


class ModeDecision(BaseModel):
    """Result of an escalation evaluation pass."""

    current_mode: Mode
    new_mode: Mode
    should_escalate: bool
    reason: str
    primary_signal: Optional[Signal] = None


def decide_mode(
    current_mode: Mode,
    signals: List[Signal],
    state: TrajectoryState,
    max_session_interventions: int = 5,
) -> ModeDecision:
    """Pure escalation policy determining whether to remain in Shadow or escalate to Assist/Intervene."""
    if not signals:
        return ModeDecision(
            current_mode=current_mode,
            new_mode=Mode.SHADOW,
            should_escalate=False,
            reason="No active risk signals detected; operating in Shadow observation mode.",
        )

    # Check session-wide intervention budget
    if state.total_interventions >= max_session_interventions:
        return ModeDecision(
            current_mode=current_mode,
            new_mode=Mode.SHADOW,
            should_escalate=False,
            reason=f"Intervention budget exhausted ({state.total_interventions}/{max_session_interventions}). Degraded to Shadow.",
        )

    # Sort signals by confidence descending
    sorted_signals = sorted(signals, key=lambda s: s.confidence, reverse=True)
    top_signal = sorted_signals[0]

    # Check if any signal requests INTERVENE
    intervene_signals = [s for s in signals if s.suggested_mode == Mode.INTERVENE.value]
    if intervene_signals:
        primary = intervene_signals[0]
        return ModeDecision(
            current_mode=current_mode,
            new_mode=Mode.INTERVENE,
            should_escalate=(current_mode != Mode.INTERVENE),
            reason=primary.rationale or "High-severity signal warrants authoritative intervention.",
            primary_signal=primary,
        )

    # Check if any signal requests ASSIST
    assist_signals = [s for s in signals if s.suggested_mode == Mode.ASSIST.value]
    if assist_signals:
        primary = assist_signals[0]
        return ModeDecision(
            current_mode=current_mode,
            new_mode=Mode.ASSIST,
            should_escalate=(current_mode == Mode.SHADOW),
            reason=primary.rationale or "Medium-severity signal warrants structured assistance.",
            primary_signal=primary,
        )

    return ModeDecision(
        current_mode=current_mode,
        new_mode=Mode.SHADOW,
        should_escalate=False,
        reason="Signals present but below escalation threshold.",
        primary_signal=top_signal,
    )
