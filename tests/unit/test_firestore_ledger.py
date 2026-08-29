from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from braille_errata_relay.adapters.firestore_ledger import (
    FirestoreGate0Ledger,
    StaleCursorError,
)
from braille_errata_relay.adapters.gcs_artifacts import source_snapshot_ref
from braille_errata_relay.domain.models import (
    DriveChangeBatch,
    DriveChangeSignal,
    SourceLocator,
    SourceMetadata,
    SourceProvider,
    SourceRevision,
    SourceSnapshot,
)


class FakeSnapshot:
    def __init__(self, value: dict[str, object] | None) -> None:
        self.exists = value is not None
        self._value = value

    def to_dict(self) -> dict[str, object] | None:
        return self._value.copy() if self._value is not None else None


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, object]], path: str) -> None:
        self.store = store
        self.path = path

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        del transaction
        return FakeSnapshot(self.store.get(self.path))


class FakeCollection:
    def __init__(self, store: dict[str, dict[str, object]], name: str) -> None:
        self.store = store
        self.name = name

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(self.store, f"{self.name}/{document_id}")


class FakeClient:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, object]] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.store, name)


class FakeTransaction:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self.store = store

    def create(self, ref: FakeDocument, value: dict[str, object]) -> None:
        if ref.path in self.store:
            raise AssertionError(f"duplicate create: {ref.path}")
        self.store[ref.path] = value.copy()

    def set(self, ref: FakeDocument, value: dict[str, object]) -> None:
        self.store[ref.path] = value.copy()


def _ledger(client: FakeClient) -> FirestoreGate0Ledger:
    return FirestoreGate0Ledger(
        project_id="test-project",
        client=client,  # type: ignore[arg-type]
        transaction_runner=lambda callback: callback(FakeTransaction(client.store)),
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )


def _batch() -> tuple[DriveChangeBatch, dict[str, Any]]:
    content = b"# Synthetic V2\n"
    digest = hashlib.sha256(content).hexdigest()
    snapshot = SourceSnapshot(
        revision=SourceRevision(
            revision_id=f"drive:drive-file:63:{digest}",
            metadata=SourceMetadata(
                locator=SourceLocator(
                    provider=SourceProvider.GOOGLE_DRIVE,
                    file_id="drive-file",
                    mime_type="text/markdown",
                ),
                provider_version="63",
                modified_at=None,
                byte_length=len(content),
            ),
            source_sha256=digest,
            fetched_at=datetime(2026, 8, 29, tzinfo=UTC),
        ),
        source_bytes=content,
    )
    batch = DriveChangeBatch(
        start_cursor="cursor-start",
        final_cursor="cursor-final",
        signals=(DriveChangeSignal(file_id="drive-file"),),
        snapshots=(snapshot,),
    )
    ref = source_snapshot_ref(snapshot, bucket_name="relay-bucket")
    return batch, {snapshot.revision.revision_id: ref}


@pytest.mark.asyncio
async def test_change_batch_commits_cursor_receipt_revision_execution_and_outbox_once() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    scope_hash = "a" * 64
    await ledger.initialize_cursor(scope_hash, "cursor-start")
    batch, refs = _batch()

    first = await ledger.commit_change_batch(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )
    replay = await ledger.commit_change_batch(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.receipt_id == first.receipt_id
    assert len(first.outbox_ids) == 1
    assert client.store[f"drive_cursors/{scope_hash}"]["raw_token"] == "cursor-final"
    assert client.store[f"drive_cursors/{scope_hash}"]["state_version"] == 1
    assert sum(path.startswith("event_receipts/") for path in client.store) == 1
    assert sum(path.startswith("source_revisions/") for path in client.store) == 1
    assert sum(path.startswith("execution_attempts/") for path in client.store) == 1
    assert sum(path.startswith("outbox/") for path in client.store) == 1
    assert "source_bytes" not in repr(client.store)
    assert content_not_present(client.store)


def content_not_present(store: dict[str, dict[str, object]]) -> bool:
    return "# Synthetic V2" not in repr(store)


@pytest.mark.asyncio
async def test_stale_cursor_cannot_advance_or_create_work() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    scope_hash = "b" * 64
    await ledger.initialize_cursor(scope_hash, "different-cursor")
    batch, refs = _batch()

    with pytest.raises(StaleCursorError, match="stale"):
        await ledger.commit_change_batch(
            principal_scope_hash=scope_hash,
            batch=batch,
            artifact_refs=refs,
        )

    assert sum(path.startswith("event_receipts/") for path in client.store) == 0
    assert sum(path.startswith("outbox/") for path in client.store) == 0


@pytest.mark.asyncio
async def test_initialize_cursor_never_overwrites_existing_principal_cursor() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    scope_hash = "c" * 64

    first = await ledger.initialize_cursor(scope_hash, "cursor-original")
    repeated = await ledger.initialize_cursor(scope_hash, "cursor-new-from-provider")

    assert repeated == first
    assert repeated.raw_token == "cursor-original"


@pytest.mark.asyncio
async def test_assessment_execution_is_create_once_and_sanitized() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    record: dict[str, object] = {
        "assessment_id": "d" * 64,
        "schema_version": "semantic-assessment.v1",
        "model_id": "gemini-test",
        "prompt_version": "semantic-assessment.v1",
        "latency_ms": 42,
        "outcome": "SCHEMA_VALID",
        "outcome_sha256": "e" * 64,
    }

    assert await ledger.record_assessment_execution(record) is False
    assert await ledger.record_assessment_execution(record) is True
    assert "source" not in client.store[f"execution_attempts/{'d' * 64}"]
