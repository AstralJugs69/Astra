from __future__ import annotations

import hashlib
from copy import deepcopy
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
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactOrigin,
    ArtifactRef,
    BaselineArtifacts,
    BaselineLinkCorrection,
    BaselineProductionLink,
    BaselineStatus,
    CaptureState,
    DriveChangeBatch,
    DriveChangeSignal,
    EndpointReceipt,
    IncidentCheckpoint,
    IncidentWorkflowStage,
    ProductionBaseline,
    RegisteredBaseline,
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


@pytest.mark.asyncio
async def test_change_batch_recovers_a_preexisting_identical_source_claim_without_rewriting_fetch_time() -> (
    None
):
    client = FakeClient()
    ledger = _ledger(client)
    scope_hash = "d" * 64
    await ledger.initialize_cursor(scope_hash, "cursor-start")
    batch, refs = _batch()
    _, revision_records, _ = ledger._prepare_change_records(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )
    revision = revision_records[0]
    original_fetched_at = datetime(2026, 8, 28, tzinfo=UTC)
    existing = {
        **revision,
        "fetched_at": original_fetched_at,
    }
    existing["payload_sha256"] = canonical_sha256(existing)
    client.store[f"source_revisions/{revision['revision_id']}"] = {
        **existing,
        "claimed_at": original_fetched_at,
    }

    committed = await ledger.commit_change_batch(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )

    assert committed.duplicate is False
    assert len(committed.outbox_ids) == 1
    persisted = client.store[f"source_revisions/{revision['revision_id']}"]
    assert persisted["fetched_at"] == original_fetched_at
    assert sum(path.startswith("event_receipts/") for path in client.store) == 1
    assert sum(path.startswith("outbox/") for path in client.store) == 1


@pytest.mark.asyncio
async def test_change_batch_rejects_a_preexisting_source_claim_with_different_immutable_lineage() -> (
    None
):
    client = FakeClient()
    ledger = _ledger(client)
    scope_hash = "e" * 64
    await ledger.initialize_cursor(scope_hash, "cursor-start")
    batch, refs = _batch()
    _, revision_records, _ = ledger._prepare_change_records(
        principal_scope_hash=scope_hash,
        batch=batch,
        artifact_refs=refs,
    )
    revision = revision_records[0]
    conflicting = {
        **revision,
        "provider_version": "different-provider-version",
    }
    conflicting["payload_sha256"] = canonical_sha256(conflicting)
    client.store[f"source_revisions/{revision['revision_id']}"] = conflicting

    with pytest.raises(LedgerIntegrityError, match="source revision claim conflicts"):
        await ledger.commit_change_batch(
            principal_scope_hash=scope_hash,
            batch=batch,
            artifact_refs=refs,
        )


def content_not_present(store: dict[str, dict[str, object]]) -> bool:
    return "# Synthetic V2" not in repr(store)


def _registered_baseline() -> RegisteredBaseline:
    def artifact(kind: ArtifactKind, marker: str) -> ArtifactRef:
        return ArtifactRef(
            sha256=marker * 64,
            kind=kind,
            byte_length=10,
            uri=f"gs://relay-test/{kind.value.lower()}/{marker * 64}",
        )

    return RegisteredBaseline(
        baseline=ProductionBaseline(
            baseline_id="a" * 64,
            production_id="WO-DEMO-001",
            source_revision_id="drive:file:62:" + "b" * 64,
            source_sha256="b" * 64,
            source_file_id="file",
            approved_brf_sha256="c" * 64,
            baseline_manifest_sha256="d" * 64,
            translation_profile_sha256="e" * 64,
            artifact_origin=ArtifactOrigin.DEMO_GENERATED_FIXTURE,
            approval_label="DEMO_FIXTURE_APPROVED",
            site_id="demo-site",
            queue_name="Braille-Embosser-Sim",
        ),
        artifacts=BaselineArtifacts(
            source=artifact(ArtifactKind.SOURCE_SNAPSHOT, "b"),
            normalized_source=artifact(ArtifactKind.NORMALIZED_SOURCE, "f"),
            approved_brf=artifact(ArtifactKind.BASELINE_BRF, "c"),
            source_map=artifact(ArtifactKind.SOURCE_MAP, "1"),
            manifest=artifact(ArtifactKind.ARTIFACT_MANIFEST, "d"),
            translation_profile=artifact(ArtifactKind.TRANSLATION_PROFILE, "e"),
        ),
        created_at=datetime(2026, 8, 29, tzinfo=UTC),
    )


def _production_link(idempotency_key: str) -> BaselineProductionLink:
    return BaselineProductionLink(
        link_id="2" * 64,
        baseline_id="a" * 64,
        scheduler_job_id=42,
        scheduler_job_title=f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
        site_observation_id="9" * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        baseline_brf_sha256="c" * 64,
        baseline_state_version=1,
        idempotency_key_sha256=canonical_sha256(
            {"scope": "baseline-production-link", "key": idempotency_key}
        ),
        evidence_observed_at=datetime(2026, 8, 29, tzinfo=UTC),
        linked_at=datetime(2026, 8, 29, tzinfo=UTC),
    )


def _endpoint_receipt(idempotency_key: str, *, receipt_id: str = "6" * 64) -> EndpointReceipt:
    return EndpointReceipt(
        receipt_id=receipt_id,
        baseline_id="a" * 64,
        production_link_id="2" * 64,
        scheduler_job_id=42,
        scheduler_job_title=f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
        site_id="demo-site",
        queue_name="Braille-Embosser-Sim",
        approved_baseline_brf_sha256="c" * 64,
        endpoint_received_sha256="c" * 64,
        capture_manifest_sha256="7" * 64,
        terminal_event_sha256="8" * 64,
        capture_state=CaptureState.COMPLETED,
        evidence_timestamp=datetime(2026, 8, 29, tzinfo=UTC),
        verified_at=datetime(2026, 8, 29, tzinfo=UTC),
        submitting_principal="endpoint@example.iam.gserviceaccount.com",
        idempotency_key_sha256=canonical_sha256(
            {"scope": "endpoint-receipt", "key": idempotency_key}
        ),
        expected_baseline_state_version=1,
        baseline_state_version=2,
        artifact_uri=f"gs://test/endpoint-receipts/{receipt_id}.json",
    )


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
async def test_production_link_transaction_is_versioned_immutable_and_idempotent() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    baseline = _registered_baseline()
    idempotency_key = "link-request"
    link = _production_link(idempotency_key)
    await ledger.register_baseline(baseline)

    first = await ledger.link_baseline_production(
        proposed_link=link,
        expected_state_version=0,
        idempotency_key=idempotency_key,
    )
    replay = await ledger.get_production_link_by_idempotency(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=42,
        expected_state_version=0,
        idempotency_key=idempotency_key,
    )
    repeated = await ledger.link_baseline_production(
        proposed_link=link,
        expected_state_version=0,
        idempotency_key=idempotency_key,
    )

    assert first.duplicate is False
    assert replay is not None and replay.duplicate is True
    assert repeated.duplicate is True
    assert first.link == replay.link == repeated.link
    assert first.baseline.baseline.status is BaselineStatus.PROVISIONAL_PRODUCTION_LINK
    assert first.baseline.baseline.state_version == 1
    assert sum(path.startswith("baseline_production_links/") for path in client.store) == 1
    assert sum(path.startswith("baseline_production_link_requests/") for path in client.store) == 1


@pytest.mark.asyncio
async def test_production_link_transaction_rejects_stale_version_without_overwrite() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    baseline = _registered_baseline()
    updated = baseline.model_copy(
        update={"baseline": baseline.baseline.model_copy(update={"state_version": 1})}
    )
    await ledger.register_baseline(updated)

    with pytest.raises(BaselineStateConflictError, match="stale"):
        await ledger.link_baseline_production(
            proposed_link=_production_link("stale-request"),
            expected_state_version=0,
            idempotency_key="stale-request",
        )

    persisted = await ledger.get_baseline(baseline.baseline.baseline_id)
    assert persisted == updated
    assert not any(path.startswith("baseline_production_links/") for path in client.store)


@pytest.mark.asyncio
async def test_endpoint_receipt_transaction_is_exact_versioned_and_replay_safe() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    baseline = _registered_baseline()
    await ledger.register_baseline(baseline)
    await ledger.link_baseline_production(
        proposed_link=_production_link("link-request"),
        expected_state_version=0,
        idempotency_key="link-request",
    )
    receipt = _endpoint_receipt("receipt-request")

    first = await ledger.confirm_endpoint_receipt(
        proposed_receipt=receipt,
        expected_state_version=1,
        idempotency_key="receipt-request",
    )
    replay = await ledger.confirm_endpoint_receipt(
        proposed_receipt=receipt,
        expected_state_version=1,
        idempotency_key="receipt-request",
    )

    assert first.duplicate is False
    assert replay.duplicate is True
    assert first.baseline.baseline.status is BaselineStatus.PRODUCTION_LINK_VERIFIED
    assert first.baseline.baseline.state_version == 2
    assert sum(path.startswith("endpoint_receipts/") for path in client.store) == 1
    assert sum(path.startswith("baseline_endpoint_confirmations/") for path in client.store) == 1


@pytest.mark.asyncio
async def test_endpoint_receipt_rejects_stale_and_conflicting_idempotency_without_overwrite() -> (
    None
):
    client = FakeClient()
    ledger = _ledger(client)
    await ledger.register_baseline(_registered_baseline())
    await ledger.link_baseline_production(
        proposed_link=_production_link("link-request"),
        expected_state_version=0,
        idempotency_key="link-request",
    )
    receipt = _endpoint_receipt("receipt-request")
    await ledger.confirm_endpoint_receipt(
        proposed_receipt=receipt,
        expected_state_version=1,
        idempotency_key="receipt-request",
    )

    with pytest.raises(LedgerIntegrityError, match="idempotency key conflicts"):
        await ledger.confirm_endpoint_receipt(
            proposed_receipt=_endpoint_receipt("receipt-request", receipt_id="5" * 64),
            expected_state_version=1,
            idempotency_key="receipt-request",
        )
    with pytest.raises(BaselineStateConflictError, match="stale"):
        await ledger.confirm_endpoint_receipt(
            proposed_receipt=_endpoint_receipt("different-request", receipt_id="4" * 64),
            expected_state_version=1,
            idempotency_key="different-request",
        )
    assert sum(path.startswith("endpoint_receipts/") for path in client.store) == 1


@pytest.mark.asyncio
async def test_historical_link_correction_is_append_only_before_endpoint_repair() -> None:
    client = FakeClient()
    ledger = _ledger(client)
    baseline = _registered_baseline()
    historical = baseline.model_copy(
        update={
            "baseline": baseline.baseline.model_copy(
                update={
                    "scheduler_job_id": 42,
                    "scheduler_job_title": f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
                    "status": BaselineStatus.PRODUCTION_LINK_VERIFIED,
                    "state_version": 1,
                }
            )
        }
    )
    await ledger.register_baseline(historical)
    legacy_link = _production_link("legacy").model_copy(
        update={
            "schema_version": "baseline-production-link.v1",
            "linked_at": None,
            "verified_at": datetime(2026, 8, 29, tzinfo=UTC),
            "verification_basis": "READ_ONLY_EXACT_JOB_QUEUE_TITLE_AND_HASH_PREFIX",
        }
    )
    client.store["baseline_production_links/" + baseline.baseline.baseline_id] = {
        "record": legacy_link.model_dump(mode="json")
    }
    key = "historical-correction"
    correction = BaselineLinkCorrection(
        correction_id="3" * 64,
        baseline_id=baseline.baseline.baseline_id,
        production_link_id=legacy_link.link_id,
        expected_baseline_state_version=1,
        baseline_state_version=2,
        prior_report_id="4" * 64,
        corrected_at=datetime(2026, 8, 29, tzinfo=UTC),
        submitting_principal="endpoint@example.iam.gserviceaccount.com",
        idempotency_key_sha256=canonical_sha256(
            {"scope": "historical-production-link-correction", "key": key}
        ),
    )

    corrected = await ledger.correct_historical_production_link(
        proposed_correction=correction,
        expected_state_version=1,
        idempotency_key=key,
    )
    replay = await ledger.correct_historical_production_link(
        proposed_correction=correction,
        expected_state_version=1,
        idempotency_key=key,
    )

    assert corrected.baseline.status is BaselineStatus.PROVISIONAL_PRODUCTION_LINK
    assert corrected.baseline.state_version == 2
    assert replay == corrected
    assert sum(path.startswith("baseline_link_corrections/") for path in client.store) == 1
    assert sum(path.startswith("endpoint_receipts/") for path in client.store) == 0


@pytest.mark.asyncio
async def test_endpoint_receipt_transaction_crash_then_retry_converges() -> None:
    client = FakeClient()
    setup = _ledger(client)
    await setup.register_baseline(_registered_baseline())
    await setup.link_baseline_production(
        proposed_link=_production_link("link-request"),
        expected_state_version=0,
        idempotency_key="link-request",
    )
    attempts = 0

    def atomic_runner(callback: Any) -> Any:
        nonlocal attempts
        staged = deepcopy(client.store)
        result = callback(FakeTransaction(staged))
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated crash before transaction commit")
        client.store.clear()
        client.store.update(staged)
        return result

    ledger = FirestoreGate0Ledger(
        project_id="test-project",
        client=client,  # type: ignore[arg-type]
        transaction_runner=atomic_runner,
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )
    receipt = _endpoint_receipt("receipt-request")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await ledger.confirm_endpoint_receipt(
            proposed_receipt=receipt,
            expected_state_version=1,
            idempotency_key="receipt-request",
        )
    assert not any(path.startswith("endpoint_receipts/") for path in client.store)

    recovered = await ledger.confirm_endpoint_receipt(
        proposed_receipt=receipt,
        expected_state_version=1,
        idempotency_key="receipt-request",
    )

    assert recovered.baseline.baseline.status is BaselineStatus.PRODUCTION_LINK_VERIFIED
    assert sum(path.startswith("endpoint_receipts/") for path in client.store) == 1


@pytest.mark.asyncio
async def test_endpoint_verification_timestamp_is_allocated_once_and_conflicts_closed() -> None:
    client = FakeClient()
    clock = [datetime(2026, 8, 29, tzinfo=UTC)]
    ledger = _ledger_with_clock(client, clock)

    first = await ledger.allocate_endpoint_verification_timestamp(
        baseline_id="a" * 64,
        expected_state_version=1,
        idempotency_key="receipt-request",
        evidence_identity_sha256="b" * 64,
        submitting_principal="endpoint@example.iam.gserviceaccount.com",
    )
    clock[0] += timedelta(minutes=1)
    replay = await ledger.allocate_endpoint_verification_timestamp(
        baseline_id="a" * 64,
        expected_state_version=1,
        idempotency_key="receipt-request",
        evidence_identity_sha256="b" * 64,
        submitting_principal="endpoint@example.iam.gserviceaccount.com",
    )

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.verified_at == first.verified_at
    with pytest.raises(LedgerIntegrityError, match="idempotency key conflicts"):
        await ledger.allocate_endpoint_verification_timestamp(
            baseline_id="a" * 64,
            expected_state_version=1,
            idempotency_key="receipt-request",
            evidence_identity_sha256="c" * 64,
            submitting_principal="endpoint@example.iam.gserviceaccount.com",
        )


@pytest.mark.asyncio
async def test_report_timestamps_are_transactionally_allocated_once_across_replay() -> None:
    client = FakeClient()
    now = [datetime(2026, 8, 29, 17, 0, tzinfo=UTC)]
    ledger = _ledger_with_clock(client, now)
    detected = IncidentCheckpoint(
        incident_id="6" * 64,
        baseline_id="7" * 64,
        new_source_revision_id="drive:file:63:" + "8" * 64,
        new_source_sha256="8" * 64,
        production_job_lineage_id="9" * 64,
        updated_at=now[0],
    )
    await ledger.claim_incident(detected)
    semantic_ready = detected.model_copy(
        update={
            "stage": IncidentWorkflowStage.SEMANTIC_READY,
            "state_version": 1,
            "updated_at": now[0],
        }
    )
    await ledger.advance_incident(semantic_ready, expected_state_version=0)

    allocated = await ledger.allocate_report_created_at(
        incident_id=detected.incident_id,
        expected_state_version=1,
    )
    created_at = allocated.checkpoint.report_created_at
    now[0] += timedelta(seconds=10)
    allocation_replay = await ledger.allocate_report_created_at(
        incident_id=detected.incident_id,
        expected_state_version=1,
    )
    report_ref = ArtifactRef(
        sha256="a" * 64,
        kind=ArtifactKind.REPORT,
        byte_length=10,
        uri="gs://relay-test/reports/report.json",
    )
    packet_ref = ArtifactRef(
        sha256="b" * 64,
        kind=ArtifactKind.HUMAN_DISPOSITION_PACKET,
        byte_length=10,
        uri="gs://relay-test/disposition/packet.json",
    )
    report_ready = allocated.checkpoint.model_copy(
        update={
            "stage": IncidentWorkflowStage.REPORT_READY,
            "state_version": allocated.checkpoint.state_version + 1,
            "report": report_ref,
            "disposition_packet": packet_ref,
            "updated_at": now[0],
        }
    )
    committed = await ledger.advance_incident(
        report_ready,
        expected_state_version=allocated.checkpoint.state_version,
    )
    ready_at = committed.checkpoint.report_ready_at
    now[0] += timedelta(minutes=1)
    ready_replay = await ledger.advance_incident(
        report_ready,
        expected_state_version=allocated.checkpoint.state_version,
    )

    assert created_at == datetime(2026, 8, 29, 17, 0, tzinfo=UTC)
    assert allocation_replay.duplicate is True
    assert allocation_replay.checkpoint.report_created_at == created_at
    assert ready_at == datetime(2026, 8, 29, 17, 0, 10, tzinfo=UTC)
    assert ready_replay.duplicate is True
    assert ready_replay.checkpoint.report_ready_at == ready_at


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
