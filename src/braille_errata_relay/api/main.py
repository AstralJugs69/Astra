"""Minimal FastAPI factory for health/readiness and later contract-first routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
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
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
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
            trace = await assessor.assess_with_trace(payload.evidence)
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
        duplicate_execution = None
        if ledger is not None:
            duplicate_execution = await ledger.record_assessment_execution(trace.sanitized_record())
        return JSONResponse(
            status_code=200,
            content={
                "schema_version": "cloud-gate0-semantic-response.v1",
                "assessment": trace.assessment.model_dump(mode="json"),
                "execution": trace.sanitized_record(),
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

    return app


_runtime = build_runtime_dependencies()
app = create_app(
    cloud_settings=_runtime.settings,
    assessor=_runtime.assessor,
    ledger=_runtime.ledger,
    drive_workflow=_runtime.drive_workflow,
    identity_verifier=_runtime.identity_verifier,
)
