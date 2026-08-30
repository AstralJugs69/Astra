from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest

from braille_errata_relay.adapters.firestore_ledger import (
    AutomationCycleClaim,
    AutomationCycleClaimStatus,
    AutomationCycleLedgerState,
)
from braille_errata_relay.application.automatic_reconciliation import (
    AutomaticReconciliationWorkflow,
    AutomationCycleStatus,
)
from braille_errata_relay.application.drive_gate0 import DriveGate0Result, DriveGate0Workflow
from braille_errata_relay.application.outbox_drain import OutboxDrainResult, OutboxDrainWorkflow


def _drive_result(
    *,
    changed: bool,
    duplicate: bool = False,
    source_unavailable: bool = False,
    new_outbox: bool | None = None,
    outbox_id: str = "1" * 64,
) -> DriveGate0Result:
    revision_ids = ("drive:file:63:" + "b" * 64,) if changed else ()
    source_hashes = ("b" * 64,) if changed else ()
    return DriveGate0Result(
        operation="RECONCILE",
        file_id_sha256="a" * 64,
        source_revision_ids=revision_ids,
        source_sha256=source_hashes,
        start_cursor_sha256="c" * 64,
        final_cursor_sha256="d" * 64,
        receipt_id="e" * 64,
        execution_id="f" * 64,
        outbox_ids=(outbox_id,) if changed else (),
        duplicate_replay=duplicate,
        source_unavailable=source_unavailable,
        new_outbox_ids=(outbox_id,) if (changed if new_outbox is None else new_outbox) else (),
    )


def _outbox_result(
    *,
    leased: int = 0,
    completed: int = 0,
    retried: int = 0,
    dead_letter_possible: int = 0,
    content_equivalent_replays: int = 0,
    content_equivalent_replay_message_ids: tuple[str, ...] = (),
    message_ids: tuple[str, ...] | None = None,
    completed_message_ids: tuple[str, ...] | None = None,
) -> OutboxDrainResult:
    effective_message_ids = (
        tuple(str(index) * 64 for index in range(1, leased + 1))
        if message_ids is None
        else message_ids
    )
    return OutboxDrainResult(
        leased=leased,
        completed=completed,
        retried=retried,
        dead_letter_possible=dead_letter_possible,
        message_ids=effective_message_ids,
        content_equivalent_replays=content_equivalent_replays,
        content_equivalent_replay_message_ids=content_equivalent_replay_message_ids,
        completed_message_ids=(
            effective_message_ids
            if completed_message_ids is None and completed
            else completed_message_ids or ()
        ),
    )


class FakeDriveWorkflow:
    principal_scope_hash = "9" * 64

    def __init__(self, outcomes: Sequence[DriveGate0Result | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def reconcile(self) -> DriveGate0Result:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeOutboxWorkflow:
    def __init__(self, outcomes: Sequence[OutboxDrainResult | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.limits: list[int] = []
        self.completed_processing_calls = 0

    async def drain(self, *, limit: int = 10) -> OutboxDrainResult:
        self.limits.append(limit)
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.completed:
            self.completed_processing_calls += 1
        return outcome


class HangingDriveWorkflow:
    principal_scope_hash = "9" * 64

    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self) -> DriveGate0Result:
        self.calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class MemoryAutomationLedger:
    def __init__(self, *, in_progress: bool = False) -> None:
        self.in_progress = in_progress
        self.active_cycle_id: str | None = None
        self.claims = 0
        self.completions: list[dict[str, object]] = []
        self.failures: list[str] = []
        self.status_state: AutomationCycleLedgerState | None = None

    async def claim_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> AutomationCycleClaim:
        assert len(cycle_key) == 64
        assert lease_token
        assert lease_seconds == 360
        self.claims += 1
        if self.in_progress:
            return AutomationCycleClaim(
                cycle_key=cycle_key,
                cycle_id="8" * 64,
                status=AutomationCycleClaimStatus.IN_PROGRESS,
            )
        self.active_cycle_id = cycle_id
        return AutomationCycleClaim(
            cycle_key=cycle_key,
            cycle_id=cycle_id,
            status=AutomationCycleClaimStatus.ACQUIRED,
            lease_token=lease_token,
        )

    async def complete_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        result: dict[str, object],
    ) -> bool:
        assert len(cycle_key) == 64
        assert cycle_id == self.active_cycle_id
        assert lease_token
        self.completions.append(result)
        self.active_cycle_id = None
        self.status_state = AutomationCycleLedgerState(
            cycle_key=cycle_key,
            state="IDLE",
            active_cycle_id=cycle_id,
            lease_expires_at=None,
            last_execution_cycle_id=cycle_id,
            last_outcome="COMPLETED",
            last_result=result,
            last_error_code=None,
            last_completed_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
        return True

    async def fail_automation_cycle(
        self,
        *,
        cycle_key: str,
        cycle_id: str,
        lease_token: str,
        error_code: str,
    ) -> bool:
        assert len(cycle_key) == 64
        assert cycle_id == self.active_cycle_id
        assert lease_token
        self.failures.append(error_code)
        self.active_cycle_id = None
        self.status_state = AutomationCycleLedgerState(
            cycle_key=cycle_key,
            state="IDLE",
            active_cycle_id=cycle_id,
            lease_expires_at=None,
            last_execution_cycle_id=cycle_id,
            last_outcome="FAILED",
            last_result=None,
            last_error_code=error_code,
            last_completed_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
        return True

    async def get_automation_cycle_state(
        self,
        *,
        cycle_key: str,
    ) -> AutomationCycleLedgerState | None:
        if self.status_state is not None:
            assert self.status_state.cycle_key == cycle_key
        return self.status_state


def _workflow(
    drive: FakeDriveWorkflow,
    outbox: FakeOutboxWorkflow,
    ledger: MemoryAutomationLedger,
) -> AutomaticReconciliationWorkflow:
    return AutomaticReconciliationWorkflow(
        drive_workflow=cast(DriveGate0Workflow, drive),
        outbox_workflow=cast(OutboxDrainWorkflow, outbox),
        ledger=ledger,
    )


@pytest.mark.asyncio
async def test_unchanged_drive_poll_runs_no_model_work_when_outbox_is_empty() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=False)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.status is AutomationCycleStatus.COMPLETED
    assert result.source_change_detected is False
    assert result.source_revision_count == 0
    assert result.outbox_leased == 0
    assert outbox.completed_processing_calls == 0
    assert outbox.limits == [1]
    assert len(ledger.completions) == 1
    assert ledger.failures == []


@pytest.mark.asyncio
async def test_changed_drive_content_drains_one_durable_incident_work_item() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True)])
    outbox = FakeOutboxWorkflow([_outbox_result(leased=1, completed=1)])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.source_change_detected is True
    assert result.source_revision_count == 1
    assert result.outbox_leased == result.outbox_completed == 1
    assert outbox.completed_processing_calls == 1
    assert ledger.completions[0]["source_change_detected"] is True


@pytest.mark.asyncio
async def test_duplicate_or_stale_drive_replay_is_quiet_when_no_outbox_work_remains() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True, duplicate=True)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.drive_duplicate_replay is True
    assert result.source_change_detected is False
    assert result.source_revision_count == 0
    assert outbox.completed_processing_calls == 0


@pytest.mark.asyncio
async def test_content_equivalent_drive_resave_is_visible_without_new_investigation() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True)])
    outbox = FakeOutboxWorkflow(
        [
            _outbox_result(
                leased=1,
                completed=1,
                content_equivalent_replays=1,
                content_equivalent_replay_message_ids=("1" * 64,),
            )
        ]
    )
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()
    status = await _workflow(drive, outbox, ledger).status()

    assert result.content_equivalent_replay is True
    assert result.source_change_detected is False
    assert result.source_revision_count == 0
    assert ledger.completions[0]["content_equivalent_replay"] is True
    assert status["content_equivalent_replay"] is True


@pytest.mark.asyncio
async def test_older_content_equivalent_recovery_does_not_hide_new_drive_content() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True, outbox_id="2" * 64)])
    outbox = FakeOutboxWorkflow(
        [
            _outbox_result(
                leased=1,
                completed=1,
                content_equivalent_replays=1,
                content_equivalent_replay_message_ids=("1" * 64,),
            )
        ]
    )
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.content_equivalent_replay is False
    assert result.source_change_detected is True
    assert result.source_revision_count == 1
    assert result.source_investigation_pending is True


@pytest.mark.asyncio
async def test_replayed_drive_revision_with_preexisting_outbox_is_not_new_source_content() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True, new_outbox=False)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.source_change_detected is False
    assert result.source_investigation_pending is False
    assert result.content_equivalent_replay is False


@pytest.mark.asyncio
async def test_overlapping_scheduler_wake_returns_in_progress_without_touching_drive() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True)])
    outbox = FakeOutboxWorkflow([_outbox_result(leased=1, completed=1)])
    ledger = MemoryAutomationLedger(in_progress=True)

    result = await _workflow(drive, outbox, ledger).run()

    assert result.status is AutomationCycleStatus.ALREADY_RUNNING
    assert result.cycle_id == "8" * 64
    assert drive.calls == 0
    assert outbox.calls == 0
    assert ledger.completions == []
    assert ledger.failures == []


@pytest.mark.asyncio
async def test_drive_failure_records_failure_but_still_recovers_preexisting_outbox_work() -> None:
    drive = FakeDriveWorkflow([RuntimeError("Drive unavailable")])
    outbox = FakeOutboxWorkflow([_outbox_result(leased=1, completed=1)])
    ledger = MemoryAutomationLedger()

    with pytest.raises(RuntimeError, match="Drive unavailable"):
        await _workflow(drive, outbox, ledger).run()

    assert outbox.completed_processing_calls == 1
    assert ledger.completions == []
    assert ledger.failures == ["RuntimeError"]


@pytest.mark.asyncio
async def test_restart_after_outbox_failure_recovers_without_reprocessing_source_signal() -> None:
    drive = FakeDriveWorkflow(
        [_drive_result(changed=True), _drive_result(changed=False, duplicate=True)]
    )
    outbox = FakeOutboxWorkflow(
        [RuntimeError("outbox unavailable"), _outbox_result(leased=1, completed=1)]
    )
    ledger = MemoryAutomationLedger()
    workflow = _workflow(drive, outbox, ledger)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await workflow.run()
    recovered = await workflow.run()

    assert recovered.source_change_detected is False
    assert recovered.outbox_completed == 1
    assert outbox.completed_processing_calls == 1
    assert ledger.failures == ["RuntimeError"]
    assert len(ledger.completions) == 1


@pytest.mark.asyncio
async def test_removed_source_is_visible_without_enqueuing_model_work() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=False, source_unavailable=True)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.status is AutomationCycleStatus.SOURCE_UNAVAILABLE
    assert result.source_unavailable is True
    assert result.source_change_detected is False
    assert outbox.completed_processing_calls == 0
    assert ledger.completions[0]["status"] == "SOURCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_terminal_outbox_failure_is_visible_as_needs_attention() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=True)])
    outbox = FakeOutboxWorkflow([_outbox_result(leased=1, retried=1, dead_letter_possible=1)])
    ledger = MemoryAutomationLedger()

    result = await _workflow(drive, outbox, ledger).run()

    assert result.status is AutomationCycleStatus.NEEDS_ATTENTION
    assert result.outbox_dead_letter_possible == 1
    assert ledger.completions[0]["status"] == "NEEDS_ATTENTION"


@pytest.mark.asyncio
async def test_drive_timeout_still_recovers_existing_durable_outbox_work() -> None:
    drive = HangingDriveWorkflow()
    outbox = FakeOutboxWorkflow([_outbox_result(leased=1, completed=1)])
    ledger = MemoryAutomationLedger()
    workflow = AutomaticReconciliationWorkflow(
        drive_workflow=cast(DriveGate0Workflow, drive),
        outbox_workflow=cast(OutboxDrainWorkflow, outbox),
        ledger=ledger,
        drive_timeout_seconds=0.01,
        outbox_timeout_seconds=1.0,
    )

    with pytest.raises(TimeoutError):
        await workflow.run()

    assert drive.calls == 1
    assert outbox.completed_processing_calls == 1
    assert ledger.failures == ["TimeoutError"]


@pytest.mark.asyncio
async def test_durable_status_reports_completed_and_failed_cycle_without_raw_error() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=False)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()
    workflow = _workflow(drive, outbox, ledger)

    assert (await workflow.status())["state"] == "NOT_YET_RUN"
    await workflow.run()
    completed = await workflow.status()
    assert completed["state"] == "IDLE"
    assert completed["last_outcome"] == "COMPLETED"
    assert completed["last_status"] == "COMPLETED"
    assert completed["last_error_code"] is None

    failed_drive = FakeDriveWorkflow([RuntimeError("sensitive error")])
    failed_outbox = FakeOutboxWorkflow([_outbox_result()])
    failed_ledger = MemoryAutomationLedger()
    failed_workflow = _workflow(failed_drive, failed_outbox, failed_ledger)
    with pytest.raises(RuntimeError):
        await failed_workflow.run()
    failed = await failed_workflow.status()
    assert failed["last_outcome"] == "FAILED"
    assert failed["last_status"] is None
    assert failed["last_error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_automatic_cycle_rejects_unbounded_outbox_batching_before_claim() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=False)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    with pytest.raises(ValueError, match="outbox limit"):
        await _workflow(drive, outbox, ledger).run(outbox_limit=2)

    assert ledger.claims == 0
    assert drive.calls == 0
    assert outbox.calls == 0


def test_automatic_cycle_rejects_unsafe_scheduler_time_budget() -> None:
    drive = FakeDriveWorkflow([_drive_result(changed=False)])
    outbox = FakeOutboxWorkflow([_outbox_result()])
    ledger = MemoryAutomationLedger()

    with pytest.raises(ValueError, match="completion margin"):
        AutomaticReconciliationWorkflow(
            drive_workflow=cast(DriveGate0Workflow, drive),
            outbox_workflow=cast(OutboxDrainWorkflow, outbox),
            ledger=ledger,
            drive_timeout_seconds=100.0,
            outbox_timeout_seconds=200.0,
        )
