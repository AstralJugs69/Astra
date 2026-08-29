from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from braille_errata_relay.adapters.firestore_ledger import (
    FirestoreGate0Ledger,
    LedgerIntegrityError,
    SemanticClaimStatus,
    StaleCursorError,
)
from braille_errata_relay.adapters.gcs_artifacts import source_snapshot_ref
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import (
    DriveChangeBatch,
    DriveChangeSignal,
    SemanticAssessment,
    SiteObservation,
    SourceLocator,
    SourceMetadata,
    SourceProvider,
    SourceRevision,
    SourceSnapshot,
)


class FakeSnapshot:
    def __init__(
        self,
        value: dict[str, object] | None,
        reference: FakeDocument | None = None,
    ) -> None:
        self.exists = value is not None
        self._value = value
        self.reference = reference

    def to_dict(self) -> dict[str, object] | None:
        return self._value.copy() if self._value is not None else None


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, object]], path: str) -> None:
        self.store = store
        self.path = path

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        del transaction
        return FakeSnapshot(self.store.get(self.path), self)


class FakeQuery:
    def __init__(self, store: dict[str, dict[str, object]], collection: str) -> None:
        self.store = store
        self.collection = collection
        self.maximum = 100

    def where(self, **_values: object) -> FakeQuery:
        return self

    def order_by(self, *_values: object) -> FakeQuery:
        return self

    def limit(self, value: int) -> FakeQuery:
        self.maximum = value
        return self

    def stream(self) -> list[FakeSnapshot]:
        prefix = f"{self.collection}/"
        rows = sorted(
            (path, value) for path, value in self.store.items() if path.startswith(prefix)
        )
        return [
            FakeSnapshot(value, FakeDocument(self.store, path))
            for path, value in rows[: self.maximum]
        ]


class FakeCollection:
    def __init__(self, store: dict[str, dict[str, object]], name: str) -> None:
        self.store = store
        self.name = name

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(self.store, f"{self.name}/{document_id}")

    def where(self, **values: object) -> FakeQuery:
        return FakeQuery(self.store, self.name).where(**values)


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

    def get(self, query: FakeQuery) -> list[FakeSnapshot]:
        return query.stream()


def _ledger(client: FakeClient) -> FirestoreGate0Ledger:
    return FirestoreGate0Ledger(
        project_id="test-project",
        client=client,  # type: ignore[arg-type]
        transaction_runner=lambda callback: callback(FakeTransaction(client.store)),
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )


def _ledger_with_clock(
    client: FakeClient,
    clock: list[datetime],
) -> FirestoreGate0Ledger:
    return FirestoreGate0Ledger(
        project_id="test-project",
        client=client,  # type: ignore[arg-type]
        transaction_runner=lambda callback: callback(FakeTransaction(client.store)),
        clock=lambda: clock[0],
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
async def test_semantic_execution_is_claimed_before_model_and_reuses_first_result() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    execution_key = "d" * 64
    lease_token = "lease-one"
    assessment = SemanticAssessment(
        assessment_id="e" * 64,
        analysis_revision=1,
        model_id="gemini-test",
        prompt_version="semantic-assessment.v1",
        materiality="MATERIAL",
        change_kind="FACTUAL_CORRECTION",
        summary="The scientific referent changed.",
        rationale=("The old and new terms identify different organelles.",),
        evidence_span_ids=("old:block-17", "new:block-17"),
        uncertainties=(),
        confidence="MEDIUM",
        requires_professional_review=True,
    )
    record: dict[str, object] = {
        "assessment_id": assessment.assessment_id,
        "schema_version": "semantic-assessment.v1",
        "model_id": "gemini-test",
        "prompt_version": "semantic-assessment.v1",
        "latency_ms": 42,
        "outcome": "SCHEMA_VALID",
        "outcome_sha256": "e" * 64,
    }

    claimed = await ledger.claim_semantic_execution(
        execution_key=execution_key,
        evidence_sha256="f" * 64,
        model_id="gemini-test",
        prompt_version="semantic-assessment.v1",
        analysis_revision=1,
        lease_token=lease_token,
    )
    assert claimed.status is SemanticClaimStatus.ACQUIRED
    assert (
        await ledger.complete_semantic_execution(
            execution_key=execution_key,
            lease_token=lease_token,
            assessment=assessment,
        )
        is False
    )
    assert (
        await ledger.record_semantic_attempt(
            execution_key=execution_key,
            lease_token=lease_token,
            sanitized_record=record,
        )
        is False
    )

    replay = await ledger.claim_semantic_execution(
        execution_key=execution_key,
        evidence_sha256="f" * 64,
        model_id="gemini-test",
        prompt_version="semantic-assessment.v1",
        analysis_revision=1,
        lease_token="lease-two",
    )

    assert replay.status is SemanticClaimStatus.READY
    assert replay.assessment == assessment
    assert sum(path.startswith("semantic_executions/") for path in client.store) == 1
    assert sum(path.startswith("semantic_attempts/") for path in client.store) == 1
    assert "source" not in repr(client.store)


@pytest.mark.asyncio
async def test_expired_semantic_lease_is_recovered_after_worker_crash() -> None:
    client = FakeClient()
    now = [datetime(2026, 8, 29, tzinfo=UTC)]
    ledger = _ledger_with_clock(client, now)
    values = {
        "execution_key": "1" * 64,
        "evidence_sha256": "2" * 64,
        "model_id": "gemini-test",
        "prompt_version": "semantic-assessment.v1",
        "analysis_revision": 1,
    }

    first = await ledger.claim_semantic_execution(
        **values,
        lease_token="crashed-worker",
        lease_seconds=10,
    )
    competing = await ledger.claim_semantic_execution(
        **values,
        lease_token="early-worker",
        lease_seconds=10,
    )
    now[0] += timedelta(seconds=11)
    recovered = await ledger.claim_semantic_execution(
        **values,
        lease_token="recovery-worker",
        lease_seconds=10,
    )

    assert first.status is SemanticClaimStatus.ACQUIRED
    assert competing.status is SemanticClaimStatus.IN_PROGRESS
    assert recovered.status is SemanticClaimStatus.ACQUIRED
    stored = client.store[f"semantic_executions/{'1' * 64}"]
    assert stored["attempt_count"] == 2
    assert stored["state_version"] == 1


def _site_observation(
    *,
    sequence: int,
    previous: str | None,
) -> tuple[SiteObservation, str]:
    observed_at = "2026-08-29T00:00:00+00:00"
    body: dict[str, object] = {
        "schema_version": "site-observation.v1",
        "site_id": "demo-site",
        "bridge_id": "demo-bridge",
        "queue_name": "Braille-Embosser-Sim",
        "sequence": sequence,
        "observed_at": observed_at,
        "observations": [],
        "printer_state": "idle",
        "printer_state_reasons": [],
        "printer_accepting_jobs": True,
        "previous_observation_sha256": previous,
        "source": "cups_read_only_observer",
    }
    digest = canonical_sha256(body)
    payload = {**body, "observation_id": digest}
    return SiteObservation.model_validate(payload), digest


@pytest.mark.asyncio
async def test_site_observation_transaction_enforces_sequence_chain_and_replay() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    first, first_hash = _site_observation(sequence=1, previous=None)
    second, second_hash = _site_observation(sequence=2, previous=first_hash)

    accepted = await ledger.ingest_site_observation(first, payload_sha256=first_hash)
    duplicate = await ledger.ingest_site_observation(first, payload_sha256=first_hash)
    advanced = await ledger.ingest_site_observation(second, payload_sha256=second_hash)

    assert accepted.duplicate is False
    assert duplicate.duplicate is True
    assert advanced.duplicate is False
    assert sum(path.startswith("site_observations/") for path in client.store) == 2
    head = next(
        value for path, value in client.store.items() if path.startswith("site_observation_heads/")
    )
    assert head["sequence"] == 2
    assert head["observation_id"] == second_hash

    out_of_order, out_of_order_hash = _site_observation(
        sequence=4,
        previous=second_hash,
    )
    with pytest.raises(LedgerIntegrityError, match="out of order"):
        await ledger.ingest_site_observation(
            out_of_order,
            payload_sha256=out_of_order_hash,
        )


@pytest.mark.asyncio
async def test_transactional_outbox_lease_completion_and_expiry_recovery() -> None:
    client = FakeClient()
    now = [datetime(2026, 8, 29, tzinfo=UTC)]
    ledger = _ledger_with_clock(client, now)
    scope_hash = "9" * 64
    await ledger.initialize_cursor(scope_hash, "cursor-start")
    batch, refs = _batch()
    committed = await ledger.commit_change_batch(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )
    message_id = committed.outbox_ids[0]

    first = await ledger.lease_outbox(
        lease_token="crashed-drain",
        limit=1,
        lease_seconds=10,
    )
    competing = await ledger.lease_outbox(
        lease_token="competing-drain",
        limit=1,
        lease_seconds=10,
    )
    now[0] += timedelta(seconds=11)
    recovered = await ledger.lease_outbox(
        lease_token="recovered-drain",
        limit=1,
        lease_seconds=10,
    )

    assert len(first) == 1
    assert competing == ()
    assert len(recovered) == 1
    assert recovered[0].attempts == 2
    assert (
        await ledger.complete_outbox(
            message_id=message_id,
            lease_token="recovered-drain",
            result={"stage": "REPORT_READY", "incident_id": "8" * 64},
        )
        is False
    )
    assert (
        await ledger.lease_outbox(
            lease_token="after-completion",
            limit=1,
            lease_seconds=10,
        )
        == ()
    )
    stored = client.store[f"outbox/{message_id}"]
    assert stored["status"] == "SENT"
    assert stored["result_sha256"] == canonical_sha256(stored["result"])


@pytest.mark.asyncio
async def test_outbox_retry_uses_bounded_backoff_before_releasing_work() -> None:
    client = FakeClient()
    now = [datetime(2026, 8, 29, tzinfo=UTC)]
    ledger = _ledger_with_clock(client, now)
    scope_hash = "7" * 64
    await ledger.initialize_cursor(scope_hash, "cursor-start")
    batch, refs = _batch()
    committed = await ledger.commit_change_batch(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )
    message_id = committed.outbox_ids[0]
    leased = await ledger.lease_outbox(
        lease_token="failed-drain",
        limit=1,
        lease_seconds=10,
    )
    assert len(leased) == 1

    await ledger.retry_outbox(
        message_id=message_id,
        lease_token="failed-drain",
        error_code="TRANSIENT_TEST_FAILURE",
        max_attempts=5,
    )
    assert (
        await ledger.lease_outbox(
            lease_token="too-early",
            limit=1,
            lease_seconds=10,
        )
        == ()
    )
    now[0] += timedelta(seconds=3)
    retry = await ledger.lease_outbox(
        lease_token="after-backoff",
        limit=1,
        lease_seconds=10,
    )
    assert len(retry) == 1
    assert retry[0].attempts == 2
