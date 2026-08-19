"""Pristine workspace lifecycle manager for isolated evaluation trials."""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional
import structlog

from astra.evaluation.models import EvaluationCondition

logger = structlog.get_logger(__name__)


class WorkspaceManager:
    """Manages pristine temporary workspaces for evaluation runs."""

    @staticmethod
    def create_isolated_workspace(
        seed_path: str,
        task_id: str,
        condition: EvaluationCondition,
        base_dir: Optional[str] = None,
    ) -> str:
        """Creates a fresh, isolated workspace from a task seed directory."""
        unique_id = uuid.uuid4().hex[:8]
        prefix = f"astra_eval_{task_id}_{condition.value}_{unique_id}_"
        target_dir = tempfile.mkdtemp(prefix=prefix, dir=base_dir)

        if os.path.exists(seed_path):
            # Copy all files from seed to isolated workspace
            shutil.copytree(seed_path, target_dir, dirs_exist_ok=True)
            logger.info("workspace_created_from_seed", target_dir=target_dir, seed_path=seed_path)
        else:
            # Create minimal scaffold if seed path does not exist on disk
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            logger.info("workspace_created_fresh", target_dir=target_dir)

        return target_dir

    @staticmethod
    def extract_workspace_diff(workspace_path: str) -> str:
        """Extracts git diff or summary of modified files from the workspace."""
        if not os.path.exists(workspace_path):
            return ""

        try:
            # Check if git repository
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
        except Exception:
            pass

        return ""

    @staticmethod
    def cleanup_isolated_workspace(workspace_path: str) -> None:
        """Safely removes temporary workspace."""
        if os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path, ignore_errors=True)
                logger.info("workspace_cleaned_up", workspace_path=workspace_path)
            except Exception as exc:
                logger.warning("workspace_cleanup_failed", workspace_path=workspace_path, error=str(exc))
