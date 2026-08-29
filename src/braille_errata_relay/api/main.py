"""Minimal FastAPI factory for health/readiness and later contract-first routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from braille_errata_relay.adapters.adk_assessor import (
    AdkSemanticAssessor,
    SemanticAssessmentBlocked,
    SemanticAssessmentUnavailable,
)
from braille_errata_relay.adapters.firestore_ledger import FirestoreGate0Ledger
from braille_errata_relay.api.dependencies import build_runtime_dependencies
from braille_errata_relay.api.security import (
    GoogleOidcVerifier,
    IdentityVerifier,
    enforce_route_identity,
)
from braille_errata_relay.application.baseline_registration import (
    BaselineRegistrationError,
    BaselineRegistrationWorkflow,
)
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
from braille_errata_relay.application.incident_workflow import IncidentWorkflow
from braille_errata_relay.application.outbox_drain import OutboxDrainWorkflow
from braille_errata_relay.application.semantic_workflow import (
    IdempotentSemanticWorkflow,
    SemanticExecutionInProgress,
)
from braille_errata_relay.application.telemetry_ingestion import (
    TelemetryIngestionWorkflow,
    TelemetryRejected,
)
from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.readiness import check_liblouis_readiness
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.configuration import resolve_config_path
from braille_errata_relay.domain.models import AssessmentInput, BlockingReason


class SourceJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cloud-gate0-source-job.v1"]
    job_kind: Literal["SEMANTIC_GATE0_SMOKE"]
    evidence: AssessmentInput


class DriveReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cloud-gate0-drive-reconcile.v1"]
    operation: Literal["INITIALIZE", "RECONCILE"]


class BaselineSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["google_drive"]
    file_id: str = Field(min_length=1, max_length=512)
    revision_id: str = Field(min_length=1, max_length=512)


class BaselineRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_id: str = Field(min_length=1, max_length=512)
    production_id_origin: Literal["EXTERNAL_REFERENCE"]
    source: BaselineSourceRequest
    artifact_origin: Literal["DEMO_GENERATED_FIXTURE"]
    approved_brf_sha256: None = None
    approval_label: Literal["DEMO_FIXTURE_APPROVED"]
    translation_profile_id: Literal["demo-ueb-40x25-v1"]
    site_id: str = Field(min_length=1, max_length=512)
    queue_name: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=512)


class OutboxDrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["outbox-drain-request.v1"] = "outbox-drain-request.v1"
    limit: int = Field(default=10, ge=1, le=100)


def _default_profile_path() -> Path:
    return resolve_config_path(
        direct_env="TRANSLATION_PROFILE_PATH",
        relative_path="translation_profiles/demo-ueb-40x25-v1.json",
    )


def create_app(
    *,
    profile_path: str | Path | None = None,
    table_root: str | Path | None = None,
    cloud_settings: CloudSettings | None = None,
    assessor: AdkSemanticAssessor | None = None,
    ledger: FirestoreGate0Ledger | None = None,
    drive_workflow: DriveGate0Workflow | None = None,
    baseline_workflow: BaselineRegistrationWorkflow | None = None,
    telemetry_workflow: TelemetryIngestionWorkflow | None = None,
    incident_workflow: IncidentWorkflow | None = None,
    outbox_workflow: OutboxDrainWorkflow | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    app = FastAPI(title="Braille Errata Relay", version="0.1.0")
    selected_profile = Path(profile_path) if profile_path is not None else _default_profile_path()
    selected_table_root = table_root or os.environ.get("LIBLOUIS_TABLEPATH")
    verifier = identity_verifier or GoogleOidcVerifier()

    @app.middleware("http")
    async def private_route_identity(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        return await enforce_route_identity(
            request,
            call_next,
            settings=cloud_settings,
            verifier=verifier,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            profile = load_translation_profile(selected_profile)
        except (OSError, ValueError) as exc:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "PROFILE_INVALID", "detail": str(exc)},
            )
        report = check_liblouis_readiness(profile, table_root=selected_table_root)
        content: dict[str, object] = {
            "ready": report.ready,
            "reason": report.reason,
            "checks": list(report.checks),
            "profile_id": profile.profile_id,
        }
        if report.liblouis_version is not None:
            content["liblouis_version"] = report.liblouis_version
        return JSONResponse(status_code=200 if report.ready else 503, content=content)

    @app.post("/internal/source-jobs")
    async def source_jobs(payload: SourceJobRequest) -> JSONResponse:
        if assessor is None:
            return JSONResponse(
                status_code=503,
                content={"state": "NEEDS_REVIEW", "blocking_reason": "MODEL_NOT_CONFIGURED"},
            )
        try:
            if ledger is None:
                trace = await assessor.assess_with_trace(payload.evidence)
                result_assessment = trace.assessment
                execution = trace.sanitized_record()
                duplicate_execution = None
            else:
                semantic_result = await IdempotentSemanticWorkflow(
                    assessor=assessor,
                    ledger=ledger,
                ).assess(payload.evidence)
                result_assessment = semantic_result.assessment
                execution = (
                    semantic_result.trace.sanitized_record()
                    if semantic_result.trace is not None
                    else {
                        "schema_version": "semantic-execution-reuse.v1",
                        "assessment_id": semantic_result.assessment.assessment_id,
                        "execution_key": semantic_result.execution_key,
                        "outcome": "REUSED_FIRST_VALID",
                    }
                )
                duplicate_execution = semantic_result.reused
        except SemanticAssessmentBlocked:
            return JSONResponse(
                status_code=422,
                content={
                    "state": "NEEDS_REVIEW",
                    "blocking_reason": BlockingReason.SEMANTIC_ASSESSMENT_INVALID.value,
                },
            )
        except SemanticAssessmentUnavailable as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "state": "NEEDS_REVIEW",
                    "blocking_reason": BlockingReason.SEMANTIC_ASSESSMENT_INVALID.value,
                    "sanitized_error": str(exc),
                },
            )
        except SemanticExecutionInProgress:
            return JSONResponse(
                status_code=409,
                content={
                    "state": "ASSESSING",
                    "blocking_reason": "SEMANTIC_EXECUTION_IN_PROGRESS",
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "schema_version": "cloud-gate0-semantic-response.v1",
                "assessment": result_assessment.model_dump(mode="json"),
                "execution": execution,
                "duplicate_execution": duplicate_execution,
            },
        )

    @app.post("/internal/drive-reconcile")
    async def drive_reconcile(payload: DriveReconcileRequest) -> JSONResponse:
        if drive_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "Drive workflow is not configured"},
            )
        try:
            result = (
                await drive_workflow.initialize()
                if payload.operation == "INITIALIZE"
                else await drive_workflow.reconcile()
            )
        except (RuntimeError, ValueError) as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "sanitized_error": type(exc).__name__,
                    "sanitized_detail": str(exc),
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "schema_version": "cloud-gate0-drive-result.v1",
                "status": "PASS",
                **result.sanitized_record(),
            },
        )

    @app.post("/internal/site-observations")
    async def ingest_site_observation(payload: dict[str, object]) -> JSONResponse:
        if telemetry_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "telemetry workflow is not configured"},
            )
        try:
            result = await telemetry_workflow.ingest(payload)
        except TelemetryRejected as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "status": "REJECTED",
                    "blocking_reason": "SITE_OBSERVATION_BLOCKING",
                    "sanitized_detail": str(exc),
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "ACCEPTED",
                "observation_id": result.observation_id,
                "duplicate": result.duplicate,
            },
        )

    @app.post("/internal/outbox-drain")
    async def drain_outbox(payload: OutboxDrainRequest) -> JSONResponse:
        if outbox_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "outbox workflow is not configured"},
            )
        result = await outbox_workflow.drain(limit=payload.limit)
        return JSONResponse(
            status_code=200,
            content={
                "schema_version": "outbox-drain-result.v1",
                "leased": result.leased,
                "completed": result.completed,
                "retried": result.retried,
                "dead_letter_possible": result.dead_letter_possible,
                "message_ids": list(result.message_ids),
                "notification_status": "NOT_CLAIMED",
            },
        )

    @app.post("/api/v1/baselines")
    async def register_baseline(payload: BaselineRegistrationRequest) -> JSONResponse:
        if baseline_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "baseline workflow is not configured"},
            )
        if payload.translation_profile_id != baseline_workflow.profile.profile_id:
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "blocking_reason": "PROFILE_MISMATCH"},
            )
        try:
            result = await baseline_workflow.register_demo_fixture(
                production_id=payload.production_id,
                source_revision_id=payload.source.revision_id,
                expected_file_id=payload.source.file_id,
                approval_label=payload.approval_label,
                site_id=payload.site_id,
                queue_name=payload.queue_name,
            )
        except (BaselineRegistrationError, ValueError) as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "status": "NEEDS_REVIEW",
                    "blocking_reason": "BASELINE_REGISTRATION_INVALID",
                    "sanitized_detail": str(exc),
                },
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": "REGISTERED",
                "duplicate": result.duplicate,
                "record": result.record.model_dump(mode="json"),
            },
        )

    @app.get("/api/v1/baselines/{baseline_id}")
    async def get_baseline(baseline_id: str) -> JSONResponse:
        if ledger is None:
            return JSONResponse(status_code=503, content={"status": "BLOCKED"})
        try:
            record = await ledger.get_baseline(baseline_id)
        except ValueError:
            return JSONResponse(status_code=404, content={"detail": "baseline not found"})
        if record is None:
            return JSONResponse(status_code=404, content={"detail": "baseline not found"})
        return JSONResponse(status_code=200, content=record.model_dump(mode="json"))

    @app.get("/api/v1/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> JSONResponse:
        if ledger is None or incident_workflow is None:
            return JSONResponse(status_code=503, content={"status": "BLOCKED"})
        try:
            checkpoint = await ledger.get_incident_checkpoint(incident_id)
        except ValueError:
            return JSONResponse(status_code=404, content={"detail": "incident not found"})
        if checkpoint is None:
            return JSONResponse(status_code=404, content={"detail": "incident not found"})
        report = (
            json.loads(
                (await incident_workflow.artifact_store.read(checkpoint.report)).decode("utf-8")
            )
            if checkpoint.report is not None
            else None
        )
        packet = (
            json.loads(
                (await incident_workflow.artifact_store.read(checkpoint.disposition_packet)).decode(
                    "utf-8"
                )
            )
            if checkpoint.disposition_packet is not None
            else None
        )
        return JSONResponse(
            status_code=200,
            content={
                "checkpoint": checkpoint.model_dump(mode="json"),
                "report": report,
                "human_disposition_packet": packet,
            },
        )

    return app


_runtime = build_runtime_dependencies()
app = create_app(
    cloud_settings=_runtime.settings,
    assessor=_runtime.assessor,
    ledger=_runtime.ledger,
    drive_workflow=_runtime.drive_workflow,
    baseline_workflow=_runtime.baseline_workflow,
    telemetry_workflow=_runtime.telemetry_workflow,
    incident_workflow=_runtime.incident_workflow,
    outbox_workflow=_runtime.outbox_workflow,
    identity_verifier=_runtime.identity_verifier,
)
