"""Unit tests for EvaluationRunner and report generation."""

from astra.evaluation.report import generate_comparative_report
from astra.evaluation.runner import EvaluationRunner
from astra.evaluation.storage import EvaluationStore


def test_runner_and_report_generation(tmp_path):
    store = EvaluationStore(runs_dir=tmp_path)
    runner = EvaluationRunner(store=store)

    runs = runner.run_all_benchmarks(mock=True)
    assert len(runs) >= 4  # Baseline + With-Astra for each task

    report = generate_comparative_report(store)
    assert "Astra POC — Benchmark Evaluation Report" in report
    assert "With-Astra" in report
    assert "Baseline" in report
    assert "Turn Reduction" in report
