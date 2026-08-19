"""Hook controller for managing Astra hook registration in evaluation workspaces."""

import json
import os
import sys
from pathlib import Path
from typing import Optional
import structlog

from astra.evaluation.models import EvaluationCondition

logger = structlog.get_logger(__name__)


class HookController:
    """Controls Astra hook registration in trial workspaces per Evaluation Architecture."""

    @staticmethod
    def enable(
        workspace_path: str,
        backend_url: str = "http://127.0.0.1:8080/event",
        auth_token: str = "astra-dev-secret-token-change-in-prod",
    ) -> bool:
        """Installs Astra PostToolUse and Stop hooks into the target workspace."""
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        hooks_dir = repo_root / "hooks"
        target_dir = Path(workspace_path) / ".agents"
        target_dir.mkdir(parents=True, exist_ok=True)

        python_exe = sys.executable or "python"
        post_tool_script = (hooks_dir / "post_tool_use.py").as_posix()
        stop_script = (hooks_dir / "stop.py").as_posix()

        hooks_config = {
            "hooks": {
                "PostToolUse": {
                    "command": python_exe,
                    "args": [post_tool_script],
                },
                "Stop": {
                    "command": python_exe,
                    "args": [stop_script],
                },
            }
        }

        try:
            config_path = target_dir / "hooks.json"
            config_path.write_text(json.dumps(hooks_config, indent=2), encoding="utf-8")
            logger.info("astra_hooks_enabled", workspace=workspace_path, config=str(config_path))
            return True
        except Exception as exc:
            logger.error("failed_to_enable_hooks", workspace=workspace_path, error=str(exc))
            return False

    @staticmethod
    def disable(workspace_path: str) -> bool:
        """Removes Astra hooks from the workspace (ensures BASELINE condition)."""
        target_dir = Path(workspace_path) / ".agents"
        config_path = target_dir / "hooks.json"

        try:
            if config_path.exists():
                config_path.unlink()
                logger.info("astra_hooks_disabled", workspace=workspace_path)
            return True
        except Exception as exc:
            logger.error("failed_to_disable_hooks", workspace=workspace_path, error=str(exc))
            return False

    @staticmethod
    def validate(workspace_path: str, expected_condition: EvaluationCondition) -> bool:
        """Validates that the workspace's hook configuration matches the expected condition."""
        config_path = Path(workspace_path) / ".agents" / "hooks.json"
        has_hooks = config_path.exists()

        if expected_condition == EvaluationCondition.WITH_ASTRA:
            if not has_hooks:
                logger.error("hook_validation_failed_expected_hooks", workspace=workspace_path)
                return False
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                return "PostToolUse" in data.get("hooks", {}) and "Stop" in data.get("hooks", {})
            except Exception:
                return False
        elif expected_condition == EvaluationCondition.BASELINE:
            if has_hooks:
                logger.error("hook_validation_failed_expected_no_hooks", workspace=workspace_path)
                return False
            return True

        return True
