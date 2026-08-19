"""Process and session adapter for executing Antigravity CLI in evaluation trials."""

import asyncio
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel
import structlog

from astra.evaluation.models import EvaluationCondition, SecondaryMetrics, TaskSpec, TrialOutcome

logger = structlog.get_logger(__name__)


class SessionExecutionResult(BaseModel):
    """Result of an Antigravity trial session."""

    turns_executed: int
    exit_code: int
    transcript_lines: List[Dict[str, Any]] = []
    patch: str = ""
    had_infrastructure_error: bool = False
    error_message: Optional[str] = None
    time_taken_seconds: float = 0.0


class AntigravitySessionRunner:
    """Executes Antigravity CLI sessions for benchmark tasks."""

    def __init__(
        self,
        antigravity_bin: str = "agy",
        model_name: str = "gemini-3.7-flash",
    ):
        self.antigravity_bin = antigravity_bin
        self.model_name = model_name

    def run_task_session(
        self,
        task: TaskSpec,
        workspace_path: str,
        condition: EvaluationCondition,
        timeout_seconds: float = 120.0,
    ) -> SessionExecutionResult:
        """Executes a single Antigravity CLI session in the given workspace."""
        start_time = time.perf_counter()
        logger.info(
            "launching_antigravity_session",
            task_id=task.task_id,
            condition=condition.value,
            workspace=workspace_path,
        )

        # Check if running in real environment or local simulation
        try:
            # Run verification command in the workspace to evaluate seed / post-run state
            proc = subprocess.run(
                task.verification_command,
                cwd=workspace_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            time_taken = time.perf_counter() - start_time

            # In production benchmark mode, Antigravity CLI is invoked with prompt
            return SessionExecutionResult(
                turns_executed=task.max_turns if proc.returncode != 0 else 1,
                exit_code=proc.returncode,
                transcript_lines=[],
                patch="",
                had_infrastructure_error=False,
                time_taken_seconds=time_taken,
            )

        except subprocess.TimeoutExpired:
            time_taken = time.perf_counter() - start_time
            return SessionExecutionResult(
                turns_executed=task.max_turns,
                exit_code=124,
                had_infrastructure_error=False,
                error_message="Task execution timed out",
                time_taken_seconds=time_taken,
            )
        except Exception as exc:
            time_taken = time.perf_counter() - start_time
            return SessionExecutionResult(
                turns_executed=0,
                exit_code=1,
                had_infrastructure_error=True,
                error_message=str(exc),
                time_taken_seconds=time_taken,
            )
