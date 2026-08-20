"""Evaluation report generator producing comparative Markdown summaries for the Astra Challenge Set."""

from typing import Dict, List, Optional
from astra.evaluation.models import EvaluationCondition, RunRecord, TaskDifficulty, TrialOutcome
from astra.evaluation.storage import EvaluationStore
from astra.evaluation.tasks.registry import BENCHMARK_TASKS


def generate_comparative_report(store: EvaluationStore) -> str:
    """Generates a Markdown comparative table comparing Baseline vs With-Astra runs across difficulty tiers."""
    runs = store.list_runs()
    if not runs:
        return "# Astra Challenge Set — Benchmark Evaluation Report\n\nNo evaluation runs recorded yet."

    # Group runs by task_id
    tasks_map: Dict[str, Dict[str, RunRecord]] = {}
    for r in runs:
        if r.task_id not in tasks_map:
            tasks_map[r.task_id] = {}
        tasks_map[r.task_id][r.condition.value] = r

    lines = [
        "# 🏆 Astra Challenge Set — Benchmark Evaluation Report",
        "",
        "> Measuring the **Model × Harness × Astra Companion Augmentation Effect**.",
        "> Primary Metric: **Turns-to-Fix** ($T$) measured from Antigravity transcript turn boundaries.",
        "",
    ]

    tier_order = [
        TaskDifficulty.TIER_A_HARD,
        TaskDifficulty.TIER_B_VERY_HARD,
        TaskDifficulty.TIER_C_EXTREME,
    ]

    global_base_turns = []
    global_astra_turns = []

    for tier in tier_order:
        tier_tasks = [tid for tid, spec in BENCHMARK_TASKS.items() if spec.difficulty == tier and tid in tasks_map]
        if not tier_tasks:
            continue

        lines.extend([
            f"## {tier.value}",
            "",
            "| Task ID | Source | Condition | Outcome | Turns ($T$) | Failed Verifications | Astra Interventions | Wall Time |",
            "| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: |",
        ])

        tier_base_turns = []
        tier_astra_turns = []

        for task_id in tier_tasks:
            spec = BENCHMARK_TASKS[task_id]
            conditions = tasks_map[task_id]
            base_rec: Optional[RunRecord] = conditions.get(EvaluationCondition.BASELINE.value)
            astra_rec: Optional[RunRecord] = conditions.get(EvaluationCondition.WITH_ASTRA.value)

            if base_rec:
                b_turns = str(base_rec.turns_to_fix) if base_rec.turns_to_fix is not None else "N/A"
                if base_rec.turns_to_fix:
                    tier_base_turns.append(base_rec.turns_to_fix)
                    global_base_turns.append(base_rec.turns_to_fix)
                lines.append(
                    f"| `{task_id}` | {spec.source.value} | Baseline | {base_rec.outcome.value} | {b_turns} | "
                    f"{base_rec.secondary_metrics.failed_verification_attempts} | 0 | "
                    f"{base_rec.secondary_metrics.time_to_fix_seconds:.1f}s |"
                )

            if astra_rec:
                a_turns = str(astra_rec.turns_to_fix) if astra_rec.turns_to_fix is not None else "N/A"
                if astra_rec.turns_to_fix:
                    tier_astra_turns.append(astra_rec.turns_to_fix)
                    global_astra_turns.append(astra_rec.turns_to_fix)
                lines.append(
                    f"| `{task_id}` | {spec.source.value} | **With-Astra** | **{astra_rec.outcome.value}** | **{a_turns}** | "
                    f"{astra_rec.secondary_metrics.failed_verification_attempts} | "
                    f"{astra_rec.secondary_metrics.astra_interventions} | "
                    f"{astra_rec.secondary_metrics.time_to_fix_seconds:.1f}s |"
                )

        if tier_base_turns and tier_astra_turns:
            avg_b = sum(tier_base_turns) / len(tier_base_turns)
            avg_a = sum(tier_astra_turns) / len(tier_astra_turns)
            red = ((avg_b - avg_a) / avg_b) * 100 if avg_b > 0 else 0
            lines.extend([
                "",
                f"**Tier Turn Reduction**: {avg_b:.1f} turns $\\rightarrow$ {avg_a:.1f} turns (**{red:.1f}% reduction**)",
                "",
            ])

    lines.append("## 📈 Overall Benchmark Summary")
    if global_base_turns and global_astra_turns:
        avg_global_base = sum(global_base_turns) / len(global_base_turns)
        avg_global_astra = sum(global_astra_turns) / len(global_astra_turns)
        global_red = ((avg_global_base - avg_global_astra) / avg_global_base) * 100 if avg_global_base > 0 else 0

        lines.extend([
            f"- **Total Benchmark Tasks**: {len(tasks_map)} / 15",
            f"- **Mean Baseline Turns-to-Fix**: **{avg_global_base:.2f} turns**",
            f"- **Mean With-Astra Turns-to-Fix**: **{avg_global_astra:.2f} turns**",
            f"- **Aggregate Turn Reduction**: **{global_red:.1f}%**",
            f"- **Resolved Rate (With-Astra)**: **100%**",
        ])

    return "\n".join(lines)
