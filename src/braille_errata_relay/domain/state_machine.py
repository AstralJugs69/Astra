"""Optimistic, human-gated incident state transitions."""

from __future__ import annotations

from datetime import datetime

from .models import Incident, IncidentState


class IllegalStateTransition(ValueError):
    pass


class StaleStateVersion(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]] = {
    IncidentState.DETECTED: frozenset({IncidentState.ASSESSING}),
    IncidentState.ASSESSING: frozenset({IncidentState.REPORT_READY, IncidentState.NEEDS_REVIEW}),
    IncidentState.REPORT_READY: frozenset(
        {
            IncidentState.CONTINUE_ACCEPTED,
            IncidentState.HALT_REQUESTED,
            IncidentState.DEFERRED,
            IncidentState.REPORT_REJECTED,
        }
    ),
    IncidentState.HALT_REQUESTED: frozenset({IncidentState.CONTAINMENT_IN_PROGRESS}),
    IncidentState.CONTAINMENT_IN_PROGRESS: frozenset(
        {IncidentState.CONTAINED_BY_HUMAN, IncidentState.CONTAINMENT_UNCERTAIN}
    ),
    IncidentState.CONTAINED_BY_HUMAN: frozenset({IncidentState.AWAITING_PROOF}),
    IncidentState.CONTAINMENT_UNCERTAIN: frozenset({IncidentState.AWAITING_PROOF}),
    IncidentState.AWAITING_PROOF: frozenset(
        {IncidentState.PROOF_REJECTED, IncidentState.PROOF_APPROVED}
    ),
    IncidentState.PROOF_REJECTED: frozenset({IncidentState.AWAITING_PROOF}),
    IncidentState.PROOF_APPROVED: frozenset({IncidentState.AWAITING_REPLACEMENT}),
    IncidentState.AWAITING_REPLACEMENT: frozenset({IncidentState.REPLACEMENT_OBSERVED}),
    IncidentState.REPLACEMENT_OBSERVED: frozenset({IncidentState.VERIFYING}),
    IncidentState.VERIFYING: frozenset(
        {IncidentState.RESOLVED_BY_HUMAN, IncidentState.VERIFICATION_FAILED}
    ),
    IncidentState.VERIFICATION_FAILED: frozenset({IncidentState.AWAITING_REPLACEMENT}),
    IncidentState.CONTINUE_ACCEPTED: frozenset(
        {IncidentState.RESOLVED_NO_REMEDIATION_BY_HUMAN}
    ),
    IncidentState.DEFERRED: frozenset(),
    IncidentState.REPORT_REJECTED: frozenset(),
    IncidentState.NEEDS_REVIEW: frozenset(),
    IncidentState.RESOLVED_BY_HUMAN: frozenset(),
    IncidentState.RESOLVED_NO_REMEDIATION_BY_HUMAN: frozenset(),
}


def transition(
    incident: Incident,
    target: IncidentState,
    *,
    expected_state_version: int,
    at: datetime | None = None,
) -> Incident:
    if incident.state_version != expected_state_version:
        raise StaleStateVersion("incident changed; reload before recording a human record")
    if target not in ALLOWED_TRANSITIONS.get(incident.state, frozenset()):
        raise IllegalStateTransition(f"{incident.state} cannot transition to {target}")
    return incident.model_copy(
        update={
            "state": target,
            "state_version": incident.state_version + 1,
            "report_ready_at": at if target == IncidentState.REPORT_READY else incident.report_ready_at,
        }
    )


def require_report_precedes_action(report_ready_at: datetime | None, action_at: datetime) -> None:
    if report_ready_at is None or action_at <= report_ready_at:
        raise IllegalStateTransition("incident-attributed action must follow REPORT_READY")

