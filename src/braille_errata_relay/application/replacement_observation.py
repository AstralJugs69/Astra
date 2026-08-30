"""Human-owned replacement observation and immutable candidate download seam.

This module has no CUPS client, device driver, subprocess invocation, endpoint
capture verifier, or closure transition.  It only re-hashes an already approved
candidate and records a human association with fresh read-only scheduler evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import (
    CandidateSynchronizationCommit,
    ReplacementObservationLinkCommit,
)
from braille_errata_relay.application.containment_proof import (
    CandidateProofProvenance,
    ContainmentProofRejected,
    ContainmentProofWorkflow,
    ProofArtifactStore,
)
from braille_errata_relay.domain.errors import (
    IncidentReviewEvidenceError,
    IncidentReviewPrerequisiteError,
    IncidentReviewStateConflictError,
)
from braille_errata_relay.domain.models import (
    ArtifactKind,
    BlockingReason,
    HumanTimelineEventKind,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    ProofDecision,
    ProofRecord,
    ReplacementObservationLink,
    ReplacementObservationLinkProposal,
    assert_no_production_control_fields,
)


class ReplacementObservationRejected(RuntimeError):
    """A human-owned replacement correlation lacks immutable evidence."""

    def __init__(self, reason: BlockingReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ReplacementObservationConflict(ReplacementObservationRejected):
    """The review head changed while a human was completing a stale form."""

    def __init__(self, message: str = "incident review state is stale") -> None:
        super().__init__(BlockingReason.STALE_STATE_VERSION, message)


class ReplacementObservationLedger(Protocol):
    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None: ...

    async def get_incident_review_state(self, incident_id: str) -> IncidentReviewState | None: ...

    async def get_proof_record(self, record_id: str) -> ProofRecord | None: ...

    async def list_incident_timeline_events(self, incident_id: str) -> tuple[object, ...]: ...

    async def synchronize_incident_review_candidate(
        self,
        *,
        incident_id: str,
    ) -> CandidateSynchronizationCommit: ...

    async def record_replacement_observation_link(
        self,
        *,
        proposal: ReplacementObservationLinkProposal,
        max_observation_age_seconds: float = 15.0,
    ) -> ReplacementObservationLinkCommit: ...


@dataclass(frozen=True)
class CurrentApprovedCandidate:
    checkpoint: IncidentCheckpoint
    state: IncidentReviewState
    provenance: CandidateProofProvenance
    proof: ProofRecord


@dataclass(frozen=True)
class ApprovedCandidateDownload:
    content: bytes
    candidate_sha256: str
    manifest_sha256: str
    proof_record_id: str
    filename: str


@dataclass(frozen=True)
class ReplacementObservationEligibility:
    eligible: bool
    blocking_reason: BlockingReason | None
    candidate_sha256: str | None = None
    candidate_manifest_sha256: str | None = None
    proof_record_id: str | None = None

    def sanitized_record(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "candidate_download_eligible": self.eligible,
            "blocking_reason": self.blocking_reason.value
            if self.blocking_reason is not None
            else None,
            "provenance": (
                {
                    "candidate_sha256": self.candidate_sha256,
                    "manifest_sha256": self.candidate_manifest_sha256,
                    "proof_record_id": self.proof_record_id,
                    "candidate_label": "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER",
                }
                if self.eligible
                else None
            ),
        }


@dataclass(frozen=True)
class ReplacementObservationLinkResult:
    state: IncidentReviewState
    link: ReplacementObservationLink
    duplicate: bool


def canonical_replacement_job_title(*, incident_id: str, candidate_sha256: str) -> str:
    """Return the exact external job title Relay expects a human to use."""

    return f"BER|{incident_id}|{candidate_sha256[:12]}|REPLACEMENT"


class ReplacementObservationWorkflow:
    """Resolve current proof lineage and append a bounded observed-job fact."""

    def __init__(
        self,
        *,
        ledger: ReplacementObservationLedger,
        containment_proof_workflow: ContainmentProofWorkflow,
        artifact_store: ProofArtifactStore,
        clock: Callable[[], datetime] | None = None,
        observation_max_age_seconds: float = 15.0,
    ) -> None:
        if observation_max_age_seconds < 0:
            raise ValueError("replacement observation maximum age cannot be negative")
        self.ledger = ledger
        self.containment_proof_workflow = containment_proof_workflow
        self.artifact_store = artifact_store
        self.clock = clock or (lambda: datetime.now(UTC))
        self.observation_max_age_seconds = observation_max_age_seconds

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    @staticmethod
    def _reason_from_error(error: IncidentReviewEvidenceError) -> BlockingReason:
        try:
            return BlockingReason(error.reason)
        except ValueError:
            return BlockingReason.REPLACEMENT_NOT_ELIGIBLE

    async def _current_approved_candidate(
        self,
        *,
        incident_id: str,
        synchronize: bool,
    ) -> CurrentApprovedCandidate:
        state: IncidentReviewState | None
        if synchronize:
            try:
                synchronization = await self.ledger.synchronize_incident_review_candidate(
                    incident_id=incident_id
                )
            except IncidentReviewEvidenceError as exc:
                raise ReplacementObservationRejected(
                    self._reason_from_error(exc), str(exc)
                ) from exc
            if synchronization.changed:
                raise ReplacementObservationRejected(
                    BlockingReason.CANDIDATE_APPROVAL_INVALIDATED,
                    "the candidate changed after proof approval; reload before human submission",
                )
            state = synchronization.state
        else:
            state = await self.ledger.get_incident_review_state(incident_id)
        if state is None:
            raise ReplacementObservationRejected(
                BlockingReason.REPLACEMENT_NOT_ELIGIBLE,
                "the incident has no current human-review state",
            )
        checkpoint = await self.ledger.get_incident_checkpoint(incident_id)
        if (
            checkpoint is None
            or checkpoint.candidate_brf is None
            or checkpoint.candidate_manifest is None
            or checkpoint.candidate_brf.kind is not ArtifactKind.FULL_CANDIDATE_BRF
        ):
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "the incident has no current full-volume candidate provenance",
            )
        if state.state is not IncidentState.AWAITING_REPLACEMENT:
            raise ReplacementObservationRejected(
                BlockingReason.REPLACEMENT_NOT_ELIGIBLE,
                "replacement observation requires an exact current proof approval",
            )
        if state.blocking_reason is not None:
            raise ReplacementObservationRejected(
                state.blocking_reason,
                "a visible incident block prevents candidate download or replacement observation",
            )
        if state.current_candidate_sha256 != checkpoint.candidate_brf.sha256:
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_APPROVAL_INVALIDATED,
                "the review head and immutable candidate artifact disagree",
            )
        try:
            provenance = await self.containment_proof_workflow.resolve_candidate_provenance(
                incident_id=incident_id
            )
        except ContainmentProofRejected as exc:
            raise ReplacementObservationRejected(exc.reason, str(exc)) from exc
        if (
            provenance.candidate_sha256 != checkpoint.candidate_brf.sha256
            or provenance.manifest_sha256 != checkpoint.candidate_manifest.sha256
        ):
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "candidate proof provenance conflicts with the current checkpoint",
            )
        matching_proofs: list[ProofRecord] = []
        for event in await self.ledger.list_incident_timeline_events(incident_id):
            if getattr(event, "kind", None) is not HumanTimelineEventKind.PROOF_RECORD:
                continue
            record_id = getattr(event, "record_id", None)
            if not isinstance(record_id, str):
                continue
            record = await self.ledger.get_proof_record(record_id)
            if (
                record is not None
                and record.incident_id == incident_id
                and record.decision is ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION
                and not record.visual_only_uncertainty
                and record.candidate_sha256 == provenance.candidate_sha256
                and record.manifest_sha256 == provenance.manifest_sha256
            ):
                matching_proofs.append(record)
        if len(matching_proofs) != 1:
            raise ReplacementObservationRejected(
                BlockingReason.PROOF_NOT_ELIGIBLE,
                "the incident does not have one attributable current exact-candidate proof approval",
            )
        return CurrentApprovedCandidate(
            checkpoint=checkpoint,
            state=state,
            provenance=provenance,
            proof=matching_proofs[0],
        )

    async def eligibility(self, *, incident_id: str) -> ReplacementObservationEligibility:
        """Return read-only action eligibility without changing workflow state."""

        try:
            current = await self._current_approved_candidate(
                incident_id=incident_id,
                synchronize=False,
            )
        except ReplacementObservationRejected as exc:
            return ReplacementObservationEligibility(False, exc.reason)
        return ReplacementObservationEligibility(
            True,
            None,
            candidate_sha256=current.provenance.candidate_sha256,
            candidate_manifest_sha256=current.provenance.manifest_sha256,
            proof_record_id=current.proof.record_id,
        )

    async def download_current_candidate(self, *, incident_id: str) -> ApprovedCandidateDownload:
        """Read and re-hash only the immutable candidate bound to current proof."""

        current = await self._current_approved_candidate(
            incident_id=incident_id,
            synchronize=False,
        )
        candidate_ref = current.checkpoint.candidate_brf
        if candidate_ref is None:  # defensive; _current_approved_candidate checks this
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "the immutable candidate reference is unavailable",
            )
        try:
            content = await self.artifact_store.read(candidate_ref)
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISSING,
                "the immutable candidate BRF could not be read",
            ) from exc
        digest = hashlib.sha256(content).hexdigest()
        if (
            digest != current.provenance.candidate_sha256
            or len(content) != candidate_ref.byte_length
        ):
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "the candidate bytes no longer match immutable approved lineage",
            )
        filename = (
            "braille-errata-relay-"
            f"{incident_id[:12]}-{current.provenance.candidate_sha256[:12]}.brf"
        )
        return ApprovedCandidateDownload(
            content=content,
            candidate_sha256=current.provenance.candidate_sha256,
            manifest_sha256=current.provenance.manifest_sha256,
            proof_record_id=current.proof.record_id,
            filename=filename,
        )

    async def record_observation_link(
        self,
        *,
        incident_id: str,
        candidate_sha256: str,
        candidate_manifest_sha256: str,
        proof_record_id: str,
        scheduler_job_id: int,
        site_observation_id: str,
        selected_role: str,
        expected_state_version: int,
        note: str,
        idempotency_key: str,
        actor_principal: str,
    ) -> ReplacementObservationLinkResult:
        """Record exactly one observed external job; never operate that job."""

        payload = {
            "candidate_sha256": candidate_sha256,
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "proof_record_id": proof_record_id,
            "scheduler_job_id": scheduler_job_id,
            "site_observation_id": site_observation_id,
            "selected_role": selected_role,
            "expected_state_version": expected_state_version,
            "note": note,
            "idempotency_key": idempotency_key,
        }
        assert_no_production_control_fields(payload)
        if selected_role != "machine_operator":
            raise ReplacementObservationRejected(
                BlockingReason.REPLACEMENT_NOT_ELIGIBLE,
                "only the machine_operator role may link a replacement observation",
            )
        proposal = ReplacementObservationLinkProposal(
            incident_id=incident_id,
            candidate_sha256=candidate_sha256,
            candidate_manifest_sha256=candidate_manifest_sha256,
            proof_record_id=proof_record_id,
            scheduler_job_id=scheduler_job_id,
            site_observation_id=site_observation_id,
            selected_role="machine_operator",
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            note=note,
            actor_principal=actor_principal,
        )
        # Once the link has been recorded, an identical retry must converge
        # without reopening a human approval gate. The ledger verifies the
        # request hash before it accepts this replay.
        existing_state = await self.ledger.get_incident_review_state(incident_id)
        if (
            existing_state is not None
            and existing_state.state is IncidentState.REPLACEMENT_OBSERVED
        ):
            try:
                commit = await self.ledger.record_replacement_observation_link(
                    proposal=proposal,
                    max_observation_age_seconds=self.observation_max_age_seconds,
                )
            except IncidentReviewStateConflictError as exc:
                raise ReplacementObservationConflict() from exc
            except IncidentReviewEvidenceError as exc:
                raise ReplacementObservationRejected(
                    self._reason_from_error(exc), str(exc)
                ) from exc
            except IncidentReviewPrerequisiteError as exc:
                raise ReplacementObservationRejected(
                    BlockingReason.REPLACEMENT_NOT_ELIGIBLE,
                    str(exc),
                ) from exc
            return ReplacementObservationLinkResult(
                state=commit.state,
                link=commit.link,
                duplicate=commit.duplicate,
            )
        current = await self._current_approved_candidate(
            incident_id=incident_id,
            synchronize=True,
        )
        if (
            candidate_sha256 != current.provenance.candidate_sha256
            or candidate_manifest_sha256 != current.provenance.manifest_sha256
            or proof_record_id != current.proof.record_id
        ):
            raise ReplacementObservationRejected(
                BlockingReason.CANDIDATE_PROVENANCE_MISMATCH,
                "the submitted replacement link does not name the current approved candidate proof",
            )
        try:
            commit = await self.ledger.record_replacement_observation_link(
                proposal=proposal,
                max_observation_age_seconds=self.observation_max_age_seconds,
            )
        except IncidentReviewStateConflictError as exc:
            raise ReplacementObservationConflict() from exc
        except IncidentReviewEvidenceError as exc:
            raise ReplacementObservationRejected(self._reason_from_error(exc), str(exc)) from exc
        except IncidentReviewPrerequisiteError as exc:
            raise ReplacementObservationRejected(
                BlockingReason.REPLACEMENT_NOT_ELIGIBLE,
                str(exc),
            ) from exc
        return ReplacementObservationLinkResult(
            state=commit.state,
            link=commit.link,
            duplicate=commit.duplicate,
        )
