"""Live Antigravity CLI (agy) task execution bridge for the Astra Challenge Set.

Spawns real `agy` sessions in isolated task workspaces under Baseline (hooks absent)
and With-Astra (hooks active) conditions, running oracle test commands to grade completion.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
import structlog

from astra.evaluation.metrics import calculate_transcript_metrics
from astra.evaluation.models import (
    EvaluationCondition,
    RunRecord,
    SecondaryMetrics,
    TaskSpec,
    TrialOutcome,
)

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"


class AntigravityLiveRunner:
    """Manages isolated workspaces and executes agy CLI runs."""

    def __init__(self, base_workspaces_dir: Optional[Path] = None):
        self.workspaces_dir = base_workspaces_dir or (REPO_ROOT / "evaluation" / "workspaces")
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def prepare_workspace(self, task: TaskSpec, condition: EvaluationCondition) -> Path:
        """Creates a fresh, isolated workspace for the task trial."""
        workspace_name = f"{task.task_id}_{condition.value}_{int(time.time())}"
        workspace_path = self.workspaces_dir / workspace_name
        if workspace_path.exists():
            shutil.rmtree(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)

        # 1. Copy seed files if available
        seed_path = REPO_ROOT / task.workspace_seed_ref
        if seed_path.exists() and seed_path.is_dir():
            shutil.copytree(seed_path, workspace_path, dirs_exist_ok=True)

        # 2. Configure hooks based on evaluation condition
        agents_dir = workspace_path / ".agents"
        if condition == EvaluationCondition.WITH_ASTRA:
            agents_dir.mkdir(parents=True, exist_ok=True)
            python_bin = sys.executable or "python"
            hooks_config = {
                "hooks": {
                    "PostToolUse": {
                        "command": python_bin,
                        "args": [str(HOOKS_DIR / "post_tool_use.py").replace("\\", "/")],
                    },
                    "Stop": {
                        "command": python_bin,
                        "args": [str(HOOKS_DIR / "stop.py").replace("\\", "/")],
                    },
                }
            }
            (agents_dir / "hooks.json").write_text(json.dumps(hooks_config, indent=2), encoding="utf-8")
        else:
            # Baseline: Guarantee NO hooks.json is present
            if agents_dir.exists():
                shutil.rmtree(agents_dir)

        return workspace_path

    def run_oracle_verification(self, workspace_path: Path, oracle_cmd: str) -> Tuple[bool, str]:
        """Runs the deterministic oracle test command inside the workspace."""
        try:
            res = subprocess.run(
                oracle_cmd,
                cwd=str(workspace_path),
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            is_resolved = (res.returncode == 0)
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            return is_resolved, output
        except Exception as exc:
            return False, f"Oracle execution failed: {exc}"

    def execute_live_trial(
        self,
        task: TaskSpec,
        condition: EvaluationCondition,
        timeout_seconds: int = 300,
    ) -> RunRecord:
        """Executes a live Antigravity CLI trial on the given task."""
        workspace = self.prepare_workspace(task, condition)
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        start_time = int(time.time() * 1000)

        logger.info(
            "executing_live_agy_trial",
            task_id=task.task_id,
            condition=condition.value,
            workspace=str(workspace),
        )

        # Find agy executable
        agy_cmd = shutil.which("agy") or "agy"

        # Execute agy CLI non-interactively with YOLO auto-accept mode
        cmd = [
            agy_cmd,
            "--prompt", task.prompt,
            "--yolo",
            "--model", "gemini-3.7-flash",
        ]

        env = os.environ.copy()
        env["ASTRA_ENDPOINT_URL"] = "http://127.0.0.1:8080/event"

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workspace),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            agy_exit = proc.returncode
        except subprocess.TimeoutExpired:
            logger.warning("agy_execution_timed_out", task_id=task.task_id, timeout=timeout_seconds)
            agy_exit = -1
        except Exception as exc:
            logger.error("agy_execution_error", task_id=task.task_id, error=str(exc))
            agy_exit = -1

        finished_time = int(time.time() * 1000)
        wall_time_s = (finished_time - start_time) / 1000.0

        # Run oracle test command in workspace
        is_resolved, oracle_output = self.run_oracle_verification(workspace, task.oracle_command)
        outcome = TrialOutcome.RESOLVED if is_resolved else TrialOutcome.UNRESOLVED

        # Parse transcript to extract exact turn count
        transcript_files = list(workspace.glob("**/.system_generated/logs/transcript.jsonl"))
        turns_to_fix = None
        failed_vers = 0

        if transcript_files and transcript_files[0].exists():
            try:
                metrics = calculate_transcript_metrics(
                    transcript_path=str(transcript_files[0]),
                    verification_command=task.oracle_command,
                )
                turns_to_fix = metrics.get("turns_to_fix")
                failed_vers = metrics.get("failed_verification_attempts", 0)
            except Exception as exc:
                logger.warning("transcript_metrics_parse_fallback", error=str(exc))

        # If transcript not found or calculation None, estimate from trial outcome
        if turns_to_fix is None:
            turns_to_fix = int(max(1, wall_time_s // 15)) if is_resolved else None

        record = RunRecord(
            run_id=run_id,
            task_id=task.task_id,
            condition=condition,
            difficulty=task.difficulty,
            source=task.source,
            antigravity_version="agy-v1",
            main_agent_model_version="gemini-3.7-flash",
            started_at=start_time,
            finished_at=finished_time,
            turns_to_fix=turns_to_fix,
            outcome=outcome,
            secondary_metrics=SecondaryMetrics(
                failed_verification_attempts=failed_vers,
                time_to_fix_seconds=wall_time_s,
                astra_interventions=1 if condition == EvaluationCondition.WITH_ASTRA else 0,
            ),
        )

        logger.info(
            "live_trial_complete",
            task_id=task.task_id,
            condition=condition.value,
            outcome=outcome.value,
            turns=turns_to_fix,
            time_s=wall_time_s,
        )

        return record
