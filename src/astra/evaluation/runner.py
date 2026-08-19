"""Evaluation runner executing comparative paired benchmark trials per Evaluation Architecture."""

import time
import uuid
from typing import List, Optional, Tuple
import structlog

from astra.evaluation.antigravity_runner import AntigravitySessionRunner
from astra.evaluation.hooks_controller import HookController
from astra.evaluation.models import (
    EvaluationCondition,
    RunManifest,
    RunRecord,
    SecondaryMetrics,
    TaskSpec,
    TrialOutcome,
)
from astra.evaluation.storage import EvaluationStore
from astra.evaluation.tasks.registry import list_benchmark_tasks
from astra.evaluation.workspace import WorkspaceManager

logger = structlog.get_logger(__name__)


class EvaluationRunner:
    """Runs paired benchmark trials under Baseline (Astra OFF) and With-Astra (Astra ON) conditions."""

    def __init__(
        self,
        store: Optional[EvaluationStore] = None,
        session_runner: Optional[AntigravitySessionRunner] = None,
        workspace_manager: Optional[WorkspaceManager] = None,
    ):
        self.store = store or EvaluationStore()
        self.session_runner = session_runner or AntigravitySessionRunner()
        self.workspace_manager = workspace_manager or WorkspaceManager()

    def run_mock_trial(
        self,
        task: TaskSpec,
        condition: EvaluationCondition,
        pair_id: str,
    ) -> RunRecord:
        """Executes a simulated trial for unit testing and dry-runs."""
        start_time = int(time.time() * 1000)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

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
            pair_id=pair_id,
            task_id=task.task_id,
            condition=condition,
            benchmark_name="astra_benchmark",
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

    def run_paired_trial(
        self,
        task: TaskSpec,
        mock: bool = False,
    ) -> Tuple[RunRecord, RunRecord, RunManifest]:
        """Executes an isolated paired trial (Baseline vs With-Astra) for a single task."""
        pair_id = f"pair-{uuid.uuid4().hex[:8]}"
        started_at = int(time.time() * 1000)

        if mock:
            baseline_rec = self.run_mock_trial(task, condition=EvaluationCondition.BASELINE, pair_id=pair_id)
            astra_rec = self.run_mock_trial(task, condition=EvaluationCondition.WITH_ASTRA, pair_id=pair_id)
        else:
            # 1. Condition A: Baseline Trial (Astra Hooks OFF)
            ws_baseline = self.workspace_manager.create_isolated_workspace(
                seed_path=task.workspace_seed_ref,
                task_id=task.task_id,
                condition=EvaluationCondition.BASELINE,
            )
            HookController.disable(ws_baseline)
            HookController.validate(ws_baseline, EvaluationCondition.BASELINE)

            t0 = int(time.time() * 1000)
            res_baseline = self.session_runner.run_task_session(
                task=task,
                workspace_path=ws_baseline,
                condition=EvaluationCondition.BASELINE,
            )
            t1 = int(time.time() * 1000)

            baseline_patch = self.workspace_manager.extract_workspace_diff(ws_baseline)
            self.workspace_manager.cleanup_isolated_workspace(ws_baseline)

            baseline_outcome = (
                TrialOutcome.INVALID
                if res_baseline.had_infrastructure_error
                else (TrialOutcome.RESOLVED if res_baseline.exit_code == 0 else TrialOutcome.UNRESOLVED)
            )

            baseline_rec = RunRecord(
                run_id=f"run-{uuid.uuid4().hex[:8]}",
                pair_id=pair_id,
                task_id=task.task_id,
                condition=EvaluationCondition.BASELINE,
                started_at=t0,
                finished_at=t1,
                turns_to_fix=res_baseline.turns_executed if baseline_outcome == TrialOutcome.RESOLVED else None,
                outcome=baseline_outcome,
                invalid_reason=res_baseline.error_message,
                secondary_metrics=SecondaryMetrics(
                    time_to_fix_seconds=res_baseline.time_taken_seconds,
                    failed_verification_attempts=1 if res_baseline.exit_code != 0 else 0,
                ),
                patch=baseline_patch,
            )
            self.store.record_run(baseline_rec)

            # 2. Condition B: With-Astra Trial (Astra Hooks ON)
            ws_astra = self.workspace_manager.create_isolated_workspace(
                seed_path=task.workspace_seed_ref,
                task_id=task.task_id,
                condition=EvaluationCondition.WITH_ASTRA,
            )
            HookController.enable(ws_astra)
            HookController.validate(ws_astra, EvaluationCondition.WITH_ASTRA)

            t2 = int(time.time() * 1000)
            res_astra = self.session_runner.run_task_session(
                task=task,
                workspace_path=ws_astra,
                condition=EvaluationCondition.WITH_ASTRA,
            )
            t3 = int(time.time() * 1000)

            astra_patch = self.workspace_manager.extract_workspace_diff(ws_astra)
            self.workspace_manager.cleanup_isolated_workspace(ws_astra)

            astra_outcome = (
                TrialOutcome.INVALID
                if res_astra.had_infrastructure_error
                else (TrialOutcome.RESOLVED if res_astra.exit_code == 0 else TrialOutcome.UNRESOLVED)
            )

            astra_rec = RunRecord(
                run_id=f"run-{uuid.uuid4().hex[:8]}",
                pair_id=pair_id,
                task_id=task.task_id,
                condition=EvaluationCondition.WITH_ASTRA,
                started_at=t2,
                finished_at=t3,
                turns_to_fix=res_astra.turns_executed if astra_outcome == TrialOutcome.RESOLVED else None,
                outcome=astra_outcome,
                invalid_reason=res_astra.error_message,
                secondary_metrics=SecondaryMetrics(
                    time_to_fix_seconds=res_astra.time_taken_seconds,
                    failed_verification_attempts=1 if res_astra.exit_code != 0 else 0,
                    astra_interventions=1 if astra_outcome == TrialOutcome.RESOLVED else 0,
                ),
                patch=astra_patch,
            )
            self.store.record_run(astra_rec)

        # 3. Calculate Paired Differences
        finished_at = int(time.time() * 1000)
        delta_turns = None
        efficiency_pct = None
        if baseline_rec.turns_to_fix and astra_rec.turns_to_fix:
            delta_turns = astra_rec.turns_to_fix - baseline_rec.turns_to_fix
            if baseline_rec.turns_to_fix > 0:
                efficiency_pct = ((baseline_rec.turns_to_fix - astra_rec.turns_to_fix) / baseline_rec.turns_to_fix) * 100.0

        delta_time = (
            astra_rec.secondary_metrics.time_to_fix_seconds - baseline_rec.secondary_metrics.time_to_fix_seconds
        )

        manifest = RunManifest(
            pair_id=pair_id,
            task_id=task.task_id,
            baseline_run_id=baseline_rec.run_id,
            with_astra_run_id=astra_rec.run_id,
            benchmark_name="astra_benchmark",
            started_at=started_at,
            finished_at=finished_at,
            delta_turns=delta_turns,
            delta_time_seconds=delta_time,
            efficiency_gain_pct=efficiency_pct,
        )

        return baseline_rec, astra_rec, manifest

    def run_all_benchmarks(self, mock: bool = False) -> List[RunRecord]:
        """Runs all registered benchmark tasks under paired conditions."""
        tasks = list_benchmark_tasks()
        results: List[RunRecord] = []

        for task in tasks:
            logger.info("starting_task_evaluation", task_id=task.task_id)
            baseline_rec, astra_rec, _ = self.run_paired_trial(task, mock=mock)
            results.append(baseline_rec)
            results.append(astra_rec)

        return results
