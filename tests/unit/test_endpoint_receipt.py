from __future__ import annotations

from datetime import UTC, datetime

import pytest

from braille_errata_relay.adapters.firestore_ledger import (
    EndpointReceiptCommit,
    EndpointVerificationClaim,
    LedgerIntegrityError,
)
from braille_errata_relay.application.endpoint_receipt import (
    EndpointEvidenceConflict,
    EndpointEvidenceRejected,
    EndpointReceiptWorkflow,
    endpoint_receipt_idempotency_key,
)
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactOrigin,
    ArtifactRef,
    BaselineArtifacts,
    BaselineProductionLink,
    BaselineStatus,
    CaptureState,
    EndpointEvidenceSubmission,
    EndpointReceipt,
    ProductionBaseline,
    ProductionLinkBlockingReason,
    RegisteredBaseline,
)

NOW = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)


def _artifact(kind: ArtifactKind, marker: str) -> ArtifactRef:
    return ArtifactRef(
        sha256=marker * 64,
        kind=kind,
        byte_length=1,
        uri=f"gs://test/{kind.value}/{marker * 64}",
    )


def _baseline(
    status: BaselineStatus = BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
) -> RegisteredBaseline:
    return RegisteredBaseline(
        baseline=ProductionBaseline(
            baseline_id="a" * 64,
            production_id="WO-DEMO-001",
            source_revision_id="drive:file:1:" + "b" * 64,
            source_sha256="b" * 64,
            approved_brf_sha256="c" * 64,
            baseline_manifest_sha256="d" * 64,
            translation_profile_sha256="e" * 64,
            artifact_origin=ArtifactOrigin.DEMO_GENERATED_FIXTURE,
            approval_label="DEMO_FIXTURE_APPROVED",
            site_id="demo-site",
            queue_name="Braille-Embosser-Sim",
            scheduler_job_id=19,
            scheduler_job_title=f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
            status=status,
            state_version=1,
        ),
        artifacts=BaselineArtifacts(
            source=_artifact(ArtifactKind.SOURCE_SNAPSHOT, "b"),
            normalized_source=_artifact(ArtifactKind.NORMALIZED_SOURCE, "f"),
            approved_brf=_artifact(ArtifactKind.BASELINE_BRF, "c"),
            source_map=_artifact(ArtifactKind.SOURCE_MAP, "1"),
            manifest=_artifact(ArtifactKind.ARTIFACT_MANIFEST, "d"),
            translation_profile=_artifact(ArtifactKind.TRANSLATION_PROFILE, "e"),
        ),
        created_at=NOW,
    )


def _link() -> BaselineProductionLink:
    return BaselineProductionLink(
        link_id="2" * 64,
        baseline_id="a" * 64,
        scheduler_job_id=19,
        scheduler_job_title=f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
        site_observation_id="9" * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        baseline_brf_sha256="c" * 64,
        baseline_state_version=1,
        idempotency_key_sha256="8" * 64,
        evidence_observed_at=NOW,
        linked_at=NOW,
    )


def _submission(**updates: object) -> EndpointEvidenceSubmission:
    values: dict[str, object] = {
        "baseline_id": "a" * 64,
        "production_link_id": "2" * 64,
        "scheduler_job_id": 19,
        "scheduler_job_title": f"BER|WO-DEMO-001|{'c' * 12}|BASELINE",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
        "approved_baseline_brf_sha256": "c" * 64,
        "endpoint_received_sha256": "c" * 64,
        "capture_manifest_sha256": "3" * 64,
        "terminal_event_sha256": "4" * 64,
        "capture_state": CaptureState.COMPLETED,
        "evidence_timestamp": NOW,
        "expected_baseline_state_version": 1,
        "idempotency_key": endpoint_receipt_idempotency_key(
            baseline_id="a" * 64,
            production_link_id="2" * 64,
            expected_state_version=1,
        ),
    }
    values.update(updates)
    return EndpointEvidenceSubmission.model_validate(values)


class MemoryArtifacts:
    bucket_name = "test"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef:
        existing = self.values.setdefault(ref.uri, artifact)
        assert existing == artifact
        return ref


class MemoryLedger:
    def __init__(self, baseline: RegisteredBaseline | None = None) -> None:
        self.baseline = baseline if baseline is not None else _baseline()
        self.link = _link()
        self.commits: dict[str, EndpointReceiptCommit] = {}
        self.claims: dict[str, tuple[str, EndpointVerificationClaim]] = {}

    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None:
        return self.baseline if baseline_id == self.baseline.baseline.baseline_id else None

    async def get_production_link(self, baseline_id: str) -> BaselineProductionLink | None:
        return self.link if baseline_id == self.link.baseline_id else None

    async def get_endpoint_receipt_by_idempotency(
        self, **values: object
    ) -> EndpointReceiptCommit | None:
        return self.commits.get(str(values["idempotency_key"]))

    async def allocate_endpoint_verification_timestamp(
        self, **values: object
    ) -> EndpointVerificationClaim:
        key = str(values["idempotency_key"])
        identity = str(values["evidence_identity_sha256"])
        existing = self.claims.get(key)
        if existing is not None:
            if existing[0] != identity:
                raise LedgerIntegrityError("endpoint receipt idempotency key conflicts")
            return EndpointVerificationClaim(existing[1].verified_at, True)
        claim = EndpointVerificationClaim(NOW, False)
        self.claims[key] = (identity, claim)
        return claim

    async def confirm_endpoint_receipt(
        self,
        *,
        proposed_receipt: EndpointReceipt,
        expected_state_version: int,
        idempotency_key: str,
    ) -> EndpointReceiptCommit:
        if self.baseline.baseline.state_version != expected_state_version:
            raise BaselineStateConflictError("stale")
        self.baseline = self.baseline.model_copy(
            update={
                "baseline": self.baseline.baseline.model_copy(
                    update={
                        "status": BaselineStatus.PRODUCTION_LINK_VERIFIED,
                        "state_version": expected_state_version + 1,
                    }
                )
            }
        )
        commit = EndpointReceiptCommit(self.baseline, proposed_receipt, False)
        self.commits[idempotency_key] = commit
        return commit

    async def correct_historical_production_link(self, **_values: object) -> RegisteredBaseline:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_exact_endpoint_bytes_are_required_before_verified_transition() -> None:
    ledger = MemoryLedger()
    artifacts = MemoryArtifacts()
    workflow = EndpointReceiptWorkflow(ledger=ledger, artifact_store=artifacts, clock=lambda: NOW)

    first = await workflow.confirm(_submission(), submitting_principal="endpoint@example.com")
    replay = await workflow.confirm(_submission(), submitting_principal="endpoint@example.com")

    assert first.duplicate is False
    assert replay.duplicate is True
    assert first.baseline.baseline.status is BaselineStatus.PRODUCTION_LINK_VERIFIED
    assert first.baseline.baseline.state_version == 2
    assert first.receipt.truth_basis == "SIMULATED_DEMO"
    assert first.receipt.capture_state is CaptureState.COMPLETED
    assert first.receipt.endpoint_received_sha256 == first.receipt.approved_baseline_brf_sha256
    assert len(artifacts.values) == 1


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"production_link_id": "7" * 64}, ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH),
        ({"scheduler_job_id": 20}, ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH),
        ({"scheduler_job_title": "wrong"}, ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH),
        ({"site_id": "wrong-site"}, ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH),
        ({"queue_name": "wrong-queue"}, ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH),
        (
            {"approved_baseline_brf_sha256": "7" * 64, "endpoint_received_sha256": "7" * 64},
            ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_MISMATCH,
        ),
    ],
)
@pytest.mark.asyncio
async def test_mismatched_endpoint_evidence_leaves_baseline_provisional(
    updates: dict[str, object], reason: ProductionLinkBlockingReason
) -> None:
    submission = _submission(**updates)
    submission = submission.model_copy(
        update={
            "idempotency_key": endpoint_receipt_idempotency_key(
                baseline_id=submission.baseline_id,
                production_link_id=submission.production_link_id,
                expected_state_version=submission.expected_baseline_state_version,
            )
        }
    )
    ledger = MemoryLedger()
    workflow = EndpointReceiptWorkflow(
        ledger=ledger, artifact_store=MemoryArtifacts(), clock=lambda: NOW
    )

    with pytest.raises(EndpointEvidenceRejected) as caught:
        await workflow.confirm(submission, submitting_principal="endpoint@example.com")

    assert caught.value.reason is reason
    assert ledger.baseline.baseline.status is BaselineStatus.PROVISIONAL_PRODUCTION_LINK


@pytest.mark.asyncio
async def test_provisional_evidence_rejects_stale_version_and_conflicting_replay() -> None:
    ledger = MemoryLedger()
    workflow = EndpointReceiptWorkflow(
        ledger=ledger, artifact_store=MemoryArtifacts(), clock=lambda: NOW
    )
    await workflow.confirm(_submission(), submitting_principal="endpoint@example.com")

    with pytest.raises(EndpointEvidenceConflict) as conflict:
        await workflow.confirm(
            _submission(capture_manifest_sha256="6" * 64),
            submitting_principal="endpoint@example.com",
        )
    assert conflict.value.reason is ProductionLinkBlockingReason.ENDPOINT_EVIDENCE_CONFLICT

    stale_ledger = MemoryLedger(
        _baseline().model_copy(
            update={"baseline": _baseline().baseline.model_copy(update={"state_version": 2})}
        )
    )
    with pytest.raises(EndpointEvidenceConflict) as stale:
        await EndpointReceiptWorkflow(
            ledger=stale_ledger, artifact_store=MemoryArtifacts(), clock=lambda: NOW
        ).confirm(_submission(), submitting_principal="endpoint@example.com")
    assert stale.value.reason is ProductionLinkBlockingReason.STALE_STATE_VERSION


@pytest.mark.asyncio
async def test_missing_endpoint_evidence_cannot_promote_or_start_incident_processing() -> None:
    ledger = MemoryLedger(_baseline(BaselineStatus.AWAITING_PRODUCTION_LINK))
    workflow = EndpointReceiptWorkflow(
        ledger=ledger, artifact_store=MemoryArtifacts(), clock=lambda: NOW
    )

    with pytest.raises(EndpointEvidenceConflict) as caught:
        await workflow.confirm(_submission(), submitting_principal="endpoint@example.com")

    assert caught.value.reason is ProductionLinkBlockingReason.MISSING_ENDPOINT_EVIDENCE
    assert ledger.baseline.baseline.status is not BaselineStatus.PRODUCTION_LINK_VERIFIED
