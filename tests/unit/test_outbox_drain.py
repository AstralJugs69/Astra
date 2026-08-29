from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from braille_errata_relay.adapters.firestore_ledger import OutboxLease, StoredSourceRevision
from braille_errata_relay.application.incident_workflow import IncidentWorkflowResult
from braille_errata_relay.application.outbox_drain import OutboxDrainWorkflow
from braille_errata_relay.application.semantic_workflow import SemanticExecutionInProgress
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    IncidentCheckpoint,
    IncidentWorkflowStage,
    RegisteredBaseline,
)


class MemoryOutboxLedger:
    def __init__(self) -> None:
        self.message_id = "a" * 64
        self.status = "PENDING"
        self.attempts = 0
        self.completion: dict[str, object] | None = None
        self.retries: list[str] = []
        self.source = StoredSourceRevision(
            revision_id="drive:file:63:" + "b" * 64,
            source_sha256="b" * 64,
            file_id="file",
            mime_type="text/markdown",
            provider_version="63",
            fetched_at=datetime(2026, 8, 29, tzinfo=UTC),
            artifact=ArtifactRef(
                sha256="b" * 64,
                kind=ArtifactKind.SOURCE_SNAPSHOT,
                byte_length=1,
                uri="gs://relay/sources/file/63/source.md",
            ),
        )

    async def lease_outbox(
        self,
        *,
        lease_token: str,
        limit: int = 10,
        lease_seconds: int = 120,
    ) -> tuple[OutboxLease, ...]:
        assert limit > 0 and lease_seconds > 0
        if self.status != "PENDING":
            return ()
        self.status = "LEASED"
        self.attempts += 1
        return (
            OutboxLease(
                message_id=self.message_id,
                kind="SOURCE_REVISION_CLAIMED",
                payload={"revision_id": self.source.revision_id},
                lease_token=lease_token,
                attempts=self.attempts,
            ),
        )

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None:
        return self.source if revision_id == self.source.revision_id else None

    async def find_baseline_for_file(self, file_id: str) -> RegisteredBaseline | None:
        if file_id != "file":
            return None
        return cast(
            RegisteredBaseline,
            SimpleNamespace(baseline=SimpleNamespace(baseline_id="c" * 64)),
        )

    async def complete_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        result: dict[str, object],
    ) -> bool:
        assert message_id == self.message_id and lease_token
        self.status = "SENT"
        self.completion = result
        return False

    async def retry_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        error_code: str,
        max_attempts: int = 5,
    ) -> None:
        assert message_id == self.message_id and lease_token
        self.retries.append(error_code)
        self.status = "DEAD_LETTER" if self.attempts >= max_attempts else "PENDING"


class RecoveringIncidentWorkflow:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        first_error: RuntimeError | None = None,
    ) -> None:
        self.fail_first = fail_first
        self.first_error = first_error
        self.calls = 0

    async def process_source_revision(
        self,
        *,
        baseline_id: str,
        new_source_revision_id: str,
    ) -> IncidentWorkflowResult:
        self.calls += 1
        if self.calls == 1 and self.first_error is not None:
            raise self.first_error
        if self.fail_first and self.calls == 1:
            raise RuntimeError("simulated crash after lease")
        return IncidentWorkflowResult(
            checkpoint=IncidentCheckpoint(
                incident_id="d" * 64,
                baseline_id=baseline_id,
                new_source_revision_id=new_source_revision_id,
                new_source_sha256="b" * 64,
                production_job_lineage_id="e" * 64,
                stage=IncidentWorkflowStage.REPORT_READY,
                updated_at=datetime(2026, 8, 29, tzinfo=UTC),
            ),
            report=None,
            disposition_packet=None,
        )


@pytest.mark.asyncio
async def test_scheduler_drain_leases_and_completes_source_work_once() -> None:
    ledger = MemoryOutboxLedger()
    incidents = RecoveringIncidentWorkflow()
    workflow = OutboxDrainWorkflow(ledger=ledger, incident_workflow=incidents)

    first = await workflow.drain()
    second = await workflow.drain()

    assert first.leased == first.completed == 1
    assert second.leased == 0
    assert incidents.calls == 1
    assert ledger.status == "SENT"
    assert ledger.completion is not None
    assert ledger.completion["stage"] == "REPORT_READY"


@pytest.mark.asyncio
async def test_failed_drain_is_retryable_and_recovers_without_duplicate_completion() -> None:
    ledger = MemoryOutboxLedger()
    incidents = RecoveringIncidentWorkflow(fail_first=True)
    workflow = OutboxDrainWorkflow(ledger=ledger, incident_workflow=incidents)

    failed = await workflow.drain()
    recovered = await workflow.drain()

    assert failed.retried == 1
    assert recovered.completed == 1
    assert incidents.calls == 2
    assert ledger.status == "SENT"
    assert ledger.retries == ["RuntimeError"]


@pytest.mark.asyncio
async def test_concurrent_scheduler_drains_claim_only_one_message() -> None:
    ledger = MemoryOutboxLedger()
    incidents = RecoveringIncidentWorkflow()
    workflow = OutboxDrainWorkflow(ledger=ledger, incident_workflow=incidents)

    results = await asyncio.gather(workflow.drain(), workflow.drain())

    assert sum(result.leased for result in results) == 1
    assert sum(result.completed for result in results) == 1
    assert incidents.calls == 1


@pytest.mark.asyncio
async def test_active_semantic_lease_retries_outbox_then_resumes_same_incident() -> None:
    ledger = MemoryOutboxLedger()
    incidents = RecoveringIncidentWorkflow(
        first_error=SemanticExecutionInProgress("semantic execution is already leased")
    )
    workflow = OutboxDrainWorkflow(ledger=ledger, incident_workflow=incidents)

    leased = await workflow.drain()
    recovered = await workflow.drain()
    replay = await workflow.drain()

    assert leased.retried == 1
    assert recovered.completed == 1
    assert replay.leased == 0
    assert ledger.retries == ["SemanticExecutionInProgress"]
    assert ledger.completion == {
        "incident_id": "d" * 64,
        "stage": "REPORT_READY",
        "duplicate_source": False,
        "report_sha256": None,
    }
    assert incidents.calls == 2
