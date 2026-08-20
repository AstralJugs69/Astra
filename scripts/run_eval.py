"""CLI runner for Astra Challenge Set evaluation suite."""

import argparse
import sys
from pathlib import Path
from astra.evaluation.models import TaskDifficulty
from astra.evaluation.report import generate_comparative_report
from astra.evaluation.runner import EvaluationRunner
from astra.evaluation.storage import EvaluationStore
from astra.evaluation.tasks.registry import BENCHMARK_TASKS

# Set UTF-8 encoding for stdout on Windows if supported
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Run Astra Challenge Set benchmark evaluation suite.")
    parser.add_argument("--mock", action="store_true", default=False, help="Run simulated trials instead of live agy execution")
    parser.add_argument("--task", type=str, default=None, help="Run a specific task ID (e.g., tb-git-bisect-merge-conflict)")
    parser.add_argument("--tier", type=str, choices=["A", "B", "C"], default=None, help="Run specific difficulty tier (A=Hard, B=Very Hard, C=Extreme)")
    parser.add_argument("--output", default="evaluation/evaluation_report.md", help="Report output file")
    parser.add_argument("--clean", action="store_true", help="Clear existing run history before starting")
    args = parser.parse_args()

    store = EvaluationStore()
    if args.clean:
        store.clear()
        print("Cleared prior evaluation run records.")

    runner = EvaluationRunner(store=store)

    tier_filter = None
    if args.tier == "A":
        tier_filter = TaskDifficulty.TIER_A_HARD
    elif args.tier == "B":
        tier_filter = TaskDifficulty.TIER_B_VERY_HARD
    elif args.tier == "C":
        tier_filter = TaskDifficulty.TIER_C_EXTREME

    mode_label = "SIMULATED (Mock)" if args.mock else "LIVE (Antigravity CLI agy)"
    print("=" * 80)
    print(f"  ASTRA CHALLENGE SET EVALUATION — {mode_label}")
    print("=" * 80)

    if args.task:
        print(f"Targeting single task: {args.task}")
    elif args.tier:
        print(f"Targeting Difficulty Tier {args.tier}")
    else:
        print(f"Running all {len(BENCHMARK_TASKS)} challenge tasks...")

    runs = runner.run_all_benchmarks(
        mock=args.mock,
        task_id_filter=args.task,
        difficulty_filter=tier_filter,
    )
    print(f"\nCompleted {len(runs)} benchmark runs (Baseline + With-Astra).")

    report = generate_comparative_report(store)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written to {args.output}")

    try:
        print("\n" + report)
    except UnicodeEncodeError:
        print("\n" + report.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
