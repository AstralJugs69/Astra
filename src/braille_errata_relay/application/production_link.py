"""Create an advisory link to an independently submitted production job."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import ProductionLinkCommit
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.errors import BaselineStateConflictError
from braille_errata_relay.domain.models import (
    BaselineProductionLink,
    BaselineStatus,
    ProductionLinkBlockingReason,
    RegisteredBaseline,
    SiteObservation,
)


class ProductionLinkRejected(RuntimeError):
    def __init__(self, reason: ProductionLinkBlockingReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class ProductionLinkConflict(ProductionLinkRejected):
    pass


class ProductionLinkLedger(Protocol):
    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None: ...

    async def get_latest_site_observation(
        self,
        *,
        site_id: str,
        bridge_id: str,
        queue_name: str,
    ) -> SiteObservation | None: ...

    async def get_production_link_by_idempotency(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit | None: ...

    async def link_baseline_production(
        self,
        *,
        proposed_link: BaselineProductionLink,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkCommit: ...


@dataclass(frozen=True)
class ProductionLinkResult:
    baseline: RegisteredBaseline
    link: BaselineProductionLink
    duplicate: bool


def canonical_baseline_job_title(baseline: RegisteredBaseline) -> str:
    value = baseline.baseline
    return f"BER|{value.production_id}|{value.approved_brf_sha256[:12]}|BASELINE"


def production_link_idempotency_key(
    *,
    baseline_id: str,
    scheduler_job_id: int,
    expected_state_version: int,
) -> str:
    return canonical_sha256(
        {
            "baseline_id": baseline_id,
            "scheduler_job_id": scheduler_job_id,
            "expected_state_version": expected_state_version,
        }
    )


class ProductionLinkWorkflow:
    def __init__(
        self,
        *,
        ledger: ProductionLinkLedger,
        bridge_id: str,
        clock: Callable[[], datetime] | None = None,
        observation_max_age_seconds: float = 15.0,
        max_future_skew_seconds: float = 5.0,
    ) -> None:
        self.ledger = ledger
        self.bridge_id = bridge_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.observation_max_age_seconds = observation_max_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds

    async def link(
        self,
        *,
        baseline_id: str,
        scheduler_job_id: int,
        expected_state_version: int,
        idempotency_key: str,
    ) -> ProductionLinkResult:
        expected_key = production_link_idempotency_key(
            baseline_id=baseline_id,
            scheduler_job_id=scheduler_job_id,
            expected_state_version=expected_state_version,
        )
        if idempotency_key != expected_key:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.IDEMPOTENCY_KEY_MISMATCH)
        replay = await self.ledger.get_production_link_by_idempotency(
            baseline_id=baseline_id,
            scheduler_job_id=scheduler_job_id,
            expected_state_version=expected_state_version,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            return ProductionLinkResult(replay.baseline, replay.link, True)

        baseline = await self.ledger.get_baseline(baseline_id)
        if baseline is None:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.BASELINE_NOT_FOUND)
        if baseline.baseline.state_version != expected_state_version:
            raise ProductionLinkConflict(ProductionLinkBlockingReason.STALE_STATE_VERSION)
        if baseline.baseline.status is not BaselineStatus.AWAITING_PRODUCTION_LINK:
            raise ProductionLinkConflict(ProductionLinkBlockingReason.BASELINE_NOT_AWAITING_LINK)
        observation = await self.ledger.get_latest_site_observation(
            site_id=baseline.baseline.site_id,
            bridge_id=self.bridge_id,
            queue_name=baseline.baseline.queue_name,
        )
        if observation is None:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.MISSING_SITE_OBSERVATION)
        if (
            observation.site_id != baseline.baseline.site_id
            or observation.bridge_id != self.bridge_id
            or observation.queue_name != baseline.baseline.queue_name
        ):
            raise ProductionLinkRejected(ProductionLinkBlockingReason.WRONG_QUEUE)
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age = (now - observed_at).total_seconds()
        if age > self.observation_max_age_seconds or age < -self.max_future_skew_seconds:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.STALE_SITE_OBSERVATION)
        matches = tuple(
            job for job in observation.observations if job.scheduler_job_id == scheduler_job_id
        )
        if not matches:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.WRONG_JOB)
        if len(matches) != 1:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.AMBIGUOUS_SITE_OBSERVATION)
        job = matches[0]
        if job.destination != baseline.baseline.queue_name:
            raise ProductionLinkRejected(ProductionLinkBlockingReason.WRONG_QUEUE)
        expected_title = canonical_baseline_job_title(baseline)
        if job.title != expected_title:
            if baseline.baseline.approved_brf_sha256[:12] not in job.title:
                raise ProductionLinkRejected(ProductionLinkBlockingReason.WRONG_ARTIFACT)
            raise ProductionLinkRejected(ProductionLinkBlockingReason.WRONG_TITLE)
        link_id = canonical_sha256(
            {
                "baseline_id": baseline_id,
                "scheduler_job_id": scheduler_job_id,
                "site_observation_id": observation.observation_id,
            }
        )
        proposed_link = BaselineProductionLink(
            link_id=link_id,
            baseline_id=baseline_id,
            scheduler_job_id=scheduler_job_id,
            scheduler_job_title=job.title,
            site_observation_id=observation.observation_id,
            site_id=observation.site_id,
            bridge_id=observation.bridge_id,
            queue_name=observation.queue_name,
            baseline_brf_sha256=baseline.baseline.approved_brf_sha256,
            baseline_state_version=expected_state_version + 1,
            idempotency_key_sha256=canonical_sha256(
                {"scope": "baseline-production-link", "key": idempotency_key}
            ),
            evidence_observed_at=observation.observed_at,
            linked_at=now,
        )
        try:
            commit = await self.ledger.link_baseline_production(
                proposed_link=proposed_link,
                expected_state_version=expected_state_version,
                idempotency_key=idempotency_key,
            )
        except BaselineStateConflictError as exc:
            raise ProductionLinkConflict(ProductionLinkBlockingReason.STALE_STATE_VERSION) from exc
        return ProductionLinkResult(commit.baseline, commit.link, commit.duplicate)
