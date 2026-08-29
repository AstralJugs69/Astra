from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from braille_errata_relay.adapters.firestore_ledger import ObservationCommitResult
from braille_errata_relay.application.telemetry_ingestion import (
    TelemetryAllowlist,
    TelemetryIngestionWorkflow,
    TelemetryRejected,
)
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import SiteObservation

ROOT = Path(__file__).resolve().parents[2]


class MemoryTelemetryLedger:
    def __init__(self) -> None:
        self.values: dict[str, SiteObservation] = {}

    async def ingest_site_observation(
        self,
        observation: SiteObservation,
        *,
        payload_sha256: str,
    ) -> ObservationCommitResult:
        assert payload_sha256 == observation.observation_id
        existing = self.values.setdefault(observation.observation_id, observation)
        assert existing == observation
        return ObservationCommitResult(
            observation_id=observation.observation_id,
            duplicate=existing is not observation,
        )


def _payload(
    observed_at: datetime,
    *,
    site_id: str = "demo-site",
    bridge_id: str = "demo-bridge",
    queue_name: str = "Braille-Embosser-Sim",
    sequence: int = 1,
    previous: str | None = None,
) -> dict[str, object]:
    timestamp = observed_at.isoformat()
    body: dict[str, object] = {
        "schema_version": "site-observation.v1",
        "site_id": site_id,
        "bridge_id": bridge_id,
        "queue_name": queue_name,
        "sequence": sequence,
        "observed_at": timestamp,
        "observations": [
            {
                "scheduler_job_id": 42,
                "owner": "relay-operator",
                "title": "BER|WO-DEMO-001|aaaaaaaaaaaa|BASELINE",
                "destination": queue_name,
                "state": "PROCESSING",
                "state_reasons": [],
                "observed_at": timestamp,
                "job_created_at": timestamp,
                "processing_at": timestamp,
                "completed_at": None,
                "impressions_completed": 0,
            }
        ],
        "printer_state": "processing",
        "printer_state_reasons": [],
        "printer_accepting_jobs": True,
        "previous_observation_sha256": previous,
        "source": "cups_read_only_observer",
    }
    return {**body, "observation_id": canonical_sha256(body)}


def _workflow(now: datetime) -> TelemetryIngestionWorkflow:
    return TelemetryIngestionWorkflow(
        ledger=MemoryTelemetryLedger(),
        allowlist=TelemetryAllowlist(
            site_id="demo-site",
            bridge_id="demo-bridge",
            queue_name="Braille-Embosser-Sim",
        ),
        clock=lambda: now,
        schema_path=ROOT / "schemas/site-observation.v1.json",
    )


@pytest.mark.asyncio
async def test_valid_observation_matches_schema_hash_allowlist_and_is_idempotent() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    workflow = _workflow(now)
    payload = _payload(now)

    first = await workflow.ingest(payload)
    second = await workflow.ingest(payload)

    assert first.duplicate is False
    assert second.duplicate is True
    assert first.observation_id == payload["observation_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-field",
        "bad-hash",
        "wrong-site",
        "wrong-bridge",
        "wrong-queue",
        "job-timestamp-mismatch",
    ],
)
async def test_invalid_telemetry_fails_closed(mutation: str) -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    payload = _payload(now)
    if mutation == "unknown-field":
        payload["raw_cups_attributes"] = {}
    elif mutation == "bad-hash":
        payload["observation_id"] = "f" * 64
    elif mutation == "wrong-site":
        payload = _payload(now, site_id="other-site")
    elif mutation == "wrong-bridge":
        payload = _payload(now, bridge_id="other-bridge")
    elif mutation == "wrong-queue":
        payload = _payload(now, queue_name="Other-Queue")
    else:
        jobs = payload["observations"]
        assert isinstance(jobs, list) and isinstance(jobs[0], dict)
        jobs[0]["observed_at"] = (now - timedelta(seconds=1)).isoformat()
        body = dict(payload)
        body.pop("observation_id")
        payload["observation_id"] = canonical_sha256(body)

    with pytest.raises(TelemetryRejected):
        await _workflow(now).ingest(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(minutes=6), timedelta(seconds=-6)])
async def test_stale_or_future_telemetry_is_rejected_at_ingest(offset: timedelta) -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

    with pytest.raises(TelemetryRejected, match="timestamp"):
        await _workflow(now).ingest(_payload(now - offset))
