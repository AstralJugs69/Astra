"""Bounded scheduler-driven drain for durable source-revision work."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import OutboxLease, StoredSourceRevision
from braille_errata_relay.application.incident_workflow import (
    IncidentWorkflow,
    IncidentWorkflowResult,
)
from braille_errata_relay.domain.models import RegisteredBaseline


class OutboxLedger(Protocol):
    async def lease_outbox(
        self,
        *,
        lease_token: str,
        limit: int = 10,
        lease_seconds: int = 120,
    ) -> tuple[OutboxLease, ...]: ...

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None: ...

    async def find_baseline_for_file(self, file_id: str) -> RegisteredBaseline | None: ...

    async def complete_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        result: Mapping[str, object],
    ) -> bool: ...

    async def retry_outbox(
        self,
        *,
        message_id: str,
        lease_token: str,
        error_code: str,
        max_attempts: int = 5,
    ) -> None: ...


class OutboxProcessingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SourceIncidentWorkflow(Protocol):
    async def process_source_revision(
        self,
        *,
        baseline_id: str,
        new_source_revision_id: str,
    ) -> IncidentWorkflowResult: ...


@dataclass(frozen=True)
class OutboxDrainResult:
    leased: int
    completed: int
    retried: int
    dead_letter_possible: int
    message_ids: tuple[str, ...]
    content_equivalent_replays: int = 0
    content_equivalent_replay_message_ids: tuple[str, ...] = ()
    completed_message_ids: tuple[str, ...] = ()


class OutboxDrainWorkflow:
    def __init__(
        self,
        *,
        ledger: OutboxLedger,
        incident_workflow: SourceIncidentWorkflow | IncidentWorkflow,
        lease_seconds: int = 360,
        max_attempts: int = 5,
    ) -> None:
        self.ledger = ledger
        self.incident_workflow = incident_workflow
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    async def drain(self, *, limit: int = 10) -> OutboxDrainResult:
        if limit < 1 or limit > 100:
            raise ValueError("outbox drain limit is invalid")
        completed = 0
        retried = 0
        dead_letter_possible = 0
        content_equivalent_replays = 0
        content_equivalent_replay_message_ids: list[str] = []
        completed_message_ids: list[str] = []
        message_ids: list[str] = []
        # Claim one message at a time. A semantic assessment can take longer
        # than a short queue scan; leasing a whole batch and then processing it
        # serially would let later leases expire before they are reached.
        for _ in range(limit):
            lease_token = uuid.uuid4().hex
            messages = await self.ledger.lease_outbox(
                lease_token=lease_token,
                limit=1,
                lease_seconds=self.lease_seconds,
            )
            if not messages:
                break
            message = messages[0]
            message_ids.append(message.message_id)
            try:
                if message.kind != "SOURCE_REVISION_CLAIMED":
                    raise OutboxProcessingError("UNSUPPORTED_OUTBOX_KIND")
                revision_id = message.payload.get("revision_id")
                if not isinstance(revision_id, str):
                    raise OutboxProcessingError("MALFORMED_SOURCE_REVISION_MESSAGE")
                source = await self.ledger.get_source_revision(revision_id)
                if source is None:
                    raise OutboxProcessingError("SOURCE_REVISION_NOT_FOUND")
                baseline = await self.ledger.find_baseline_for_file(source.file_id)
                if baseline is None:
                    raise OutboxProcessingError("BASELINE_NOT_REGISTERED")
                outcome = await self.incident_workflow.process_source_revision(
                    baseline_id=baseline.baseline.baseline_id,
                    new_source_revision_id=revision_id,
                )
                await self.ledger.complete_outbox(
                    message_id=message.message_id,
                    lease_token=message.lease_token,
                    result={
                        "incident_id": outcome.checkpoint.incident_id,
                        "stage": outcome.checkpoint.stage.value,
                        "duplicate_source": outcome.duplicate_source,
                        "content_equivalent_replay": outcome.content_equivalent_replay,
                        "report_sha256": (
                            outcome.checkpoint.report.sha256
                            if outcome.checkpoint.report is not None
                            else None
                        ),
                    },
                )
                completed += 1
                completed_message_ids.append(message.message_id)
                if outcome.content_equivalent_replay:
                    content_equivalent_replays += 1
                    content_equivalent_replay_message_ids.append(message.message_id)
            except Exception as exc:  # noqa: BLE001 - lease boundary must durably retry crashes
                error_code = (
                    exc.code if isinstance(exc, OutboxProcessingError) else type(exc).__name__
                )
                await self.ledger.retry_outbox(
                    message_id=message.message_id,
                    lease_token=message.lease_token,
                    error_code=error_code[:128],
                    max_attempts=self.max_attempts,
                )
                retried += 1
                if message.attempts >= self.max_attempts:
                    dead_letter_possible += 1
                # Retry backoff is deliberately observed on a later drain. In
                # particular, a memory-backed or nonstandard ledger must not
                # be able to spin the same failing message within one request.
                break
        return OutboxDrainResult(
            leased=len(message_ids),
            completed=completed,
            retried=retried,
            dead_letter_possible=dead_letter_possible,
            message_ids=tuple(message_ids),
            content_equivalent_replays=content_equivalent_replays,
            content_equivalent_replay_message_ids=tuple(content_equivalent_replay_message_ids),
            completed_message_ids=tuple(completed_message_ids),
        )
