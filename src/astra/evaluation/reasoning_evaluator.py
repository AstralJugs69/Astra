"""Official Reasoning Benchmark Evaluator integrated from MR-Ben and ProcessBench.

Evaluates meta-reasoning, error localization (earliest erroneous step),
and reasoning critique quality on multi-step reasoning traces.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class ReasoningTaskItem(BaseModel):
    """Specification of a reasoning benchmark problem."""

    task_id: str
    benchmark: str = "MR-Ben"  # "MR-Ben" or "ProcessBench"
    subject: str  # "coding", "logic", "olympiad_math"
    question: str
    solution_steps: List[str]
    ground_truth_correctness: str  # "correct" or "incorrect"
    ground_truth_first_error_step: int  # -1 if correct, else 0-indexed step
    ground_truth_error_reason: str
    ground_truth_answer: str


class ReasoningEvaluationResult(BaseModel):
    """Grading result for a single reasoning benchmark task."""

    task_id: str
    subject: str
    condition: str  # "baseline" or "with_astra"
    predicted_correctness: str
    predicted_first_error_step: Optional[int]
    critique_text: str
    correctness_match: bool
    step_localization_match: bool
    mr_score: float  # Composite Meta-Reasoning Score [0.0 to 1.0]
    wall_time_seconds: float = 0.0


def extract_boxed_answer(text: str) -> Optional[int]:
    """Extracts boxed integer step from model output: \\boxed{N}."""
    match = re.search(r"\\boxed\{(-?\d+)\}", text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    # Fallback to looking for step mentions: "Step 2" or "step 2"
    match_step = re.search(r"step\s*(\d+)", text, re.IGNORECASE)
    if match_step:
        try:
            return int(match_step.group(1))
        except ValueError:
            pass
    if "no error" in text.lower() or "correct" in text.lower():
        return -1
    return None


def compute_mr_score(
    predicted_correctness: str,
    predicted_step: Optional[int],
    gt_correctness: str,
    gt_step: int,
) -> Tuple[bool, bool, float]:
    """Computes the official MR-Ben / ProcessBench composite score.
    
    Score rubric:
    - 0.0: Misclassified correctness (e.g. said correct when incorrect).
    - 0.5: Correctly identified error exists, but missed the exact earliest error step.
    - 1.0: Correctly identified error and pinpointed the exact earliest faulty step.
    """
    is_correctness_match = (
        predicted_correctness.strip().lower() == gt_correctness.strip().lower()
    )

    if not is_correctness_match:
        return False, False, 0.0

    if gt_correctness.lower() == "correct":
        # Problem was correct and model identified it as correct
        return True, True, 1.0

    # For incorrect solutions:
    is_step_match = (predicted_step == gt_step)
    score = 1.0 if is_step_match else 0.5
    return is_correctness_match, is_step_match, score
