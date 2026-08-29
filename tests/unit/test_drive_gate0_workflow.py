from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast

import pytest

from braille_errata_relay.adapters.drive import DriveBlobProvider, DriveChangeReconciler
from braille_errata_relay.adapters.firestore_ledger import (
    ChangeCommitResult,
    CursorState,
    FirestoreGate0Ledger,
)
from braille_errata_relay.adapters.gcs_artifacts import GcsArtifactStore
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
from braille_errata_relay.domain.models import (
    ArtifactRef,
    DriveChangeBatch,
    SourceLocator,
    SourceMetadata,
    SourceProvider,
    SourceRevision,
    SourceSnapshot,
)


def _snapshot() -> SourceSnapshot:
    source = b"# Synthetic V1\n"
    digest = hashlib.sha256(source).hexdigest()
    return SourceSnapshot(
        revision=SourceRevision(
            revision_id=f"drive:file-id:62:{digest}",
            metadata=SourceMetadata(
                locator=SourceLocator(
                    provider=SourceProvider.GOOGLE_DRIVE,
                    file_id="file-id",
                    mime_type="text/markdown",
                ),
                provider_version="62",
                byte_length=len(source),
            ),
            source_sha256=digest,
            fetched_at=datetime(2026, 8, 29, tzinfo=UTC),
        ),
        source_bytes=source,
    )


class FakeProvider:
    expected_file_id = "file-id"
    supported_mime_type = "text/markdown"

    async def fetch_revision(self, _locator: SourceLocator) -> SourceSnapshot:
        return _snapshot()


class FakeReconciler:
    async def get_start_cursor(self) -> str:
        return "cursor-start"


class FakeStore:
    bucket_name = "relay-bucket"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def put_once(self, _artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef:
        self.events.append("gcs-put")
        return ref

    async def read(self, _ref: ArtifactRef) -> bytes:
        self.events.append("gcs-read")
        return _snapshot().source_bytes


class FakeLedger:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cursor: CursorState | None = None
        self.commits = 0

    async def get_cursor(self, _scope: str) -> CursorState | None:
        return self.cursor

    async def initialize_cursor(self, _scope: str, raw_token: str) -> CursorState:
        self.cursor = CursorState(raw_token, hashlib.sha256(raw_token.encode()).hexdigest(), 0)
        return self.cursor

    async def commit_change_batch(
        self,
        *,
        principal_scope_hash: str,
        batch: DriveChangeBatch,
        artifact_refs: dict[str, ArtifactRef],
    ) -> ChangeCommitResult:
        del principal_scope_hash, batch, artifact_refs
        self.events.append("firestore-commit")
        self.commits += 1
        return ChangeCommitResult(
            receipt_id="a" * 64,
            execution_id="b" * 64,
            outbox_ids=("c" * 64,),
            final_cursor_sha256="d" * 64,
            duplicate=False,
        )


@pytest.mark.asyncio
async def test_initialize_stores_bytes_before_one_durable_cursor_commit() -> None:
    events: list[str] = []
    ledger = FakeLedger(events)
    workflow = DriveGate0Workflow(
        provider=cast(DriveBlobProvider, FakeProvider()),
        reconciler=cast(DriveChangeReconciler, FakeReconciler()),
        artifact_store=cast(GcsArtifactStore, FakeStore(events)),
        ledger=cast(FirestoreGate0Ledger, ledger),
        runtime_service_account_email="runtime@example.iam.gserviceaccount.com",
    )

    result = await workflow.initialize()
    sanitized = result.sanitized_record()

    assert events == ["gcs-put", "gcs-read", "firestore-commit"]
    assert ledger.commits == 1
    assert result.duplicate_replay is False
    assert sanitized["file_id_sha256"] != "file-id"
    assert "cursor-start" not in repr(sanitized)
    assert "runtime@example" not in repr(sanitized)


@pytest.mark.asyncio
async def test_reconcile_requires_a_persisted_cursor_before_drive_call() -> None:
    events: list[str] = []
    workflow = DriveGate0Workflow(
        provider=cast(DriveBlobProvider, FakeProvider()),
        reconciler=cast(DriveChangeReconciler, FakeReconciler()),
        artifact_store=cast(GcsArtifactStore, FakeStore(events)),
        ledger=cast(FirestoreGate0Ledger, FakeLedger(events)),
        runtime_service_account_email="runtime@example.iam.gserviceaccount.com",
    )

    with pytest.raises(RuntimeError, match="not been initialized"):
        await workflow.reconcile()

    assert events == []
