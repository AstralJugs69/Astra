"""Durable same-file Drive reconciliation workflow for Cloud Gate 0."""

from __future__ import annotations

from dataclasses import dataclass

from braille_errata_relay.adapters.drive import DriveBlobProvider, DriveChangeReconciler
from braille_errata_relay.adapters.firestore_ledger import FirestoreGate0Ledger
from braille_errata_relay.adapters.gcs_artifacts import GcsArtifactStore, source_snapshot_ref
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import (
    DriveChangeBatch,
    DriveChangeSignal,
    SourceLocator,
    SourceProvider,
)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


@dataclass(frozen=True)
class DriveGate0Result:
    operation: str
    file_id_sha256: str
    source_revision_ids: tuple[str, ...]
    source_sha256: tuple[str, ...]
    start_cursor_sha256: str
    final_cursor_sha256: str
    receipt_id: str
    execution_id: str
    outbox_ids: tuple[str, ...]
    duplicate_replay: bool
    source_unavailable: bool = False
    new_outbox_ids: tuple[str, ...] = ()

    def sanitized_record(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "file_id_sha256": self.file_id_sha256,
            "source_revision_ids_sha256": [
                canonical_sha256({"revision_id": revision_id})
                for revision_id in self.source_revision_ids
            ],
            "source_sha256": list(self.source_sha256),
            "start_cursor_sha256": self.start_cursor_sha256,
            "final_cursor_sha256": self.final_cursor_sha256,
            "receipt_id": self.receipt_id,
            "execution_id": self.execution_id,
            "outbox_ids": list(self.outbox_ids),
            "new_outbox_ids": list(self.new_outbox_ids),
            "duplicate_replay": self.duplicate_replay,
            "source_unavailable": self.source_unavailable,
        }


class DriveGate0Workflow:
    def __init__(
        self,
        *,
        provider: DriveBlobProvider,
        reconciler: DriveChangeReconciler,
        artifact_store: GcsArtifactStore,
        ledger: FirestoreGate0Ledger,
        runtime_service_account_email: str,
    ) -> None:
        self.provider = provider
        self.reconciler = reconciler
        self.artifact_store = artifact_store
        self.ledger = ledger
        self.locator = SourceLocator(
            provider=SourceProvider.GOOGLE_DRIVE,
            file_id=provider.expected_file_id,
            mime_type=provider.supported_mime_type,
        )
        self.principal_scope_hash = canonical_sha256(
            {
                "principal": runtime_service_account_email,
                "scope": DRIVE_READONLY_SCOPE,
                "file_id": provider.expected_file_id,
            }
        )

    async def _store_and_commit(
        self,
        *,
        operation: str,
        batch: DriveChangeBatch,
    ) -> DriveGate0Result:
        refs = {}
        for snapshot in batch.snapshots:
            ref = source_snapshot_ref(snapshot, bucket_name=self.artifact_store.bucket_name)
            await self.artifact_store.put_once(snapshot.source_bytes, ref=ref)
            await self.artifact_store.read(ref)
            refs[snapshot.revision.revision_id] = ref
        committed = await self.ledger.commit_change_batch(
            principal_scope_hash=self.principal_scope_hash,
            batch=batch,
            artifact_refs=refs,
        )
        return DriveGate0Result(
            operation=operation,
            file_id_sha256=canonical_sha256({"file_id": self.provider.expected_file_id}),
            source_revision_ids=tuple(
                snapshot.revision.revision_id for snapshot in batch.snapshots
            ),
            source_sha256=tuple(snapshot.revision.source_sha256 for snapshot in batch.snapshots),
            start_cursor_sha256=canonical_sha256({"cursor": batch.start_cursor}),
            final_cursor_sha256=canonical_sha256({"cursor": batch.final_cursor}),
            receipt_id=committed.receipt_id,
            execution_id=committed.execution_id,
            outbox_ids=committed.outbox_ids,
            duplicate_replay=committed.duplicate,
            source_unavailable=(
                any(signal.removed for signal in batch.signals) and not batch.snapshots
            ),
            new_outbox_ids=committed.new_outbox_ids,
        )

    async def initialize(self) -> DriveGate0Result:
        cursor = await self.ledger.get_cursor(self.principal_scope_hash)
        if cursor is None:
            start = await self.reconciler.get_start_cursor()
            cursor = await self.ledger.initialize_cursor(self.principal_scope_hash, start)
        snapshot = await self.provider.fetch_revision(self.locator)
        batch = DriveChangeBatch(
            start_cursor=cursor.raw_token,
            final_cursor=cursor.raw_token,
            signals=(DriveChangeSignal(file_id=self.provider.expected_file_id),),
            snapshots=(snapshot,),
        )
        return await self._store_and_commit(operation="INITIALIZE", batch=batch)

    async def reconcile(self) -> DriveGate0Result:
        cursor = await self.ledger.get_cursor(self.principal_scope_hash)
        if cursor is None:
            raise RuntimeError("Drive reconciliation cursor has not been initialized")
        batch = await self.reconciler.drain(cursor.raw_token)
        return await self._store_and_commit(operation="RECONCILE", batch=batch)
