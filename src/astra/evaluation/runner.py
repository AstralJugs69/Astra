"""Evaluation runner executing comparative benchmark tasks for the Astra Challenge Set."""

import time
import uuid
from typing import Dict, List, Optional
import structlog

from astra.evaluation.live_runner import AntigravityLiveRunner
from astra.evaluation.metrics import calculate_transcript_metrics
from astra.evaluation.models import (
    EvaluationCondition,
    RunRecord,
    SecondaryMetrics,
    TaskDifficulty,
    TaskSpec,
    TrialOutcome,
)
from astra.evaluation.storage import EvaluationStore
from astra.evaluation.tasks.registry import list_benchmark_tasks

logger = structlog.get_logger(__name__)


class EvaluationRunner:
    """Runs benchmark trials under Baseline and With-Astra conditions."""

    def __init__(
        self,
        store: Optional[EvaluationStore] = None,
        live_runner: Optional[AntigravityLiveRunner] = None,
    ):
        self.store = store or EvaluationStore()
        self.live_runner = live_runner or AntigravityLiveRunner()

    def run_mock_trial(
        self,
        task: TaskSpec,
        condition: EvaluationCondition,
    ) -> RunRecord:
        """Executes a simulated trial across difficulty bands for verification."""
        start_time = int(time.time() * 1000)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # Difficulty-adjusted turn scaling
        if task.difficulty == TaskDifficulty.TIER_A_HARD:
            base_turns = 7
            astra_turns = 3
        elif task.difficulty == TaskDifficulty.TIER_B_VERY_HARD:
            base_turns = 10
            astra_turns = 4
        else:  # TIER_C_EXTREME
            base_turns = 14
            astra_turns = 6

        if condition == EvaluationCondition.BASELINE:
            turns_to_fix = base_turns
            outcome = TrialOutcome.RESOLVED
            failed_vers = max(1, base_turns // 3)
            astra_ints = 0
        else:
            turns_to_fix = astra_turns
            outcome = TrialOutcome.RESOLVED
            failed_vers = 1
            astra_ints = max(1, astra_turns // 2)

        finished_time = start_time + (turns_to_fix * 5000)

        record = RunRecord(
            run_id=run_id,
            task_id=task.task_id,
            condition=condition,
            difficulty=task.difficulty,
            source=task.source,
            antigravity_version="agy-v1",
            main_agent_model_version="gemini-3.7-flash",
            started_at=start_time,
            finished_at=finished_time,
            turns_to_fix=turns_to_fix,
            outcome=outcome,
            secondary_metrics=SecondaryMetrics(
                failed_verification_attempts=failed_vers,
                time_to_fix_seconds=(finished_time - start_time) / 1000.0,
                astra_interventions=astra_ints,
            ),
        )

        self.store.record_run(record)
        return record

    def run_trial(
        self,
        task: TaskSpec,
        condition: EvaluationCondition,
        mock: bool = False,
    ) -> RunRecord:
        """Executes a single trial either live via agy CLI or simulated."""
        if mock:
            return self.run_mock_trial(task, condition)
        else:
            record = self.live_runner.execute_live_trial(task, condition)
            self.store.record_run(record)
            return record

    def run_all_benchmarks(
        self,
        mock: bool = False,
        task_id_filter: Optional[str] = None,
        difficulty_filter: Optional[TaskDifficulty] = None,
    ) -> List[RunRecord]:
        """Runs registered benchmark tasks under both conditions back-to-back."""
        tasks = list_benchmark_tasks()
        if task_id_filter:
            tasks = [t for t in tasks if t.task_id == task_id_filter]
        if difficulty_filter:
            tasks = [t for t in tasks if t.difficulty == difficulty_filter]

        results: List[RunRecord] = []

        for task in tasks:
            logger.info("starting_task_evaluation", task_id=task.task_id, difficulty=task.difficulty.value, mock=mock)
            # Condition A: Baseline (no Astra)
            res_baseline = self.run_trial(task, condition=EvaluationCondition.BASELINE, mock=mock)
            results.append(res_baseline)

            # Condition B: With-Astra
            res_astra = self.run_trial(task, condition=EvaluationCondition.WITH_ASTRA, mock=mock)
            results.append(res_astra)

        return results
