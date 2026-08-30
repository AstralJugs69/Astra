"""Optimistic, human-gated incident state transitions."""

from __future__ import annotations

from datetime import datetime

from .models import Incident, IncidentState


class IllegalStateTransition(ValueError):
    pass


class StaleStateVersion(ValueError):
    pass


class AttributableEvidenceRequired(ValueError):
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
    # An uncertain containment fact cannot enter proof/replacement/closure.
    IncidentState.CONTAINMENT_UNCERTAIN: frozenset({IncidentState.NEEDS_REVIEW}),
    IncidentState.AWAITING_PROOF: frozenset(
        {IncidentState.PROOF_REJECTED, IncidentState.PROOF_APPROVED}
    ),
    IncidentState.PROOF_REJECTED: frozenset({IncidentState.AWAITING_PROOF}),
    # A newly rendered candidate invalidates an earlier proof approval without
    # rewriting that immutable proof record. The deterministic lineage event
    # returns the aggregate to the human proof gate.
    IncidentState.PROOF_APPROVED: frozenset(
        {IncidentState.AWAITING_REPLACEMENT, IncidentState.AWAITING_PROOF}
    ),
    IncidentState.AWAITING_REPLACEMENT: frozenset(
        {IncidentState.REPLACEMENT_OBSERVED, IncidentState.AWAITING_PROOF}
    ),
    IncidentState.REPLACEMENT_OBSERVED: frozenset({IncidentState.VERIFYING}),
    IncidentState.VERIFYING: frozenset(
        {IncidentState.RESOLVED_BY_HUMAN, IncidentState.VERIFICATION_FAILED}
    ),
    IncidentState.VERIFICATION_FAILED: frozenset({IncidentState.AWAITING_REPLACEMENT}),
    IncidentState.CONTINUE_ACCEPTED: frozenset({IncidentState.RESOLVED_NO_REMEDIATION_BY_HUMAN}),
    # Blocked states recover only into assessment/disposition after an
    # attributable evidence record is attached.
    IncidentState.DEFERRED: frozenset({IncidentState.ASSESSING, IncidentState.REPORT_REJECTED}),
    IncidentState.REPORT_REJECTED: frozenset({IncidentState.ASSESSING, IncidentState.DEFERRED}),
    IncidentState.NEEDS_REVIEW: frozenset(
        {
            IncidentState.ASSESSING,
            IncidentState.CONTINUE_ACCEPTED,
            IncidentState.HALT_REQUESTED,
            IncidentState.DEFERRED,
            IncidentState.REPORT_REJECTED,
        }
    ),
    IncidentState.RESOLVED_BY_HUMAN: frozenset(),
    IncidentState.RESOLVED_NO_REMEDIATION_BY_HUMAN: frozenset(),
}


_EVIDENCE_REQUIRED_ON_EXIT = frozenset(
    {IncidentState.NEEDS_REVIEW, IncidentState.DEFERRED, IncidentState.REPORT_REJECTED}
)


def transition(
    incident: Incident,
    target: IncidentState,
    *,
    expected_state_version: int,
    at: datetime | None = None,
    evidence_id: str | None = None,
) -> Incident:
    if incident.state_version != expected_state_version:
        raise StaleStateVersion("incident changed; reload before recording a human record")
    if target not in ALLOWED_TRANSITIONS.get(incident.state, frozenset()):
        raise IllegalStateTransition(f"{incident.state} cannot transition to {target}")
    if incident.state in _EVIDENCE_REQUIRED_ON_EXIT and not evidence_id:
        raise AttributableEvidenceRequired(
            f"{incident.state} requires new attributable evidence before recovery"
        )
    if evidence_id is not None and not evidence_id.strip():
        raise AttributableEvidenceRequired("attributable evidence ID cannot be blank")
    return incident.model_copy(
        update={
            "state": target,
            "state_version": incident.state_version + 1,
            "report_ready_at": at
            if target == IncidentState.REPORT_READY
            else incident.report_ready_at,
            "last_attributable_evidence_id": (
                evidence_id if evidence_id is not None else incident.last_attributable_evidence_id
            ),
        }
    )


def require_report_precedes_action(report_ready_at: datetime | None, action_at: datetime) -> None:
    if report_ready_at is None or action_at <= report_ready_at:
        raise IllegalStateTransition("incident-attributed action must follow REPORT_READY")
