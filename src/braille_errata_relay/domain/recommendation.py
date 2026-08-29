"""Deterministic recommendations; values are instructions for people, not commands."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from braille_errata_relay.configuration import resolve_config_path

from .models import (
    BlockingReason,
    BrailleImpact,
    Confidence,
    JobState,
    Materiality,
    SemanticAssessment,
    SiteObservation,
)


class HumanStep(StrEnum):
    COORDINATOR_REVIEW = "COORDINATOR_REVIEW"
    QUALIFIED_PROOF_REQUIRED = "QUALIFIED_PROOF_REQUIRED"
    CONSIDER_OPERATOR_STOP_AND_ISOLATION = "CONSIDER_OPERATOR_STOP_AND_ISOLATION"
    INSPECT_POTENTIALLY_STALE_OUTPUT = "INSPECT_POTENTIALLY_STALE_OUTPUT"
    FULL_VOLUME_REPLACEMENT_REVIEW = "FULL_VOLUME_REPLACEMENT_REVIEW"
    CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE = "CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE"


class SiteEvidenceStatus(StrEnum):
    FRESH = "FRESH"
    MISSING = "MISSING"
    STALE = "STALE"
    AMBIGUOUS = "AMBIGUOUS"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True)
class SiteEvidenceCheck:
    status: SiteEvidenceStatus
    blocking_reason: BlockingReason | None
    job_state: JobState | None


@dataclass(frozen=True)
class ContainmentRecommendation:
    steps: tuple[HumanStep, ...]
    precise_containment: bool
    blocking_reason: BlockingReason | None


@dataclass(frozen=True)
class RecommendationPolicy:
    """The fail-closed semantic threshold from the versioned policy file."""

    policy_id: str
    min_semantic_confidence: Confidence


def _default_recommendation_policy_path() -> Path:
    return resolve_config_path(
        direct_env=_POLICY_PATH_ENV,
        relative_path="policies/recommendation.v1.json",
    )


def load_recommendation_policy(path: str | Path | None = None) -> RecommendationPolicy:
    """Load the policy that controls deterministic semantic fail-closed behavior.

    A missing or malformed policy is an error rather than a weaker implicit
    default. Callers must surface that as a blocked/review state before any
    containment recommendation can be considered precise.
    """

    configured_path = path or os.environ.get(_POLICY_PATH_ENV)
    policy_path = (
        Path(configured_path) if configured_path else _default_recommendation_policy_path()
    )
    try:
        value = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("recommendation policy could not be loaded") from exc
    if not isinstance(value, dict):
        raise TypeError("recommendation policy must be a JSON object")
    if value.get("schema_version") != "recommendation-policy.v1":
        raise ValueError("recommendation policy schema version is unsupported")
    policy_id = value.get("policy_id")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("recommendation policy ID is missing")
    try:
        minimum = Confidence(value["min_semantic_confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recommendation policy confidence threshold is invalid") from exc
    return RecommendationPolicy(policy_id=policy_id, min_semantic_confidence=minimum)


_POLICY_PATH_ENV = "RELAY_RECOMMENDATION_POLICY"
_CONFIDENCE_RANK = {
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
}


def _dedupe(steps: list[HumanStep]) -> tuple[HumanStep, ...]:
    return tuple(dict.fromkeys(steps))


def semantic_review_required(
    assessment: SemanticAssessment,
    *,
    policy: RecommendationPolicy | None = None,
) -> bool:
    """Return whether model output is safe only as review context.

    The model may explain a change, but deterministic policy requires positive
    materiality, configured policy confidence, no unresolved uncertainty, and an explicit
    indication that this recommendation does not replace professional review.
    """
    active_policy = policy or load_recommendation_policy()

    return (
        assessment.materiality != Materiality.MATERIAL
        or assessment.requires_professional_review
        or _CONFIDENCE_RANK[assessment.confidence]
        < _CONFIDENCE_RANK[active_policy.min_semantic_confidence]
        or bool(assessment.uncertainties)
    )


def assess_site_evidence(
    *,
    site_observation: SiteObservation | None,
    expected_job_id: int | None,
    expected_queue_name: str | None,
    now: datetime,
    max_age_seconds: float,
) -> SiteEvidenceCheck:
    if max_age_seconds < 0:
        raise ValueError("site observation max age cannot be negative")
    if expected_job_id is None or not expected_queue_name:
        return SiteEvidenceCheck(
            SiteEvidenceStatus.MISSING,
            BlockingReason.MISSING_LINEAGE,
            None,
        )
    if site_observation is None:
        return SiteEvidenceCheck(
            SiteEvidenceStatus.MISSING,
            BlockingReason.MISSING_LINEAGE,
            None,
        )
    observed_at = site_observation.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    age = (current - observed_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        return SiteEvidenceCheck(
            SiteEvidenceStatus.STALE,
            BlockingReason.SITE_OBSERVATION_STALE,
            None,
        )
    if site_observation.queue_name != expected_queue_name:
        return SiteEvidenceCheck(
            SiteEvidenceStatus.MISSING,
            BlockingReason.MISSING_LINEAGE,
            None,
        )
    matches = [
        observation
        for observation in site_observation.observations
        if observation.scheduler_job_id == expected_job_id
    ]
    if len(matches) != 1:
        return SiteEvidenceCheck(
            SiteEvidenceStatus.AMBIGUOUS if len(matches) > 1 else SiteEvidenceStatus.MISSING,
            BlockingReason.AMBIGUOUS_SITE_EVIDENCE
            if len(matches) > 1
            else BlockingReason.MISSING_LINEAGE,
            None,
        )
    selected = matches[0]
    if selected.state == JobState.UNKNOWN or site_observation.printer_state == "unknown":
        return SiteEvidenceCheck(
            SiteEvidenceStatus.BLOCKING,
            BlockingReason.SITE_OBSERVATION_BLOCKING,
            selected.state,
        )
    return SiteEvidenceCheck(SiteEvidenceStatus.FRESH, None, selected.state)


def containment_recommendation(
    *,
    assessment: SemanticAssessment,
    impact: BrailleImpact,
    job_state: JobState | None,
    blocking_reason: BlockingReason | None = None,
    site_observation: SiteObservation | None = None,
    expected_job_id: int | None = None,
    expected_queue_name: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float = 15.0,
    policy: RecommendationPolicy | None = None,
) -> ContainmentRecommendation:
    steps: list[HumanStep] = [HumanStep.COORDINATOR_REVIEW]
    semantic_blocking = semantic_review_required(assessment, policy=policy)
    if blocking_reason is not None or semantic_blocking:
        steps.append(HumanStep.QUALIFIED_PROOF_REQUIRED)
    if not impact.pages_changed:
        steps.append(HumanStep.CONTINUE_ONLY_AFTER_HUMAN_ACCEPTANCE)
        return ContainmentRecommendation(
            _dedupe(steps),
            False,
            blocking_reason
            or (BlockingReason.SEMANTIC_REVIEW_REQUIRED if semantic_blocking else None),
        )

    evidence = assess_site_evidence(
        site_observation=site_observation,
        expected_job_id=expected_job_id,
        expected_queue_name=expected_queue_name,
        now=now or datetime.now(UTC),
        max_age_seconds=max_age_seconds,
    )
    effective_reason = (
        blocking_reason
        or (BlockingReason.SEMANTIC_REVIEW_REQUIRED if semantic_blocking else None)
        or evidence.blocking_reason
    )
    if effective_reason is not None or evidence.status != SiteEvidenceStatus.FRESH:
        steps.append(HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW)
        return ContainmentRecommendation(_dedupe(steps), False, effective_reason)

    observed_state = evidence.job_state or job_state
    if impact.baseline_page_count != impact.candidate_page_count:
        steps.append(HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW)
    elif observed_state in {JobState.PENDING, JobState.PENDING_HELD, JobState.PROCESSING}:
        steps.append(HumanStep.CONSIDER_OPERATOR_STOP_AND_ISOLATION)
    elif observed_state == JobState.COMPLETED:
        steps.append(HumanStep.INSPECT_POTENTIALLY_STALE_OUTPUT)
    else:
        steps.append(HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW)
    return ContainmentRecommendation(_dedupe(steps), True, None)


def recommend_human_steps(
    *,
    assessment: SemanticAssessment,
    impact: BrailleImpact,
    job_state: JobState | None,
    blocking_reason: BlockingReason | None = None,
    site_observation: SiteObservation | None = None,
    expected_job_id: int | None = None,
    expected_queue_name: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float = 15.0,
    policy: RecommendationPolicy | None = None,
) -> tuple[HumanStep, ...]:
    return containment_recommendation(
        assessment=assessment,
        impact=impact,
        job_state=job_state,
        blocking_reason=blocking_reason,
        site_observation=site_observation,
        expected_job_id=expected_job_id,
        expected_queue_name=expected_queue_name,
        now=now,
        max_age_seconds=max_age_seconds,
        policy=policy,
    ).steps
