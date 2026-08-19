"""Pure rule-based signal detectors.

Zero I/O, zero framework imports. Detects epistemic weaknesses and failure patterns
from TrajectoryState and AstraEvents.
"""

import hashlib
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.events import AstraEvent, EventType, VerificationOutcome
from astra.domain.trajectory import EvidenceRef, TrajectoryState


class SignalType(str, Enum):
    REPEATED_VERIFICATION_FAILURE = "REPEATED_VERIFICATION_FAILURE"
    SAME_FILE_REPEATED_EDITS = "SAME_FILE_REPEATED_EDITS"
    PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
    RECURRING_FAILURE_SIGNATURE = "RECURRING_FAILURE_SIGNATURE"
    UNSUPPORTED_SUCCESS_CLAIM = "UNSUPPORTED_SUCCESS_CLAIM"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"


class Signal(BaseModel):
    """Normalized signal representation produced by rule or model detectors."""

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: SignalType
    confidence: float  # 0.0 to 1.0
    source: str = "rule"  # "rule" or "model"
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    suggested_mode: str = "ASSIST"  # "SHADOW", "ASSIST", "INTERVENE"
    rationale: str = ""
    failure_signature_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def compute_failure_signature(error_text: str) -> str:
    """Computes a stable hash for an error or test failure message."""
    # Normalize error by stripping whitespace and non-alphanumeric noise
    lines = [line.strip() for line in error_text.splitlines() if line.strip()]
    normalized = " ".join(lines[:5])  # Focus on top 5 lines
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def detect_repeated_verification_failures(
    state: TrajectoryState, event: AstraEvent, threshold: int = 2
) -> Optional[Signal]:
    """Flags when consecutive verification checks have failed."""
    if state.failure_count >= threshold:
        latest = state.latest_verification
        ref = EvidenceRef(
            source_type="TEST_OUTPUT",
            locator=f"verification_record_{len(state.verification_history)}",
            summary=latest.summary if latest else "Multiple test failures",
            timestamp=event.received_at,
        )
        return Signal(
            type=SignalType.REPEATED_VERIFICATION_FAILURE,
            confidence=0.9,
            source="rule",
            evidence_refs=[ref],
            suggested_mode="ASSIST" if state.failure_count == threshold else "INTERVENE",
            rationale=f"Agent has failed verification {state.failure_count} times consecutively.",
            metadata={"consecutive_failures": state.failure_count},
        )
    return None


def detect_same_file_repeated_edits(
    state: TrajectoryState, event: AstraEvent, threshold: int = 3
) -> Optional[Signal]:
    """Flags when the agent repeatedly edits the same file without running verification."""
    if not event.tool or event.tool.name not in ["replace_file_content", "write_to_file", "edit_file"]:
        return None

    # Scan backwards from recent actions to count edits without verification
    edits_count = 0
    target_file = event.tool.arguments_summary

    for action in reversed(state.actions_taken):
        if action.tool_name == "run_command":
            break  # A command/verification was run
        if action.tool_name in ["replace_file_content", "write_to_file", "edit_file"]:
            if action.arguments_summary == target_file or not target_file:
                edits_count += 1

    if edits_count >= threshold:
        ref = EvidenceRef(
            source_type="CHANGED_FILE_SLICE",
            locator=target_file,
            summary=f"Repeated edits ({edits_count} times) without verification",
            timestamp=event.received_at,
        )
        return Signal(
            type=SignalType.SAME_FILE_REPEATED_EDITS,
            confidence=0.8,
            source="rule",
            evidence_refs=[ref],
            suggested_mode="ASSIST",
            rationale=f"Agent edited file {edits_count} times without intermediate verification.",
            metadata={"edits_count": edits_count, "target_file": target_file},
        )
    return None


def detect_premature_termination(
    state: TrajectoryState, event: AstraEvent
) -> Optional[Signal]:
    """Flags when agent attempts to Stop immediately after a failed or absent verification."""
    if event.event_type != EventType.STOP:
        return None

    # Case 1: Latest verification was explicitly FAILED
    if state.latest_verification and state.latest_verification.outcome == VerificationOutcome.FAILED:
        latest = state.latest_verification
        sig_hash = compute_failure_signature(latest.summary or latest.command)
        ref = EvidenceRef(
            source_type="TEST_OUTPUT",
            locator=f"verification_{len(state.verification_history)}",
            summary=latest.summary,
            timestamp=event.received_at,
        )
        return Signal(
            type=SignalType.PREMATURE_TERMINATION,
            confidence=0.95,
            source="rule",
            evidence_refs=[ref],
            suggested_mode="INTERVENE",
            rationale=f"Agent attempted to terminate but latest verification failed: {latest.command}",
            failure_signature_hash=sig_hash,
            metadata={"latest_command": latest.command},
        )

    # Case 2: Code changes were made but zero verification was performed
    has_code_edits = any(
        a.tool_name in ["replace_file_content", "write_to_file", "edit_file"]
        for a in state.actions_taken
    )
    if has_code_edits and not state.verification_history:
        return Signal(
            type=SignalType.UNSUPPORTED_SUCCESS_CLAIM,
            confidence=0.85,
            source="rule",
            suggested_mode="INTERVENE",
            rationale="Agent attempted to terminate after code changes without running any verification check.",
            metadata={"code_edits_count": len(state.actions_taken)},
        )

    return None


def run_all_rule_detectors(
    state: TrajectoryState,
    event: AstraEvent,
    repeated_failures_threshold: int = 2,
    repeated_edits_threshold: int = 3,
) -> List[Signal]:
    """Runs all pure rule detectors against state and event, returning all matched signals."""
    signals: List[Signal] = []

    s_fail = detect_repeated_verification_failures(
        state, event, threshold=repeated_failures_threshold
    )
    if s_fail:
        signals.append(s_fail)

    s_edit = detect_same_file_repeated_edits(
        state, event, threshold=repeated_edits_threshold
    )
    if s_edit:
        signals.append(s_edit)

    s_term = detect_premature_termination(state, event)
    if s_term:
        signals.append(s_term)

    return signals
