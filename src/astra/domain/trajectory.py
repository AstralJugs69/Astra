"""Pure trajectory state models and reducers.

Zero I/O, zero framework imports. Represents compact trajectory state for an agent session.
"""

import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.events import AstraEvent, EventType, VerificationOutcome


class EvidenceRef(BaseModel):
    """Reference to evidence stored locally or in session artifacts."""

    source_type: str  # e.g., "TRANSCRIPT_SLICE", "TEST_OUTPUT", "CHANGED_FILE_SLICE"
    locator: str  # Path, line range, or turn index
    summary: str = ""
    timestamp: int = 0


class ActionRecord(BaseModel):
    """Compact record of a tool execution action."""

    step_index: Optional[int] = None
    tool_name: str
    arguments_summary: str = ""
    had_error: bool = False
    outcome_summary: str = ""
    timestamp: int


class VerificationRecord(BaseModel):
    """Record of an explicit verification check (e.g. running tests or lint)."""

    step_index: Optional[int] = None
    command: str
    outcome: VerificationOutcome
    summary: str = ""
    timestamp: int


class InterventionRecord(BaseModel):
    """Record of an Astra assistance or intervention."""

    intervention_id: str
    mode: str  # "ASSIST" or "INTERVENE"
    trigger_signal: str
    message: str
    timestamp: int
    was_forced_continuation: bool = False
    failure_signature_hash: Optional[str] = None


class TrajectoryState(BaseModel):
    """Compact, durable epistemic trajectory state for an agent session."""

    session_id: str
    schema_version: int = 1
    state_version: int = 1
    task: Optional[str] = None
    current_hypothesis: Optional[str] = None
    evidence_gathered: List[EvidenceRef] = Field(default_factory=list)
    actions_taken: List[ActionRecord] = Field(default_factory=list)
    modified_files: List[str] = Field(default_factory=list)
    verification_history: List[VerificationRecord] = Field(default_factory=list)
    failure_signatures: Dict[str, int] = Field(default_factory=dict)
    current_mode: str = "SHADOW"
    interventions: List[InterventionRecord] = Field(default_factory=list)
    unresolved_questions: List[str] = Field(default_factory=list)
    created_at: int
    updated_at: int

    @property
    def failure_count(self) -> int:
        """Derived count of consecutive verification failures."""
        count = 0
        for record in reversed(self.verification_history):
            if record.outcome == VerificationOutcome.FAILED:
                count += 1
            else:
                break
        return count

    @property
    def total_interventions(self) -> int:
        """Derived count of interventions made in this session."""
        return len(self.interventions)

    @property
    def latest_verification(self) -> Optional[VerificationRecord]:
        """Returns the most recent verification record, if any."""
        if self.verification_history:
            return self.verification_history[-1]
        return None


def create_initial_trajectory(session_id: str, timestamp_ms: Optional[int] = None) -> TrajectoryState:
    """Creates a new initial TrajectoryState for a session."""
    now = timestamp_ms or int(time.time() * 1000)
    return TrajectoryState(
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )


def is_verification_command(command: str) -> bool:
    """Checks if a shell command is a verification command (tests, lint, typecheck)."""
    cmd_lower = command.lower()
    verification_keywords = [
        "pytest", "unittest", "pytest", "npm test", "npm run test",
        "mvn test", "cargo test", "go test", "ctest", "make test",
        "ruff", "flake8", "eslint", "mypy", "pyright", "tsc"
    ]
    return any(kw in cmd_lower for kw in verification_keywords)


def reduce_trajectory(state: TrajectoryState, event: AstraEvent) -> TrajectoryState:
    """Pure state transition reducer: produces a new TrajectoryState given an AstraEvent."""
    new_state = state.model_copy(deep=True)
    new_state.state_version += 1
    new_state.updated_at = event.received_at

    if event.event_type == EventType.POST_TOOL_USE and event.tool:
        tool = event.tool
        # Record action
        action = ActionRecord(
            step_index=event.step_index,
            tool_name=tool.name,
            arguments_summary=tool.arguments_summary,
            had_error=tool.had_error,
            outcome_summary=tool.output_summary,
            timestamp=event.received_at,
        )
        new_state.actions_taken.append(action)

        # Check if action was a file modification
        if tool.name in ["write_to_file", "replace_file_content", "multi_replace_file_content", "edit_file"]:
            target = tool.arguments_summary or ""
            if target and target not in new_state.modified_files:
                new_state.modified_files.append(target)

        # Check if action was a verification attempt
        if tool.name == "run_command":
            if is_verification_command(tool.arguments_summary):
                outcome = VerificationOutcome.FAILED if tool.had_error else VerificationOutcome.PASSED
                ver_record = VerificationRecord(
                    step_index=event.step_index,
                    command=tool.arguments_summary,
                    outcome=outcome,
                    summary=tool.output_summary[:500],
                    timestamp=event.received_at,
                )
                new_state.verification_history.append(ver_record)

    return new_state
