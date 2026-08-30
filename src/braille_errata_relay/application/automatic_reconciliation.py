"""Private scheduler cycle for automatic Drive reconciliation and recovery work."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import (
    AutomationCycleClaim,
    AutomationCycleClaimStatus,
    AutomationCycleLedgerState,
)
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
from braille_errata_relay.application.outbox_drain import OutboxDrainWorkflow
from braille_errata_relay.contracts.canonical_json import canonical_sha256


class AutomationCycleLedger(Protocol):
    async def claim_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> AutomationCycleClaim: ...

    async def complete_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        result: dict[str, object],
    ) -> bool: ...

    async def fail_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        error_code: str,
    ) -> bool: ...

    async def get_automation_cycle_state(
        self,
        *,
        cycle_key: str,
    ) -> AutomationCycleLedgerState | None: ...


class AutomationCycleStatus(StrEnum):
    COMPLETED = "COMPLETED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


_SCHEDULER_REQUEST_DEADLINE_SECONDS = 300
_COMPLETION_MARGIN_SECONDS = 30


@dataclass(frozen=True)
class AutomaticReconciliationResult:
    cycle_id: str
    status: AutomationCycleStatus
    source_change_detected: bool = False
    source_revision_count: int = 0
    drive_duplicate_replay: bool = False
    content_equivalent_replay: bool = False
    source_investigation_pending: bool = False
    drive_receipt_id: str | None = None
    source_unavailable: bool = False
    outbox_leased: int = 0
    outbox_completed: int = 0
    outbox_retried: int = 0
    outbox_dead_letter_possible: int = 0

    def sanitized_record(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "status": self.status.value,
            "source_change_detected": self.source_change_detected,
            "source_revision_count": self.source_revision_count,
            "drive_duplicate_replay": self.drive_duplicate_replay,
            "content_equivalent_replay": self.content_equivalent_replay,
            "source_investigation_pending": self.source_investigation_pending,
            "drive_receipt_id": self.drive_receipt_id,
            "source_unavailable": self.source_unavailable,
            "outbox": {
                "leased": self.outbox_leased,
                "completed": self.outbox_completed,
                "retried": self.outbox_retried,
                "dead_letter_possible": self.outbox_dead_letter_possible,
            },
        }


class AutomaticReconciliationWorkflow:
    """Run one lease-protected poll and process only durable revision work.

    Drive's change feed and authoritative byte refetch establish source truth.
    The scheduler request is only a wake-up signal.  If the process fails after
    committing the Drive cursor but before draining the outbox, the next cycle
    sees the durable pending message and resumes the incident workflow.
    """

    def __init__(
        self,
        *,
        drive_workflow: DriveGate0Workflow,
        outbox_workflow: OutboxDrainWorkflow,
        ledger: AutomationCycleLedger,
        lease_seconds: int = 360,
        drive_timeout_seconds: float = 60.0,
        outbox_timeout_seconds: float = 210.0,
    ) -> None:
        if lease_seconds < 300:
            raise ValueError("automation cycle lease must outlive the scheduler request deadline")
        if drive_timeout_seconds <= 0 or outbox_timeout_seconds <= 0:
            raise ValueError("automation cycle time budgets must be positive")
        if (
            drive_timeout_seconds + outbox_timeout_seconds
            > _SCHEDULER_REQUEST_DEADLINE_SECONDS - _COMPLETION_MARGIN_SECONDS
        ):
            raise ValueError("automation cycle budgets leave no durable completion margin")
        self.drive_workflow = drive_workflow
        self.outbox_workflow = outbox_workflow
        self.ledger = ledger
        self.lease_seconds = lease_seconds
        self.drive_timeout_seconds = drive_timeout_seconds
        self.outbox_timeout_seconds = outbox_timeout_seconds
        self.cycle_key = canonical_sha256(
            {
                "purpose": "AUTOMATIC_DRIVE_RECONCILIATION",
                "principal_scope_hash": drive_workflow.principal_scope_hash,
            }
        )

    async def run(self, *, outbox_limit: int = 1) -> AutomaticReconciliationResult:
        if outbox_limit != 1:
            raise ValueError("automation cycle outbox limit is invalid")
        lease_token = uuid.uuid4().hex
        cycle_id = canonical_sha256(
            {
                "cycle_key": self.cycle_key,
                "lease_token": lease_token,
            }
        )
        claim = await self.ledger.claim_automation_cycle(
            cycle_key=self.cycle_key,
            cycle_id=cycle_id,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
        )
        if claim.status is AutomationCycleClaimStatus.IN_PROGRESS:
            return AutomaticReconciliationResult(
                cycle_id=claim.cycle_id,
                status=AutomationCycleStatus.ALREADY_RUNNING,
            )

        try:
            drive_error: Exception | None = None
            try:
                # The Drive check gets its own bounded budget.  Its failure
                # must not strand a source revision that was already committed
                # to the durable outbox during a prior invocation.
                async with asyncio.timeout(self.drive_timeout_seconds):
                    drive_result = await self.drive_workflow.reconcile()
            except Exception as exc:  # noqa: BLE001 - continue durable recovery work
                # A Drive failure must be retried, but it must not strand an
                # already-committed source revision in the transactional outbox.
                drive_error = exc
                drive_result = None

            # One outbox item is deliberately processed in a separate budget.
            # A semantic assessment may use two bounded 90-second attempts;
            # the remaining margin is reserved for the completion transaction.
            async with asyncio.timeout(self.outbox_timeout_seconds):
                outbox_result = await self.outbox_workflow.drain(limit=outbox_limit)
            if drive_error is not None:
                raise drive_error
            if drive_result is None:
                raise RuntimeError("Drive reconciliation returned no result")
            status = AutomationCycleStatus.COMPLETED
            if drive_result.source_unavailable:
                status = AutomationCycleStatus.SOURCE_UNAVAILABLE
            if outbox_result.dead_letter_possible:
                status = AutomationCycleStatus.NEEDS_ATTENTION
            # This cycle can legally recover an older durable outbox record
            # while a newly observed Drive revision remains pending.  Only
            # records newly created by this reconciliation describe the
            # current Drive observation; pre-existing matching outbox records
            # are recovery work, not fresh source-content evidence.
            new_source_outbox_ids = set(drive_result.new_outbox_ids)
            content_equivalent_replay = bool(
                new_source_outbox_ids.intersection(
                    outbox_result.content_equivalent_replay_message_ids
                )
            )
            source_investigation_pending = bool(
                new_source_outbox_ids.difference(outbox_result.completed_message_ids)
            )
            new_revision_count = (
                0
                if drive_result.duplicate_replay or content_equivalent_replay
                else len(new_source_outbox_ids)
            )
            result = AutomaticReconciliationResult(
                cycle_id=cycle_id,
                status=status,
                source_change_detected=new_revision_count > 0,
                source_revision_count=new_revision_count,
                drive_duplicate_replay=drive_result.duplicate_replay,
                content_equivalent_replay=content_equivalent_replay,
                source_investigation_pending=source_investigation_pending,
                drive_receipt_id=drive_result.receipt_id,
                source_unavailable=drive_result.source_unavailable,
                outbox_leased=outbox_result.leased,
                outbox_completed=outbox_result.completed,
                outbox_retried=outbox_result.retried,
                outbox_dead_letter_possible=outbox_result.dead_letter_possible,
            )
            completed = await self.ledger.complete_automation_cycle(
                cycle_key=self.cycle_key,
                cycle_id=cycle_id,
                lease_token=lease_token,
                result=result.sanitized_record(),
            )
            if not completed:
                raise RuntimeError("automation cycle lease was lost before completion")
            return result
        except Exception as exc:
            failed = await self.ledger.fail_automation_cycle(
                cycle_key=self.cycle_key,
                cycle_id=cycle_id,
                lease_token=lease_token,
                error_code=type(exc).__name__[:128],
            )
            if not failed:
                raise RuntimeError("automation cycle failure could not be recorded") from exc
            raise

    async def status(self) -> dict[str, object]:
        """Return one browser-safe, durable status record for the watch floor."""

        durable = await self.ledger.get_automation_cycle_state(cycle_key=self.cycle_key)
        if durable is None:
            return {
                "schema_version": "automation-cycle-status.v1",
                "state": "NOT_YET_RUN",
                "last_outcome": None,
                "last_status": None,
                "last_completed_at": None,
                "source_change_detected": False,
                "content_equivalent_replay": False,
                "source_investigation_pending": False,
                "source_unavailable": False,
                "outbox": {
                    "leased": 0,
                    "completed": 0,
                    "retried": 0,
                    "dead_letter_possible": 0,
                },
                "last_error_code": None,
            }
        result = durable.last_result or {}
        status = result.get("status")
        last_status = status if status in {item.value for item in AutomationCycleStatus} else None
        outbox = result.get("outbox")
        safe_outbox = outbox if isinstance(outbox, dict) else {}

        def safe_count(name: str) -> int:
            value = safe_outbox.get(name)
            return value if isinstance(value, int) and value >= 0 else 0

        return {
            "schema_version": "automation-cycle-status.v1",
            "state": durable.state,
            "last_outcome": durable.last_outcome,
            "last_status": last_status,
            "last_completed_at": (
                durable.last_completed_at.isoformat()
                if durable.last_completed_at is not None
                else None
            ),
            "source_change_detected": result.get("source_change_detected") is True,
            "content_equivalent_replay": result.get("content_equivalent_replay") is True,
            "source_investigation_pending": result.get("source_investigation_pending") is True,
            "source_unavailable": result.get("source_unavailable") is True,
            "outbox": {
                "leased": safe_count("leased"),
                "completed": safe_count("completed"),
                "retried": safe_count("retried"),
                "dead_letter_possible": safe_count("dead_letter_possible"),
            },
            "last_error_code": durable.last_error_code,
        }
