"""Evaluation harness data models per Section 31.4."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.model_ports import CostMetadata


class TaskCategory(str, Enum):
    REPRODUCIBLE_BUG = "reproducible_bug"
    ZINDI = "zindi"


class EvaluationCondition(str, Enum):
    BASELINE = "baseline"  # No hooks.json active
    WITH_ASTRA = "with_astra"  # Astra hooks active


class TrialOutcome(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


class SecondaryMetrics(BaseModel):
    """Secondary evaluation metrics."""

    failed_verification_attempts: int = 0
    time_to_fix_seconds: float = 0.0
    unnecessary_changes: int = 0
    astra_interventions: int = 0
    model_cost: CostMetadata = Field(default_factory=CostMetadata)


class TaskSpec(BaseModel):
    """Specification of an evaluation task."""

    task_id: str
    category: TaskCategory
    workspace_seed_ref: str
    prompt: str
    verification_command: str
    max_turns: int = 15
    condition: EvaluationCondition = EvaluationCondition.BASELINE


class RunRecord(BaseModel):
    """Immutable record of an evaluation trial."""

    run_id: str
    task_id: str
    condition: EvaluationCondition
    antigravity_version: str = "agy-v1"
    main_agent_model_version: str = "gemini-2.5-pro"
    started_at: int
    finished_at: int
    turns_to_fix: Optional[int] = None  # None if unresolved/invalid
    outcome: TrialOutcome
    invalid_reason: Optional[str] = None
    secondary_metrics: SecondaryMetrics = Field(default_factory=SecondaryMetrics)
