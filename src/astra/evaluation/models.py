"""Evaluation harness data models for the Astra Challenge Set."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.model_ports import CostMetadata


class TaskDifficulty(str, Enum):
    TIER_A_HARD = "Tier A - Hard"
    TIER_B_VERY_HARD = "Tier B - Very Hard"
    TIER_C_EXTREME = "Tier C - Extreme"


class BenchmarkSource(str, Enum):
    TERMINAL_BENCH = "Terminal-Bench"
    SWE_BENCH_PRO = "SWE-Bench Pro"
    HARNESS_BENCH = "Harness-Bench"
    SWE_SMITH = "SWE-smith"


class TaskCategory(str, Enum):
    DEVOPS_SYSTEMS = "devops_systems"
    NETWORKING_ASYNC = "networking_async"
    DATA_PROCESSING_ML = "data_processing_ml"
    CORE_SOFTWARE_REPAIR = "core_software_repair"
    STORAGE_SYSTEMS = "storage_systems"


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
    """Specification of an Astra Challenge Set benchmark task."""

    task_id: str
    source: BenchmarkSource = BenchmarkSource.TERMINAL_BENCH
    difficulty: TaskDifficulty = TaskDifficulty.TIER_A_HARD
    category: TaskCategory = TaskCategory.CORE_SOFTWARE_REPAIR
    workspace_seed_ref: str
    prompt: str
    target_failure_mode: str
    oracle_command: str
    precondition_command: Optional[str] = None
    hidden_test_command: Optional[str] = None
    max_turns: int = 20
    condition: EvaluationCondition = EvaluationCondition.BASELINE

    @property
    def verification_command(self) -> str:
        return self.oracle_command


class RunRecord(BaseModel):
    """Immutable record of an evaluation trial."""

    run_id: str
    task_id: str
    condition: EvaluationCondition
    difficulty: TaskDifficulty = TaskDifficulty.TIER_A_HARD
    source: BenchmarkSource = BenchmarkSource.TERMINAL_BENCH
    antigravity_version: str = "agy-v1"
    main_agent_model_version: str = "gemini-3.7-flash"
    started_at: int
    finished_at: int
    turns_to_fix: Optional[int] = None  # None if unresolved/invalid
    outcome: TrialOutcome
    invalid_reason: Optional[str] = None
    secondary_metrics: SecondaryMetrics = Field(default_factory=SecondaryMetrics)
