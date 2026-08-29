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
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    BaselineProductionLink,
    BaselineStatus,
    DriveChangeBatch,
    IncidentCheckpoint,
    IncidentWorkflowStage,
    RegisteredBaseline,
    SemanticAssessment,
    SiteObservation,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
T = TypeVar("T")


class LedgerIntegrityError(RuntimeError):
    pass


class StaleCursorError(LedgerIntegrityError):
    pass


class SemanticLeaseError(LedgerIntegrityError):
    pass


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
        request_ref = self._document("baseline_production_link_requests", request_id)

        def operation(transaction: Any) -> ProductionLinkCommit:
            baseline_data = self._snapshot_data(baseline_ref.get(transaction=transaction))
            link_data = self._snapshot_data(link_ref.get(transaction=transaction))
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
                if link_data is None or not isinstance(link_data.get("record"), dict):
                    raise LedgerIntegrityError("production link receipt has no immutable link")
                persisted_link = BaselineProductionLink.model_validate(link_data["record"])
                return ProductionLinkCommit(current, persisted_link, True)
            if current.baseline.state_version != expected_state_version:
                raise BaselineStateConflictError("baseline state version is stale")
            if current.baseline.status is not BaselineStatus.AWAITING_PRODUCTION_LINK:
                raise BaselineStateConflictError("baseline is not awaiting a production link")
            if link_data is not None:
                raise LedgerIntegrityError("baseline already has an unreceipted production link")
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
                            "status": BaselineStatus.PRODUCTION_LINK_VERIFIED,
                            "state_version": expected_state_version + 1,
                        }
                    )
                }
            )
            baseline_body = {"record": updated_baseline.model_dump(mode="json")}
            link_body = {"record": proposed_link.model_dump(mode="json")}
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
                    or parsed.new_source_revision_id != checkpoint.new_source_revision_id
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
            for ref, record, existing in zip(
                outbox_refs, outbox_records, outbox_existing, strict=True
            ):
                if existing is None:
                    transaction.create(ref, {**record, "created_at": now})
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
        lease_seconds: int = 120,
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
