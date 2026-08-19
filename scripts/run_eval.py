"""CLI runner for evaluation suite."""

import argparse
import sys
from pathlib import Path
from astra.evaluation.report import generate_comparative_report
from astra.evaluation.runner import EvaluationRunner
from astra.evaluation.storage import EvaluationStore

# Set UTF-8 encoding for stdout on Windows if supported
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Run Astra benchmark evaluation suite.")
    parser.add_argument("--mock", action="store_true", default=False, help="Run mock benchmark trials")
    parser.add_argument("--output", default="evaluation/evaluation_report.md", help="Report output file")
    args = parser.parse_args()

    store = EvaluationStore()
    runner = EvaluationRunner(store=store)

    print("Running benchmark suite...")
    runs = runner.run_all_benchmarks(mock=args.mock)
    print(f"Completed {len(runs)} benchmark runs.")

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
