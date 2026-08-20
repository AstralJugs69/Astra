"""Unit tests for the official MR-Ben and ProcessBench reasoning evaluator."""

from astra.evaluation.reasoning_evaluator import compute_mr_score, extract_boxed_answer
from astra.evaluation.tasks.reasoning_registry import get_reasoning_task, list_reasoning_tasks


def test_reasoning_registry_loading():
    tasks = list_reasoning_tasks()
    assert len(tasks) == 15

    coding_tasks = [t for t in tasks if t.subject == "coding"]
    logic_tasks = [t for t in tasks if t.subject == "logic"]
    math_tasks = [t for t in tasks if t.subject == "olympiad_math"]

    assert len(coding_tasks) == 5
    assert len(logic_tasks) == 5
    assert len(math_tasks) == 5

    task1 = get_reasoning_task("mrben-coding-01")
    assert task1.ground_truth_correctness == "incorrect"
    assert task1.ground_truth_first_error_step == 3


def test_boxed_answer_extraction():
    assert extract_boxed_answer("The earliest error is at \\boxed{3}.") == 3
    assert extract_boxed_answer("The derivation is sound: \\boxed{-1}") == -1
    assert extract_boxed_answer("Found in step 2 of the proof.") == 2
    assert extract_boxed_answer("The solution is correct with no errors.") == -1


def test_mr_score_computation():
    # Perfect match on error and step -> 1.0
    c_match, s_match, score = compute_mr_score("incorrect", 3, "incorrect", 3)
    assert c_match is True
    assert s_match is True
    assert score == 1.0

    # Identified error but wrong step -> 0.5
    c_match, s_match, score = compute_mr_score("incorrect", 4, "incorrect", 3)
    assert c_match is True
    assert s_match is False
    assert score == 0.5

    # Misclassified correctness -> 0.0
    c_match, s_match, score = compute_mr_score("correct", -1, "incorrect", 3)
    assert c_match is False
    assert score == 0.0
