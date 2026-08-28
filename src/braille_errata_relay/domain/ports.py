"""Dependency-inversion ports used by application workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    ArtifactRef,
    Incident,
    ProductionBaseline,
    ProductionIncidentReport,
    SemanticAssessment,
    SiteObservation,
    SourceLocator,
    SourceRevision,
    TranslationProfile,
)


class SourceProvider(Protocol):
    async def fetch_revision(self, locator: SourceLocator) -> SourceRevision: ...


class SourceSignalAdapter(Protocol):
    async def normalize_signal(self, raw_envelope: bytes) -> object: ...


class SourceReconciler(Protocol):
    async def drain(self, cursor: str) -> object: ...


class ArtifactStore(Protocol):
    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef: ...

    async def read(self, ref: ArtifactRef) -> bytes: ...


class IncidentRepository(Protocol):
    async def claim_once(self, idempotency_key: str) -> str: ...

    async def get_incident(self, incident_id: str) -> Incident: ...

    async def append_event(
        self, incident_id: str, event: dict[str, object], expected_version: int
    ) -> Incident: ...

    async def save_baseline(self, baseline: ProductionBaseline) -> ProductionBaseline: ...

    async def save_report(self, report: ProductionIncidentReport) -> ProductionIncidentReport: ...


class SemanticAssessor(Protocol):
    async def assess(self, evidence: dict[str, object]) -> SemanticAssessment: ...


class BrailleRenderer(Protocol):
    def render(self, normalized_source: object, profile: TranslationProfile) -> object: ...


class ProductionObserver(Protocol):
    async def latest_snapshot(self, site_id: str) -> SiteObservation | None: ...

    async def job_history(self, site_id: str, scheduler_job_id: int) -> tuple[object, ...]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ReadOnlyObserverPort(Protocol):
    """Marker protocol for an observer whose public surface is evidence-only."""
