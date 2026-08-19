"""Unit tests for EvaluationStore."""

import pytest
from astra.evaluation.models import EvaluationCondition, RunRecord, SecondaryMetrics, TrialOutcome
from astra.evaluation.storage import EvaluationStore


def test_eval_store_records_and_lists_runs(tmp_path):
    store = EvaluationStore(runs_dir=tmp_path)
    record = RunRecord(
        run_id="run-001",
        pair_id="pair-001",
        task_id="task-bug-1",
        condition=EvaluationCondition.BASELINE,
        started_at=1000,
        finished_at=2000,
        turns_to_fix=5,
        outcome=TrialOutcome.RESOLVED,
        secondary_metrics=SecondaryMetrics(failed_verification_attempts=2),
    )

    store.record_run(record)
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == "run-001"
    assert runs[0].turns_to_fix == 5
    assert runs[0].condition == EvaluationCondition.BASELINE
