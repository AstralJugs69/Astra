"""Confirm advisory production lineage from exact simulated-endpoint bytes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import (
    EndpointReceiptCommit,
    EndpointVerificationClaim,
    LedgerIntegrityError,
)
from braille_errata_relay.adapters.gcs_artifacts import content_addressed_ref
from braille_errata_relay.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    BaselineLinkCorrection,
    BaselineProductionLink,
    BaselineStatus,
    CaptureState,
    EndpointEvidenceSubmission,
    EndpointReceipt,
    ProductionLinkBlockingReason,
    RegisteredBaseline,
)


class EndpointEvidenceRejected(RuntimeError):
    def __init__(self, reason: ProductionLinkBlockingReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class EndpointEvidenceConflict(EndpointEvidenceRejected):
    pass


class EndpointReceiptLedger(Protocol):
    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None: ...

    async def get_production_link(self, baseline_id: str) -> BaselineProductionLink | None: ...

    async def get_endpoint_receipt_by_idempotency(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit | None: ...

    async def allocate_endpoint_verification_timestamp(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
        evidence_identity_sha256: str,
        submitting_principal: str,
    ) -> EndpointVerificationClaim: ...

    async def confirm_endpoint_receipt(
        self,
        *,
        proposed_receipt: EndpointReceipt,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit: ...

    async def correct_historical_production_link(
        self,
        *,
        proposed_correction: BaselineLinkCorrection,
        expected_state_version: int,
        idempotency_key: str,
    ) -> RegisteredBaseline: ...


class EndpointArtifactStore(Protocol):
    bucket_name: str

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef: ...


@dataclass(frozen=True)
class EndpointReceiptResult:
    baseline: RegisteredBaseline
    receipt: EndpointReceipt
    duplicate: bool


def endpoint_receipt_idempotency_key(
    *, baseline_id: str, production_link_id: str, expected_state_version: int
) -> str:
    return canonical_sha256(
        {
            "scope": "endpoint-receipt",
            "baseline_id": baseline_id,
            "production_link_id": production_link_id,
            "expected_state_version": expected_state_version,
        }
    )


class EndpointReceiptWorkflow:
    def __init__(
        self,
        *,
        ledger: EndpointReceiptLedger,
        artifact_store: EndpointArtifactStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.clock = clock or (lambda: datetime.now(UTC))

    async def confirm(
        self,
        submission: EndpointEvidenceSubmission,
        *,
        submitting_principal: str,
    ) -> EndpointReceiptResult:
        expected_key = endpoint_receipt_idempotency_key(
            baseline_id=submission.baseline_id,
            production_link_id=submission.production_link_id,
            expected_state_version=submission.expected_baseline_state_version,
        )
        if submission.idempotency_key != expected_key:
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.IDEMPOTENCY_KEY_MISMATCH)
        replay = await self.ledger.get_endpoint_receipt_by_idempotency(
            baseline_id=submission.baseline_id,
            expected_state_version=submission.expected_baseline_state_version,
            idempotency_key=submission.idempotency_key,
        )
        if replay is not None:
            receipt = replay.receipt
            if (
                receipt.production_link_id != submission.production_link_id
                or receipt.scheduler_job_id != submission.scheduler_job_id
                or receipt.scheduler_job_title != submission.scheduler_job_title
                or receipt.site_id != submission.site_id
                or receipt.queue_name != submission.queue_name
                or receipt.approved_baseline_brf_sha256 != submission.approved_baseline_brf_sha256
                or receipt.endpoint_received_sha256 != submission.endpoint_received_sha256
                or receipt.capture_manifest_sha256 != submission.capture_manifest_sha256
                or receipt.terminal_event_sha256 != submission.terminal_event_sha256
                or receipt.capture_acceptance_sha256 != submission.capture_acceptance_sha256
                or receipt.accepted_event_sha256 != submission.accepted_event_sha256
                or receipt.previous_event_sha256 != submission.previous_event_sha256
                or receipt.capture_state != submission.capture_state
                or receipt.evidence_timestamp != submission.evidence_timestamp
                or receipt.submitting_principal != submitting_principal
            ):
                raise EndpointEvidenceConflict(
                    ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_CONFLICT
                )
            return EndpointReceiptResult(replay.baseline, replay.receipt, True)

        baseline = await self.ledger.get_baseline(submission.baseline_id)
        if baseline is None:
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.BASELINE_NOT_FOUND)
        if baseline.baseline.state_version != submission.expected_baseline_state_version:
            raise EndpointEvidenceConflict(ProductionLinkBlockingReason.STALE_STATE_VERSION)
        if baseline.baseline.status is not BaselineStatus.PROVISIONAL_PRODUCTION_LINK:
            raise EndpointEvidenceConflict(ProductionLinkBlockingReason.MISSING_ENDPOINT_EVIDENCE)
        link = await self.ledger.get_production_link(submission.baseline_id)
        if link is None or link.link_id != submission.production_link_id:
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH)
        expected = baseline.baseline
        if (
            submission.scheduler_job_id != expected.scheduler_job_id
            or submission.scheduler_job_id != link.scheduler_job_id
            or submission.scheduler_job_title != expected.scheduler_job_title
            or submission.scheduler_job_title != link.scheduler_job_title
            or submission.site_id != expected.site_id
            or submission.site_id != link.site_id
            or submission.queue_name != expected.queue_name
            or submission.queue_name != link.queue_name
            or submission.approved_baseline_brf_sha256 != expected.approved_brf_sha256
            or submission.endpoint_received_sha256 != expected.approved_brf_sha256
        ):
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH)

        idempotency_key_sha256 = canonical_sha256(
            {"scope": "endpoint-receipt", "key": submission.idempotency_key}
        )
        submission_body = submission.model_dump(
            mode="json",
            exclude={"idempotency_key"},
            exclude_none=submission.schema_version == "endpoint-evidence-submission.v1",
        )
        evidence_identity_sha256 = canonical_sha256(
            {
                **submission_body,
                "submitting_principal": submitting_principal,
                "idempotency_key_sha256": idempotency_key_sha256,
            }
        )
        try:
            claim = await self.ledger.allocate_endpoint_verification_timestamp(
                baseline_id=submission.baseline_id,
                expected_state_version=submission.expected_baseline_state_version,
                idempotency_key=submission.idempotency_key,
                evidence_identity_sha256=evidence_identity_sha256,
                submitting_principal=submitting_principal,
            )
        except LedgerIntegrityError as exc:
            raise EndpointEvidenceConflict(
                ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_CONFLICT
            ) from exc
        now = claim.verified_at
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        evidence_body = {
            **submission_body,
            "schema_version": (
                "endpoint-receipt-evidence.v2"
                if submission.capture_state is CaptureState.RECEIVED
                else "endpoint-receipt-evidence.v1"
            ),
            "submitting_principal": submitting_principal,
            "idempotency_key_sha256": idempotency_key_sha256,
            "verified_at": now.isoformat(),
        }
        evidence_bytes = canonical_json_bytes(evidence_body)
        evidence_sha256 = canonical_sha256(evidence_body)
        artifact_ref = content_addressed_ref(
            evidence_bytes,
            bucket_name=self.artifact_store.bucket_name,
            object_name=f"endpoint-receipts/{submission.baseline_id}/{evidence_sha256}.json",
            kind=ArtifactKind.ENDPOINT_RECEIPT,
        )
        await self.artifact_store.put_once(evidence_bytes, ref=artifact_ref)
        receipt_id = canonical_sha256(
            {
                "evidence_sha256": evidence_sha256,
                "artifact_uri": artifact_ref.uri,
                "submitting_principal": submitting_principal,
            }
        )
        receipt = EndpointReceipt(
            schema_version=(
                "endpoint-receipt.v2"
                if submission.capture_state is CaptureState.RECEIVED
                else "endpoint-receipt.v1"
            ),
            receipt_id=receipt_id,
            baseline_id=submission.baseline_id,
            production_link_id=submission.production_link_id,
            scheduler_job_id=submission.scheduler_job_id,
            scheduler_job_title=submission.scheduler_job_title,
            site_id=submission.site_id,
            queue_name=submission.queue_name,
            approved_baseline_brf_sha256=submission.approved_baseline_brf_sha256,
            endpoint_received_sha256=submission.endpoint_received_sha256,
            capture_manifest_sha256=submission.capture_manifest_sha256,
            terminal_event_sha256=submission.terminal_event_sha256,
            capture_acceptance_sha256=submission.capture_acceptance_sha256,
            accepted_event_sha256=submission.accepted_event_sha256,
            previous_event_sha256=submission.previous_event_sha256,
            capture_state=submission.capture_state,
            evidence_timestamp=submission.evidence_timestamp,
            verified_at=now,
            submitting_principal=submitting_principal,
            idempotency_key_sha256=idempotency_key_sha256,
            expected_baseline_state_version=submission.expected_baseline_state_version,
            baseline_state_version=submission.expected_baseline_state_version + 1,
            artifact_uri=artifact_ref.uri,
        )
        try:
            commit = await self.ledger.confirm_endpoint_receipt(
                proposed_receipt=receipt,
                expected_state_version=submission.expected_baseline_state_version,
                idempotency_key=submission.idempotency_key,
            )
        except BaselineStateConflictError as exc:
            raise EndpointEvidenceConflict(
                ProductionLinkBlockingReason.STALE_STATE_VERSION
            ) from exc
        return EndpointReceiptResult(commit.baseline, commit.receipt, commit.duplicate)


def historical_correction_idempotency_key(
    *, baseline_id: str, production_link_id: str, expected_state_version: int
) -> str:
    return canonical_sha256(
        {
            "scope": "historical-production-link-correction",
            "baseline_id": baseline_id,
            "production_link_id": production_link_id,
            "expected_state_version": expected_state_version,
        }
    )


class HistoricalLinkCorrectionWorkflow:
    def __init__(
        self,
        *,
        ledger: EndpointReceiptLedger,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock or (lambda: datetime.now(UTC))

    async def correct(
        self,
        *,
        baseline_id: str,
        production_link_id: str,
        expected_state_version: int,
        prior_report_id: str | None,
        idempotency_key: str,
        submitting_principal: str,
    ) -> RegisteredBaseline:
        if idempotency_key != historical_correction_idempotency_key(
            baseline_id=baseline_id,
            production_link_id=production_link_id,
            expected_state_version=expected_state_version,
        ):
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.IDEMPOTENCY_KEY_MISMATCH)
        baseline = await self.ledger.get_baseline(baseline_id)
        if baseline is None:
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.BASELINE_NOT_FOUND)
        link = await self.ledger.get_production_link(baseline_id)
        if link is None or link.link_id != production_link_id:
            raise EndpointEvidenceRejected(ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH)
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        correction = BaselineLinkCorrection(
            correction_id=canonical_sha256(
                {
                    "baseline_id": baseline_id,
                    "production_link_id": production_link_id,
                    "expected_state_version": expected_state_version,
                    "prior_report_id": prior_report_id,
                }
            ),
            baseline_id=baseline_id,
            production_link_id=production_link_id,
            expected_baseline_state_version=expected_state_version,
            baseline_state_version=expected_state_version + 1,
            prior_report_id=prior_report_id,
            corrected_at=now,
            submitting_principal=submitting_principal,
            idempotency_key_sha256=canonical_sha256(
                {"scope": "historical-production-link-correction", "key": idempotency_key}
            ),
        )
        try:
            return await self.ledger.correct_historical_production_link(
                proposed_correction=correction,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
            )
        except BaselineStateConflictError as exc:
            raise EndpointEvidenceConflict(
                ProductionLinkBlockingReason.STALE_STATE_VERSION
            ) from exc
