"""Firestore Gate 0 ledger with pure transactions and a transactional outbox."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, TypeVar, cast

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.errors import (
    BaselineStateConflictError,
    IncidentReviewEvidenceError,
    IncidentReviewPrerequisiteError,
    IncidentReviewStateConflictError,
)
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactOrigin,
    ArtifactRef,
    BaselineLinkCorrection,
    BaselineProductionLink,
    BaselineStatus,
    BlockingReason,
    CandidateApprovalInvalidation,
    ContainmentConfirmation,
    ContainmentConfirmationProposal,
    DriveChangeBatch,
    EndpointReceipt,
    HumanTimelineEventKind,
    Incident,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    IncidentTimelineEvent,
    IncidentWorkflowStage,
    JobState,
    OperatorAttestation,
    ProfessionalDisposition,
    ProofRecord,
    RegisteredBaseline,
    ReplacementObservationLink,
    ReplacementObservationLinkProposal,
    SemanticAssessment,
    SiteObservation,
    TruthBasis,
)
from braille_errata_relay.domain.recommendation import SiteEvidenceStatus, assess_site_evidence
from braille_errata_relay.domain.state_machine import (
    IllegalStateTransition,
    StaleStateVersion,
    require_report_precedes_action,
    transition,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
T = TypeVar("T")


class LedgerIntegrityError(RuntimeError):
    pass


class StaleCursorError(LedgerIntegrityError):
    pass


class SemanticLeaseError(LedgerIntegrityError):
    pass


class AutomationLeaseError(LedgerIntegrityError):
    pass


class AutomationCycleClaimStatus(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True)
class AutomationCycleClaim:
    cycle_key: str
    cycle_id: str
    status: AutomationCycleClaimStatus
    lease_token: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class AutomationCycleLedgerState:
    """Sanitized durable state for the one automatic reconciliation lane."""

    cycle_key: str
    state: str
    active_cycle_id: str | None
    lease_expires_at: datetime | None
    last_execution_cycle_id: str | None
    last_outcome: str | None
    last_result: dict[str, object] | None
    last_error_code: str | None
    last_completed_at: datetime | None


class SemanticClaimStatus(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"


@dataclass(frozen=True)
class SemanticExecutionClaim:
    execution_key: str
    status: SemanticClaimStatus
    lease_token: str | None = None
    assessment: SemanticAssessment | None = None


@dataclass(frozen=True)
class StoredSourceRevision:
    revision_id: str
    source_sha256: str
    file_id: str
    mime_type: str
    provider_version: str
    fetched_at: datetime
    artifact: ArtifactRef


@dataclass(frozen=True)
class ObservationCommitResult:
    observation_id: str
    duplicate: bool


@dataclass(frozen=True)
class IncidentCheckpointCommit:
    checkpoint: IncidentCheckpoint
    duplicate: bool


@dataclass(frozen=True)
class ProductionLinkCommit:
    baseline: RegisteredBaseline
    link: BaselineProductionLink
    duplicate: bool


@dataclass(frozen=True)
class EndpointReceiptCommit:
    baseline: RegisteredBaseline
    receipt: EndpointReceipt
    duplicate: bool


@dataclass(frozen=True)
class EndpointVerificationClaim:
    verified_at: datetime
    duplicate: bool


@dataclass(frozen=True)
class ProfessionalDispositionCommit:
    state: IncidentReviewState
    disposition: ProfessionalDisposition
    duplicate: bool


@dataclass(frozen=True)
class OperatorAttestationCommit:
    state: IncidentReviewState
    attestation: OperatorAttestation
    duplicate: bool


@dataclass(frozen=True)
class ContainmentConfirmationCommit:
    state: IncidentReviewState
    confirmation: ContainmentConfirmation
    duplicate: bool


@dataclass(frozen=True)
class ProofRecordCommit:
    state: IncidentReviewState
    proof: ProofRecord
    duplicate: bool


@dataclass(frozen=True)
class ReplacementObservationLinkCommit:
    state: IncidentReviewState
    link: ReplacementObservationLink
    duplicate: bool


@dataclass(frozen=True)
class CandidateSynchronizationCommit:
    state: IncidentReviewState
    invalidation: CandidateApprovalInvalidation | None
    changed: bool


@dataclass(frozen=True)
class OutboxLease:
    message_id: str
    kind: str
    payload: dict[str, object]
    lease_token: str
    attempts: int


@dataclass(frozen=True)
class CursorState:
    raw_token: str
    token_sha256: str
    state_version: int


@dataclass(frozen=True)
class ChangeCommitResult:
    receipt_id: str
    execution_id: str
    outbox_ids: tuple[str, ...]
    final_cursor_sha256: str
    duplicate: bool
    new_outbox_ids: tuple[str, ...] = ()


TransactionRunner = Callable[[Callable[[Any], T]], T]


def _token_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: str, *, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")


_SOURCE_REVISION_IDENTITY_FIELDS = (
    "revision_id",
    "provider",
    "file_id",
    "mime_type",
    "provider_version",
    "source_sha256",
    "byte_length",
    "artifact_sha256",
    "artifact_uri",
)


def _same_source_revision_identity(
    existing: Mapping[str, object], proposed: Mapping[str, object]
) -> bool:
    """Compare immutable source lineage without a transient fetch timestamp.

    A Drive retry can re-observe the same provider revision at a later wall
    time.  The first durable observation keeps its original ``fetched_at``;
    the immutable source, provider, and artifact fields must still match
    exactly before the transaction can attach a missing receipt or outbox row.
    """

    return all(
        existing.get(field) == proposed.get(field) for field in _SOURCE_REVISION_IDENTITY_FIELDS
    )


class FirestoreGate0Ledger:
    def __init__(
        self,
        *,
        project_id: str,
        database: str = "(default)",
        client: firestore.Client | None = None,
        transaction_runner: TransactionRunner[Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client or firestore.Client(project=project_id, database=database)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transaction_runner = transaction_runner or self._google_transaction

    def _google_transaction(self, callback: Callable[[Any], T]) -> T:
        @firestore.transactional
        def invoke(transaction: Any) -> T:
            return callback(transaction)

        return cast(T, invoke(self.client.transaction()))

    def _document(self, collection: str, document_id: str) -> Any:
        return self.client.collection(collection).document(document_id)

    @staticmethod
    def _snapshot_data(snapshot: Any) -> dict[str, object] | None:
        if not getattr(snapshot, "exists", False):
            return None
        value = snapshot.to_dict()
        if not isinstance(value, dict):
            raise LedgerIntegrityError("Firestore document is not an object")
        return value

    def _initialize_cursor_sync(self, principal_scope_hash: str, raw_token: str) -> CursorState:
        _require_sha256(principal_scope_hash, label="principal scope hash")
        if not raw_token:
            raise ValueError("Drive cursor token is required")
        cursor_ref = self._document("drive_cursors", principal_scope_hash)

        def operation(transaction: Any) -> CursorState:
            existing = self._snapshot_data(cursor_ref.get(transaction=transaction))
            if existing is not None:
                stored = existing.get("raw_token")
                stored_hash = existing.get("token_sha256")
                version = existing.get("state_version")
                if not isinstance(stored, str) or not isinstance(stored_hash, str):
                    raise LedgerIntegrityError("stored Drive cursor is malformed")
                if not isinstance(version, int):
                    raise LedgerIntegrityError("stored Drive cursor version is malformed")
                if _token_sha256(stored) != stored_hash:
                    raise LedgerIntegrityError("stored Drive cursor hash is inconsistent")
                return CursorState(stored, stored_hash, version)
            now = self._clock()
            payload = {
                "principal_scope_hash": principal_scope_hash,
                "raw_token": raw_token,
                "token_sha256": _token_sha256(raw_token),
                "state_version": 0,
                "updated_at": now,
            }
            transaction.create(cursor_ref, payload)
            return CursorState(raw_token, _token_sha256(raw_token), 0)

        return cast(CursorState, self._transaction_runner(operation))

    async def initialize_cursor(self, principal_scope_hash: str, raw_token: str) -> CursorState:
        return await asyncio.to_thread(
            self._initialize_cursor_sync,
            principal_scope_hash,
            raw_token,
        )

    def _get_cursor_sync(self, principal_scope_hash: str) -> CursorState | None:
        _require_sha256(principal_scope_hash, label="principal scope hash")
        data = self._snapshot_data(self._document("drive_cursors", principal_scope_hash).get())
        if data is None:
            return None
        raw_token = data.get("raw_token")
        token_sha = data.get("token_sha256")
        version = data.get("state_version")
        if not isinstance(raw_token, str) or not isinstance(token_sha, str):
            raise LedgerIntegrityError("stored Drive cursor is malformed")
        if not isinstance(version, int) or _token_sha256(raw_token) != token_sha:
            raise LedgerIntegrityError("stored Drive cursor integrity check failed")
        return CursorState(raw_token, token_sha, version)

    async def get_cursor(self, principal_scope_hash: str) -> CursorState | None:
        return await asyncio.to_thread(self._get_cursor_sync, principal_scope_hash)

    def _get_source_revision_sync(self, revision_id: str) -> StoredSourceRevision | None:
        if not revision_id:
            raise ValueError("source revision ID is required")
        data = self._snapshot_data(self._document("source_revisions", revision_id).get())
        if data is None:
            return None
        required_strings = {
            name: data.get(name)
            for name in (
                "revision_id",
                "source_sha256",
                "file_id",
                "mime_type",
                "provider_version",
                "artifact_uri",
            )
        }
        if any(not isinstance(value, str) for value in required_strings.values()):
            raise LedgerIntegrityError("stored source revision is malformed")
        fetched_at = data.get("fetched_at")
        byte_length = data.get("byte_length")
        if not isinstance(fetched_at, datetime) or not isinstance(byte_length, int):
            raise LedgerIntegrityError("stored source revision metadata is malformed")
        source_sha256 = cast(str, required_strings["source_sha256"])
        _require_sha256(source_sha256, label="stored source SHA-256")
        return StoredSourceRevision(
            revision_id=cast(str, required_strings["revision_id"]),
            source_sha256=source_sha256,
            file_id=cast(str, required_strings["file_id"]),
            mime_type=cast(str, required_strings["mime_type"]),
            provider_version=cast(str, required_strings["provider_version"]),
            fetched_at=fetched_at,
            artifact=ArtifactRef(
                sha256=source_sha256,
                kind=ArtifactKind.SOURCE_SNAPSHOT,
                byte_length=byte_length,
                uri=cast(str, required_strings["artifact_uri"]),
            ),
        )

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None:
        return await asyncio.to_thread(self._get_source_revision_sync, revision_id)

    async def register_baseline(self, baseline: RegisteredBaseline) -> bool:
        return await asyncio.to_thread(
            self._create_once_sync,
            "baselines",
            baseline.baseline.baseline_id,
            {"record": baseline.model_dump(mode="json")},
        )

    def _get_baseline_sync(self, baseline_id: str) -> RegisteredBaseline | None:
        _require_sha256(baseline_id, label="baseline ID")
        data = self._snapshot_data(self._document("baselines", baseline_id).get())
        if data is None:
            return None
        payload = data.get("record")
        if not isinstance(payload, dict):
            raise LedgerIntegrityError("stored baseline is malformed")
        return RegisteredBaseline.model_validate(payload)

    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None:
        return await asyncio.to_thread(self._get_baseline_sync, baseline_id)

    def _find_baseline_for_file_sync(self, file_id: str) -> RegisteredBaseline | None:
        if not file_id:
            raise ValueError("Drive file ID is required")
        query = (
            self.client.collection("baselines")
            .where(filter=FieldFilter("record.baseline.source_file_id", "==", file_id))
            .limit(2)
        )
        records = list(query.stream())
        if len(records) > 1:
            raise LedgerIntegrityError("multiple baselines match the source file")
        if not records:
            return None
        data = self._snapshot_data(records[0])
        if data is None or not isinstance(data.get("record"), dict):
            raise LedgerIntegrityError("stored baseline is malformed")
        return RegisteredBaseline.model_validate(data["record"])

    async def find_baseline_for_file(self, file_id: str) -> RegisteredBaseline | None:
        return await asyncio.to_thread(self._find_baseline_for_file_sync, file_id)

    def _link_baseline_production_sync(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        _require_sha256(proposed_link.baseline_id, label="baseline ID")
        _require_sha256(proposed_link.link_id, label="production link ID")
        if expected_state_version < 0 or not idempotency_key:
            raise ValueError("production link transition parameters are invalid")
        idempotency_key_sha256 = canonical_sha256(
            {"scope": "baseline-production-link", "key": idempotency_key}
        )
        if proposed_link.idempotency_key_sha256 != idempotency_key_sha256:
            raise ValueError("production link idempotency hash is inconsistent")
        request_body = {
            "baseline_id": proposed_link.baseline_id,
            "scheduler_job_id": proposed_link.scheduler_job_id,
            "expected_state_version": expected_state_version,
            "idempotency_key_sha256": idempotency_key_sha256,
        }
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {
                "scope": "baseline-production-link-request",
                "idempotency_key_sha256": idempotency_key_sha256,
            }
        )
        baseline_ref = self._document("baselines", proposed_link.baseline_id)
        link_ref = self._document("baseline_production_links", proposed_link.baseline_id)
        record_ref = self._document("baseline_production_link_records", proposed_link.link_id)
        request_ref = self._document("baseline_production_link_requests", request_id)

        def operation(transaction: Any) -> ProductionLinkCommit:
            baseline_data = self._snapshot_data(baseline_ref.get(transaction=transaction))
            link_data = self._snapshot_data(link_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise LedgerIntegrityError("baseline is missing or malformed")
            current = RegisteredBaseline.model_validate(baseline_data["record"])
            if request_data is not None:
                if (
                    request_data.get("request_sha256") != request_sha256
                    or request_data.get("link_id") != proposed_link.link_id
                ):
                    raise LedgerIntegrityError("production link idempotency key conflicts")
                replay_data = record_data if record_data is not None else link_data
                if replay_data is None or not isinstance(replay_data.get("record"), dict):
                    raise LedgerIntegrityError("production link receipt has no immutable link")
                persisted_link = BaselineProductionLink.model_validate(replay_data["record"])
                return ProductionLinkCommit(current, persisted_link, True)
            if current.baseline.state_version != expected_state_version:
                raise BaselineStateConflictError("baseline state version is stale")
            if current.baseline.status is not BaselineStatus.AWAITING_PRODUCTION_LINK:
                raise BaselineStateConflictError("baseline is not awaiting a production link")
            if link_data is not None:
                raise LedgerIntegrityError("baseline already has an unreceipted production link")
            if record_data is not None:
                raise LedgerIntegrityError("production link ID already exists")
            if proposed_link.baseline_state_version != expected_state_version + 1:
                raise ValueError("production link target version is invalid")
            if proposed_link.baseline_brf_sha256 != current.baseline.approved_brf_sha256:
                raise LedgerIntegrityError("production link changed baseline artifact lineage")
            updated_baseline = current.model_copy(
                update={
                    "baseline": current.baseline.model_copy(
                        update={
                            "scheduler_job_id": proposed_link.scheduler_job_id,
                            "scheduler_job_title": proposed_link.scheduler_job_title,
                            "status": BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
                            "state_version": expected_state_version + 1,
                        }
                    )
                }
            )
            baseline_body = {"record": updated_baseline.model_dump(mode="json")}
            link_body = {"record": proposed_link.model_dump(mode="json", exclude_none=True)}
            now = self._clock()
            transaction.set(
                baseline_ref,
                {
                    **baseline_data,
                    **baseline_body,
                    "payload_sha256": canonical_sha256(baseline_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                link_ref,
                {
                    **link_body,
                    "payload_sha256": canonical_sha256(link_body),
                    "created_at": now,
                },
            )
            transaction.create(
                record_ref,
                {
                    **link_body,
                    "payload_sha256": canonical_sha256(link_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "link_id": proposed_link.link_id,
                    "created_at": now,
                },
            )
            return ProductionLinkCommit(updated_baseline, proposed_link, False)

        return cast(ProductionLinkCommit, self._transaction_runner(operation))

    async def link_baseline_production(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        return await asyncio.to_thread(
            self._link_baseline_production_sync,
            proposed_link=proposed_link,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _get_production_link_by_idempotency_sync(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit | None:
        _require_sha256(baseline_id, label="baseline ID")
        if scheduler_job_id < 1 or expected_state_version < 0 or not idempotency_key:
            raise ValueError("production link request identity is invalid")
        idempotency_key_sha256 = canonical_sha256(
            {"scope": "baseline-production-link", "key": idempotency_key}
        )
        request_body = {
            "baseline_id": baseline_id,
            "scheduler_job_id": scheduler_job_id,
            "expected_state_version": expected_state_version,
            "idempotency_key_sha256": idempotency_key_sha256,
        }
        request_id = canonical_sha256(
            {
                "scope": "baseline-production-link-request",
                "idempotency_key_sha256": idempotency_key_sha256,
            }
        )
        request_data = self._snapshot_data(
            self._document("baseline_production_link_requests", request_id).get()
        )
        if request_data is None:
            return None
        if request_data.get("request_sha256") != canonical_sha256(request_body):
            raise LedgerIntegrityError("production link idempotency key conflicts")
        baseline = self._get_baseline_sync(baseline_id)
        link_data = self._snapshot_data(
            self._document("baseline_production_links", baseline_id).get()
        )
        if baseline is None or link_data is None or not isinstance(link_data.get("record"), dict):
            raise LedgerIntegrityError("production link receipt lineage is incomplete")
        link = BaselineProductionLink.model_validate(link_data["record"])
        if request_data.get("link_id") != link.link_id:
            raise LedgerIntegrityError("production link receipt identity conflicts")
        return ProductionLinkCommit(baseline, link, True)

    async def get_production_link_by_idempotency(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit | None:
        return await asyncio.to_thread(
            self._get_production_link_by_idempotency_sync,
            baseline_id=baseline_id,
            scheduler_job_id=scheduler_job_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _supersede_baseline_production_sync(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        _require_sha256(proposed_link.baseline_id, label="baseline ID")
        _require_sha256(proposed_link.link_id, label="production link ID")
        if (
            proposed_link.schema_version != "baseline-production-link.v3"
            or proposed_link.supersedes_production_link_id is None
            or expected_state_version < 1
            or not idempotency_key
        ):
            raise ValueError("production link supersession parameters are invalid")
        key_hash = canonical_sha256(
            {"scope": "baseline-production-link-supersession", "key": idempotency_key}
        )
        if proposed_link.idempotency_key_sha256 != key_hash:
            raise ValueError("production link supersession idempotency hash is inconsistent")
        request_body = {
            "baseline_id": proposed_link.baseline_id,
            "supersedes_production_link_id": proposed_link.supersedes_production_link_id,
            "scheduler_job_id": proposed_link.scheduler_job_id,
            "expected_state_version": expected_state_version,
            "idempotency_key_sha256": key_hash,
        }
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {
                "scope": "baseline-production-link-supersession-request",
                "idempotency_key_sha256": key_hash,
            }
        )
        baseline_ref = self._document("baselines", proposed_link.baseline_id)
        link_ref = self._document("baseline_production_links", proposed_link.baseline_id)
        prior_record_ref = self._document(
            "baseline_production_link_records", proposed_link.supersedes_production_link_id
        )
        next_record_ref = self._document("baseline_production_link_records", proposed_link.link_id)
        confirmation_ref = self._document(
            "baseline_endpoint_confirmations", proposed_link.baseline_id
        )
        request_ref = self._document("baseline_production_link_supersession_requests", request_id)

        def operation(transaction: Any) -> ProductionLinkCommit:
            baseline_data = self._snapshot_data(baseline_ref.get(transaction=transaction))
            link_data = self._snapshot_data(link_ref.get(transaction=transaction))
            prior_record_data = self._snapshot_data(prior_record_ref.get(transaction=transaction))
            next_record_data = self._snapshot_data(next_record_ref.get(transaction=transaction))
            confirmation_data = self._snapshot_data(confirmation_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise LedgerIntegrityError("baseline is missing or malformed")
            current = RegisteredBaseline.model_validate(baseline_data["record"])
            if request_data is not None:
                if (
                    request_data.get("request_sha256") != request_sha256
                    or request_data.get("link_id") != proposed_link.link_id
                ):
                    raise LedgerIntegrityError(
                        "production link supersession idempotency key conflicts"
                    )
                if next_record_data is None or not isinstance(next_record_data.get("record"), dict):
                    raise LedgerIntegrityError(
                        "production link supersession has no immutable record"
                    )
                return ProductionLinkCommit(
                    current,
                    BaselineProductionLink.model_validate(next_record_data["record"]),
                    True,
                )
            if current.baseline.state_version != expected_state_version:
                raise BaselineStateConflictError("baseline state version is stale")
            if current.baseline.status is not BaselineStatus.PROVISIONAL_PRODUCTION_LINK:
                raise BaselineStateConflictError("baseline production link is not provisional")
            if link_data is None or not isinstance(link_data.get("record"), dict):
                raise LedgerIntegrityError("provisional baseline has no active production link")
            active_link = BaselineProductionLink.model_validate(link_data["record"])
            if active_link.link_id != proposed_link.supersedes_production_link_id:
                raise LedgerIntegrityError("production link supersession targets a non-active link")
            if (
                active_link.scheduler_job_id == proposed_link.scheduler_job_id
                or current.baseline.scheduler_job_id != active_link.scheduler_job_id
                or current.baseline.scheduler_job_title != active_link.scheduler_job_title
            ):
                raise LedgerIntegrityError(
                    "production link supersession changes no active job lineage"
                )
            if confirmation_data is not None:
                raise LedgerIntegrityError(
                    "active production link already has endpoint confirmation"
                )
            if proposed_link.baseline_state_version != expected_state_version + 1:
                raise ValueError("production link supersession target version is invalid")
            if proposed_link.baseline_brf_sha256 != current.baseline.approved_brf_sha256:
                raise LedgerIntegrityError(
                    "production link supersession changed baseline artifact lineage"
                )
            if next_record_data is not None:
                raise LedgerIntegrityError("superseding production link ID already exists")
            active_body = {"record": active_link.model_dump(mode="json", exclude_none=True)}
            if prior_record_data is not None and prior_record_data.get(
                "payload_sha256"
            ) != canonical_sha256(active_body):
                raise LedgerIntegrityError("active production link archive conflicts")
            updated_baseline = current.model_copy(
                update={
                    "baseline": current.baseline.model_copy(
                        update={
                            "scheduler_job_id": proposed_link.scheduler_job_id,
                            "scheduler_job_title": proposed_link.scheduler_job_title,
                            "status": BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
                            "state_version": expected_state_version + 1,
                        }
                    )
                }
            )
            baseline_body = {"record": updated_baseline.model_dump(mode="json")}
            next_link_body = {"record": proposed_link.model_dump(mode="json", exclude_none=True)}
            now = self._clock()
            transaction.set(
                baseline_ref,
                {
                    **baseline_data,
                    **baseline_body,
                    "payload_sha256": canonical_sha256(baseline_body),
                    "updated_at": now,
                },
            )
            transaction.set(
                link_ref,
                {
                    **next_link_body,
                    "payload_sha256": canonical_sha256(next_link_body),
                    "updated_at": now,
                },
            )
            if prior_record_data is None:
                transaction.create(
                    prior_record_ref,
                    {
                        **active_body,
                        "payload_sha256": canonical_sha256(active_body),
                        "created_at": now,
                    },
                )
            transaction.create(
                next_record_ref,
                {
                    **next_link_body,
                    "payload_sha256": canonical_sha256(next_link_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "link_id": proposed_link.link_id,
                    "created_at": now,
                },
            )
            return ProductionLinkCommit(updated_baseline, proposed_link, False)

        return cast(ProductionLinkCommit, self._transaction_runner(operation))

    async def supersede_baseline_production(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        return await asyncio.to_thread(
            self._supersede_baseline_production_sync,
            proposed_link=proposed_link,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _get_production_link_supersession_by_idempotency_sync(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit | None:
        _require_sha256(baseline_id, label="baseline ID")
        if scheduler_job_id < 1 or expected_state_version < 1 or not idempotency_key:
            raise ValueError("production link supersession request identity is invalid")
        key_hash = canonical_sha256(
            {"scope": "baseline-production-link-supersession", "key": idempotency_key}
        )
        request_id = canonical_sha256(
            {
                "scope": "baseline-production-link-supersession-request",
                "idempotency_key_sha256": key_hash,
            }
        )
        request_data = self._snapshot_data(
            self._document("baseline_production_link_supersession_requests", request_id).get()
        )
        if request_data is None:
            return None
        if (
            request_data.get("baseline_id") != baseline_id
            or request_data.get("scheduler_job_id") != scheduler_job_id
            or request_data.get("expected_state_version") != expected_state_version
            or request_data.get("idempotency_key_sha256") != key_hash
        ):
            raise LedgerIntegrityError("production link supersession idempotency key conflicts")
        link_id = request_data.get("link_id")
        if not isinstance(link_id, str):
            raise LedgerIntegrityError("production link supersession request is malformed")
        record_data = self._snapshot_data(
            self._document("baseline_production_link_records", link_id).get()
        )
        baseline = self._get_baseline_sync(baseline_id)
        if (
            baseline is None
            or record_data is None
            or not isinstance(record_data.get("record"), dict)
        ):
            raise LedgerIntegrityError("production link supersession replay lineage is incomplete")
        return ProductionLinkCommit(
            baseline,
            BaselineProductionLink.model_validate(record_data["record"]),
            True,
        )

    async def get_production_link_supersession_by_idempotency(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit | None:
        return await asyncio.to_thread(
            self._get_production_link_supersession_by_idempotency_sync,
            baseline_id=baseline_id,
            scheduler_job_id=scheduler_job_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _get_production_link_sync(self, baseline_id: str) -> BaselineProductionLink | None:
        _require_sha256(baseline_id, label="baseline ID")
        data = self._snapshot_data(self._document("baseline_production_links", baseline_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("stored production link is malformed")
        return BaselineProductionLink.model_validate(record)

    async def get_production_link(self, baseline_id: str) -> BaselineProductionLink | None:
        return await asyncio.to_thread(self._get_production_link_sync, baseline_id)

    def _get_endpoint_receipt_for_link_sync(
        self,
        *,
        baseline_id: str,
        production_link_id: str,
    ) -> EndpointReceipt | None:
        _require_sha256(baseline_id, label="baseline ID")
        _require_sha256(production_link_id, label="production link ID")
        pointer = self._snapshot_data(
            self._document("baseline_endpoint_confirmations", baseline_id).get()
        )
        if pointer is None:
            return None
        receipt_id = pointer.get("receipt_id")
        if not isinstance(receipt_id, str):
            raise LedgerIntegrityError("endpoint confirmation pointer is malformed")
        receipt_data = self._snapshot_data(self._document("endpoint_receipts", receipt_id).get())
        if receipt_data is None or not isinstance(receipt_data.get("record"), dict):
            raise LedgerIntegrityError("endpoint confirmation receipt is missing")
        receipt = EndpointReceipt.model_validate(receipt_data["record"])
        return receipt if receipt.production_link_id == production_link_id else None

    async def get_endpoint_receipt_for_link(
        self,
        *,
        baseline_id: str,
        production_link_id: str,
    ) -> EndpointReceipt | None:
        return await asyncio.to_thread(
            self._get_endpoint_receipt_for_link_sync,
            baseline_id=baseline_id,
            production_link_id=production_link_id,
        )

    def _confirm_endpoint_receipt_sync(
        self,
        *,
        proposed_receipt: EndpointReceipt,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit:
        _require_sha256(proposed_receipt.baseline_id, label="baseline ID")
        _require_sha256(proposed_receipt.receipt_id, label="endpoint receipt ID")
        if expected_state_version < 1 or not idempotency_key:
            raise ValueError("endpoint receipt transition parameters are invalid")
        key_hash = canonical_sha256({"scope": "endpoint-receipt", "key": idempotency_key})
        if proposed_receipt.idempotency_key_sha256 != key_hash:
            raise ValueError("endpoint receipt idempotency hash is inconsistent")
        request_body = {
            "baseline_id": proposed_receipt.baseline_id,
            "production_link_id": proposed_receipt.production_link_id,
            "receipt_id": proposed_receipt.receipt_id,
            "expected_state_version": expected_state_version,
            "idempotency_key_sha256": key_hash,
        }
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {"scope": "endpoint-receipt-request", "idempotency_key_sha256": key_hash}
        )
        baseline_ref = self._document("baselines", proposed_receipt.baseline_id)
        link_ref = self._document("baseline_production_links", proposed_receipt.baseline_id)
        pointer_ref = self._document(
            "baseline_endpoint_confirmations", proposed_receipt.baseline_id
        )
        receipt_ref = self._document("endpoint_receipts", proposed_receipt.receipt_id)
        request_ref = self._document("endpoint_receipt_requests", request_id)

        def operation(transaction: Any) -> EndpointReceiptCommit:
            baseline_data = self._snapshot_data(baseline_ref.get(transaction=transaction))
            link_data = self._snapshot_data(link_ref.get(transaction=transaction))
            pointer_data = self._snapshot_data(pointer_ref.get(transaction=transaction))
            receipt_data = self._snapshot_data(receipt_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise LedgerIntegrityError("baseline is missing or malformed")
            current = RegisteredBaseline.model_validate(baseline_data["record"])
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise LedgerIntegrityError("endpoint receipt idempotency key conflicts")
                if receipt_data is None or not isinstance(receipt_data.get("record"), dict):
                    raise LedgerIntegrityError("endpoint receipt request has no receipt")
                persisted = EndpointReceipt.model_validate(receipt_data["record"])
                return EndpointReceiptCommit(current, persisted, True)
            if current.baseline.state_version != expected_state_version:
                raise BaselineStateConflictError("baseline state version is stale")
            if current.baseline.status is not BaselineStatus.PROVISIONAL_PRODUCTION_LINK:
                raise BaselineStateConflictError("baseline production link is not provisional")
            if link_data is None or not isinstance(link_data.get("record"), dict):
                raise LedgerIntegrityError("provisional baseline has no immutable production link")
            link = BaselineProductionLink.model_validate(link_data["record"])
            if (
                link.link_id != proposed_receipt.production_link_id
                or link.scheduler_job_id != proposed_receipt.scheduler_job_id
                or link.scheduler_job_title != proposed_receipt.scheduler_job_title
                or link.queue_name != proposed_receipt.queue_name
                or link.site_id != proposed_receipt.site_id
                or link.baseline_brf_sha256 != proposed_receipt.approved_baseline_brf_sha256
            ):
                raise LedgerIntegrityError("endpoint receipt conflicts with production link")
            if pointer_data is not None or receipt_data is not None:
                raise LedgerIntegrityError("baseline already has conflicting endpoint evidence")
            if proposed_receipt.baseline_state_version != expected_state_version + 1:
                raise ValueError("endpoint receipt target version is invalid")
            updated = current.model_copy(
                update={
                    "baseline": current.baseline.model_copy(
                        update={
                            "status": BaselineStatus.PRODUCTION_LINK_VERIFIED,
                            "state_version": expected_state_version + 1,
                        }
                    )
                }
            )
            baseline_body = {"record": updated.model_dump(mode="json")}
            receipt_body = {
                "record": proposed_receipt.model_dump(
                    mode="json",
                    exclude_none=proposed_receipt.schema_version == "endpoint-receipt.v1",
                )
            }
            now = self._clock()
            transaction.set(
                baseline_ref,
                {
                    **baseline_data,
                    **baseline_body,
                    "payload_sha256": canonical_sha256(baseline_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                receipt_ref,
                {
                    **receipt_body,
                    "payload_sha256": canonical_sha256(receipt_body),
                    "created_at": now,
                },
            )
            transaction.create(
                pointer_ref,
                {
                    "baseline_id": proposed_receipt.baseline_id,
                    "production_link_id": proposed_receipt.production_link_id,
                    "receipt_id": proposed_receipt.receipt_id,
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "created_at": now,
                },
            )
            return EndpointReceiptCommit(updated, proposed_receipt, False)

        return cast(EndpointReceiptCommit, self._transaction_runner(operation))

    async def confirm_endpoint_receipt(
        self,
        *,
        proposed_receipt: EndpointReceipt,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit:
        return await asyncio.to_thread(
            self._confirm_endpoint_receipt_sync,
            proposed_receipt=proposed_receipt,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _get_endpoint_receipt_by_idempotency_sync(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit | None:
        _require_sha256(baseline_id, label="baseline ID")
        key_hash = canonical_sha256({"scope": "endpoint-receipt", "key": idempotency_key})
        request_id = canonical_sha256(
            {"scope": "endpoint-receipt-request", "idempotency_key_sha256": key_hash}
        )
        request = self._snapshot_data(self._document("endpoint_receipt_requests", request_id).get())
        if request is None:
            return None
        if (
            request.get("baseline_id") != baseline_id
            or request.get("expected_state_version") != expected_state_version
            or request.get("idempotency_key_sha256") != key_hash
        ):
            raise LedgerIntegrityError("endpoint receipt idempotency key conflicts")
        receipt_id = request.get("receipt_id")
        if not isinstance(receipt_id, str):
            raise LedgerIntegrityError("endpoint receipt request identity is malformed")
        receipt_data = self._snapshot_data(self._document("endpoint_receipts", receipt_id).get())
        baseline = self._get_baseline_sync(baseline_id)
        if (
            baseline is None
            or receipt_data is None
            or not isinstance(receipt_data.get("record"), dict)
        ):
            raise LedgerIntegrityError("endpoint receipt replay lineage is incomplete")
        return EndpointReceiptCommit(
            baseline,
            EndpointReceipt.model_validate(receipt_data["record"]),
            True,
        )

    async def get_endpoint_receipt_by_idempotency(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit | None:
        return await asyncio.to_thread(
            self._get_endpoint_receipt_by_idempotency_sync,
            baseline_id=baseline_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _allocate_endpoint_verification_timestamp_sync(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
        evidence_identity_sha256: str,
        submitting_principal: str,
    ) -> EndpointVerificationClaim:
        _require_sha256(baseline_id, label="baseline ID")
        _require_sha256(evidence_identity_sha256, label="endpoint evidence identity")
        key_hash = canonical_sha256({"scope": "endpoint-receipt", "key": idempotency_key})
        claim_id = canonical_sha256(
            {"scope": "endpoint-receipt-verification-claim", "key": key_hash}
        )
        identity = {
            "baseline_id": baseline_id,
            "expected_state_version": expected_state_version,
            "idempotency_key_sha256": key_hash,
            "evidence_identity_sha256": evidence_identity_sha256,
            "submitting_principal": submitting_principal,
        }
        identity_sha256 = canonical_sha256(identity)
        ref = self._document("endpoint_receipt_verification_claims", claim_id)

        def operation(transaction: Any) -> EndpointVerificationClaim:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is not None:
                if existing.get("identity_sha256") != identity_sha256:
                    raise LedgerIntegrityError("endpoint receipt idempotency key conflicts")
                verified_at = existing.get("verified_at")
                if not isinstance(verified_at, datetime):
                    raise LedgerIntegrityError("endpoint verification claim is malformed")
                return EndpointVerificationClaim(verified_at=verified_at, duplicate=True)
            verified_at = self._clock()
            transaction.create(
                ref,
                {
                    **identity,
                    "identity_sha256": identity_sha256,
                    "verified_at": verified_at,
                },
            )
            return EndpointVerificationClaim(verified_at=verified_at, duplicate=False)

        return cast(EndpointVerificationClaim, self._transaction_runner(operation))

    async def allocate_endpoint_verification_timestamp(
        self,
        *,
        baseline_id: str,
        expected_state_version: int,
        idempotency_key: str,
        evidence_identity_sha256: str,
        submitting_principal: str,
    ) -> EndpointVerificationClaim:
        return await asyncio.to_thread(
            self._allocate_endpoint_verification_timestamp_sync,
            baseline_id=baseline_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
            evidence_identity_sha256=evidence_identity_sha256,
            submitting_principal=submitting_principal,
        )

    def _correct_historical_production_link_sync(
        self,
        *,
        proposed_correction: BaselineLinkCorrection,
        expected_state_version: int,
        idempotency_key: str,
    ) -> RegisteredBaseline:
        key_hash = canonical_sha256(
            {"scope": "historical-production-link-correction", "key": idempotency_key}
        )
        if proposed_correction.idempotency_key_sha256 != key_hash:
            raise ValueError("historical correction idempotency hash is inconsistent")
        request_id = canonical_sha256(
            {"scope": "historical-production-link-correction-request", "key": key_hash}
        )
        request_sha256 = canonical_sha256(
            {
                "baseline_id": proposed_correction.baseline_id,
                "correction_id": proposed_correction.correction_id,
                "expected_state_version": expected_state_version,
                "idempotency_key_sha256": key_hash,
            }
        )
        baseline_ref = self._document("baselines", proposed_correction.baseline_id)
        pointer_ref = self._document(
            "baseline_endpoint_confirmations", proposed_correction.baseline_id
        )
        correction_ref = self._document(
            "baseline_link_corrections", proposed_correction.correction_id
        )
        request_ref = self._document("baseline_link_correction_requests", request_id)

        def operation(transaction: Any) -> RegisteredBaseline:
            baseline_data = self._snapshot_data(baseline_ref.get(transaction=transaction))
            pointer_data = self._snapshot_data(pointer_ref.get(transaction=transaction))
            correction_data = self._snapshot_data(correction_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise LedgerIntegrityError("baseline is missing or malformed")
            current = RegisteredBaseline.model_validate(baseline_data["record"])
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise LedgerIntegrityError("historical correction idempotency key conflicts")
                if correction_data is None:
                    raise LedgerIntegrityError("historical correction request has no audit record")
                return current
            if current.baseline.state_version != expected_state_version:
                raise BaselineStateConflictError("baseline state version is stale")
            if current.baseline.status is not BaselineStatus.PRODUCTION_LINK_VERIFIED:
                raise BaselineStateConflictError("baseline is not historically verified")
            if pointer_data is not None:
                raise LedgerIntegrityError("baseline already has endpoint confirmation")
            updated = current.model_copy(
                update={
                    "baseline": current.baseline.model_copy(
                        update={
                            "status": BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
                            "state_version": expected_state_version + 1,
                        }
                    )
                }
            )
            baseline_body = {"record": updated.model_dump(mode="json")}
            correction_body = {"record": proposed_correction.model_dump(mode="json")}
            now = self._clock()
            transaction.set(
                baseline_ref,
                {
                    **baseline_data,
                    **baseline_body,
                    "payload_sha256": canonical_sha256(baseline_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                correction_ref,
                {
                    **correction_body,
                    "payload_sha256": canonical_sha256(correction_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    "request_sha256": request_sha256,
                    "correction_id": proposed_correction.correction_id,
                    "created_at": now,
                },
            )
            return updated

        return cast(RegisteredBaseline, self._transaction_runner(operation))

    async def correct_historical_production_link(
        self,
        *,
        proposed_correction: BaselineLinkCorrection,
        expected_state_version: int,
        idempotency_key: str,
    ) -> RegisteredBaseline:
        return await asyncio.to_thread(
            self._correct_historical_production_link_sync,
            proposed_correction=proposed_correction,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )

    def _claim_incident_sync(
        self,
        checkpoint: IncidentCheckpoint,
    ) -> IncidentCheckpointCommit:
        if checkpoint.stage is not IncidentWorkflowStage.DETECTED or checkpoint.state_version != 0:
            raise ValueError("new incident checkpoint must start at DETECTED version zero")
        ref = self._document("incidents", checkpoint.incident_id)
        record = checkpoint.model_dump(mode="json")

        def operation(transaction: Any) -> IncidentCheckpointCommit:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is not None:
                existing_record = existing.get("record")
                if not isinstance(existing_record, dict):
                    raise LedgerIntegrityError("stored incident is malformed")
                parsed = IncidentCheckpoint.model_validate(existing_record)
                if (
                    parsed.baseline_id != checkpoint.baseline_id
                    # A Drive provider version is immutable observation
                    # lineage, not incident identity.  Re-saving identical
                    # authoritative bytes must reuse the incident keyed by
                    # its baseline, content digest, and production lineage.
                    or parsed.new_source_sha256 != checkpoint.new_source_sha256
                    or parsed.production_job_lineage_id != checkpoint.production_job_lineage_id
                ):
                    raise LedgerIntegrityError("incident identity conflicts with durable data")
                return IncidentCheckpointCommit(parsed, True)
            transaction.create(
                ref,
                {
                    "record": record,
                    "identity_sha256": canonical_sha256(
                        {
                            "baseline_id": checkpoint.baseline_id,
                            "new_source_sha256": checkpoint.new_source_sha256,
                            "production_job_lineage_id": checkpoint.production_job_lineage_id,
                        }
                    ),
                    "created_at": self._clock(),
                },
            )
            return IncidentCheckpointCommit(checkpoint, False)

        return cast(IncidentCheckpointCommit, self._transaction_runner(operation))

    async def claim_incident(
        self,
        checkpoint: IncidentCheckpoint,
    ) -> IncidentCheckpointCommit:
        return await asyncio.to_thread(self._claim_incident_sync, checkpoint)

    def _get_incident_checkpoint_sync(self, incident_id: str) -> IncidentCheckpoint | None:
        _require_sha256(incident_id, label="incident ID")
        data = self._snapshot_data(self._document("incidents", incident_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("stored incident is malformed")
        return IncidentCheckpoint.model_validate(record)

    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None:
        return await asyncio.to_thread(self._get_incident_checkpoint_sync, incident_id)

    def _list_incident_checkpoints_sync(self) -> tuple[IncidentCheckpoint, ...]:
        query = self.client.collection("incidents").order_by("record.updated_at").limit(200)
        checkpoints: list[IncidentCheckpoint] = []
        for snapshot in query.stream():
            data = self._snapshot_data(snapshot)
            if data is None or not isinstance(data.get("record"), dict):
                raise LedgerIntegrityError("stored incident is malformed")
            checkpoint = IncidentCheckpoint.model_validate(data["record"])
            if checkpoint.report is not None and checkpoint.disposition_packet is not None:
                checkpoints.append(checkpoint)
        return tuple(
            sorted(checkpoints, key=lambda checkpoint: checkpoint.updated_at, reverse=True)
        )

    async def list_incident_checkpoints(self) -> tuple[IncidentCheckpoint, ...]:
        return await asyncio.to_thread(self._list_incident_checkpoints_sync)

    def _initial_incident_review_state(
        self,
        checkpoint: IncidentCheckpoint,
        *,
        now: datetime,
    ) -> IncidentReviewState:
        if (
            checkpoint.report is None
            or checkpoint.disposition_packet is None
            or checkpoint.report_ready_at is None
            or checkpoint.candidate_brf is None
        ):
            raise IncidentReviewPrerequisiteError(
                "report and disposition packet must exist before human review"
            )
        if checkpoint.stage not in {
            IncidentWorkflowStage.REPORT_READY,
            IncidentWorkflowStage.NEEDS_REVIEW,
        }:
            raise IncidentReviewPrerequisiteError("incident is not ready for professional review")
        return IncidentReviewState(
            incident_id=checkpoint.incident_id,
            baseline_id=checkpoint.baseline_id,
            state=(
                IncidentState.NEEDS_REVIEW
                if checkpoint.stage is IncidentWorkflowStage.NEEDS_REVIEW
                else IncidentState.REPORT_READY
            ),
            state_version=0,
            report_ready_at=checkpoint.report_ready_at,
            current_candidate_sha256=checkpoint.candidate_brf.sha256,
            blocking_reason=checkpoint.blocking_reason,
            updated_at=now,
        )

    def _read_review_state(
        self,
        head_data: dict[str, object] | None,
        checkpoint: IncidentCheckpoint,
        *,
        now: datetime,
    ) -> IncidentReviewState:
        if head_data is None:
            return self._initial_incident_review_state(checkpoint, now=now)
        record = head_data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("incident review head is malformed")
        state = IncidentReviewState.model_validate(record)
        if state.baseline_id != checkpoint.baseline_id:
            raise LedgerIntegrityError("incident review head baseline lineage conflicts")
        return state

    def _record_professional_disposition_sync(
        self,
        *,
        proposed_record: ProfessionalDisposition,
    ) -> ProfessionalDispositionCommit:
        _require_sha256(proposed_record.incident_id, label="incident ID")
        _require_sha256(proposed_record.record_id, label="professional disposition ID")
        key_hash = canonical_sha256(
            {"scope": "professional-disposition", "key": proposed_record.idempotency_key}
        )
        request_body = {
            "incident_id": proposed_record.incident_id,
            "record_id": proposed_record.record_id,
            "decision": proposed_record.decision.value,
            "selected_role": proposed_record.selected_role,
            "expected_state_version": proposed_record.expected_state_version,
            "note": proposed_record.note,
            "actor_principal": proposed_record.actor_principal,
            "idempotency_key_sha256": key_hash,
        }
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {"scope": "professional-disposition-request", "idempotency_key_sha256": key_hash}
        )
        incident_ref = self._document("incidents", proposed_record.incident_id)
        head_ref = self._document("incident_review_heads", proposed_record.incident_id)
        record_ref = self._document("professional_dispositions", proposed_record.record_id)
        event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.PROFESSIONAL_DISPOSITION.value,
                "incident_id": proposed_record.incident_id,
                "record_id": proposed_record.record_id,
            }
        )
        event_ref = self._document("incident_timeline_events", event_id)
        request_ref = self._document("professional_disposition_requests", request_id)

        def operation(transaction: Any) -> ProfessionalDispositionCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            head_data = self._snapshot_data(head_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            event_data = self._snapshot_data(event_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(head_data, checkpoint, now=now)
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise IncidentReviewStateConflictError(
                        "professional disposition idempotency key conflicts"
                    )
                if record_data is None or not isinstance(record_data.get("record"), dict):
                    raise LedgerIntegrityError("professional disposition replay record is missing")
                return ProfessionalDispositionCommit(
                    state,
                    ProfessionalDisposition.model_validate(record_data["record"]),
                    True,
                )
            if record_data is not None or event_data is not None:
                raise LedgerIntegrityError("professional disposition identity already exists")
            record = proposed_record.model_copy(update={"recorded_at": now})
            try:
                require_report_precedes_action(state.report_ready_at, record.recorded_at)
                updated_incident = transition(
                    Incident(
                        incident_id=state.incident_id,
                        baseline_id=state.baseline_id,
                        state=state.state,
                        state_version=state.state_version,
                        report_ready_at=state.report_ready_at,
                        current_candidate_sha256=state.current_candidate_sha256,
                        blocking_reason=state.blocking_reason,
                        last_attributable_evidence_id=state.last_attributable_evidence_id,
                    ),
                    IncidentState(record.decision.value),
                    expected_state_version=record.expected_state_version,
                    at=record.recorded_at,
                    evidence_id=record.record_id,
                )
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": updated_incident.state,
                    "state_version": updated_incident.state_version,
                    "last_attributable_evidence_id": updated_incident.last_attributable_evidence_id,
                    "updated_at": now,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.PROFESSIONAL_DISPOSITION,
                record_id=record.record_id,
                state_version=updated.state_version,
                actor_principal=record.actor_principal,
                recorded_at=record.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            record_body = {"record": record.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                record_ref,
                {
                    **record_body,
                    "payload_sha256": canonical_sha256(record_body),
                    "created_at": now,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "created_at": now,
                },
            )
            return ProfessionalDispositionCommit(updated, record, False)

        return cast(ProfessionalDispositionCommit, self._transaction_runner(operation))

    async def record_professional_disposition(
        self,
        *,
        proposed_record: ProfessionalDisposition,
    ) -> ProfessionalDispositionCommit:
        return await asyncio.to_thread(
            self._record_professional_disposition_sync,
            proposed_record=proposed_record,
        )

    def _record_operator_attestation_sync(
        self,
        *,
        proposed_record: OperatorAttestation,
    ) -> OperatorAttestationCommit:
        _require_sha256(proposed_record.incident_id, label="incident ID")
        _require_sha256(proposed_record.record_id, label="operator attestation ID")
        key_hash = canonical_sha256(
            {"scope": "operator-attestation", "key": proposed_record.idempotency_key}
        )
        request_body = {
            "incident_id": proposed_record.incident_id,
            "record_id": proposed_record.record_id,
            "attestation_type": proposed_record.attestation_type.value,
            "truth_basis": proposed_record.truth_basis.value,
            "selected_role": proposed_record.selected_role,
            "expected_state_version": proposed_record.expected_state_version,
            "note": proposed_record.note,
            "actor_principal": proposed_record.actor_principal,
            "idempotency_key_sha256": key_hash,
        }
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {"scope": "operator-attestation-request", "idempotency_key_sha256": key_hash}
        )
        incident_ref = self._document("incidents", proposed_record.incident_id)
        head_ref = self._document("incident_review_heads", proposed_record.incident_id)
        record_ref = self._document("operator_attestations", proposed_record.record_id)
        event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.OPERATOR_ATTESTATION.value,
                "incident_id": proposed_record.incident_id,
                "record_id": proposed_record.record_id,
            }
        )
        event_ref = self._document("incident_timeline_events", event_id)
        request_ref = self._document("operator_attestation_requests", request_id)

        def operation(transaction: Any) -> OperatorAttestationCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            head_data = self._snapshot_data(head_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            event_data = self._snapshot_data(event_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(head_data, checkpoint, now=now)
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise IncidentReviewStateConflictError(
                        "operator attestation idempotency key conflicts"
                    )
                if record_data is None or not isinstance(record_data.get("record"), dict):
                    raise LedgerIntegrityError("operator attestation replay record is missing")
                return OperatorAttestationCommit(
                    state,
                    OperatorAttestation.model_validate(record_data["record"]),
                    True,
                )
            if record_data is not None or event_data is not None:
                raise LedgerIntegrityError("operator attestation identity already exists")
            record = proposed_record.model_copy(update={"recorded_at": now})
            try:
                require_report_precedes_action(state.report_ready_at, record.recorded_at)
                if state.state is IncidentState.HALT_REQUESTED:
                    updated_incident = transition(
                        Incident(
                            incident_id=state.incident_id,
                            baseline_id=state.baseline_id,
                            state=state.state,
                            state_version=state.state_version,
                            report_ready_at=state.report_ready_at,
                            current_candidate_sha256=state.current_candidate_sha256,
                            blocking_reason=state.blocking_reason,
                            last_attributable_evidence_id=state.last_attributable_evidence_id,
                        ),
                        IncidentState.CONTAINMENT_IN_PROGRESS,
                        expected_state_version=record.expected_state_version,
                        evidence_id=record.record_id,
                    )
                elif state.state is IncidentState.CONTAINMENT_IN_PROGRESS:
                    if state.state_version != record.expected_state_version:
                        raise StaleStateVersion("incident review state is stale")
                    updated_incident = Incident(
                        incident_id=state.incident_id,
                        baseline_id=state.baseline_id,
                        state=state.state,
                        state_version=state.state_version + 1,
                        report_ready_at=state.report_ready_at,
                        current_candidate_sha256=state.current_candidate_sha256,
                        blocking_reason=state.blocking_reason,
                        last_attributable_evidence_id=record.record_id,
                    )
                else:
                    raise IncidentReviewPrerequisiteError(
                        "an operator containment attestation requires an earlier HALT_REQUESTED"
                    )
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": updated_incident.state,
                    "state_version": updated_incident.state_version,
                    "last_attributable_evidence_id": updated_incident.last_attributable_evidence_id,
                    "updated_at": now,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.OPERATOR_ATTESTATION,
                record_id=record.record_id,
                state_version=updated.state_version,
                actor_principal=record.actor_principal,
                recorded_at=record.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            record_body = {"record": record.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                record_ref,
                {
                    **record_body,
                    "payload_sha256": canonical_sha256(record_body),
                    "created_at": now,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "created_at": now,
                },
            )
            return OperatorAttestationCommit(updated, record, False)

        return cast(OperatorAttestationCommit, self._transaction_runner(operation))

    async def record_operator_attestation(
        self,
        *,
        proposed_record: OperatorAttestation,
    ) -> OperatorAttestationCommit:
        return await asyncio.to_thread(
            self._record_operator_attestation_sync,
            proposed_record=proposed_record,
        )

    @staticmethod
    def _incident_from_review_state(state: IncidentReviewState) -> Incident:
        return Incident(
            incident_id=state.incident_id,
            baseline_id=state.baseline_id,
            state=state.state,
            state_version=state.state_version,
            report_ready_at=state.report_ready_at,
            current_candidate_sha256=state.current_candidate_sha256,
            blocking_reason=state.blocking_reason,
            last_attributable_evidence_id=state.last_attributable_evidence_id,
        )

    @staticmethod
    def _containment_evidence_reason(status: SiteEvidenceStatus) -> BlockingReason:
        if status is SiteEvidenceStatus.STALE:
            return BlockingReason.CONTAINMENT_EVIDENCE_STALE
        if status is SiteEvidenceStatus.AMBIGUOUS:
            return BlockingReason.CONTAINMENT_EVIDENCE_AMBIGUOUS
        if status is SiteEvidenceStatus.MISSING:
            return BlockingReason.CONTAINMENT_EVIDENCE_MISSING
        return BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH

    def _record_containment_confirmation_sync(
        self,
        *,
        proposal: ContainmentConfirmationProposal,
        max_observation_age_seconds: float,
    ) -> ContainmentConfirmationCommit:
        if max_observation_age_seconds < 0:
            raise ValueError("containment observation maximum age cannot be negative")
        _require_sha256(proposal.incident_id, label="incident ID")
        _require_sha256(proposal.halt_disposition_record_id, label="halt disposition record ID")
        _require_sha256(proposal.site_observation_id, label="site observation ID")
        _require_sha256(
            proposal.physical_output_isolation_attestation_id,
            label="physical-output isolation attestation record ID",
        )
        key_hash = canonical_sha256(
            {"scope": "containment-confirmation", "key": proposal.idempotency_key}
        )
        request_body = {
            "incident_id": proposal.incident_id,
            "halt_disposition_record_id": proposal.halt_disposition_record_id,
            "site_observation_id": proposal.site_observation_id,
            "physical_output_isolation_attestation_id": (
                proposal.physical_output_isolation_attestation_id
            ),
            "selected_role": proposal.selected_role,
            "expected_state_version": proposal.expected_state_version,
            "note": proposal.note,
            "actor_principal": proposal.actor_principal,
            "idempotency_key_sha256": key_hash,
        }
        request_sha256 = canonical_sha256(request_body)
        record_id = canonical_sha256(
            {"kind": "containment-confirmation", "request_sha256": request_sha256}
        )
        request_id = canonical_sha256(
            {"scope": "containment-confirmation-request", "idempotency_key_sha256": key_hash}
        )
        incident_ref = self._document("incidents", proposal.incident_id)
        review_head_ref = self._document("incident_review_heads", proposal.incident_id)
        halt_ref = self._document("professional_dispositions", proposal.halt_disposition_record_id)
        attestation_ref = self._document(
            "operator_attestations", proposal.physical_output_isolation_attestation_id
        )
        observation_ref = self._document("site_observations", proposal.site_observation_id)
        record_ref = self._document("containment_confirmations", record_id)
        event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.CONTAINMENT_CONFIRMATION.value,
                "incident_id": proposal.incident_id,
                "record_id": record_id,
            }
        )
        event_ref = self._document("incident_timeline_events", event_id)
        request_ref = self._document("containment_confirmation_requests", request_id)

        def operation(transaction: Any) -> ContainmentConfirmationCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            review_head_data = self._snapshot_data(review_head_ref.get(transaction=transaction))
            halt_data = self._snapshot_data(halt_ref.get(transaction=transaction))
            attestation_data = self._snapshot_data(attestation_ref.get(transaction=transaction))
            observation_data = self._snapshot_data(observation_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            event_data = self._snapshot_data(event_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(review_head_data, checkpoint, now=now)
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise IncidentReviewStateConflictError(
                        "containment confirmation idempotency key conflicts"
                    )
                if record_data is None or not isinstance(record_data.get("record"), dict):
                    raise LedgerIntegrityError("containment confirmation replay record is missing")
                return ContainmentConfirmationCommit(
                    state,
                    ContainmentConfirmation.model_validate(record_data["record"]),
                    True,
                )
            if record_data is not None or event_data is not None:
                raise LedgerIntegrityError("containment confirmation identity already exists")
            if state.state_version != proposal.expected_state_version:
                raise IncidentReviewStateConflictError("incident review state is stale")
            if state.state is not IncidentState.CONTAINMENT_IN_PROGRESS:
                raise IncidentReviewPrerequisiteError(
                    "containment confirmation requires CONTAINMENT_IN_PROGRESS"
                )
            baseline_data = self._snapshot_data(
                self._document("baselines", checkpoint.baseline_id).get(transaction=transaction)
            )
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "production baseline lineage is unavailable",
                )
            baseline = RegisteredBaseline.model_validate(baseline_data["record"])
            production = baseline.baseline
            if (
                production.scheduler_job_id is None
                or production.scheduler_job_title is None
                or not production.queue_name
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "expected production queue identity is incomplete",
                )
            production_link_data = self._snapshot_data(
                self._document("baseline_production_links", checkpoint.baseline_id).get(
                    transaction=transaction
                )
            )
            if production_link_data is None or not isinstance(
                production_link_data.get("record"), dict
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "current production-link lineage is unavailable",
                )
            production_link = BaselineProductionLink.model_validate(production_link_data["record"])
            if (
                production_link.scheduler_job_id != production.scheduler_job_id
                or production_link.queue_name != production.queue_name
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.JOB_LINEAGE_MISMATCH.value,
                    "production-link queue identity conflicts with the baseline",
                )
            if halt_data is None or not isinstance(halt_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISSING.value,
                    "the selected halt disposition record is unavailable",
                )
            halt = ProfessionalDisposition.model_validate(halt_data["record"])
            if (
                halt.incident_id != proposal.incident_id
                or halt.record_id != proposal.halt_disposition_record_id
                or halt.decision.value != "HALT_REQUESTED"
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH.value,
                    "the selected disposition is not an attributable HALT_REQUESTED record",
                )
            if attestation_data is None or not isinstance(attestation_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PHYSICAL_OUTPUT_ISOLATION_REQUIRED.value,
                    "the selected physical-output isolation attestation is unavailable",
                )
            attestation = OperatorAttestation.model_validate(attestation_data["record"])
            if (
                attestation.incident_id != proposal.incident_id
                or attestation.record_id != proposal.physical_output_isolation_attestation_id
                or attestation.attestation_type.value != "PHYSICAL_OUTPUT_ISOLATED"
                or attestation.recorded_at <= halt.recorded_at
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PHYSICAL_OUTPUT_ISOLATION_REQUIRED.value,
                    "a distinct post-halt physical-output-isolated attestation is required",
                )
            if (
                production.artifact_origin is ArtifactOrigin.DEMO_GENERATED_FIXTURE
                and attestation.truth_basis is not TruthBasis.SIMULATED_DEMO
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH.value,
                    "demo fixture containment requires a SIMULATED_DEMO isolation label",
                )
            if observation_data is None or not isinstance(observation_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISSING.value,
                    "the selected read-only site observation is unavailable",
                )
            observation = SiteObservation.model_validate(observation_data["record"])
            if (
                observation.site_id != production.site_id
                or observation.queue_name != production.queue_name
                or observation.bridge_id != production_link.bridge_id
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH.value,
                    "the selected observation does not match the linked site, queue, and bridge",
                )
            observation_head_id = canonical_sha256(
                {
                    "site_id": production.site_id,
                    "bridge_id": production_link.bridge_id,
                    "queue_name": production.queue_name,
                }
            )
            observation_head_data = self._snapshot_data(
                self._document("site_observation_heads", observation_head_id).get(
                    transaction=transaction
                )
            )
            # Only the currently admitted canonical cloud head can support a
            # confirmation. An unadmitted or superseded bridge outbox payload
            # therefore cannot be treated as a containment fact.
            if (
                observation_head_data is None
                or observation_head_data.get("observation_id") != proposal.site_observation_id
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.OBSERVATION_OUTBOX_CONTRADICTION.value,
                    "the selected observation is not the current admitted bridge observation",
                )
            evidence = assess_site_evidence(
                site_observation=observation,
                expected_job_id=production.scheduler_job_id,
                expected_queue_name=production.queue_name,
                expected_job_title=production.scheduler_job_title,
                expected_artifact_sha256=production.approved_brf_sha256,
                now=now,
                max_age_seconds=max_observation_age_seconds,
            )
            if evidence.status is not SiteEvidenceStatus.FRESH:
                reason = self._containment_evidence_reason(evidence.status)
                raise IncidentReviewEvidenceError(
                    reason.value,
                    "the selected post-disposition site observation is not current exact evidence",
                )
            if observation.observed_at <= halt.recorded_at:
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH.value,
                    "the queue observation predates the HALT_REQUESTED disposition",
                )
            if evidence.job_state not in {
                JobState.CANCELED,
                JobState.ABORTED,
                JobState.COMPLETED,
            }:
                raise IncidentReviewEvidenceError(
                    BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH.value,
                    "the scheduler observation is not terminal containment evidence",
                )
            record = ContainmentConfirmation(
                record_id=record_id,
                incident_id=proposal.incident_id,
                halt_disposition_record_id=proposal.halt_disposition_record_id,
                site_observation_id=proposal.site_observation_id,
                queue_name=production.queue_name,
                scheduler_job_id=production.scheduler_job_id,
                observed_job_state=evidence.job_state,
                observed_at=observation.observed_at,
                physical_output_isolation_attestation_id=(
                    proposal.physical_output_isolation_attestation_id
                ),
                selected_role=proposal.selected_role,
                expected_state_version=proposal.expected_state_version,
                idempotency_key=proposal.idempotency_key,
                note=proposal.note,
                actor_principal=proposal.actor_principal,
                recorded_at=now,
            )
            try:
                contained = transition(
                    self._incident_from_review_state(state),
                    IncidentState.CONTAINED_BY_HUMAN,
                    expected_state_version=proposal.expected_state_version,
                    evidence_id=record.record_id,
                )
                proof_ready = transition(
                    contained,
                    IncidentState.AWAITING_PROOF,
                    expected_state_version=contained.state_version,
                    evidence_id=record.record_id,
                )
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": proof_ready.state,
                    "state_version": proof_ready.state_version,
                    "blocking_reason": (
                        None
                        if state.blocking_reason is BlockingReason.SITE_OBSERVATION_STALE
                        else state.blocking_reason
                    ),
                    "last_attributable_evidence_id": record.record_id,
                    "updated_at": now,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=proposal.incident_id,
                kind=HumanTimelineEventKind.CONTAINMENT_CONFIRMATION,
                record_id=record.record_id,
                state_version=updated.state_version,
                actor_principal=record.actor_principal,
                recorded_at=record.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            record_body = {"record": record.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                review_head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                record_ref,
                {
                    **record_body,
                    "payload_sha256": canonical_sha256(record_body),
                    "created_at": now,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "created_at": now,
                },
            )
            return ContainmentConfirmationCommit(updated, record, False)

        return cast(ContainmentConfirmationCommit, self._transaction_runner(operation))

    async def record_containment_confirmation(
        self,
        *,
        proposal: ContainmentConfirmationProposal,
        max_observation_age_seconds: float = 15.0,
    ) -> ContainmentConfirmationCommit:
        return await asyncio.to_thread(
            self._record_containment_confirmation_sync,
            proposal=proposal,
            max_observation_age_seconds=max_observation_age_seconds,
        )

    def _synchronize_incident_review_candidate_sync(
        self,
        *,
        incident_id: str,
    ) -> CandidateSynchronizationCommit:
        _require_sha256(incident_id, label="incident ID")
        incident_ref = self._document("incidents", incident_id)
        review_head_ref = self._document("incident_review_heads", incident_id)

        def operation(transaction: Any) -> CandidateSynchronizationCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            review_head_data = self._snapshot_data(review_head_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(review_head_data, checkpoint, now=now)
            if checkpoint.candidate_brf is None:
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_PROVENANCE_MISSING.value,
                    "incident has no current candidate artifact",
                )
            if state.current_candidate_sha256 == checkpoint.candidate_brf.sha256:
                return CandidateSynchronizationCommit(state, None, False)
            if state.state not in {
                IncidentState.PROOF_APPROVED,
                IncidentState.AWAITING_REPLACEMENT,
                IncidentState.PROOF_REJECTED,
            }:
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_PROVENANCE_MISMATCH.value,
                    "the candidate changed outside a proof-state invalidation boundary",
                )
            invalidation_id = canonical_sha256(
                {
                    "kind": "candidate-approval-invalidation",
                    "incident_id": incident_id,
                    "prior_candidate_sha256": state.current_candidate_sha256,
                    "current_candidate_sha256": checkpoint.candidate_brf.sha256,
                    "prior_state": state.state.value,
                    "state_version": state.state_version,
                }
            )
            invalidation_ref = self._document(
                "candidate_approval_invalidations",
                invalidation_id,
            )
            event_id = canonical_sha256(
                {
                    "kind": HumanTimelineEventKind.CANDIDATE_APPROVAL_INVALIDATED.value,
                    "incident_id": incident_id,
                    "record_id": invalidation_id,
                }
            )
            event_ref = self._document("incident_timeline_events", event_id)
            existing_invalidation = self._snapshot_data(
                invalidation_ref.get(transaction=transaction)
            )
            existing_event = self._snapshot_data(event_ref.get(transaction=transaction))
            if existing_invalidation is not None or existing_event is not None:
                raise LedgerIntegrityError("candidate invalidation identity already exists")
            invalidation = CandidateApprovalInvalidation(
                record_id=invalidation_id,
                incident_id=incident_id,
                prior_candidate_sha256=state.current_candidate_sha256,
                current_candidate_sha256=checkpoint.candidate_brf.sha256,
                prior_state=state.state,
                recorded_at=now,
            )
            try:
                invalidated = transition(
                    self._incident_from_review_state(state),
                    IncidentState.AWAITING_PROOF,
                    expected_state_version=state.state_version,
                    evidence_id=invalidation.record_id,
                )
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": invalidated.state,
                    "state_version": invalidated.state_version,
                    "current_candidate_sha256": checkpoint.candidate_brf.sha256,
                    "blocking_reason": None,
                    "last_attributable_evidence_id": invalidation.record_id,
                    "updated_at": now,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=incident_id,
                kind=HumanTimelineEventKind.CANDIDATE_APPROVAL_INVALIDATED,
                record_id=invalidation.record_id,
                state_version=updated.state_version,
                actor_principal=invalidation.actor_principal,
                recorded_at=invalidation.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            invalidation_body = {"record": invalidation.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                review_head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                invalidation_ref,
                {
                    **invalidation_body,
                    "payload_sha256": canonical_sha256(invalidation_body),
                    "created_at": now,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": now,
                },
            )
            return CandidateSynchronizationCommit(updated, invalidation, True)

        return cast(CandidateSynchronizationCommit, self._transaction_runner(operation))

    async def synchronize_incident_review_candidate(
        self,
        *,
        incident_id: str,
    ) -> CandidateSynchronizationCommit:
        return await asyncio.to_thread(
            self._synchronize_incident_review_candidate_sync,
            incident_id=incident_id,
        )

    def _record_proof_sync(
        self,
        *,
        proposed_record: ProofRecord,
    ) -> ProofRecordCommit:
        _require_sha256(proposed_record.incident_id, label="incident ID")
        _require_sha256(proposed_record.record_id, label="proof record ID")
        key_hash = canonical_sha256(
            {"scope": "proof-record", "key": proposed_record.idempotency_key}
        )
        request_body = proposed_record.model_dump(
            mode="json",
            exclude={"record_id", "recorded_at", "idempotency_key"},
        )
        request_body["idempotency_key_sha256"] = key_hash
        request_sha256 = canonical_sha256(request_body)
        request_id = canonical_sha256(
            {"scope": "proof-record-request", "idempotency_key_sha256": key_hash}
        )
        incident_ref = self._document("incidents", proposed_record.incident_id)
        review_head_ref = self._document("incident_review_heads", proposed_record.incident_id)
        record_ref = self._document("proof_records", proposed_record.record_id)
        event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.PROOF_RECORD.value,
                "incident_id": proposed_record.incident_id,
                "record_id": proposed_record.record_id,
            }
        )
        event_ref = self._document("incident_timeline_events", event_id)
        request_ref = self._document("proof_record_requests", request_id)

        def operation(transaction: Any) -> ProofRecordCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            review_head_data = self._snapshot_data(review_head_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            event_data = self._snapshot_data(event_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(review_head_data, checkpoint, now=now)
            if request_data is not None:
                if request_data.get("request_sha256") != request_sha256:
                    raise IncidentReviewStateConflictError("proof record idempotency key conflicts")
                if record_data is None or not isinstance(record_data.get("record"), dict):
                    raise LedgerIntegrityError("proof record replay record is missing")
                return ProofRecordCommit(
                    state,
                    ProofRecord.model_validate(record_data["record"]),
                    True,
                )
            if record_data is not None or event_data is not None:
                raise LedgerIntegrityError("proof record identity already exists")
            if state.state_version != proposed_record.expected_state_version:
                raise IncidentReviewStateConflictError("incident review state is stale")
            if state.state is not IncidentState.AWAITING_PROOF:
                raise IncidentReviewEvidenceError(
                    BlockingReason.PROOF_NOT_ELIGIBLE.value,
                    "proof recording requires verified containment and AWAITING_PROOF",
                )
            if state.blocking_reason is not None:
                raise IncidentReviewEvidenceError(
                    state.blocking_reason.value,
                    "a visible incident blocking reason prevents proof approval",
                )
            if (
                checkpoint.candidate_brf is None
                or checkpoint.candidate_manifest is None
                or checkpoint.candidate_brf.sha256 != proposed_record.candidate_sha256
                or checkpoint.candidate_manifest.sha256 != proposed_record.manifest_sha256
                or checkpoint.new_source_revision_id != proposed_record.source_revision_id
                or checkpoint.new_source_sha256 != proposed_record.source_sha256
                or state.current_candidate_sha256 != proposed_record.candidate_sha256
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_PROVENANCE_MISMATCH.value,
                    "proof record does not bind the current candidate and source lineage",
                )
            baseline_data = self._snapshot_data(
                self._document("baselines", checkpoint.baseline_id).get(transaction=transaction)
            )
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_PROVENANCE_MISSING.value,
                    "baseline profile lineage is unavailable",
                )
            baseline = RegisteredBaseline.model_validate(baseline_data["record"])
            if (
                baseline.baseline.translation_profile_sha256
                != proposed_record.translation_profile_sha256
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_PROVENANCE_MISMATCH.value,
                    "proof profile identity conflicts with the approved baseline lineage",
                )
            if (
                proposed_record.decision.value == "APPROVED_FOR_HUMAN_SUBMISSION"
                and proposed_record.visual_only_uncertainty
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PROOF_REVIEW_REQUIRED.value,
                    "visual-only uncertainty cannot approve a candidate",
                )
            record = proposed_record.model_copy(update={"recorded_at": now})
            try:
                if record.decision.value == "APPROVED_FOR_HUMAN_SUBMISSION":
                    approved = transition(
                        self._incident_from_review_state(state),
                        IncidentState.PROOF_APPROVED,
                        expected_state_version=record.expected_state_version,
                        evidence_id=record.record_id,
                    )
                    updated_incident = transition(
                        approved,
                        IncidentState.AWAITING_REPLACEMENT,
                        expected_state_version=approved.state_version,
                        evidence_id=record.record_id,
                    )
                    blocking_reason: BlockingReason | None = None
                else:
                    updated_incident = transition(
                        self._incident_from_review_state(state),
                        IncidentState.PROOF_REJECTED,
                        expected_state_version=record.expected_state_version,
                        evidence_id=record.record_id,
                    )
                    blocking_reason = BlockingReason.PROOF_REJECTED
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": updated_incident.state,
                    "state_version": updated_incident.state_version,
                    "blocking_reason": blocking_reason,
                    "last_attributable_evidence_id": record.record_id,
                    "updated_at": now,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.PROOF_RECORD,
                record_id=record.record_id,
                state_version=updated.state_version,
                actor_principal=record.actor_principal,
                recorded_at=record.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            record_body = {"record": record.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                review_head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": now,
                },
            )
            transaction.create(
                record_ref,
                {
                    **record_body,
                    "payload_sha256": canonical_sha256(record_body),
                    "created_at": now,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": now,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "created_at": now,
                },
            )
            return ProofRecordCommit(updated, record, False)

        return cast(ProofRecordCommit, self._transaction_runner(operation))

    async def record_proof(self, *, proposed_record: ProofRecord) -> ProofRecordCommit:
        return await asyncio.to_thread(self._record_proof_sync, proposed_record=proposed_record)

    def _record_replacement_observation_link_sync(
        self,
        *,
        proposal: ReplacementObservationLinkProposal,
        max_observation_age_seconds: float,
    ) -> ReplacementObservationLinkCommit:
        """Append a human-owned correlation to one fresh canonical observation.

        The transaction repeats every material lineage check.  It intentionally
        records no endpoint capture or physical-output conclusion and invokes
        no scheduler or device client.
        """

        if max_observation_age_seconds < 0:
            raise ValueError("replacement observation maximum age cannot be negative")
        for value, label in (
            (proposal.incident_id, "incident ID"),
            (proposal.candidate_sha256, "candidate SHA-256"),
            (proposal.candidate_manifest_sha256, "candidate manifest SHA-256"),
            (proposal.proof_record_id, "proof record ID"),
            (proposal.site_observation_id, "site observation ID"),
        ):
            _require_sha256(value, label=label)
        key_hash = canonical_sha256(
            {"scope": "replacement-observation-link", "key": proposal.idempotency_key}
        )
        request_body = proposal.model_dump(
            mode="json",
            exclude={"idempotency_key"},
        )
        request_body["idempotency_key_sha256"] = key_hash
        request_sha256 = canonical_sha256(request_body)
        record_id = canonical_sha256(
            {"kind": "replacement-observation-link", "request_sha256": request_sha256}
        )
        request_id = canonical_sha256(
            {
                "scope": "replacement-observation-link-request",
                "idempotency_key_sha256": key_hash,
            }
        )
        incident_ref = self._document("incidents", proposal.incident_id)
        review_head_ref = self._document("incident_review_heads", proposal.incident_id)
        proof_ref = self._document("proof_records", proposal.proof_record_id)
        proof_event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.PROOF_RECORD.value,
                "incident_id": proposal.incident_id,
                "record_id": proposal.proof_record_id,
            }
        )
        proof_event_ref = self._document("incident_timeline_events", proof_event_id)
        observation_ref = self._document("site_observations", proposal.site_observation_id)
        record_ref = self._document("replacement_observation_links", record_id)
        replacement_head_ref = self._document("replacement_observation_heads", proposal.incident_id)
        event_id = canonical_sha256(
            {
                "kind": HumanTimelineEventKind.REPLACEMENT_OBSERVATION_LINK.value,
                "incident_id": proposal.incident_id,
                "record_id": record_id,
            }
        )
        event_ref = self._document("incident_timeline_events", event_id)
        request_ref = self._document("replacement_observation_link_requests", request_id)

        def operation(transaction: Any) -> ReplacementObservationLinkCommit:
            incident_data = self._snapshot_data(incident_ref.get(transaction=transaction))
            review_head_data = self._snapshot_data(review_head_ref.get(transaction=transaction))
            proof_data = self._snapshot_data(proof_ref.get(transaction=transaction))
            proof_event_data = self._snapshot_data(proof_event_ref.get(transaction=transaction))
            observation_data = self._snapshot_data(observation_ref.get(transaction=transaction))
            record_data = self._snapshot_data(record_ref.get(transaction=transaction))
            replacement_head_data = self._snapshot_data(
                replacement_head_ref.get(transaction=transaction)
            )
            event_data = self._snapshot_data(event_ref.get(transaction=transaction))
            request_data = self._snapshot_data(request_ref.get(transaction=transaction))
            if incident_data is None or not isinstance(incident_data.get("record"), dict):
                raise IncidentReviewPrerequisiteError("incident report checkpoint is unavailable")
            checkpoint = IncidentCheckpoint.model_validate(incident_data["record"])
            now = self._clock()
            state = self._read_review_state(review_head_data, checkpoint, now=now)
            if request_data is not None:
                if (
                    request_data.get("request_sha256") != request_sha256
                    or request_data.get("record_id") != record_id
                ):
                    raise IncidentReviewStateConflictError(
                        "replacement observation idempotency key conflicts"
                    )
                if record_data is None or not isinstance(record_data.get("record"), dict):
                    raise LedgerIntegrityError("replacement observation replay record is missing")
                return ReplacementObservationLinkCommit(
                    state,
                    ReplacementObservationLink.model_validate(record_data["record"]),
                    True,
                )
            if record_data is not None or event_data is not None:
                raise LedgerIntegrityError("replacement observation record identity already exists")
            if replacement_head_data is not None:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_LINK_CONFLICT.value,
                    "the incident already has an immutable replacement observation link",
                )
            if state.state_version != proposal.expected_state_version:
                raise IncidentReviewStateConflictError("incident review state is stale")
            if state.state is not IncidentState.AWAITING_REPLACEMENT:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_NOT_ELIGIBLE.value,
                    "replacement correlation requires AWAITING_REPLACEMENT",
                )
            if state.blocking_reason is not None:
                raise IncidentReviewEvidenceError(
                    state.blocking_reason.value,
                    "a visible incident block prevents replacement correlation",
                )
            if (
                checkpoint.candidate_brf is None
                or checkpoint.candidate_manifest is None
                or checkpoint.candidate_brf.kind is not ArtifactKind.FULL_CANDIDATE_BRF
                or checkpoint.candidate_brf.sha256 != proposal.candidate_sha256
                or checkpoint.candidate_manifest.sha256 != proposal.candidate_manifest_sha256
                or state.current_candidate_sha256 != proposal.candidate_sha256
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.CANDIDATE_APPROVAL_INVALIDATED.value,
                    "the current immutable candidate no longer matches the requested replacement link",
                )
            if proof_data is None or not isinstance(proof_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PROOF_NOT_ELIGIBLE.value,
                    "the selected proof record is unavailable",
                )
            if proof_event_data is None or not isinstance(proof_event_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PROOF_NOT_ELIGIBLE.value,
                    "the selected proof record is not attributable timeline evidence",
                )
            proof = ProofRecord.model_validate(proof_data["record"])
            proof_event = IncidentTimelineEvent.model_validate(proof_event_data["record"])
            if (
                proof.incident_id != proposal.incident_id
                or proof.record_id != proposal.proof_record_id
                or proof.decision.value != "APPROVED_FOR_HUMAN_SUBMISSION"
                or proof.visual_only_uncertainty
                or proof.candidate_sha256 != proposal.candidate_sha256
                or proof.manifest_sha256 != proposal.candidate_manifest_sha256
                or proof_event.kind is not HumanTimelineEventKind.PROOF_RECORD
                or proof_event.record_id != proposal.proof_record_id
                or proof_event.incident_id != proposal.incident_id
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.PROOF_NOT_ELIGIBLE.value,
                    "the selected proof record is not the current approved exact candidate proof",
                )
            baseline_data = self._snapshot_data(
                self._document("baselines", checkpoint.baseline_id).get(transaction=transaction)
            )
            production_link_data = self._snapshot_data(
                self._document("baseline_production_links", checkpoint.baseline_id).get(
                    transaction=transaction
                )
            )
            if baseline_data is None or not isinstance(baseline_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "replacement correlation requires the original baseline lineage",
                )
            if production_link_data is None or not isinstance(
                production_link_data.get("record"), dict
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "replacement correlation requires the original production link",
                )
            baseline = RegisteredBaseline.model_validate(baseline_data["record"])
            production_link = BaselineProductionLink.model_validate(production_link_data["record"])
            original_job_id = baseline.baseline.scheduler_job_id
            if (
                original_job_id is None
                or baseline.baseline.scheduler_job_title is None
                or production_link.scheduler_job_id != original_job_id
                or production_link.scheduler_job_title != baseline.baseline.scheduler_job_title
                or production_link.site_id != baseline.baseline.site_id
                or production_link.queue_name != baseline.baseline.queue_name
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.MISSING_LINEAGE.value,
                    "original scheduler, site, bridge, or queue lineage is incomplete",
                )
            if proposal.scheduler_job_id == original_job_id:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_REUSES_ORIGINAL_JOB.value,
                    "the replacement must be a distinct externally submitted scheduler job",
                )
            observation_head_id = canonical_sha256(
                {
                    "site_id": production_link.site_id,
                    "bridge_id": production_link.bridge_id,
                    "queue_name": production_link.queue_name,
                }
            )
            observation_head_data = self._snapshot_data(
                self._document("site_observation_heads", observation_head_id).get(
                    transaction=transaction
                )
            )
            if (
                observation_head_data is None
                or observation_head_data.get("observation_id") != proposal.site_observation_id
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_MISSING.value,
                    "the selected site observation is not the admitted canonical head",
                )
            if observation_data is None or not isinstance(observation_data.get("record"), dict):
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_MISSING.value,
                    "the selected site observation is unavailable",
                )
            observation = SiteObservation.model_validate(observation_data["record"])
            if (
                observation.observation_id != proposal.site_observation_id
                or observation.site_id != production_link.site_id
                or observation.bridge_id != production_link.bridge_id
                or observation.queue_name != production_link.queue_name
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_MISMATCH.value,
                    "the selected observation conflicts with the original site, bridge, or queue",
                )
            observed_at = observation.observed_at
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
            age_seconds = (current - observed_at).total_seconds()
            if age_seconds < 0 or age_seconds > max_observation_age_seconds:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_STALE.value,
                    "the selected replacement observation is stale",
                )
            matches = [
                job
                for job in observation.observations
                if job.scheduler_job_id == proposal.scheduler_job_id
            ]
            if not matches:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_MISSING.value,
                    "the selected replacement scheduler job is absent from the observation",
                )
            if len(matches) != 1:
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_AMBIGUOUS.value,
                    "the selected replacement scheduler job is ambiguous",
                )
            job = matches[0]
            expected_title = (
                f"BER|{proposal.incident_id}|{proposal.candidate_sha256[:12]}|REPLACEMENT"
            )
            if (
                job.destination != production_link.queue_name
                or job.title != expected_title
                or proposal.candidate_sha256[:12] not in job.title
                or job.state is JobState.UNKNOWN
                or observation.printer_state == "unknown"
            ):
                raise IncidentReviewEvidenceError(
                    BlockingReason.REPLACEMENT_EVIDENCE_MISMATCH.value,
                    "the selected job does not match the exact replacement title and queue lineage",
                )
            record = ReplacementObservationLink(
                record_id=record_id,
                incident_id=proposal.incident_id,
                approved_candidate_sha256=proposal.candidate_sha256,
                candidate_manifest_sha256=proposal.candidate_manifest_sha256,
                proof_record_id=proposal.proof_record_id,
                original_scheduler_job_id=original_job_id,
                scheduler_job_id=job.scheduler_job_id,
                observed_job_title=job.title,
                site_id=observation.site_id,
                bridge_id=observation.bridge_id,
                queue_name=observation.queue_name,
                site_observation_id=observation.observation_id,
                observed_job_state=job.state,
                observed_at=job.observed_at,
                selected_role=proposal.selected_role,
                expected_state_version=proposal.expected_state_version,
                idempotency_key=proposal.idempotency_key,
                note=proposal.note,
                actor_principal=proposal.actor_principal,
                recorded_at=current,
            )
            try:
                updated_incident = transition(
                    self._incident_from_review_state(state),
                    IncidentState.REPLACEMENT_OBSERVED,
                    expected_state_version=proposal.expected_state_version,
                    evidence_id=record.record_id,
                )
            except (IllegalStateTransition, StaleStateVersion) as exc:
                raise IncidentReviewStateConflictError(str(exc)) from exc
            updated = state.model_copy(
                update={
                    "state": updated_incident.state,
                    "state_version": updated_incident.state_version,
                    "blocking_reason": None,
                    "last_attributable_evidence_id": record.record_id,
                    "updated_at": current,
                }
            )
            event = IncidentTimelineEvent(
                event_id=event_id,
                incident_id=record.incident_id,
                kind=HumanTimelineEventKind.REPLACEMENT_OBSERVATION_LINK,
                record_id=record.record_id,
                state_version=updated.state_version,
                actor_principal=record.actor_principal,
                recorded_at=record.recorded_at,
            )
            head_body = {"record": updated.model_dump(mode="json")}
            record_body = {"record": record.model_dump(mode="json")}
            event_body = {"record": event.model_dump(mode="json")}
            transaction.set(
                review_head_ref,
                {
                    **head_body,
                    "payload_sha256": canonical_sha256(head_body),
                    "updated_at": current,
                },
            )
            transaction.create(
                record_ref,
                {
                    **record_body,
                    "payload_sha256": canonical_sha256(record_body),
                    "created_at": current,
                },
            )
            transaction.create(
                replacement_head_ref,
                {
                    "record_id": record.record_id,
                    "incident_id": record.incident_id,
                    "payload_sha256": canonical_sha256(
                        {"record_id": record.record_id, "incident_id": record.incident_id}
                    ),
                    "created_at": current,
                },
            )
            transaction.create(
                event_ref,
                {
                    **event_body,
                    "payload_sha256": canonical_sha256(event_body),
                    "created_at": current,
                },
            )
            transaction.create(
                request_ref,
                {
                    **request_body,
                    "request_sha256": request_sha256,
                    "record_id": record.record_id,
                    "created_at": current,
                },
            )
            return ReplacementObservationLinkCommit(updated, record, False)

        return cast(ReplacementObservationLinkCommit, self._transaction_runner(operation))

    async def record_replacement_observation_link(
        self,
        *,
        proposal: ReplacementObservationLinkProposal,
        max_observation_age_seconds: float = 15.0,
    ) -> ReplacementObservationLinkCommit:
        return await asyncio.to_thread(
            self._record_replacement_observation_link_sync,
            proposal=proposal,
            max_observation_age_seconds=max_observation_age_seconds,
        )

    def _get_replacement_observation_link_sync(
        self,
        record_id: str,
    ) -> ReplacementObservationLink | None:
        _require_sha256(record_id, label="replacement observation link record ID")
        data = self._snapshot_data(self._document("replacement_observation_links", record_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("replacement observation link record is malformed")
        return ReplacementObservationLink.model_validate(record)

    async def get_replacement_observation_link(
        self,
        record_id: str,
    ) -> ReplacementObservationLink | None:
        return await asyncio.to_thread(self._get_replacement_observation_link_sync, record_id)

    def _get_incident_review_state_sync(self, incident_id: str) -> IncidentReviewState | None:
        _require_sha256(incident_id, label="incident ID")
        data = self._snapshot_data(self._document("incident_review_heads", incident_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("incident review head is malformed")
        return IncidentReviewState.model_validate(record)

    async def get_incident_review_state(self, incident_id: str) -> IncidentReviewState | None:
        return await asyncio.to_thread(self._get_incident_review_state_sync, incident_id)

    def _get_human_record_sync(
        self,
        *,
        collection: str,
        record_id: str,
        model_type: type[ProfessionalDisposition | OperatorAttestation],
    ) -> ProfessionalDisposition | OperatorAttestation | None:
        _require_sha256(record_id, label="human record ID")
        data = self._snapshot_data(self._document(collection, record_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("human review record is malformed")
        return model_type.model_validate(record)

    async def get_professional_disposition(self, record_id: str) -> ProfessionalDisposition | None:
        value = await asyncio.to_thread(
            self._get_human_record_sync,
            collection="professional_dispositions",
            record_id=record_id,
            model_type=ProfessionalDisposition,
        )
        return cast(ProfessionalDisposition | None, value)

    async def get_operator_attestation(self, record_id: str) -> OperatorAttestation | None:
        value = await asyncio.to_thread(
            self._get_human_record_sync,
            collection="operator_attestations",
            record_id=record_id,
            model_type=OperatorAttestation,
        )
        return cast(OperatorAttestation | None, value)

    def _get_containment_confirmation_sync(
        self,
        record_id: str,
    ) -> ContainmentConfirmation | None:
        _require_sha256(record_id, label="containment confirmation record ID")
        data = self._snapshot_data(self._document("containment_confirmations", record_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("containment confirmation record is malformed")
        return ContainmentConfirmation.model_validate(record)

    async def get_containment_confirmation(
        self,
        record_id: str,
    ) -> ContainmentConfirmation | None:
        return await asyncio.to_thread(self._get_containment_confirmation_sync, record_id)

    def _get_proof_record_sync(self, record_id: str) -> ProofRecord | None:
        _require_sha256(record_id, label="proof record ID")
        data = self._snapshot_data(self._document("proof_records", record_id).get())
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("proof record is malformed")
        return ProofRecord.model_validate(record)

    async def get_proof_record(self, record_id: str) -> ProofRecord | None:
        return await asyncio.to_thread(self._get_proof_record_sync, record_id)

    def _get_candidate_approval_invalidation_sync(
        self,
        record_id: str,
    ) -> CandidateApprovalInvalidation | None:
        _require_sha256(record_id, label="candidate invalidation record ID")
        data = self._snapshot_data(
            self._document("candidate_approval_invalidations", record_id).get()
        )
        if data is None:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            raise LedgerIntegrityError("candidate invalidation record is malformed")
        return CandidateApprovalInvalidation.model_validate(record)

    async def get_candidate_approval_invalidation(
        self,
        record_id: str,
    ) -> CandidateApprovalInvalidation | None:
        return await asyncio.to_thread(self._get_candidate_approval_invalidation_sync, record_id)

    def _list_incident_timeline_events_sync(
        self,
        incident_id: str,
    ) -> tuple[IncidentTimelineEvent, ...]:
        _require_sha256(incident_id, label="incident ID")
        query = (
            self.client.collection("incident_timeline_events")
            .where(filter=FieldFilter("record.incident_id", "==", incident_id))
            .order_by("record.recorded_at")
            .limit(200)
        )
        events: list[IncidentTimelineEvent] = []
        for snapshot in query.stream():
            data = self._snapshot_data(snapshot)
            if data is None or not isinstance(data.get("record"), dict):
                raise LedgerIntegrityError("incident timeline record is malformed")
            event = IncidentTimelineEvent.model_validate(data["record"])
            if event.incident_id == incident_id:
                events.append(event)
        return tuple(sorted(events, key=lambda event: (event.recorded_at, event.event_id)))

    async def list_incident_timeline_events(
        self,
        incident_id: str,
    ) -> tuple[IncidentTimelineEvent, ...]:
        return await asyncio.to_thread(self._list_incident_timeline_events_sync, incident_id)

    def _allocate_report_created_at_sync(
        self,
        *,
        incident_id: str,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        _require_sha256(incident_id, label="incident ID")
        if expected_state_version < 0:
            raise ValueError("expected incident state version is invalid")
        ref = self._document("incidents", incident_id)

        def operation(transaction: Any) -> IncidentCheckpointCommit:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is None or not isinstance(existing.get("record"), dict):
                raise LedgerIntegrityError("incident checkpoint is missing or malformed")
            current = IncidentCheckpoint.model_validate(existing["record"])
            if current.report_created_at is not None:
                return IncidentCheckpointCommit(current, True)
            if current.state_version != expected_state_version:
                raise LedgerIntegrityError("incident checkpoint state version is stale")
            if current.stage is not IncidentWorkflowStage.SEMANTIC_READY:
                raise LedgerIntegrityError("report timestamp requires SEMANTIC_READY")
            now = self._clock()
            allocated = current.model_copy(
                update={
                    "report_created_at": now,
                    "state_version": current.state_version + 1,
                    "updated_at": now,
                }
            )
            transaction.set(
                ref,
                {
                    **existing,
                    "record": allocated.model_dump(mode="json"),
                    "updated_at": now,
                },
            )
            return IncidentCheckpointCommit(allocated, False)

        return cast(IncidentCheckpointCommit, self._transaction_runner(operation))

    async def allocate_report_created_at(
        self,
        *,
        incident_id: str,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        return await asyncio.to_thread(
            self._allocate_report_created_at_sync,
            incident_id=incident_id,
            expected_state_version=expected_state_version,
        )

    def _advance_incident_sync(
        self,
        checkpoint: IncidentCheckpoint,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        if checkpoint.state_version != expected_state_version + 1:
            raise ValueError("target incident version must increment by exactly one")
        ref = self._document("incidents", checkpoint.incident_id)

        def operation(transaction: Any) -> IncidentCheckpointCommit:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is None:
                raise LedgerIntegrityError("incident checkpoint is missing")
            existing_record = existing.get("record")
            if not isinstance(existing_record, dict):
                raise LedgerIntegrityError("stored incident is malformed")
            current = IncidentCheckpoint.model_validate(existing_record)
            if current.state_version == checkpoint.state_version:
                replay_target = checkpoint
                if current.report_ready_at is not None and checkpoint.report_ready_at is None:
                    replay_target = checkpoint.model_copy(
                        update={"report_ready_at": current.report_ready_at}
                    )
                if current != replay_target:
                    raise LedgerIntegrityError("incident checkpoint version conflicts")
                return IncidentCheckpointCommit(current, True)
            if current.state_version != expected_state_version:
                raise LedgerIntegrityError("incident checkpoint state version is stale")
            if (
                current.incident_id != checkpoint.incident_id
                or current.baseline_id != checkpoint.baseline_id
                or current.new_source_revision_id != checkpoint.new_source_revision_id
                or current.production_job_lineage_id != checkpoint.production_job_lineage_id
            ):
                raise LedgerIntegrityError("incident immutable lineage changed")
            committed = checkpoint
            if (
                checkpoint.stage
                in {
                    IncidentWorkflowStage.REPORT_READY,
                    IncidentWorkflowStage.NEEDS_REVIEW,
                }
                and checkpoint.report is not None
            ):
                if (
                    current.report_created_at is None
                    or checkpoint.report_created_at != current.report_created_at
                ):
                    raise LedgerIntegrityError("report creation time lineage is missing")
                committed = checkpoint.model_copy(
                    update={"report_ready_at": current.report_ready_at or self._clock()}
                )
            transaction.set(
                ref,
                {
                    **existing,
                    "record": committed.model_dump(mode="json"),
                    "updated_at": self._clock(),
                },
            )
            return IncidentCheckpointCommit(committed, False)

        return cast(IncidentCheckpointCommit, self._transaction_runner(operation))

    async def advance_incident(
        self,
        checkpoint: IncidentCheckpoint,
        *,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        return await asyncio.to_thread(
            self._advance_incident_sync,
            checkpoint,
            expected_state_version,
        )

    def _get_latest_site_observation_sync(
        self,
        *,
        site_id: str,
        bridge_id: str,
        queue_name: str,
    ) -> SiteObservation | None:
        head_id = canonical_sha256(
            {"site_id": site_id, "bridge_id": bridge_id, "queue_name": queue_name}
        )
        head = self._snapshot_data(self._document("site_observation_heads", head_id).get())
        if head is None:
            return None
        observation_id = head.get("observation_id")
        if not isinstance(observation_id, str):
            raise LedgerIntegrityError("observation chain head has no observation ID")
        record = self._snapshot_data(self._document("site_observations", observation_id).get())
        if record is None or not isinstance(record.get("record"), dict):
            raise LedgerIntegrityError("latest site observation record is missing")
        return SiteObservation.model_validate(record["record"])

    async def get_latest_site_observation(
        self,
        *,
        site_id: str,
        bridge_id: str,
        queue_name: str,
    ) -> SiteObservation | None:
        return await asyncio.to_thread(
            self._get_latest_site_observation_sync,
            site_id=site_id,
            bridge_id=bridge_id,
            queue_name=queue_name,
        )

    def _lease_outbox_sync(
        self,
        *,
        lease_token: str,
        limit: int,
        lease_seconds: int,
    ) -> tuple[OutboxLease, ...]:
        if not lease_token or limit < 1 or limit > 100 or lease_seconds < 1:
            raise ValueError("outbox lease parameters are invalid")
        query = (
            self.client.collection("outbox")
            .where(filter=FieldFilter("status", "in", ["PENDING", "LEASED"]))
            .order_by("created_at")
            .limit(limit * 4)
        )

        def operation(transaction: Any) -> tuple[OutboxLease, ...]:
            now = self._clock()
            selected: list[OutboxLease] = []
            for snapshot in transaction.get(query):
                if len(selected) >= limit:
                    break
                data = self._snapshot_data(snapshot)
                if data is None:
                    continue
                status = data.get("status")
                if status not in {"PENDING", "LEASED"}:
                    continue
                lease_expires_at = data.get("lease_expires_at")
                next_attempt_at = data.get("next_attempt_at")
                if status == "LEASED" and (
                    not isinstance(lease_expires_at, datetime) or lease_expires_at > now
                ):
                    continue
                if isinstance(next_attempt_at, datetime) and next_attempt_at > now:
                    continue
                message_id = data.get("message_id")
                kind = data.get("kind")
                payload = data.get("payload")
                attempts = data.get("attempts")
                state_version = data.get("state_version")
                if (
                    not isinstance(message_id, str)
                    or not isinstance(kind, str)
                    or not isinstance(payload, dict)
                    or not isinstance(attempts, int)
                    or not isinstance(state_version, int)
                ):
                    raise LedgerIntegrityError("outbox record is malformed")
                attempts += 1
                transaction.set(
                    snapshot.reference,
                    {
                        **data,
                        "status": "LEASED",
                        "lease_token": lease_token,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "attempts": attempts,
                        "state_version": state_version + 1,
                        "updated_at": now,
                    },
                )
                selected.append(OutboxLease(message_id, kind, payload, lease_token, attempts))
            return tuple(selected)

        return cast(tuple[OutboxLease, ...], self._transaction_runner(operation))

    async def lease_outbox(
        self,
        *,
        lease_token: str,
        limit: int = 10,
        lease_seconds: int = 120,
    ) -> tuple[OutboxLease, ...]:
        return await asyncio.to_thread(
            self._lease_outbox_sync,
            lease_token=lease_token,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    def _complete_outbox_sync(
        self,
        *,
        message_id: str,
        lease_token: str,
        result: Mapping[str, object],
    ) -> bool:
        _require_sha256(message_id, label="outbox message ID")
        ref = self._document("outbox", message_id)
        result_body = dict(result)
        result_sha256 = canonical_sha256(result_body)

        def operation(transaction: Any) -> bool:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is None:
                raise LedgerIntegrityError("outbox message is missing")
            if existing.get("status") == "SENT":
                if existing.get("result_sha256") != result_sha256:
                    raise LedgerIntegrityError("outbox completion result conflicts")
                return True
            if existing.get("status") != "LEASED" or existing.get("lease_token") != lease_token:
                raise LedgerIntegrityError("outbox lease is stale")
            version = existing.get("state_version")
            if not isinstance(version, int):
                raise LedgerIntegrityError("outbox version is malformed")
            now = self._clock()
            transaction.set(
                ref,
                {
                    **existing,
                    "status": "SENT",
                    "result": result_body,
                    "result_sha256": result_sha256,
                    "state_version": version + 1,
                    "sent_at": now,
                    "updated_at": now,
                },
            )
            return False

        return cast(bool, self._transaction_runner(operation))

    async def complete_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        result: Mapping[str, object],
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_outbox_sync,
            message_id=message_id,
            lease_token=lease_token,
            result=result,
        )

    def _retry_outbox_sync(
        self,
        *,
        message_id: str,
        lease_token: str,
        error_code: str,
        max_attempts: int,
    ) -> None:
        _require_sha256(message_id, label="outbox message ID")
        if not error_code or max_attempts < 1:
            raise ValueError("outbox retry policy is invalid")
        ref = self._document("outbox", message_id)

        def operation(transaction: Any) -> None:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is None:
                raise LedgerIntegrityError("outbox message is missing")
            if existing.get("status") != "LEASED" or existing.get("lease_token") != lease_token:
                raise LedgerIntegrityError("outbox lease is stale")
            attempts = existing.get("attempts")
            version = existing.get("state_version")
            if not isinstance(attempts, int) or not isinstance(version, int):
                raise LedgerIntegrityError("outbox retry counters are malformed")
            now = self._clock()
            terminal = attempts >= max_attempts
            transaction.set(
                ref,
                {
                    **existing,
                    "status": "DEAD_LETTER" if terminal else "PENDING",
                    "last_error_code": error_code,
                    "next_attempt_at": (
                        None
                        if terminal
                        else now + timedelta(seconds=min(300, 2 ** min(attempts, 8)))
                    ),
                    "state_version": version + 1,
                    "updated_at": now,
                },
            )

        self._transaction_runner(operation)

    async def retry_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        error_code: str,
        max_attempts: int = 5,
    ) -> None:
        await asyncio.to_thread(
            self._retry_outbox_sync,
            message_id=message_id,
            lease_token=lease_token,
            error_code=error_code,
            max_attempts=max_attempts,
        )

    def _ingest_site_observation_sync(
        self,
        observation: SiteObservation,
        payload_sha256: str,
    ) -> ObservationCommitResult:
        _require_sha256(payload_sha256, label="observation payload SHA-256")
        if payload_sha256 != observation.observation_id:
            raise LedgerIntegrityError("observation ID does not match canonical payload")
        head_id = canonical_sha256(
            {
                "site_id": observation.site_id,
                "bridge_id": observation.bridge_id,
                "queue_name": observation.queue_name,
            }
        )
        observation_ref = self._document("site_observations", observation.observation_id)
        head_ref = self._document("site_observation_heads", head_id)
        record = observation.model_dump(mode="json")

        def operation(transaction: Any) -> ObservationCommitResult:
            existing = self._snapshot_data(observation_ref.get(transaction=transaction))
            head = self._snapshot_data(head_ref.get(transaction=transaction))
            if existing is not None:
                if existing.get("payload_sha256") != payload_sha256:
                    raise LedgerIntegrityError("observation replay conflicts with durable data")
                return ObservationCommitResult(observation.observation_id, True)
            if head is None:
                if observation.sequence != 1 or observation.previous_observation_sha256 is not None:
                    raise LedgerIntegrityError("observation does not start a new sequence")
                state_version = 0
            else:
                prior_sequence = head.get("sequence")
                prior_observation_id = head.get("observation_id")
                prior_version = head.get("state_version")
                if (
                    not isinstance(prior_sequence, int)
                    or not isinstance(prior_observation_id, str)
                    or not isinstance(prior_version, int)
                ):
                    raise LedgerIntegrityError("observation chain head is malformed")
                if observation.sequence != prior_sequence + 1:
                    raise LedgerIntegrityError("observation sequence is stale or out of order")
                if observation.previous_observation_sha256 != prior_observation_id:
                    raise LedgerIntegrityError("observation hash chain is discontinuous")
                state_version = prior_version + 1
            now = self._clock()
            transaction.create(
                observation_ref,
                {
                    "record": record,
                    "payload_sha256": payload_sha256,
                    "received_at": now,
                },
            )
            transaction.set(
                head_ref,
                {
                    "site_id": observation.site_id,
                    "bridge_id": observation.bridge_id,
                    "queue_name": observation.queue_name,
                    "sequence": observation.sequence,
                    "observation_id": observation.observation_id,
                    "observed_at": observation.observed_at,
                    "state_version": state_version,
                    "updated_at": now,
                },
            )
            return ObservationCommitResult(observation.observation_id, False)

        return cast(ObservationCommitResult, self._transaction_runner(operation))

    async def ingest_site_observation(
        self,
        observation: SiteObservation,
        *,
        payload_sha256: str,
    ) -> ObservationCommitResult:
        return await asyncio.to_thread(
            self._ingest_site_observation_sync,
            observation,
            payload_sha256,
        )

    def _claim_automation_cycle_sync(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> AutomationCycleClaim:
        _require_sha256(cycle_key, label="automation cycle key")
        _require_sha256(cycle_id, label="automation cycle ID")
        if not lease_token or lease_seconds < 1:
            raise ValueError("automation cycle lease parameters are invalid")
        lease_ref = self._document("automation_cycle_leases", cycle_key)

        def operation(transaction: Any) -> AutomationCycleClaim:
            now = self._clock()
            existing = self._snapshot_data(lease_ref.get(transaction=transaction))
            state_version = 0
            attempts = 0
            created_at = now
            abandoned_cycle_id: str | None = None
            prior_summary: dict[str, object] = {}
            if existing is not None:
                stored_key = existing.get("cycle_key")
                stored_status = existing.get("status")
                stored_cycle_id = existing.get("cycle_id")
                stored_expiry = existing.get("lease_expires_at")
                stored_version = existing.get("state_version")
                stored_attempts = existing.get("attempts")
                if stored_key != cycle_key or not isinstance(stored_version, int):
                    raise AutomationLeaseError("automation cycle lease identity is malformed")
                if not isinstance(stored_attempts, int) or stored_attempts < 0:
                    raise AutomationLeaseError("automation cycle attempt count is malformed")
                state_version = stored_version
                attempts = stored_attempts
                existing_created_at = existing.get("created_at")
                if isinstance(existing_created_at, datetime):
                    created_at = existing_created_at
                for field in (
                    "last_execution_cycle_id",
                    "last_outcome",
                    "last_result_sha256",
                    "last_error_code",
                ):
                    if field in existing:
                        prior_summary[field] = existing[field]
                if stored_status == "RUNNING":
                    if not isinstance(stored_cycle_id, str) or not isinstance(
                        stored_expiry, datetime
                    ):
                        raise AutomationLeaseError("active automation cycle lease is malformed")
                    if stored_expiry > now:
                        return AutomationCycleClaim(
                            cycle_key=cycle_key,
                            cycle_id=stored_cycle_id,
                            status=AutomationCycleClaimStatus.IN_PROGRESS,
                            lease_expires_at=stored_expiry,
                        )
                    abandoned_cycle_id = stored_cycle_id
                elif stored_status != "IDLE":
                    raise AutomationLeaseError("automation cycle lease status is malformed")

            lease_expires_at = now + timedelta(seconds=lease_seconds)
            payload: dict[str, object] = {
                "cycle_key": cycle_key,
                "cycle_id": cycle_id,
                "status": "RUNNING",
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at,
                "attempts": attempts + 1,
                "state_version": state_version + 1,
                "created_at": created_at,
                "updated_at": now,
            }
            if abandoned_cycle_id is not None:
                payload["last_abandoned_cycle_id"] = abandoned_cycle_id
            payload.update(prior_summary)
            transaction.set(lease_ref, payload)
            return AutomationCycleClaim(
                cycle_key=cycle_key,
                cycle_id=cycle_id,
                status=AutomationCycleClaimStatus.ACQUIRED,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
            )

        return cast(AutomationCycleClaim, self._transaction_runner(operation))

    async def claim_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> AutomationCycleClaim:
        return await asyncio.to_thread(
            self._claim_automation_cycle_sync,
            cycle_key=cycle_key,
            cycle_id=cycle_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )

    def _finish_automation_cycle_sync(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        outcome: str,
        result: Mapping[str, object] | None = None,
        error_code: str | None = None,
    ) -> bool:
        _require_sha256(cycle_key, label="automation cycle key")
        _require_sha256(cycle_id, label="automation cycle ID")
        if not lease_token or outcome not in {"COMPLETED", "FAILED"}:
            raise ValueError("automation cycle completion parameters are invalid")
        if outcome == "COMPLETED" and result is None:
            raise ValueError("completed automation cycle requires a result")
        if outcome == "FAILED" and not error_code:
            raise ValueError("failed automation cycle requires an error code")
        lease_ref = self._document("automation_cycle_leases", cycle_key)
        execution_ref = self._document("automation_cycle_executions", cycle_id)

        def operation(transaction: Any) -> bool:
            now = self._clock()
            lease = self._snapshot_data(lease_ref.get(transaction=transaction))
            execution = self._snapshot_data(execution_ref.get(transaction=transaction))
            if execution is not None:
                return execution.get("outcome") == outcome
            if lease is None:
                raise AutomationLeaseError("automation cycle lease is missing")
            if (
                lease.get("cycle_key") != cycle_key
                or lease.get("cycle_id") != cycle_id
                or lease.get("status") != "RUNNING"
                or lease.get("lease_token") != lease_token
            ):
                return False
            state_version = lease.get("state_version")
            attempts = lease.get("attempts")
            if not isinstance(state_version, int) or not isinstance(attempts, int):
                raise AutomationLeaseError("automation cycle lease version is malformed")

            result_body = dict(result or {})
            result_sha256 = canonical_sha256(result_body) if result is not None else None
            execution_body: dict[str, object] = {
                "cycle_key": cycle_key,
                "cycle_id": cycle_id,
                "outcome": outcome,
                "completed_at": now,
            }
            if result_sha256 is not None:
                execution_body["result"] = result_body
                execution_body["result_sha256"] = result_sha256
            if error_code is not None:
                execution_body["error_code"] = error_code[:128]
            execution_body["payload_sha256"] = canonical_sha256(execution_body)
            transaction.create(execution_ref, execution_body)

            idle_body: dict[str, object] = {
                "cycle_key": cycle_key,
                "cycle_id": cycle_id,
                "status": "IDLE",
                "lease_token": None,
                "lease_expires_at": now,
                "attempts": attempts,
                "state_version": state_version + 1,
                "created_at": lease.get("created_at", now),
                "updated_at": now,
                "last_execution_cycle_id": cycle_id,
                "last_outcome": outcome,
            }
            if result_sha256 is not None:
                idle_body["last_result_sha256"] = result_sha256
            if error_code is not None:
                idle_body["last_error_code"] = error_code[:128]
            transaction.set(lease_ref, idle_body)
            return True

        return cast(bool, self._transaction_runner(operation))

    def _get_automation_cycle_state_sync(
        self,
        *,
        cycle_key: str,
    ) -> AutomationCycleLedgerState | None:
        _require_sha256(cycle_key, label="automation cycle key")
        lease = self._snapshot_data(self._document("automation_cycle_leases", cycle_key).get())
        if lease is None:
            return None
        if lease.get("cycle_key") != cycle_key:
            raise AutomationLeaseError("automation cycle lease identity is malformed")
        state = lease.get("status")
        if state not in {"RUNNING", "IDLE"}:
            raise AutomationLeaseError("automation cycle lease status is malformed")
        active_cycle_id = lease.get("cycle_id")
        lease_expires_at = lease.get("lease_expires_at")
        if not isinstance(active_cycle_id, str) or not isinstance(lease_expires_at, datetime):
            raise AutomationLeaseError("automation cycle lease state is malformed")
        last_execution_cycle_id = lease.get("last_execution_cycle_id")
        if last_execution_cycle_id is not None and not isinstance(last_execution_cycle_id, str):
            raise AutomationLeaseError("automation cycle execution identity is malformed")
        last_outcome = lease.get("last_outcome")
        if last_outcome is not None and last_outcome not in {"COMPLETED", "FAILED"}:
            raise AutomationLeaseError("automation cycle outcome is malformed")
        last_error_code = lease.get("last_error_code")
        if last_error_code is not None and not isinstance(last_error_code, str):
            raise AutomationLeaseError("automation cycle error code is malformed")
        last_result: dict[str, object] | None = None
        last_completed_at: datetime | None = None
        if last_execution_cycle_id is not None:
            execution = self._snapshot_data(
                self._document("automation_cycle_executions", last_execution_cycle_id).get()
            )
            if execution is None:
                raise AutomationLeaseError("automation cycle execution is missing")
            if (
                execution.get("cycle_key") != cycle_key
                or execution.get("cycle_id") != last_execution_cycle_id
            ):
                raise AutomationLeaseError("automation cycle execution identity is malformed")
            completed_at = execution.get("completed_at")
            if not isinstance(completed_at, datetime):
                raise AutomationLeaseError("automation cycle execution time is malformed")
            last_completed_at = completed_at
            result = execution.get("result")
            if result is not None:
                if not isinstance(result, dict):
                    raise AutomationLeaseError("automation cycle result is malformed")
                last_result = dict(result)
        return AutomationCycleLedgerState(
            cycle_key=cycle_key,
            state=state,
            active_cycle_id=active_cycle_id,
            lease_expires_at=lease_expires_at,
            last_execution_cycle_id=last_execution_cycle_id,
            last_outcome=last_outcome,
            last_result=last_result,
            last_error_code=last_error_code,
            last_completed_at=last_completed_at,
        )

    async def get_automation_cycle_state(
        self,
        *,
        cycle_key: str,
    ) -> AutomationCycleLedgerState | None:
        return await asyncio.to_thread(
            self._get_automation_cycle_state_sync,
            cycle_key=cycle_key,
        )

    async def complete_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        result: Mapping[str, object],
    ) -> bool:
        return await asyncio.to_thread(
            self._finish_automation_cycle_sync,
            cycle_key=cycle_key,
            cycle_id=cycle_id,
            lease_token=lease_token,
            outcome="COMPLETED",
            result=result,
        )

    async def fail_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        error_code: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._finish_automation_cycle_sync,
            cycle_key=cycle_key,
            cycle_id=cycle_id,
            lease_token=lease_token,
            outcome="FAILED",
            error_code=error_code,
        )

    def _prepare_change_records(
        self,
        *,
        principal_scope_hash: str,
        batch: DriveChangeBatch,
        artifact_refs: Mapping[str, ArtifactRef],
    ) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
        revision_records: list[dict[str, object]] = []
        outbox_records: list[dict[str, object]] = []
        for snapshot in batch.snapshots:
            revision = snapshot.revision
            ref = artifact_refs.get(revision.revision_id)
            if ref is None:
                raise LedgerIntegrityError("source revision has no immutable artifact reference")
            if ref.sha256 != revision.source_sha256 or ref.byte_length != len(
                snapshot.source_bytes
            ):
                raise LedgerIntegrityError("source artifact reference does not match fetched bytes")
            revision_body: dict[str, object] = {
                "revision_id": revision.revision_id,
                "provider": revision.metadata.locator.provider.value,
                "file_id": revision.metadata.locator.file_id,
                "mime_type": revision.metadata.locator.mime_type,
                "provider_version": revision.metadata.provider_version,
                "source_sha256": revision.source_sha256,
                "byte_length": revision.metadata.byte_length,
                "fetched_at": revision.fetched_at,
                "artifact_sha256": ref.sha256,
                "artifact_uri": ref.uri,
            }
            revision_body["payload_sha256"] = canonical_sha256(revision_body)
            revision_records.append(revision_body)

            outbox_id = canonical_sha256(
                {
                    "kind": "SOURCE_REVISION_CLAIMED",
                    "revision_id": revision.revision_id,
                    "artifact_sha256": ref.sha256,
                }
            )
            outbox_body: dict[str, object] = {
                "message_id": outbox_id,
                "kind": "SOURCE_REVISION_CLAIMED",
                "payload": {
                    "revision_id": revision.revision_id,
                    "artifact_sha256": ref.sha256,
                },
                "status": "PENDING",
                "attempts": 0,
                "state_version": 0,
            }
            outbox_body["payload_sha256"] = canonical_sha256(outbox_body)
            outbox_records.append(outbox_body)

        receipt_body: dict[str, object] = {
            "principal_scope_hash": principal_scope_hash,
            "start_cursor_sha256": _token_sha256(batch.start_cursor),
            "final_cursor_sha256": _token_sha256(batch.final_cursor),
            "signal_count": len(batch.signals),
            "revision_ids": sorted(snapshot.revision.revision_id for snapshot in batch.snapshots),
        }
        receipt_body["payload_sha256"] = canonical_sha256(receipt_body)
        return receipt_body, revision_records, outbox_records

    def _commit_change_batch_sync(
        self,
        principal_scope_hash: str,
        batch: DriveChangeBatch,
        artifact_refs: Mapping[str, ArtifactRef],
    ) -> ChangeCommitResult:
        _require_sha256(principal_scope_hash, label="principal scope hash")
        receipt_body, revision_records, outbox_records = self._prepare_change_records(
            principal_scope_hash=principal_scope_hash,
            batch=batch,
            artifact_refs=artifact_refs,
        )
        receipt_id = canonical_sha256(receipt_body)
        execution_id = canonical_sha256({"receipt_id": receipt_id, "purpose": "SOURCE_CHANGE"})
        cursor_ref = self._document("drive_cursors", principal_scope_hash)
        receipt_ref = self._document("event_receipts", receipt_id)
        execution_ref = self._document("execution_attempts", execution_id)
        revision_refs = [
            self._document("source_revisions", str(record["revision_id"]))
            for record in revision_records
        ]
        outbox_refs = [
            self._document("outbox", str(record["message_id"])) for record in outbox_records
        ]

        def operation(transaction: Any) -> ChangeCommitResult:
            receipt_existing = self._snapshot_data(receipt_ref.get(transaction=transaction))
            cursor_existing = self._snapshot_data(cursor_ref.get(transaction=transaction))
            revision_existing = [
                self._snapshot_data(ref.get(transaction=transaction)) for ref in revision_refs
            ]
            outbox_existing = [
                self._snapshot_data(ref.get(transaction=transaction)) for ref in outbox_refs
            ]
            execution_existing = self._snapshot_data(execution_ref.get(transaction=transaction))

            if receipt_existing is not None:
                if receipt_existing.get("payload_sha256") != receipt_body["payload_sha256"]:
                    raise LedgerIntegrityError("receipt ID collision has different content")
                return ChangeCommitResult(
                    receipt_id=receipt_id,
                    execution_id=execution_id,
                    outbox_ids=tuple(str(record["message_id"]) for record in outbox_records),
                    final_cursor_sha256=_token_sha256(batch.final_cursor),
                    duplicate=True,
                    new_outbox_ids=(),
                )
            if cursor_existing is None:
                raise StaleCursorError("Drive cursor was not initialized")
            if cursor_existing.get("raw_token") != batch.start_cursor:
                raise StaleCursorError("Drive batch start cursor is stale")
            state_version = cursor_existing.get("state_version")
            if not isinstance(state_version, int):
                raise LedgerIntegrityError("Drive cursor version is malformed")

            now = self._clock()
            transaction.create(
                receipt_ref,
                {
                    **receipt_body,
                    "receipt_id": receipt_id,
                    "status": "DURABLE",
                    "created_at": now,
                },
            )
            for ref, record, existing in zip(
                revision_refs, revision_records, revision_existing, strict=True
            ):
                if existing is None:
                    transaction.create(ref, {**record, "claimed_at": now})
                elif not _same_source_revision_identity(existing, record):
                    raise LedgerIntegrityError("source revision claim conflicts with existing data")
            new_outbox_ids: list[str] = []
            for ref, record, existing in zip(
                outbox_refs, outbox_records, outbox_existing, strict=True
            ):
                if existing is None:
                    transaction.create(ref, {**record, "created_at": now})
                    new_outbox_ids.append(str(record["message_id"]))
                elif existing.get("payload_sha256") != record["payload_sha256"]:
                    raise LedgerIntegrityError("outbox identity conflicts with existing data")
            if execution_existing is None:
                transaction.create(
                    execution_ref,
                    {
                        "execution_id": execution_id,
                        "receipt_id": receipt_id,
                        "status": "PENDING",
                        "state_version": 0,
                        "created_at": now,
                    },
                )
            transaction.set(
                cursor_ref,
                {
                    "principal_scope_hash": principal_scope_hash,
                    "raw_token": batch.final_cursor,
                    "token_sha256": _token_sha256(batch.final_cursor),
                    "state_version": state_version + 1,
                    "updated_at": now,
                },
            )
            return ChangeCommitResult(
                receipt_id=receipt_id,
                execution_id=execution_id,
                outbox_ids=tuple(str(record["message_id"]) for record in outbox_records),
                final_cursor_sha256=_token_sha256(batch.final_cursor),
                duplicate=False,
                new_outbox_ids=tuple(new_outbox_ids),
            )

        return cast(ChangeCommitResult, self._transaction_runner(operation))

    async def commit_change_batch(
        self,
        *,
        principal_scope_hash: str,
        batch: DriveChangeBatch,
        artifact_refs: Mapping[str, ArtifactRef],
    ) -> ChangeCommitResult:
        return await asyncio.to_thread(
            self._commit_change_batch_sync,
            principal_scope_hash,
            batch,
            artifact_refs,
        )

    def _claim_semantic_execution_sync(
        self,
        *,
        execution_key: str,
        evidence_sha256: str,
        model_id: str,
        prompt_version: str,
        analysis_revision: int,
        lease_token: str,
        lease_seconds: int,
    ) -> SemanticExecutionClaim:
        _require_sha256(execution_key, label="semantic execution key")
        _require_sha256(evidence_sha256, label="semantic evidence SHA-256")
        if not model_id or not prompt_version or not lease_token:
            raise ValueError("semantic execution identity and lease token are required")
        if analysis_revision < 1 or lease_seconds < 1:
            raise ValueError("semantic revision and lease duration must be positive")
        ref = self._document("semantic_executions", execution_key)
        identity: dict[str, object] = {
            "schema_version": "semantic-execution.v1",
            "execution_key": execution_key,
            "evidence_sha256": evidence_sha256,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "analysis_revision": analysis_revision,
        }
        identity_sha256 = canonical_sha256(identity)

        def operation(transaction: Any) -> SemanticExecutionClaim:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            now = self._clock()
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            if existing is None:
                transaction.create(
                    ref,
                    {
                        **identity,
                        "identity_sha256": identity_sha256,
                        "status": "LEASED",
                        "lease_token": lease_token,
                        "lease_expires_at": lease_expires_at,
                        "attempt_count": 1,
                        "state_version": 0,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                return SemanticExecutionClaim(
                    execution_key=execution_key,
                    status=SemanticClaimStatus.ACQUIRED,
                    lease_token=lease_token,
                )
            if existing.get("identity_sha256") != identity_sha256:
                raise LedgerIntegrityError("semantic execution identity conflicts")
            status = existing.get("status")
            if status == "READY":
                assessment_body = existing.get("assessment")
                if not isinstance(assessment_body, dict):
                    raise LedgerIntegrityError("ready semantic execution has no assessment")
                assessment = SemanticAssessment.model_validate(assessment_body)
                return SemanticExecutionClaim(
                    execution_key=execution_key,
                    status=SemanticClaimStatus.READY,
                    assessment=assessment,
                )
            existing_expiry = existing.get("lease_expires_at")
            if status != "LEASED" or not isinstance(existing_expiry, datetime):
                raise LedgerIntegrityError("semantic execution lease is malformed")
            if existing_expiry > now:
                return SemanticExecutionClaim(
                    execution_key=execution_key,
                    status=SemanticClaimStatus.IN_PROGRESS,
                )
            attempt_count = existing.get("attempt_count")
            state_version = existing.get("state_version")
            if not isinstance(attempt_count, int) or not isinstance(state_version, int):
                raise LedgerIntegrityError("semantic execution counters are malformed")
            transaction.set(
                ref,
                {
                    **existing,
                    "status": "LEASED",
                    "lease_token": lease_token,
                    "lease_expires_at": lease_expires_at,
                    "attempt_count": attempt_count + 1,
                    "state_version": state_version + 1,
                    "updated_at": now,
                },
            )
            return SemanticExecutionClaim(
                execution_key=execution_key,
                status=SemanticClaimStatus.ACQUIRED,
                lease_token=lease_token,
            )

        return cast(SemanticExecutionClaim, self._transaction_runner(operation))

    async def claim_semantic_execution(
        self,
        *,
        execution_key: str,
        evidence_sha256: str,
        model_id: str,
        prompt_version: str,
        analysis_revision: int,
        lease_token: str,
        lease_seconds: int = 240,
    ) -> SemanticExecutionClaim:
        return await asyncio.to_thread(
            self._claim_semantic_execution_sync,
            execution_key=execution_key,
            evidence_sha256=evidence_sha256,
            model_id=model_id,
            prompt_version=prompt_version,
            analysis_revision=analysis_revision,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )

    def _complete_semantic_execution_sync(
        self,
        *,
        execution_key: str,
        lease_token: str,
        assessment: SemanticAssessment,
    ) -> bool:
        _require_sha256(execution_key, label="semantic execution key")
        ref = self._document("semantic_executions", execution_key)
        assessment_body = assessment.model_dump(mode="json")
        assessment_sha256 = canonical_sha256(assessment_body)

        def operation(transaction: Any) -> bool:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is None:
                raise SemanticLeaseError("semantic execution claim is missing")
            if existing.get("status") == "READY":
                if existing.get("assessment_sha256") != assessment_sha256:
                    raise LedgerIntegrityError("semantic result conflicts with ready result")
                return True
            if existing.get("status") != "LEASED" or existing.get("lease_token") != lease_token:
                raise SemanticLeaseError("semantic execution lease is stale")
            state_version = existing.get("state_version")
            if not isinstance(state_version, int):
                raise LedgerIntegrityError("semantic execution version is malformed")
            now = self._clock()
            transaction.set(
                ref,
                {
                    **existing,
                    "status": "READY",
                    "assessment": assessment_body,
                    "assessment_sha256": assessment_sha256,
                    "state_version": state_version + 1,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            return False

        return cast(bool, self._transaction_runner(operation))

    async def complete_semantic_execution(
        self,
        *,
        execution_key: str,
        lease_token: str,
        assessment: SemanticAssessment,
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_semantic_execution_sync,
            execution_key=execution_key,
            lease_token=lease_token,
            assessment=assessment,
        )

    async def record_semantic_attempt(
        self,
        *,
        execution_key: str,
        lease_token: str,
        sanitized_record: Mapping[str, object],
    ) -> bool:
        attempt_id = canonical_sha256({"execution_key": execution_key, "lease_token": lease_token})
        return await asyncio.to_thread(
            self._create_once_sync,
            "semantic_attempts",
            attempt_id,
            {"execution_key": execution_key, **dict(sanitized_record)},
        )

    def _create_once_sync(
        self,
        collection: str,
        document_id: str,
        body: Mapping[str, object],
    ) -> bool:
        _require_sha256(document_id, label=f"{collection} document ID")
        ref = self._document(collection, document_id)
        payload = dict(body)
        payload_hash = canonical_sha256(payload)

        def operation(transaction: Any) -> bool:
            existing = self._snapshot_data(ref.get(transaction=transaction))
            if existing is not None:
                if existing.get("payload_sha256") != payload_hash:
                    raise LedgerIntegrityError("create-once record conflicts with existing data")
                return True
            transaction.create(
                ref,
                {
                    **payload,
                    "payload_sha256": payload_hash,
                    "created_at": self._clock(),
                },
            )
            return False

        return cast(bool, self._transaction_runner(operation))

    async def record_gate0_evidence_ref(
        self,
        *,
        evidence_sha256: str,
        artifact_uri: str,
    ) -> bool:
        _require_sha256(evidence_sha256, label="evidence SHA-256")
        if not artifact_uri.startswith("gs://"):
            raise ValueError("Gate 0 evidence reference must be a private GCS URI")
        return await asyncio.to_thread(
            self._create_once_sync,
            "gate0_evidence",
            evidence_sha256,
            {
                "schema_version": "cloud-gate0-evidence.v1",
                "evidence_sha256": evidence_sha256,
                "artifact_uri": artifact_uri,
            },
        )
