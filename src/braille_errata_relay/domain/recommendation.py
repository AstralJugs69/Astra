"""Deterministic recommendations; values are instructions for people, not commands."""

from __future__ import annotations

from enum import StrEnum

from .models import BrailleImpact, BlockingReason, JobState, SemanticAssessment


class HumanStep(StrEnum):
    COORDINATOR_REVIEW = "COORDINATOR_REVIEW"
    QUALIFIED_PROOF_REQUIRED = "QUALIFIED_PROOF_REQUIRED"
    CONSIDER_OPERATOR_STOP_AND_ISOLATION = "CONSIDER_OPERATOR_STOP_AND_ISOLATION"
    INSPECT_POTENTIALLY_STALE_OUTPUT = "INSPECT_POTENTIALLY_STALE_OUTPUT"
    FULL_VOLUME_REPLACEMENT_REVIEW = "FULL_VOLUME_REPLACEMENT_REVIEW"
    CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE = "CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE"


def recommend_human_steps(
    *,
    assessment: SemanticAssessment,
    impact: BrailleImpact,
    job_state: JobState | None,
    blocking_reason: BlockingReason | None = None,
) -> tuple[HumanStep, ...]:
    steps: list[HumanStep] = [HumanStep.COORDINATOR_REVIEW]
    if blocking_reason is not None or assessment.requires_professional_review:
        steps.append(HumanStep.QUALIFIED_PROOF_REQUIRED)
    if not impact.pages_changed:
        steps.append(HumanStep.CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE)
        return tuple(dict.fromkeys(steps))
    if impact.baseline_page_count != impact.candidate_page_count:
        steps.append(HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW)
    elif job_state in {JobState.PENDING, JobState.PENDING_HELD, JobState.PROCESSING}:
        steps.append(HumanStep.CONSIDER_OPERATOR_STOP_AND_ISOLATION)
    elif job_state == JobState.COMPLETED:
        steps.append(HumanStep.INSPECT_POTENTIALLY_STALE_OUTPUT)
    else:
        steps.append(HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW)
    return tuple(dict.fromkeys(steps))

