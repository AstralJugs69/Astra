"""Benchmark task registry for evaluation."""

from typing import Dict, List
from astra.evaluation.models import TaskCategory, TaskSpec

BENCHMARK_TASKS: Dict[str, TaskSpec] = {
    "bug-01-off-by-one": TaskSpec(
        task_id="bug-01-off-by-one",
        category=TaskCategory.REPRODUCIBLE_BUG,
        workspace_seed_ref="evaluation/seeds/bug_01",
        prompt="Fix the off-by-one indexing error in calculate_sliding_window() in window.py so tests pass.",
        verification_command="pytest tests/test_window.py",
        max_turns=10,
    ),
    "bug-02-dict-key-mismatch": TaskSpec(
        task_id="bug-02-dict-key-mismatch",
        category=TaskCategory.REPRODUCIBLE_BUG,
        workspace_seed_ref="evaluation/seeds/bug_02",
        prompt="Fix the KeyError in session deserialization in session_manager.py so tests pass.",
        verification_command="pytest tests/test_session.py",
        max_turns=10,
    ),
    "zindi-01-feature-leakage": TaskSpec(
        task_id="zindi-01-feature-leakage",
        category=TaskCategory.ZINDI,
        workspace_seed_ref="evaluation/seeds/zindi_01",
        prompt="Identify and fix the data leakage bug where the target column is inadvertently normalized into features.",
        verification_command="python evaluate_pipeline.py",
        max_turns=15,
    ),
}


def get_task_spec(task_id: str) -> TaskSpec:
    """Retrieves a benchmark task spec by ID."""
    if task_id not in BENCHMARK_TASKS:
        raise KeyError(f"Task ID '{task_id}' not found in benchmark registry.")
    return BENCHMARK_TASKS[task_id]


def list_benchmark_tasks() -> List[TaskSpec]:
    """Returns all registered benchmark tasks."""
    return list(BENCHMARK_TASKS.values())
