from __future__ import annotations

from datetime import UTC, datetime

import pytest

from braille_errata_relay.domain.models import (
    BrailleImpact,
    ChangeKind,
    Confidence,
    Incident,
    IncidentState,
    JobState,
    Materiality,
    SemanticAssessment,
)
from braille_errata_relay.domain.recommendation import HumanStep, recommend_human_steps
from braille_errata_relay.domain.state_machine import (
    IllegalStateTransition,
    StaleStateVersion,
    require_report_precedes_action,
    transition,
)


def assessment(review: bool = False) -> SemanticAssessment:
    return SemanticAssessment(
        assessment_id="a" * 64,
        analysis_revision=1,
        model_id="test-model",
        prompt_version="semantic-assessment.v1",
        materiality=Materiality.MATERIAL,
        change_kind=ChangeKind.FACTUAL_CORRECTION,
        summary="A fact changed.",
        rationale=("The changed term has a different referent.",),
        evidence_span_ids=("old:block-000002", "new:block-000002"),
        confidence=Confidence.HIGH,
        requires_professional_review=review,
    )


def impact(changed: bool = True, page_count: int = 2) -> BrailleImpact:
    return BrailleImpact(
        baseline_artifact_sha256="a" * 64,
        candidate_artifact_sha256="b" * 64,
        old_page_range=None,
        new_page_range=None,
        candidate_page_count=page_count,
        baseline_page_count=page_count,
        pages_changed=changed,
    )


def test_report_ready_requires_human_disposition_and_version_match() -> None:
    incident = Incident(
        incident_id="a" * 64,
        baseline_id="b" * 64,
        state=IncidentState.REPORT_READY,
        state_version=3,
    )
    with pytest.raises(StaleStateVersion):
        transition(incident, IncidentState.HALT_REQUESTED, expected_state_version=2)
    updated = transition(incident, IncidentState.HALT_REQUESTED, expected_state_version=3)
    assert updated.state == IncidentState.HALT_REQUESTED
    assert updated.state_version == 4
    with pytest.raises(IllegalStateTransition):
        transition(updated, IncidentState.RESOLVED_BY_HUMAN, expected_state_version=4)


def test_report_timestamp_precedes_attributed_action() -> None:
    ready_at = datetime(2026, 8, 28, 17, 0, tzinfo=UTC)
    require_report_precedes_action(ready_at, datetime(2026, 8, 28, 17, 1, tzinfo=UTC))
    with pytest.raises(IllegalStateTransition):
        require_report_precedes_action(ready_at, ready_at)


def test_recommendation_never_returns_a_device_command() -> None:
    steps = recommend_human_steps(
        assessment=assessment(), impact=impact(), job_state=JobState.PROCESSING
    )
    assert steps == (
        HumanStep.COORDINATOR_REVIEW,
        HumanStep.FULL_VOLUME_REPLACEMENT_REVIEW,
    )
    assert not any("CANCEL" in step.value or "SUBMIT" in step.value for step in steps)
