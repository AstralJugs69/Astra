"""Firestore Gate 0 ledger with pure transactions and a transactional outbox."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from google.cloud import firestore

from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import ArtifactRef, DriveChangeBatch

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
T = TypeVar("T")


class LedgerIntegrityError(RuntimeError):
    pass


class StaleCursorError(LedgerIntegrityError):
    pass


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
                elif existing.get("payload_sha256") != record["payload_sha256"]:
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

    async def record_assessment_execution(self, sanitized_record: Mapping[str, object]) -> bool:
        assessment_id = sanitized_record.get("assessment_id")
        if not isinstance(assessment_id, str):
            raise TypeError("assessment execution record has no assessment ID")
        return await asyncio.to_thread(
            self._create_once_sync,
            "execution_attempts",
            assessment_id,
            sanitized_record,
        )

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
