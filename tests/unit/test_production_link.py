from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from braille_errata_relay.adapters.firestore_ledger import ProductionLinkCommit
from braille_errata_relay.application.production_link import (
    ProductionLinkConflict,
    ProductionLinkRejected,
    ProductionLinkWorkflow,
    canonical_baseline_job_title,
    production_link_idempotency_key,
    production_link_supersession_idempotency_key,
)
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactOrigin,
    ArtifactRef,
    BaselineArtifacts,
    BaselineProductionLink,
    BaselineStatus,
    JobState,
    ProductionBaseline,
    ProductionLinkBlockingReason,
    QueueObservation,
    RegisteredBaseline,
    SiteObservation,
)

NOW = datetime(2026, 8, 29, 17, 0, 10, tzinfo=UTC)


def _artifact(kind: ArtifactKind, marker: str) -> ArtifactRef:
    return ArtifactRef(
        sha256=marker * 64,
        kind=kind,
        byte_length=10,
        uri=f"gs://relay-test/{kind.value.lower()}/{marker * 64}",
    )


def _baseline() -> RegisteredBaseline:
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
            source=_artifact(ArtifactKind.SOURCE_SNAPSHOT, "b"),
            normalized_source=_artifact(ArtifactKind.NORMALIZED_SOURCE, "f"),
            approved_brf=_artifact(ArtifactKind.BASELINE_BRF, "c"),
            source_map=_artifact(ArtifactKind.SOURCE_MAP, "1"),
            manifest=_artifact(ArtifactKind.ARTIFACT_MANIFEST, "d"),
            translation_profile=_artifact(ArtifactKind.TRANSLATION_PROFILE, "e"),
        ),
        created_at=NOW - timedelta(minutes=5),
    )


def _observation(
    baseline: RegisteredBaseline,
    *,
    observed_at: datetime | None = None,
    scheduler_job_id: int = 42,
    title: str | None = None,
    destination: str = "Braille-Embosser-Sim",
    queue_name: str = "Braille-Embosser-Sim",
    duplicate_job: bool = False,
) -> SiteObservation:
    job = QueueObservation(
        scheduler_job_id=scheduler_job_id,
        owner="relay-operator",
        title=title or canonical_baseline_job_title(baseline),
        destination=destination,
        state=JobState.PROCESSING,
        observed_at=observed_at or NOW - timedelta(seconds=1),
        impressions_completed=1,
    )
    jobs = (job, job) if duplicate_job else (job,)
    return SiteObservation(
        observation_id="9" * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name=queue_name,
        sequence=1,
        observed_at=observed_at or NOW - timedelta(seconds=1),
        observations=jobs,
    )


class MemoryLinkLedger:
    def __init__(
        self,
        baseline: RegisteredBaseline,
        observation: SiteObservation | None,
    ) -> None:
        self.baseline = baseline
        self.observation = observation
        self.commits: dict[str, ProductionLinkCommit] = {}
        self.supersession_commits: dict[str, ProductionLinkCommit] = {}
        self.active_link: BaselineProductionLink | None = None
        self.records: dict[str, BaselineProductionLink] = {}
        self.confirmed_link_id: str | None = None

    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None:
        return self.baseline if baseline_id == self.baseline.baseline.baseline_id else None

    async def get_latest_site_observation(self, **_values: str) -> SiteObservation | None:
        return self.observation

    async def get_production_link_by_idempotency(
        self,
        **values: object,
    ) -> ProductionLinkCommit | None:
        commit = self.commits.get(str(values["idempotency_key"]))
        if commit is None:
            return None
        assert commit.link.scheduler_job_id == values["scheduler_job_id"]
        return ProductionLinkCommit(commit.baseline, commit.link, True)

    async def link_baseline_production(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        if self.baseline.baseline.state_version != expected_state_version:
            raise BaselineStateConflictError("stale")
        self.baseline = self.baseline.model_copy(
            update={
                "baseline": self.baseline.baseline.model_copy(
                    update={
                        "scheduler_job_id": proposed_link.scheduler_job_id,
                        "scheduler_job_title": proposed_link.scheduler_job_title,
                        "status": BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
                        "state_version": expected_state_version + 1,
                    }
                )
            }
        )
        commit = ProductionLinkCommit(self.baseline, proposed_link, False)
        self.commits[idempotency_key] = commit
        self.active_link = proposed_link
        self.records[proposed_link.link_id] = proposed_link
        return commit

    async def get_production_link(self, baseline_id: str) -> BaselineProductionLink | None:
        if baseline_id != self.baseline.baseline.baseline_id:
            return None
        return self.active_link

    async def get_endpoint_receipt_for_link(
        self,
        *,
        baseline_id: str,
        production_link_id: str,
    ) -> object | None:
        if baseline_id == self.baseline.baseline.baseline_id and (
            production_link_id == self.confirmed_link_id
        ):
            return object()
        return None

    async def get_production_link_supersession_by_idempotency(
        self,
        **values: object,
    ) -> ProductionLinkCommit | None:
        commit = self.supersession_commits.get(str(values["idempotency_key"]))
        if commit is None:
            return None
        assert commit.link.scheduler_job_id == values["scheduler_job_id"]
        return ProductionLinkCommit(commit.baseline, commit.link, True)

    async def supersede_baseline_production(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit:
        if self.baseline.baseline.state_version != expected_state_version:
            raise BaselineStateConflictError("stale")
        assert self.active_link is not None
        assert proposed_link.supersedes_production_link_id == self.active_link.link_id
        self.records.setdefault(self.active_link.link_id, self.active_link)
        self.records[proposed_link.link_id] = proposed_link
        self.active_link = proposed_link
        self.baseline = self.baseline.model_copy(
            update={
                "baseline": self.baseline.baseline.model_copy(
                    update={
                        "scheduler_job_id": proposed_link.scheduler_job_id,
                        "scheduler_job_title": proposed_link.scheduler_job_title,
                        "status": BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
                        "state_version": expected_state_version + 1,
                    }
                )
            }
        )
        commit = ProductionLinkCommit(self.baseline, proposed_link, False)
        self.supersession_commits[idempotency_key] = commit
        return commit


def _key(baseline: RegisteredBaseline, *, version: int = 0, job_id: int = 42) -> str:
    return production_link_idempotency_key(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=job_id,
        expected_state_version=version,
    )


def test_new_baseline_is_explicitly_not_production_linked() -> None:
    baseline = _baseline()

    assert baseline.baseline.status is BaselineStatus.AWAITING_PRODUCTION_LINK
    assert baseline.baseline.state_version == 0
    assert baseline.baseline.scheduler_job_id is None
    assert baseline.baseline.scheduler_job_title is None


@pytest.mark.asyncio
async def test_fresh_exact_observation_creates_one_idempotent_production_link() -> None:
    baseline = _baseline()
    ledger = MemoryLinkLedger(baseline, _observation(baseline))
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )
    values = {
        "baseline_id": baseline.baseline.baseline_id,
        "scheduler_job_id": 42,
        "expected_state_version": 0,
        "idempotency_key": _key(baseline),
    }

    first = await workflow.link(**values)
    ledger.observation = None
    replay = await workflow.link(**values)

    assert first.duplicate is False
    assert replay.duplicate is True
    assert first.link == replay.link
    assert first.baseline.baseline.status is BaselineStatus.PROVISIONAL_PRODUCTION_LINK
    assert first.baseline.baseline.state_version == 1
    assert first.link.scheduler_job_title == canonical_baseline_job_title(baseline)
    assert first.link.verification_basis == ("READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY")
    assert first.link.verified_at is None
    assert len(ledger.commits) == 1


@pytest.mark.parametrize(
    ("observation_factory", "reason"),
    [
        (lambda baseline: None, ProductionLinkBlockingReason.MISSING_SITE_OBSERVATION),
        (
            lambda baseline: _observation(baseline, observed_at=NOW - timedelta(minutes=1)),
            ProductionLinkBlockingReason.STALE_SITE_OBSERVATION,
        ),
        (
            lambda baseline: _observation(baseline, scheduler_job_id=43),
            ProductionLinkBlockingReason.WRONG_JOB,
        ),
        (
            lambda baseline: _observation(
                baseline,
                title=f"BER|OTHER|{baseline.baseline.approved_brf_sha256[:12]}|BASELINE",
            ),
            ProductionLinkBlockingReason.WRONG_TITLE,
        ),
        (
            lambda baseline: _observation(
                baseline,
                title="BER|WO-DEMO-001|000000000000|BASELINE",
            ),
            ProductionLinkBlockingReason.WRONG_ARTIFACT,
        ),
        (
            lambda baseline: _observation(baseline, destination="Other-Queue"),
            ProductionLinkBlockingReason.WRONG_QUEUE,
        ),
        (
            lambda baseline: _observation(baseline, queue_name="Other-Queue"),
            ProductionLinkBlockingReason.WRONG_QUEUE,
        ),
        (
            lambda baseline: _observation(baseline, duplicate_job=True),
            ProductionLinkBlockingReason.AMBIGUOUS_SITE_OBSERVATION,
        ),
    ],
)
@pytest.mark.asyncio
async def test_production_link_evidence_failures_are_explicit_and_closed(
    observation_factory: object,
    reason: ProductionLinkBlockingReason,
) -> None:
    baseline = _baseline()
    observation = observation_factory(baseline)  # type: ignore[operator]
    ledger = MemoryLinkLedger(baseline, observation)
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )

    with pytest.raises(ProductionLinkRejected) as caught:
        await workflow.link(
            baseline_id=baseline.baseline.baseline_id,
            scheduler_job_id=42,
            expected_state_version=0,
            idempotency_key=_key(baseline),
        )

    assert caught.value.reason is reason
    assert ledger.baseline == baseline
    assert ledger.commits == {}


@pytest.mark.asyncio
async def test_stale_expected_version_conflicts_without_overwrite() -> None:
    baseline = _baseline().model_copy(
        update={"baseline": _baseline().baseline.model_copy(update={"state_version": 1})}
    )
    ledger = MemoryLinkLedger(baseline, _observation(baseline))
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )

    with pytest.raises(ProductionLinkConflict) as caught:
        await workflow.link(
            baseline_id=baseline.baseline.baseline_id,
            scheduler_job_id=42,
            expected_state_version=0,
            idempotency_key=_key(baseline),
        )

    assert caught.value.reason is ProductionLinkBlockingReason.STALE_STATE_VERSION
    assert ledger.baseline == baseline


@pytest.mark.asyncio
async def test_supersession_appends_a_distinct_active_link_and_preserves_the_old_link() -> None:
    baseline = _baseline()
    ledger = MemoryLinkLedger(baseline, _observation(baseline))
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )
    initial = await workflow.link(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=42,
        expected_state_version=0,
        idempotency_key=_key(baseline),
    )
    ledger.observation = _observation(ledger.baseline, scheduler_job_id=43)
    key = production_link_supersession_idempotency_key(
        baseline_id=baseline.baseline.baseline_id,
        supersedes_production_link_id=initial.link.link_id,
        scheduler_job_id=43,
        expected_state_version=1,
    )

    first = await workflow.supersede(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=43,
        expected_state_version=1,
        idempotency_key=key,
    )
    replay = await workflow.supersede(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=43,
        expected_state_version=1,
        idempotency_key=key,
    )

    assert first.duplicate is False
    assert replay.duplicate is True
    assert first.baseline.baseline.status is BaselineStatus.PROVISIONAL_PRODUCTION_LINK
    assert first.baseline.baseline.scheduler_job_id == 43
    assert first.baseline.baseline.state_version == 2
    assert first.link.schema_version == "baseline-production-link.v3"
    assert first.link.supersedes_production_link_id == initial.link.link_id
    assert ledger.records[initial.link.link_id] == initial.link
    assert ledger.records[first.link.link_id] == first.link
    assert ledger.active_link == first.link


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduler_job_id", "confirmed", "reason"),
    [
        (42, False, ProductionLinkBlockingReason.DUPLICATE_SCHEDULER_JOB),
        (43, True, ProductionLinkBlockingReason.ACTIVE_LINK_ALREADY_CONFIRMED),
    ],
)
async def test_supersession_fails_closed_for_same_job_or_confirmed_active_link(
    scheduler_job_id: int,
    confirmed: bool,
    reason: ProductionLinkBlockingReason,
) -> None:
    baseline = _baseline()
    ledger = MemoryLinkLedger(baseline, _observation(baseline))
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )
    initial = await workflow.link(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=42,
        expected_state_version=0,
        idempotency_key=_key(baseline),
    )
    ledger.observation = _observation(ledger.baseline, scheduler_job_id=43)
    if confirmed:
        ledger.confirmed_link_id = initial.link.link_id
    key = production_link_supersession_idempotency_key(
        baseline_id=baseline.baseline.baseline_id,
        supersedes_production_link_id=initial.link.link_id,
        scheduler_job_id=scheduler_job_id,
        expected_state_version=1,
    )

    error_type = ProductionLinkConflict if confirmed else ProductionLinkRejected
    with pytest.raises(error_type) as caught:
        await workflow.supersede(
            baseline_id=baseline.baseline.baseline_id,
            scheduler_job_id=scheduler_job_id,
            expected_state_version=1,
            idempotency_key=key,
        )

    assert caught.value.reason is reason
    assert ledger.active_link == initial.link
    assert ledger.baseline.baseline.scheduler_job_id == 42


@pytest.mark.asyncio
async def test_supersession_stale_version_recovers_without_replacing_active_pointer() -> None:
    baseline = _baseline()
    ledger = MemoryLinkLedger(baseline, _observation(baseline))
    workflow = ProductionLinkWorkflow(
        ledger=ledger,
        bridge_id="single-pc-bridge",
        clock=lambda: NOW,
    )
    initial = await workflow.link(
        baseline_id=baseline.baseline.baseline_id,
        scheduler_job_id=42,
        expected_state_version=0,
        idempotency_key=_key(baseline),
    )
    ledger.observation = _observation(ledger.baseline, scheduler_job_id=43)
    ledger.baseline = ledger.baseline.model_copy(
        update={"baseline": ledger.baseline.baseline.model_copy(update={"state_version": 2})}
    )
    key = production_link_supersession_idempotency_key(
        baseline_id=baseline.baseline.baseline_id,
        supersedes_production_link_id=initial.link.link_id,
        scheduler_job_id=43,
        expected_state_version=1,
    )

    with pytest.raises(ProductionLinkConflict) as caught:
        await workflow.supersede(
            baseline_id=baseline.baseline.baseline_id,
            scheduler_job_id=43,
            expected_state_version=1,
            idempotency_key=key,
        )

    assert caught.value.reason is ProductionLinkBlockingReason.STALE_STATE_VERSION
    assert ledger.active_link == initial.link
