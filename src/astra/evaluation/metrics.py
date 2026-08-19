"""Turns-to-Fix metric computation from Antigravity transcripts per Section 31.4."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astra.evaluation.models import SecondaryMetrics, TrialOutcome


def parse_transcript_file(transcript_path: Path) -> List[Dict[str, Any]]:
    """Parses an Antigravity transcript .jsonl file into a list of step dicts."""
    if not transcript_path.exists():
        return []

    steps: List[Dict[str, Any]] = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                steps.append(json.loads(line))
            except Exception:
                pass
    return steps


def calculate_transcript_metrics(
    steps: List[Dict[str, Any]],
    verification_command: str,
    max_turns: int = 15,
) -> Tuple[Optional[int], TrialOutcome, SecondaryMetrics]:
    """Calculates ground-truth turns-to-fix and secondary metrics from Antigravity transcript steps."""
    if not steps:
        return None, TrialOutcome.INVALID, SecondaryMetrics()

    failed_verifications = 0
    turns_count = 0
    first_passing_turn: Optional[int] = None
    cmd_lower = verification_command.lower().strip()

    for idx, step in enumerate(steps):
        step_type = step.get("type", "")
        # Track turn boundary
        if step_type in ["USER_INPUT", "PLANNER_RESPONSE"]:
            turns_count += 1

        # Inspect tool calls and results
        tool_calls = step.get("tool_calls", [])
        content = str(step.get("content", ""))

        is_ver_call = False
        for tc in tool_calls:
            args = tc.get("args", {})
            cmd_run = str(args.get("CommandLine", "")).lower()
            if cmd_lower in cmd_run or (cmd_run and cmd_run in cmd_lower):
                is_ver_call = True
                break

        if not is_ver_call and cmd_lower in content.lower():
            is_ver_call = True

        if is_ver_call:
            # Check exit status
            has_error = bool(step.get("status") == "ERROR" or "exit code 1" in content or "FAILED" in content)
            if has_error:
                failed_verifications += 1
                # If it previously passed and now failed, reset first_passing_turn
                first_passing_turn = None
            else:
                if first_passing_turn is None:
                    first_passing_turn = turns_count

    secondary = SecondaryMetrics(
        failed_verification_attempts=failed_verifications,
    )

    if first_passing_turn is not None:
        return first_passing_turn, TrialOutcome.RESOLVED, secondary

    if turns_count >= max_turns:
        return None, TrialOutcome.UNRESOLVED, secondary

    return None, TrialOutcome.UNRESOLVED, secondary
