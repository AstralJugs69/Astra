"""Human-owned containment confirmation and exact-candidate proof workflow.

The workflow only persists attributable review facts. It has no CUPS client,
device driver, subprocess invocation, replacement-job linker, or notification
delivery capability.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import (
    CandidateSynchronizationCommit,
    ContainmentConfirmationCommit,
    ProofRecordCommit,
)
from braille_errata_relay.braille.profile import profile_sha256, require_bound_profile
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.errors import (
    IncidentReviewEvidenceError,
    IncidentReviewPrerequisiteError,
    IncidentReviewStateConflictError,
)
from braille_errata_relay.domain.models import (
    ArtifactManifest,
    ArtifactRef,
    BlockingReason,
    BoundTranslationTable,
    ContainmentConfirmation,
    ContainmentConfirmationProposal,
    HumanTimelineEventKind,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    JobState,
    OperatorAttestation,
    ProfessionalDisposition,
    ProofDecision,
    ProofRecord,
    RegisteredBaseline,
    SiteObservation,
    TranslationProfile,
    assert_no_production_control_fields,
)
from braille_errata_relay.domain.recommendation import SiteEvidenceStatus, assess_site_evidence


class ContainmentProofRejected(RuntimeError):
    """A human gate has insufficient or inconsistent immutable evidence."""

    def __init__(self, reason: BlockingReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ContainmentProofConflict(ContainmentProofRejected):
    """A user submitted an optimistic-version or candidate-stale form."""

    def __init__(self, message: str = "incident review state is stale") -> None:
        super().__init__(BlockingReason.STALE_STATE_VERSION, message)


class ProofArtifactStore(Protocol):
    async def read(self, ref: ArtifactRef) -> bytes: ...


class ContainmentProofLedger(Protocol):
    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None: ...

    async def get_incident_review_state(self, incident_id: str) -> IncidentReviewState | None: ...

    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None: ...

    async def get_production_link(self, baseline_id: str) -> object | None: ...

    async def get_latest_site_observation(
        self,
        *,
        site_id: str,
        bridge_id: str,
        queue_name: str,
    ) -> SiteObservation | None: ...

    async def get_professional_disposition(
        self,
        record_id: str,
    ) -> ProfessionalDisposition | None: ...

    async def get_operator_attestation(self, record_id: str) -> OperatorAttestation | None: ...

    async def list_incident_timeline_events(self, incident_id: str) -> tuple[object, ...]: ...

    async def record_containment_confirmation(
        self,
        *,
        proposal: ContainmentConfirmationProposal,
        max_observation_age_seconds: float = 15.0,
    ) -> ContainmentConfirmationCommit: ...

    async def synchronize_incident_review_candidate(
        self,
        *,
        incident_id: str,
    ) -> CandidateSynchronizationCommit: ...

    async def record_proof(self, *, proposed_record: ProofRecord) -> ProofRecordCommit: ...


@dataclass(frozen=True)
class CandidateProofProvenance:
    candidate_sha256: str
    manifest_sha256: str
    source_revision_id: str
    source_sha256: str
    translation_profile_id: str
    translation_profile_sha256: str
    liblouis_version: str
    translation_tables: tuple[BoundTranslationTable, ...]
    formatter_version: str

    def sanitized_record(self) -> dict[str, object]:
        return {
            "candidate_sha256": self.candidate_sha256,
            "manifest_sha256": self.manifest_sha256,
            "source_revision_id": self.source_revision_id,
            "source_sha256": self.source_sha256,
            "translation_profile_id": self.translation_profile_id,
            "translation_profile_sha256": self.translation_profile_sha256,
            "liblouis_version": self.liblouis_version,
            "translation_tables": [
                table.model_dump(mode="json") for table in self.translation_tables
            ],
            "formatter_version": self.formatter_version,
            "candidate_label": "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER",
        }


@dataclass(frozen=True)
class ContainmentEligibility:
    eligible: bool
    blocking_reason: BlockingReason | None
    halt_disposition_record_id: str | None
    site_observation_id: str | None
    physical_output_isolation_attestation_id: str | None

    def sanitized_record(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "blocking_reason": self.blocking_reason.value
            if self.blocking_reason is not None
            else None,
            "halt_disposition_record_id": self.halt_disposition_record_id,
            "site_observation_id": self.site_observation_id,
            "physical_output_isolation_attestation_id": (
                self.physical_output_isolation_attestation_id
            ),
        }


@dataclass(frozen=True)
class ProofEligibility:
    eligible: bool
    blocking_reason: BlockingReason | None
    provenance: CandidateProofProvenance | None

    def sanitized_record(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "blocking_reason": self.blocking_reason.value
            if self.blocking_reason is not None
            else None,
            "provenance": self.provenance.sanitized_record()
            if self.provenance is not None
            else None,
        }


@dataclass(frozen=True)
class ContainmentConfirmationResult:
    state: IncidentReviewState
    confirmation: ContainmentConfirmation
    duplicate: bool


@dataclass(frozen=True)
class ProofRecordResult:
    state: IncidentReviewState
    proof: ProofRecord
    duplicate: bool


def _normalize_now(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _site_evidence_reason(status: SiteEvidenceStatus) -> BlockingReason:
    if status is SiteEvidenceStatus.STALE:
        return BlockingReason.CONTAINMENT_EVIDENCE_STALE
    if status is SiteEvidenceStatus.AMBIGUOUS:
        return BlockingReason.CONTAINMENT_EVIDENCE_AMBIGUOUS
    if status is SiteEvidenceStatus.MISSING:
        return BlockingReason.CONTAINMENT_EVIDENCE_MISSING
    return BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH


class ContainmentProofWorkflow:
    """Record human-only containment/proof facts against immutable lineage."""

    def __init__(
        self,
        *,
        ledger: ContainmentProofLedger,
        artifact_store: ProofArtifactStore,
        profile: TranslationProfile,
        bridge_id: str,
        clock: Callable[[], datetime] | None = None,
        observation_max_age_seconds: float = 15.0,
    ) -> None:
        if not bridge_id:
            raise ValueError("containment proof requires an explicit bridge ID")
        if observation_max_age_seconds < 0:
            raise ValueError("containment observation maximum age cannot be negative")
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.profile = profile
        self.bridge_id = bridge_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.observation_max_age_seconds = observation_max_age_seconds

    def _now(self) -> datetime:
        return _normalize_now(self.clock())

    @staticmethod
    def _reason_from_error(error: IncidentReviewEvidenceError) -> BlockingReason:
        try:
            return BlockingReason(error.reason)
        except ValueError:
            return BlockingReason.PROOF_NOT_ELIGIBLE

    async def resolve_candidate_provenance(
        self,
        *,
        incident_id: str,
    ) -> CandidateProofProvenance:
        checkpoint = await self.ledger.get_incident_checkpoint(incident_id)
        if (
            checkpoint is None
            or checkpoint.candidate_brf is None
            or checkpoint.candidate_manifest is None
        ):
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "the authoritative incident does not contain candidate provenance",
            )
        try:
            candidate_bytes = await self.artifact_store.read(checkpoint.candidate_brf)
            manifest_bytes = await self.artifact_store.read(checkpoint.candidate_manifest)
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "the immutable candidate BRF or manifest could not be read",
            ) from exc
        if hashlib.sha256(candidate_bytes).hexdigest() != checkpoint.candidate_brf.sha256:
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "the candidate BRF bytes do not match their immutable reference",
            )
        if hashlib.sha256(manifest_bytes).hexdigest() != checkpoint.candidate_manifest.sha256:
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "the candidate manifest bytes do not match their immutable reference",
            )
        try:
            manifest = ArtifactManifest.model_validate_json(manifest_bytes)
            require_bound_profile(self.profile)
        except (ValueError, TypeError) as exc:
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "candidate manifest or translation profile is not proof-ready",
            ) from exc
        profile_digest = profile_sha256(self.profile)
        if (
            manifest.artifact_sha256 != checkpoint.candidate_brf.sha256
            or manifest.source_revision_id != checkpoint.new_source_revision_id
            or manifest.source_sha256 != checkpoint.new_source_sha256
            or manifest.translation_profile_sha256 != profile_digest
            or manifest.liblouis_version != self.profile.liblouis_version
            or manifest.formatter_version != self.profile.formatter_version
        ):
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "candidate manifest conflicts with current source/profile lineage",
            )
        table_identities: list[BoundTranslationTable] = []
        for table in self.profile.translation_tables:
            if table.sha256 is None:
                raise ContainmentProofRejected(
                    BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                    "translation table identity is not bound",
                )
            table_identities.append(BoundTranslationTable(name=table.name, sha256=table.sha256))
        return CandidateProofProvenance(
            candidate_sha256=checkpoint.candidate_brf.sha256,
            manifest_sha256=checkpoint.candidate_manifest.sha256,
            source_revision_id=checkpoint.new_source_revision_id,
            source_sha256=checkpoint.new_source_sha256,
            translation_profile_id=self.profile.profile_id,
            translation_profile_sha256=profile_digest,
            liblouis_version=self.profile.liblouis_version,
            translation_tables=tuple(table_identities),
            formatter_version=self.profile.formatter_version,
        )

    async def containment_eligibility(self, *, incident_id: str) -> ContainmentEligibility:
        state = await self.ledger.get_incident_review_state(incident_id)
        checkpoint = await self.ledger.get_incident_checkpoint(incident_id)
        if state is None or checkpoint is None:
            return ContainmentEligibility(
                False,
                BlockingReason.CONTAINMENT_EVIDENCE_MISSING,
                None,
                None,
                None,
            )
        if state.state is not IncidentState.CONTAINMENT_IN_PROGRESS:
            return ContainmentEligibility(
                False,
                BlockingReason.CONTAINMENT_CONFIRMATION_REQUIRED,
                None,
                None,
                None,
            )
        baseline = await self.ledger.get_baseline(checkpoint.baseline_id)
        if baseline is None:
            return ContainmentEligibility(
                False,
                BlockingReason.MISSING_LINEAGE,
                None,
                None,
                None,
            )
        production_link = await self.ledger.get_production_link(checkpoint.baseline_id)
        bridge_id = getattr(production_link, "bridge_id", None)
        if (
            baseline.baseline.scheduler_job_id is None
            or baseline.baseline.scheduler_job_title is None
            or not isinstance(bridge_id, str)
            or bridge_id != self.bridge_id
        ):
            return ContainmentEligibility(
                False,
                BlockingReason.MISSING_LINEAGE,
                None,
                None,
                None,
            )
        try:
            observation = await self.ledger.get_latest_site_observation(
                site_id=baseline.baseline.site_id,
                bridge_id=bridge_id,
                queue_name=baseline.baseline.queue_name,
            )
        except (RuntimeError, ValueError):
            # A broken or replaced cloud observation head is itself an
            # admission/outbox contradiction, never a reason to guess from an
            # older scheduler snapshot.
            return ContainmentEligibility(
                False,
                BlockingReason.OBSERVATION_OUTBOX_CONTRADICTION,
                None,
                None,
                None,
            )
        halt: ProfessionalDisposition | None = None
        isolation: OperatorAttestation | None = None
        for event in await self.ledger.list_incident_timeline_events(incident_id):
            kind = getattr(event, "kind", None)
            record_id = getattr(event, "record_id", None)
            if not isinstance(record_id, str):
                continue
            if kind is HumanTimelineEventKind.PROFESSIONAL_DISPOSITION:
                disposition = await self.ledger.get_professional_disposition(record_id)
                if disposition is not None and disposition.decision.value == "HALT_REQUESTED":
                    halt = disposition
            elif kind is HumanTimelineEventKind.OPERATOR_ATTESTATION:
                attestation = await self.ledger.get_operator_attestation(record_id)
                if (
                    attestation is not None
                    and attestation.attestation_type.value == "PHYSICAL_OUTPUT_ISOLATED"
                    and halt is not None
                    and attestation.recorded_at > halt.recorded_at
                ):
                    isolation = attestation
        if halt is None:
            return ContainmentEligibility(
                False,
                BlockingReason.CONTAINMENT_EVIDENCE_MISSING,
                None,
                observation.observation_id if observation is not None else None,
                isolation.record_id if isolation is not None else None,
            )
        if isolation is None:
            return ContainmentEligibility(
                False,
                BlockingReason.PHYSICAL_OUTPUT_ISOLATION_REQUIRED,
                halt.record_id,
                observation.observation_id if observation is not None else None,
                None,
            )
        evidence = assess_site_evidence(
            site_observation=observation,
            expected_job_id=baseline.baseline.scheduler_job_id,
            expected_queue_name=baseline.baseline.queue_name,
            expected_job_title=baseline.baseline.scheduler_job_title,
            expected_artifact_sha256=baseline.baseline.approved_brf_sha256,
            now=self._now(),
            max_age_seconds=self.observation_max_age_seconds,
        )
        if (
            evidence.status is not SiteEvidenceStatus.FRESH
            or observation is None
            or observation.observed_at <= halt.recorded_at
            or evidence.job_state not in {JobState.CANCELED, JobState.ABORTED, JobState.COMPLETED}
        ):
            reason = (
                _site_evidence_reason(evidence.status)
                if evidence.status is not SiteEvidenceStatus.FRESH
                else BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH
            )
            return ContainmentEligibility(
                False,
                reason,
                halt.record_id,
                observation.observation_id if observation is not None else None,
                isolation.record_id,
            )
        return ContainmentEligibility(
            True,
            None,
            halt.record_id,
            observation.observation_id,
            isolation.record_id,
        )

    async def proof_eligibility(self, *, incident_id: str) -> ProofEligibility:
        state = await self.ledger.get_incident_review_state(incident_id)
        if state is None:
            return ProofEligibility(False, BlockingReason.PROOF_NOT_ELIGIBLE, None)
        if state.state is not IncidentState.AWAITING_PROOF:
            return ProofEligibility(False, BlockingReason.PROOF_NOT_ELIGIBLE, None)
        if state.blocking_reason is not None:
            return ProofEligibility(False, state.blocking_reason, None)
        try:
            provenance = await self.resolve_candidate_provenance(incident_id=incident_id)
        except ContainmentProofRejected as exc:
            return ProofEligibility(False, exc.reason, None)
        if provenance.candidate_sha256 != state.current_candidate_sha256:
            return ProofEligibility(False, BlockingReason.CANDIDATE_PROVENANCE_MISMATCH, None)
        return ProofEligibility(True, None, provenance)

    async def record_containment_confirmation(
        self,
        *,
        incident_id: str,
        halt_disposition_record_id: str,
        site_observation_id: str,
        physical_output_isolation_attestation_id: str,
        selected_role: str,
        expected_state_version: int,
        note: str,
        idempotency_key: str,
        actor_principal: str,
    ) -> ContainmentConfirmationResult:
        payload = {
            "halt_disposition_record_id": halt_disposition_record_id,
            "site_observation_id": site_observation_id,
            "physical_output_isolation_attestation_id": physical_output_isolation_attestation_id,
            "selected_role": selected_role,
            "expected_state_version": expected_state_version,
            "note": note,
            "idempotency_key": idempotency_key,
        }
        assert_no_production_control_fields(payload)
        if selected_role != "production_coordinator":
            raise ContainmentProofRejected(
                BlockingReason.CONTAINMENT_CONFIRMATION_REQUIRED,
                "selected role is not authorized for containment confirmation",
            )
        proposal = ContainmentConfirmationProposal(
            incident_id=incident_id,
            halt_disposition_record_id=halt_disposition_record_id,
            site_observation_id=site_observation_id,
            physical_output_isolation_attestation_id=physical_output_isolation_attestation_id,
            selected_role="production_coordinator",
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            note=note,
            actor_principal=actor_principal,
        )
        try:
            commit = await self.ledger.record_containment_confirmation(
                proposal=proposal,
                max_observation_age_seconds=self.observation_max_age_seconds,
            )
        except IncidentReviewStateConflictError as exc:
            raise ContainmentProofConflict() from exc
        except IncidentReviewEvidenceError as exc:
            raise ContainmentProofRejected(self._reason_from_error(exc), str(exc)) from exc
        except IncidentReviewPrerequisiteError as exc:
            raise ContainmentProofRejected(
                BlockingReason.CONTAINMENT_CONFIRMATION_REQUIRED,
                str(exc),
            ) from exc
        return ContainmentConfirmationResult(
            state=commit.state,
            confirmation=commit.confirmation,
            duplicate=commit.duplicate,
        )

    async def record_proof(
        self,
        *,
        incident_id: str,
        candidate_sha256: str,
        manifest_sha256: str,
        decision: ProofDecision,
        review_basis: str,
        selected_role: str,
        expected_state_version: int,
        note: str,
        findings: tuple[str, ...],
        visual_only_uncertainty: bool,
        idempotency_key: str,
        actor_principal: str,
    ) -> ProofRecordResult:
        payload = {
            "candidate_sha256": candidate_sha256,
            "manifest_sha256": manifest_sha256,
            "decision": decision.value,
            "review_basis": review_basis,
            "selected_role": selected_role,
            "expected_state_version": expected_state_version,
            "note": note,
            "findings": findings,
            "visual_only_uncertainty": visual_only_uncertainty,
            "idempotency_key": idempotency_key,
        }
        assert_no_production_control_fields(payload)
        if selected_role != "proofreader":
            raise ContainmentProofRejected(
                BlockingReason.PROOF_NOT_ELIGIBLE,
                "selected role is not authorized for proof review",
            )
        if review_basis != "DEMO_FIXTURE_REVIEW":
            raise ContainmentProofRejected(
                BlockingReason.PROOF_REVIEW_REQUIRED,
                "this fixture can be recorded only as DEMO_FIXTURE_REVIEW",
            )
        try:
            synchronization = await self.ledger.synchronize_incident_review_candidate(
                incident_id=incident_id
            )
        except IncidentReviewEvidenceError as exc:
            raise ContainmentProofRejected(self._reason_from_error(exc), str(exc)) from exc
        if synchronization.changed:
            raise ContainmentProofConflict(
                "the candidate changed and prior proof is invalidated; reload before review"
            )
        provenance = await self.resolve_candidate_provenance(incident_id=incident_id)
        if (
            candidate_sha256 != provenance.candidate_sha256
            or manifest_sha256 != provenance.manifest_sha256
        ):
            raise ContainmentProofRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "the submitted proof form does not name the current candidate and manifest",
            )
        if decision is ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION and visual_only_uncertainty:
            raise ContainmentProofRejected(
                BlockingReason.PROOF_REVIEW_REQUIRED,
                "visual-only uncertainty blocks proof approval",
            )
        record_id = canonical_sha256(
            {
                "kind": "proof-record",
                "incident_id": incident_id,
                **provenance.sanitized_record(),
                "decision": decision.value,
                "review_basis": review_basis,
                "selected_role": selected_role,
                "expected_state_version": expected_state_version,
                "note": note,
                "findings": findings,
                "visual_only_uncertainty": visual_only_uncertainty,
                "idempotency_key": idempotency_key,
                "actor_principal": actor_principal,
            }
        )
        record = ProofRecord(
            record_id=record_id,
            incident_id=incident_id,
            candidate_sha256=provenance.candidate_sha256,
            manifest_sha256=provenance.manifest_sha256,
            source_revision_id=provenance.source_revision_id,
            source_sha256=provenance.source_sha256,
            translation_profile_id=provenance.translation_profile_id,
            translation_profile_sha256=provenance.translation_profile_sha256,
            liblouis_version=provenance.liblouis_version,
            translation_tables=provenance.translation_tables,
            formatter_version=provenance.formatter_version,
            decision=decision,
            review_basis="DEMO_FIXTURE_REVIEW",
            selected_role="proofreader",
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            note=note,
            findings=findings,
            visual_only_uncertainty=visual_only_uncertainty,
            actor_principal=actor_principal,
            recorded_at=self._now(),
        )
        try:
            commit = await self.ledger.record_proof(proposed_record=record)
        except IncidentReviewStateConflictError as exc:
            raise ContainmentProofConflict() from exc
        except IncidentReviewEvidenceError as exc:
            raise ContainmentProofRejected(self._reason_from_error(exc), str(exc)) from exc
        except IncidentReviewPrerequisiteError as exc:
            raise ContainmentProofRejected(BlockingReason.PROOF_NOT_ELIGIBLE, str(exc)) from exc
        return ProofRecordResult(state=commit.state, proof=commit.proof, duplicate=commit.duplicate)
