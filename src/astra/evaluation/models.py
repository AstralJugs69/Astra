"""Evaluation harness data models per Section 31.4 and docs/evaluation-architecture.md."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.model_ports import CostMetadata


class TaskCategory(str, Enum):
    REPRODUCIBLE_BUG = "reproducible_bug"
    SWEBENCH = "swebench"
    ZINDI = "zindi"


class EvaluationCondition(str, Enum):
    BASELINE = "baseline"        # Astra hooks absent
    WITH_ASTRA = "with_astra"    # Normal production Astra hooks active


class TrialOutcome(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


class SecondaryMetrics(BaseModel):
    """Secondary evaluation metrics."""

    failed_verification_attempts: int = 0
    time_to_fix_seconds: float = 0.0
    total_tool_calls: int = 0
    unnecessary_changes: int = 0
    astra_interventions: int = 0
    astra_assists: int = 0
    blocked_stop_events: int = 0
    fast_model_calls: int = 0
    deep_model_calls: int = 0
    model_cost: CostMetadata = Field(default_factory=CostMetadata)


class TaskSpec(BaseModel):
    """Specification of a benchmark-neutral evaluation task."""

    task_id: str
    category: TaskCategory
    workspace_seed_ref: str
    prompt: str
    verification_command: str
    max_turns: int = 15
    instance_id: Optional[str] = None
    repo: Optional[str] = None
    base_commit: Optional[str] = None


class RunRecord(BaseModel):
    """Immutable record of an evaluation trial."""

    run_id: str
    pair_id: str = Field(default="", description="Identifier grouping paired Baseline and With-Astra runs")
    task_id: str
    condition: EvaluationCondition
    benchmark_name: str = "astra_benchmark"
    antigravity_version: str = "agy-v1"
    main_agent_model_version: str = "gemini-3.7-flash"
    started_at: int
    finished_at: int
    turns_to_fix: Optional[int] = None  # None if unresolved/invalid
    outcome: TrialOutcome
    invalid_reason: Optional[str] = None
    secondary_metrics: SecondaryMetrics = Field(default_factory=SecondaryMetrics)
    patch: Optional[str] = None


class RunManifest(BaseModel):
    """Immutable manifest for an evaluation trial pair."""

    pair_id: str
    task_id: str
    baseline_run_id: str
    with_astra_run_id: str
    benchmark_name: str
    started_at: int
    finished_at: int
    delta_turns: Optional[int] = None
    delta_time_seconds: Optional[float] = None
    efficiency_gain_pct: Optional[float] = None
