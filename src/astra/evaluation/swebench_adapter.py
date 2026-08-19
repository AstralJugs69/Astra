"""SWE-bench task and evaluation adapter per Evaluation Architecture."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel
import structlog

from astra.evaluation.models import TaskCategory, TaskSpec

logger = structlog.get_logger(__name__)


class SwebenchInstance(BaseModel):
    """Metadata for a SWE-bench Lite instance."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    version: str = ""
    environment_setup_commit: str = ""


class SwebenchAdapter:
    """Translates SWE-bench Lite instances into benchmark-neutral TaskSpecs."""

    @staticmethod
    def to_task_spec(instance: SwebenchInstance) -> TaskSpec:
        """Converts a SWE-bench instance into a TaskSpec."""
        verification_cmd = f"pytest -q {instance.repo.replace('/', '_')}_eval.py"
        prompt = (
            f"Solve the following issue in {instance.repo}:\n\n"
            f"{instance.problem_statement}\n\n"
            f"Ensure all changes are verified with passing tests before terminating."
        )

        return TaskSpec(
            task_id=f"swebench-{instance.instance_id}",
            category=TaskCategory.SWEBENCH,
            workspace_seed_ref=f"swebench_{instance.instance_id}",
            prompt=prompt,
            verification_command=verification_cmd,
            max_turns=20,
            instance_id=instance.instance_id,
            repo=instance.repo,
            base_commit=instance.base_commit,
        )
