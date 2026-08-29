"""Fail-closed admission of hash-chained read-only CUPS observations."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import ValidationError

from braille_errata_relay.adapters.firestore_ledger import ObservationCommitResult
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import SiteObservation


class TelemetryRejected(RuntimeError):
    pass


class TelemetryLedger(Protocol):
    async def ingest_site_observation(
        self,
        observation: SiteObservation,
        *,
        payload_sha256: str,
    ) -> ObservationCommitResult: ...


@dataclass(frozen=True)
class TelemetryAllowlist:
    site_id: str
    bridge_id: str
    queue_name: str


class TelemetryIngestionWorkflow:
    def __init__(
        self,
        *,
        ledger: TelemetryLedger,
        allowlist: TelemetryAllowlist,
        clock: Callable[[], datetime] | None = None,
        max_ingest_age_seconds: float = 300.0,
        max_future_skew_seconds: float = 5.0,
        schema_path: str | Path | None = None,
    ) -> None:
        self.ledger = ledger
        self.allowlist = allowlist
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_ingest_age_seconds = max_ingest_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds
        configured_schema = schema_path or os.environ.get("SITE_OBSERVATION_SCHEMA_PATH")
        path = (
            Path(configured_schema)
            if configured_schema
            else Path("schemas/site-observation.v1.json")
        )
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    async def ingest(self, payload: dict[str, object]) -> ObservationCommitResult:
        errors = sorted(self.validator.iter_errors(payload), key=lambda error: list(error.path))
        if errors:
            raise TelemetryRejected("site observation failed its JSON Schema contract")
        body = dict(payload)
        supplied_id = body.pop("observation_id", None)
        expected_id = canonical_sha256(body)
        if supplied_id != expected_id:
            raise TelemetryRejected("site observation content hash is invalid")
        try:
            observation = SiteObservation.model_validate(payload)
        except ValidationError as exc:
            raise TelemetryRejected("site observation failed its typed contract") from exc
        if (
            observation.site_id != self.allowlist.site_id
            or observation.bridge_id != self.allowlist.bridge_id
            or observation.queue_name != self.allowlist.queue_name
        ):
            raise TelemetryRejected("site observation identity is not allowlisted")
        if any(job.observed_at != observation.observed_at for job in observation.observations):
            raise TelemetryRejected("job timestamps do not match the observation envelope")
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age = (now - observed_at).total_seconds()
        if age > self.max_ingest_age_seconds or age < -self.max_future_skew_seconds:
            raise TelemetryRejected("site observation timestamp is outside the ingest window")
        try:
            return await self.ledger.ingest_site_observation(
                observation,
                payload_sha256=expected_id,
            )
        except RuntimeError as exc:
            raise TelemetryRejected("site observation replay or chain validation failed") from exc
