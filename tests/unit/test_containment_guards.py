from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from braille_errata_relay.domain.models import (
    BrailleImpact,
    ChangeKind,
    Confidence,
    Incident,
    IncidentState,
    JobState,
    Materiality,
    QueueObservation,
    SemanticAssessment,
    SiteObservation,
)
from braille_errata_relay.domain.recommendation import (
    HumanStep,
    SiteEvidenceStatus,
    assess_site_evidence,
    containment_recommendation,
)
from braille_errata_relay.domain.state_machine import (
    AttributableEvidenceRequired,
    IllegalStateTransition,
    transition,
)


def assessment() -> SemanticAssessment:
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
    )


def impact() -> BrailleImpact:
    return BrailleImpact(
        baseline_artifact_sha256="a" * 64,
        candidate_artifact_sha256="b" * 64,
        old_page_range=None,
        new_page_range=None,
        candidate_page_count=2,
        baseline_page_count=2,
        pages_changed=True,
    )


def observation(*, observed_at: datetime, state: JobState = JobState.PROCESSING) -> SiteObservation:
    job = QueueObservation(
        scheduler_job_id=42,
        owner="operator",
        title="candidate",
        destination="queue",
        state=state,
        observed_at=observed_at,
    )
    return SiteObservation(
        observation_id="c" * 64,
        site_id="site",
        bridge_id="bridge",
        queue_name="queue",
        sequence=1,
        observed_at=observed_at,
        observations=(job,),
        printer_state="processing",
        printer_accepting_jobs=True,
    )


def test_uncertain_containment_cannot_jump_to_proof_or_close() -> None:
    incident = Incident(
        incident_id="a" * 64,
        baseline_id="b" * 64,
        state=IncidentState.CONTAINMENT_UNCERTAIN,
        state_version=4,
    )
    with pytest.raises(IllegalStateTransition):
        transition(incident, IncidentState.AWAITING_PROOF, expected_state_version=4)
    reviewed = transition(incident, IncidentState.NEEDS_REVIEW, expected_state_version=4)
    with pytest.raises(AttributableEvidenceRequired):
        transition(reviewed, IncidentState.ASSESSING, expected_state_version=5)
    recovered = transition(
        reviewed,
        IncidentState.ASSESSING,
        expected_state_version=5,
        evidence_id="site-observation:5",
    )
    assert recovered.last_attributable_evidence_id == "site-observation:5"


def test_deferred_and_rejected_states_have_evidence_gated_recovery() -> None:
    for state in (IncidentState.DEFERRED, IncidentState.REPORT_REJECTED):
        incident = Incident(
            incident_id="a" * 64,
            baseline_id="b" * 64,
            state=state,
            state_version=1,
        )
        with pytest.raises(AttributableEvidenceRequired):
            transition(incident, IncidentState.ASSESSING, expected_state_version=1)
        assert (
            transition(
                incident,
                IncidentState.ASSESSING,
                expected_state_version=1,
                evidence_id="review:1",
            ).state
            == IncidentState.ASSESSING
        )


def test_stale_missing_ambiguous_and_blocking_evidence_never_becomes_precise() -> None:
    now = datetime(2026, 8, 28, 17, 0, tzinfo=UTC)
    stale = assess_site_evidence(
        site_observation=observation(observed_at=now - timedelta(seconds=16)),
        expected_job_id=42,
        expected_queue_name="queue",
        now=now,
        max_age_seconds=15,
    )
    missing = assess_site_evidence(
        site_observation=None,
        expected_job_id=42,
        expected_queue_name="queue",
        now=now,
        max_age_seconds=15,
    )
    blocking = assess_site_evidence(
        site_observation=observation(observed_at=now, state=JobState.UNKNOWN),
        expected_job_id=42,
        expected_queue_name="queue",
        now=now,
        max_age_seconds=15,
    )
    ambiguous_observation = observation(observed_at=now).model_copy(
        update={
            "observations": (
                observation(observed_at=now).observations[0],
                observation(observed_at=now)
                .observations[0]
                .model_copy(update={"scheduler_job_id": 43}),
            )
        }
    )
    ambiguous = assess_site_evidence(
        site_observation=ambiguous_observation,
        expected_job_id=None,
        expected_queue_name="queue",
        now=now,
        max_age_seconds=15,
    )
    assert {stale.status, missing.status, blocking.status, ambiguous.status} == {
        SiteEvidenceStatus.STALE,
        SiteEvidenceStatus.MISSING,
        SiteEvidenceStatus.BLOCKING,
        SiteEvidenceStatus.AMBIGUOUS,
    }
    for evidence in (None, stale, missing, blocking, ambiguous):
        site = (
            None
            if evidence is None
            else (
                observation(observed_at=now - timedelta(seconds=16))
                if evidence is stale
                else observation(observed_at=now, state=JobState.UNKNOWN)
                if evidence is blocking
                else ambiguous_observation
                if evidence is ambiguous
                else None
            )
        )
        result = containment_recommendation(
            assessment=assessment(),
            impact=impact(),
            job_state=JobState.PROCESSING,
            site_observation=site,
            expected_job_id=42 if evidence is not ambiguous else None,
            expected_queue_name="queue",
            now=now,
            max_age_seconds=15,
        )
        assert result.precise_containment is False
        assert HumanStep.CONSIDER_OPERATOR_STOP_AND_ISOLATION not in result.steps


def test_fresh_attributable_evidence_can_describe_a_human_step() -> None:
    now = datetime(2026, 8, 28, 17, 0, tzinfo=UTC)
    result = containment_recommendation(
        assessment=assessment(),
        impact=impact(),
        job_state=JobState.PROCESSING,
        site_observation=observation(observed_at=now),
        expected_job_id=42,
        expected_queue_name="queue",
        now=now,
        max_age_seconds=15,
    )
    assert result.precise_containment is True
    assert HumanStep.CONSIDER_OPERATOR_STOP_AND_ISOLATION in result.steps
