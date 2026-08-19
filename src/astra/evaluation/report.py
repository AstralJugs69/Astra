"""Evaluation report generator producing comparative Markdown summaries."""

from typing import List, Optional
from astra.evaluation.models import EvaluationCondition, RunRecord, TrialOutcome
from astra.evaluation.storage import EvaluationStore


def generate_comparative_report(store: EvaluationStore) -> str:
    """Generates a Markdown comparative table comparing Baseline vs With-Astra runs."""
    runs = store.list_runs()
    if not runs:
        return "# Astra Benchmark Evaluation Report\n\nNo evaluation runs recorded yet."

    # Group runs by task_id
    tasks_map = {}
    for r in runs:
        if r.task_id not in tasks_map:
            tasks_map[r.task_id] = {}
        tasks_map[r.task_id][r.condition.value] = r

    lines = [
        "# 📊 Astra POC — Benchmark Evaluation Report",
        "",
        "> Ground truth success metric: **Turns-to-Fix** measured from Antigravity transcript turn boundaries.",
        "",
        "| Task ID | Condition | Outcome | Turns-to-Fix | Failed Verifications | Astra Interventions | Time (s) |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: |",
    ]

    baseline_turns = []
    astra_turns = []

    for task_id, conditions in tasks_map.items():
        base_rec: Optional[RunRecord] = conditions.get(EvaluationCondition.BASELINE.value)
        astra_rec: Optional[RunRecord] = conditions.get(EvaluationCondition.WITH_ASTRA.value)

        if base_rec:
            b_turns = str(base_rec.turns_to_fix) if base_rec.turns_to_fix is not None else "N/A"
            if base_rec.turns_to_fix:
                baseline_turns.append(base_rec.turns_to_fix)
            lines.append(
                f"| `{task_id}` | Baseline | {base_rec.outcome.value} | {b_turns} | "
                f"{base_rec.secondary_metrics.failed_verification_attempts} | 0 | "
                f"{base_rec.secondary_metrics.time_to_fix_seconds:.1f} |"
            )

        if astra_rec:
            a_turns = str(astra_rec.turns_to_fix) if astra_rec.turns_to_fix is not None else "N/A"
            if astra_rec.turns_to_fix:
                astra_turns.append(astra_rec.turns_to_fix)
            lines.append(
                f"| `{task_id}` | **With-Astra** | **{astra_rec.outcome.value}** | **{a_turns}** | "
                f"{astra_rec.secondary_metrics.failed_verification_attempts} | "
                f"{astra_rec.secondary_metrics.astra_interventions} | "
                f"{astra_rec.secondary_metrics.time_to_fix_seconds:.1f} |"
            )

    lines.append("")
    if baseline_turns and astra_turns:
        avg_base = sum(baseline_turns) / len(baseline_turns)
        avg_astra = sum(astra_turns) / len(astra_turns)
        reduction = ((avg_base - avg_astra) / avg_base) * 100 if avg_base > 0 else 0
        lines.extend([
            "### Summary Statistics",
            f"- **Mean Baseline Turns-to-Fix**: {avg_base:.2f} turns",
            f"- **Mean With-Astra Turns-to-Fix**: {avg_astra:.2f} turns",
            f"- **Turn Reduction / Efficiency Gain**: **{reduction:.1f}%**",
        ])

    return "\n".join(lines)
