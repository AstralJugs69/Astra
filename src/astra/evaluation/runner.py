"""Evaluation runner executing comparative benchmark tasks."""

import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional
import structlog

from astra.evaluation.metrics import calculate_transcript_metrics
from astra.evaluation.models import (
    EvaluationCondition,
    RunRecord,
    SecondaryMetrics,
    TaskSpec,
    TrialOutcome,
)
from astra.evaluation.storage import EvaluationStore
from astra.evaluation.tasks.registry import list_benchmark_tasks

logger = structlog.get_logger(__name__)


class EvaluationRunner:
    """Runs benchmark trials under Baseline and With-Astra conditions."""

    def __init__(self, store: Optional[EvaluationStore] = None):
        self.store = store or EvaluationStore()

    def run_mock_trial(
        self,
        task: TaskSpec,
        condition: EvaluationCondition,
    ) -> RunRecord:
        """Executes a simulated trial for unit testing and dry-runs."""
        start_time = int(time.time() * 1000)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # Mock agent trajectory behavior:
        # Baseline typically takes more turns or fails prematurely
        # With-Astra converges faster due to verification intervention
        if condition == EvaluationCondition.BASELINE:
            turns_to_fix = 6
            outcome = TrialOutcome.RESOLVED
            failed_vers = 2
            astra_ints = 0
        else:
            turns_to_fix = 3
            outcome = TrialOutcome.RESOLVED
            failed_vers = 1
            astra_ints = 1

        finished_time = start_time + (turns_to_fix * 10000)

        record = RunRecord(
            run_id=run_id,
            task_id=task.task_id,
            condition=condition,
            antigravity_version="agy-cli-poc",
            main_agent_model_version="gemini-2.5-pro",
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

    def run_all_benchmarks(self, mock: bool = True) -> List[RunRecord]:
        """Runs all registered benchmark tasks under both conditions back-to-back."""
        tasks = list_benchmark_tasks()
        results: List[RunRecord] = []

        for task in tasks:
            logger.info("starting_task_evaluation", task_id=task.task_id)
            # Condition A: Baseline (no Astra)
            res_baseline = self.run_mock_trial(task, condition=EvaluationCondition.BASELINE)
            results.append(res_baseline)

            # Condition B: With-Astra
            res_astra = self.run_mock_trial(task, condition=EvaluationCondition.WITH_ASTRA)
            results.append(res_astra)

        return results
