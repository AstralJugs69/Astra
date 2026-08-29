"""Append-only human disposition and containment-attestation workflow.

The module records attributable human decisions only. It deliberately contains
no CUPS client, device driver, subprocess invocation, or production-control
command.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from braille_errata_relay.adapters.firestore_ledger import (
    OperatorAttestationCommit,
    ProfessionalDispositionCommit,
)
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.errors import (
    IncidentReviewPrerequisiteError,
    IncidentReviewStateConflictError,
)
from braille_errata_relay.domain.models import (
    AttestationType,
    IncidentReviewState,
    IncidentTimelineEvent,
    OperatorAttestation,
    ProfessionalDecision,
    ProfessionalDisposition,
    TruthBasis,
    assert_no_production_control_fields,
)


class ProfessionalReviewRejected(RuntimeError):
    pass


class ProfessionalReviewConflict(ProfessionalReviewRejected):
    pass


class ProfessionalReviewLedger(Protocol):
    async def record_professional_disposition(
        self,
        *,
        proposed_record: ProfessionalDisposition,
    ) -> ProfessionalDispositionCommit: ...

    async def record_operator_attestation(
        self,
        *,
        proposed_record: OperatorAttestation,
    ) -> OperatorAttestationCommit: ...

    async def get_incident_review_state(self, incident_id: str) -> IncidentReviewState | None: ...

    async def list_incident_timeline_events(
        self,
        incident_id: str,
    ) -> tuple[IncidentTimelineEvent, ...]: ...


@dataclass(frozen=True)
class ProfessionalDispositionResult:
    state: IncidentReviewState
    disposition: ProfessionalDisposition
    duplicate: bool


@dataclass(frozen=True)
class OperatorAttestationResult:
    state: IncidentReviewState
    attestation: OperatorAttestation
    duplicate: bool


class ProfessionalReviewWorkflow:
    def __init__(
        self,
        *,
        ledger: ProfessionalReviewLedger,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    async def record_disposition(
        self,
        *,
        incident_id: str,
        decision: ProfessionalDecision,
        selected_role: str,
        expected_state_version: int,
        note: str,
        idempotency_key: str,
        actor_principal: str,
    ) -> ProfessionalDispositionResult:
        payload = {
            "decision": decision.value,
            "selected_role": selected_role,
            "expected_state_version": expected_state_version,
            "note": note,
            "idempotency_key": idempotency_key,
        }
        assert_no_production_control_fields(payload)
        if selected_role != "production_coordinator":
            raise ProfessionalReviewRejected("selected role is not authorized for disposition")
        coordinator_role = cast(Literal["production_coordinator"], selected_role)
        record = ProfessionalDisposition(
            record_id=canonical_sha256(
                {
                    "kind": "professional-disposition",
                    "incident_id": incident_id,
                    "decision": decision.value,
                    "selected_role": selected_role,
                    "expected_state_version": expected_state_version,
                    "note": note,
                    "idempotency_key": idempotency_key,
                    "actor_principal": actor_principal,
                }
            ),
            incident_id=incident_id,
            decision=decision,
            selected_role=coordinator_role,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            note=note,
            actor_principal=actor_principal,
            recorded_at=self._now(),
        )
        try:
            commit = await self.ledger.record_professional_disposition(proposed_record=record)
        except IncidentReviewStateConflictError as exc:
            raise ProfessionalReviewConflict("incident review state is stale") from exc
        except IncidentReviewPrerequisiteError as exc:
            raise ProfessionalReviewRejected(str(exc)) from exc
        return ProfessionalDispositionResult(commit.state, commit.disposition, commit.duplicate)

    async def record_operator_attestation(
        self,
        *,
        incident_id: str,
        attestation_type: AttestationType,
        truth_basis: TruthBasis,
        selected_role: str,
        expected_state_version: int,
        note: str,
        idempotency_key: str,
        actor_principal: str,
    ) -> OperatorAttestationResult:
        payload = {
            "attestation_type": attestation_type.value,
            "truth_basis": truth_basis.value,
            "selected_role": selected_role,
            "expected_state_version": expected_state_version,
            "note": note,
            "idempotency_key": idempotency_key,
        }
        assert_no_production_control_fields(payload)
        if selected_role != "machine_operator":
            raise ProfessionalReviewRejected("selected role is not authorized for attestation")
        machine_operator_role = cast(Literal["machine_operator"], selected_role)
        record = OperatorAttestation(
            record_id=canonical_sha256(
                {
                    "kind": "operator-attestation",
                    "incident_id": incident_id,
                    "attestation_type": attestation_type.value,
                    "truth_basis": truth_basis.value,
                    "selected_role": selected_role,
                    "expected_state_version": expected_state_version,
                    "note": note,
                    "idempotency_key": idempotency_key,
                    "actor_principal": actor_principal,
                }
            ),
            incident_id=incident_id,
            attestation_type=attestation_type,
            truth_basis=truth_basis,
            selected_role=machine_operator_role,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            note=note,
            actor_principal=actor_principal,
            recorded_at=self._now(),
        )
        try:
            commit = await self.ledger.record_operator_attestation(proposed_record=record)
        except IncidentReviewStateConflictError as exc:
            raise ProfessionalReviewConflict("incident review state is stale") from exc
        except IncidentReviewPrerequisiteError as exc:
            raise ProfessionalReviewRejected(str(exc)) from exc
        return OperatorAttestationResult(commit.state, commit.attestation, commit.duplicate)
