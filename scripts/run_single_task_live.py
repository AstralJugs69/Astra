"""Interactive live runner for executing Antigravity CLI (agy) on a benchmark task.

Streams agy tool calls, thoughts, and output directly to the console in real time.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from astra.evaluation.live_runner import AntigravityLiveRunner, REPO_ROOT
from astra.evaluation.models import EvaluationCondition
from astra.evaluation.tasks.registry import get_task_spec, BENCHMARK_TASKS
from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")


def main():
    parser = argparse.ArgumentParser(description="Run a single benchmark task live in your terminal with real-time streaming agy output.")
    parser.add_argument("task_id", choices=list(BENCHMARK_TASKS.keys()), help="Benchmark task ID to run")
    parser.add_argument("--with-astra", action="store_true", default=False, help="Enable Astra companion hooks (default is Baseline / Astra OFF)")
    args = parser.parse_args()

    task = get_task_spec(args.task_id)
    condition = EvaluationCondition.WITH_ASTRA if args.with_astra else EvaluationCondition.BASELINE

    runner = AntigravityLiveRunner()
    workspace = runner.prepare_workspace(task, condition)

    print("=" * 80)
    print(f"🎯 LAUNCHING LIVE BENCHMARK TRIAL")
    print(f"   Task ID:    {task.task_id} ({task.difficulty.value})")
    print(f"   Source:     {task.source.value}")
    print(f"   Condition:  {'🟢 WITH-ASTRA (Hooks Active)' if args.with_astra else '⚪ BASELINE (Astra OFF)'}")
    print(f"   Workspace:  {workspace}")
    print(f"   Prompt:     {task.prompt}")
    print("=" * 80)
    print("Streaming Antigravity CLI (agy) output below...\n")

    agy_cmd = shutil.which("agy") or "agy"
    cmd = [
        agy_cmd,
        "--prompt", task.prompt,
        "--dangerously-skip-permissions",
    ]

    env = os.environ.copy()
    if args.with_astra:
        env["ASTRA_ENDPOINT_URL"] = "http://127.0.0.1:8080/event"

    start_time = time.time()
    
    # Run agy directly connected to terminal stdout/stderr for full streaming visibility
    proc = subprocess.run(
        cmd,
        cwd=str(workspace),
        env=env,
    )

    elapsed_s = time.time() - start_time
    print("\n" + "=" * 80)
    print(f"🏁 Antigravity CLI finished in {elapsed_s:.1f}s with exit code {proc.returncode}")
    print("=" * 80)

    print(f"\n🔍 Running Deterministic Oracle Grader: `{task.oracle_command}` ...")
    is_resolved, oracle_out = runner.run_oracle_verification(workspace, task.oracle_command)

    print(oracle_out)
    print("-" * 80)
    if is_resolved:
        print(f"🎉 TASK OUTCOME: RESOLVED (Oracle passed with exit code 0)")
    else:
        print(f"❌ TASK OUTCOME: UNRESOLVED (Oracle failed)")
    print("=" * 80)


if __name__ == "__main__":
    main()
