"""Minimal FastAPI factory for health/readiness and later contract-first routes."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
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
from braille_errata_relay.application.automatic_reconciliation import (
    AutomaticReconciliationWorkflow,
)
from braille_errata_relay.application.baseline_registration import (
    BaselineRegistrationError,
    BaselineRegistrationWorkflow,
)
from braille_errata_relay.application.containment_proof import (
    ContainmentProofConflict,
    ContainmentProofRejected,
    ContainmentProofWorkflow,
)
from braille_errata_relay.application.drive_gate0 import DriveGate0Workflow
from braille_errata_relay.application.endpoint_receipt import (
    EndpointEvidenceConflict,
    EndpointEvidenceRejected,
    EndpointReceiptWorkflow,
    HistoricalLinkCorrectionWorkflow,
)
from braille_errata_relay.application.incident_workflow import IncidentWorkflow
from braille_errata_relay.application.outbox_drain import OutboxDrainWorkflow
from braille_errata_relay.application.production_link import (
    ProductionLinkConflict,
    ProductionLinkRejected,
    ProductionLinkWorkflow,
)
from braille_errata_relay.application.professional_review import (
    ProfessionalReviewConflict,
    ProfessionalReviewRejected,
    ProfessionalReviewWorkflow,
)
from braille_errata_relay.application.replacement_observation import (
    ReplacementObservationConflict,
    ReplacementObservationRejected,
    ReplacementObservationWorkflow,
)
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
from braille_errata_relay.domain.models import (
    ArtifactRef,
    AssessmentInput,
    AttestationType,
    BlockingReason,
    CandidateApprovalInvalidation,
    ContainmentConfirmation,
    EndpointEvidenceSubmission,
    HumanTimelineEventKind,
    IncidentCheckpoint,
    IncidentReviewState,
    IncidentState,
    IncidentWorkflowStage,
    JobState,
    OperatorAttestation,
    ProductionLinkBlockingReason,
    ProfessionalDecision,
    ProfessionalDisposition,
    ProofDecision,
    ProofRecord,
    ReplacementObservationLink,
    TruthBasis,
)


class SourceJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cloud-gate0-source-job.v1"]
    job_kind: Literal["SEMANTIC_GATE0_SMOKE"]
    evidence: AssessmentInput


class DriveReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cloud-gate0-drive-reconcile.v1"]
    operation: Literal["INITIALIZE", "RECONCILE"]


class AutomationCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["automation-cycle-request.v1"] = "automation-cycle-request.v1"
    outbox_limit: int = Field(default=1, ge=1, le=1)


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


class BaselineProductionLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["baseline-production-link-request.v1"] = (
        "baseline-production-link-request.v1"
    )
    scheduler_job_id: int = Field(gt=0)
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)


class BaselineProductionLinkSupersessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["baseline-production-link-supersession-request.v1"] = (
        "baseline-production-link-supersession-request.v1"
    )
    scheduler_job_id: int = Field(gt=0)
    expected_state_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)


class OutboxDrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["outbox-drain-request.v1"] = "outbox-drain-request.v1"
    limit: int = Field(default=10, ge=1, le=100)


class HistoricalLinkCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["baseline-link-correction-request.v1"] = (
        "baseline-link-correction-request.v1"
    )
    baseline_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_link_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_state_version: int = Field(ge=1)
    prior_report_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=512)


class ProfessionalDispositionRequest(BaseModel):
    """A human decision record; never a CUPS or device-control request."""

    model_config = ConfigDict(extra="forbid")

    decision: ProfessionalDecision
    selected_role: Literal["production_coordinator"]
    expected_state_version: int = Field(ge=0)
    note: str = Field(default="", max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=512)


class OperatorAttestationRequest(BaseModel):
    """A human physical/device fact; it never performs the asserted action."""

    model_config = ConfigDict(extra="forbid")

    attestation_type: AttestationType
    truth_basis: TruthBasis
    selected_role: Literal["machine_operator"]
    expected_state_version: int = Field(ge=0)
    note: str = Field(default="", max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=512)


class ContainmentConfirmationRequest(BaseModel):
    """Coordinator conclusion bound to immutable human and read-only evidence."""

    model_config = ConfigDict(extra="forbid")

    halt_disposition_record_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_observation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_output_isolation_attestation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_role: Literal["production_coordinator"]
    expected_state_version: int = Field(ge=0)
    note: str = Field(default="", max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=512)


class ProofRecordRequest(BaseModel):
    """Proofreader decision for exactly the candidate/manifest they reviewed."""

    model_config = ConfigDict(extra="forbid")

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ProofDecision
    review_basis: Literal["DEMO_FIXTURE_REVIEW"]
    selected_role: Literal["proofreader"]
    expected_state_version: int = Field(ge=0)
    note: str = Field(default="", max_length=2_000)
    findings: tuple[str, ...] = Field(default=(), max_length=16)
    visual_only_uncertainty: bool = False
    idempotency_key: str = Field(min_length=1, max_length=512)


class ReplacementObservationLinkRequest(BaseModel):
    """A machine operator's evidence link, never a production-control request."""

    model_config = ConfigDict(extra="forbid")

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof_record_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    scheduler_job_id: int = Field(gt=0)
    site_observation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_role: Literal["machine_operator"]
    expected_state_version: int = Field(ge=0)
    note: str = Field(default="", max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=512)


def _safe_candidate_manifest_evidence(
    manifest: dict[str, object] | None,
) -> dict[str, object] | None:
    """Return deterministic proof identity without an artifact location.

    The browser needs enough immutable evidence to identify the candidate it is
    reviewing.  It never needs the GCS source-map URI, so that URI is not
    carried across the private API/presentation boundary.
    """

    if manifest is None:
        return None
    allowed_fields = (
        "schema_version",
        "artifact_kind",
        "artifact_sha256",
        "byte_length",
        "source_revision_id",
        "source_sha256",
        "normalized_source_sha256",
        "baseline_manifest_sha256",
        "translation_profile_sha256",
        "liblouis_version",
        "formatter_version",
        "page_count",
        "page_sha256",
        "created_at",
        "generator_build",
        "parent_artifact_sha256",
        "page_range",
    )
    return {name: manifest[name] for name in allowed_fields if name in manifest}


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
    production_link_workflow: ProductionLinkWorkflow | None = None,
    endpoint_receipt_workflow: EndpointReceiptWorkflow | None = None,
    historical_link_correction_workflow: HistoricalLinkCorrectionWorkflow | None = None,
    telemetry_workflow: TelemetryIngestionWorkflow | None = None,
    incident_workflow: IncidentWorkflow | None = None,
    professional_review_workflow: ProfessionalReviewWorkflow | None = None,
    containment_proof_workflow: ContainmentProofWorkflow | None = None,
    replacement_observation_workflow: ReplacementObservationWorkflow | None = None,
    outbox_workflow: OutboxDrainWorkflow | None = None,
    automatic_reconciliation_workflow: AutomaticReconciliationWorkflow | None = None,
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

    @app.get("/health")
    @app.get("/healthz")
    async def health() -> dict[str, str]:
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
        except (RuntimeError, TimeoutError, ValueError) as exc:
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

    @app.post("/internal/automation-cycle")
    async def automation_cycle(payload: AutomationCycleRequest) -> JSONResponse:
        if automatic_reconciliation_workflow is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "detail": "automatic reconciliation workflow is not configured",
                },
            )
        try:
            result = await automatic_reconciliation_workflow.run(outbox_limit=payload.outbox_limit)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "sanitized_error": type(exc).__name__,
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "schema_version": "automation-cycle-result.v1",
                **result.sanitized_record(),
            },
        )

    @app.get("/api/v1/automation-status")
    async def automation_status() -> JSONResponse:
        """Expose only durable automatic-cycle state to the local watch floor."""

        if automatic_reconciliation_workflow is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "detail": "automatic reconciliation workflow is not configured",
                },
            )
        try:
            status = await automatic_reconciliation_workflow.status()
        except (RuntimeError, TimeoutError, ValueError) as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "sanitized_error": type(exc).__name__,
                },
            )
        return JSONResponse(status_code=200, content=status)

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
                idempotency_key=payload.idempotency_key,
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

    @app.post("/api/v1/baselines/{baseline_id}/production-links")
    async def link_baseline_production(
        baseline_id: str,
        payload: BaselineProductionLinkRequest,
    ) -> JSONResponse:
        if production_link_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "production link is not configured"},
            )
        try:
            result = await production_link_workflow.link(
                baseline_id=baseline_id,
                scheduler_job_id=payload.scheduler_job_id,
                expected_state_version=payload.expected_state_version,
                idempotency_key=payload.idempotency_key,
            )
        except ProductionLinkConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": exc.reason.value},
            )
        except ProductionLinkRejected as exc:
            status_code = (
                404 if exc.reason is ProductionLinkBlockingReason.BASELINE_NOT_FOUND else 422
            )
            return JSONResponse(
                status_code=status_code,
                content={"status": "NEEDS_REVIEW", "blocking_reason": exc.reason.value},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": "PROVISIONAL_PRODUCTION_LINK",
                "duplicate": result.duplicate,
                "baseline": result.baseline.model_dump(mode="json"),
                "production_link": result.link.model_dump(mode="json", exclude_none=True),
            },
        )

    @app.post("/api/v1/baselines/{baseline_id}/production-link-supersessions")
    async def supersede_baseline_production(
        baseline_id: str,
        payload: BaselineProductionLinkSupersessionRequest,
    ) -> JSONResponse:
        if production_link_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "production link is not configured"},
            )
        try:
            result = await production_link_workflow.supersede(
                baseline_id=baseline_id,
                scheduler_job_id=payload.scheduler_job_id,
                expected_state_version=payload.expected_state_version,
                idempotency_key=payload.idempotency_key,
            )
        except ProductionLinkConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": exc.reason.value},
            )
        except ProductionLinkRejected as exc:
            status_code = (
                404 if exc.reason is ProductionLinkBlockingReason.BASELINE_NOT_FOUND else 422
            )
            return JSONResponse(
                status_code=status_code,
                content={"status": "NEEDS_REVIEW", "blocking_reason": exc.reason.value},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": "PROVISIONAL_PRODUCTION_LINK",
                "duplicate": result.duplicate,
                "baseline": result.baseline.model_dump(mode="json"),
                "production_link": result.link.model_dump(mode="json", exclude_none=True),
            },
        )

    @app.post("/internal/endpoint-receipts")
    async def confirm_endpoint_receipt(
        payload: EndpointEvidenceSubmission,
        request: Request,
    ) -> JSONResponse:
        if endpoint_receipt_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "endpoint receipt is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await endpoint_receipt_workflow.confirm(
                payload,
                submitting_principal=principal,
            )
        except EndpointEvidenceConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": exc.reason.value},
            )
        except EndpointEvidenceRejected as exc:
            status_code = (
                404 if exc.reason is ProductionLinkBlockingReason.BASELINE_NOT_FOUND else 422
            )
            return JSONResponse(
                status_code=status_code,
                content={"status": "NEEDS_REVIEW", "blocking_reason": exc.reason.value},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": "PRODUCTION_LINK_VERIFIED",
                "duplicate": result.duplicate,
                "baseline": result.baseline.model_dump(mode="json"),
                "endpoint_receipt": result.receipt.model_dump(mode="json"),
            },
        )

    @app.post("/internal/baseline-link-corrections")
    async def correct_historical_link(
        payload: HistoricalLinkCorrectionRequest,
        request: Request,
    ) -> JSONResponse:
        if historical_link_correction_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "historical correction is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            baseline = await historical_link_correction_workflow.correct(
                baseline_id=payload.baseline_id,
                production_link_id=payload.production_link_id,
                expected_state_version=payload.expected_state_version,
                prior_report_id=payload.prior_report_id,
                idempotency_key=payload.idempotency_key,
                submitting_principal=principal,
            )
        except EndpointEvidenceConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": exc.reason.value},
            )
        except EndpointEvidenceRejected as exc:
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "blocking_reason": exc.reason.value},
            )
        return JSONResponse(
            status_code=201,
            content={
                "status": "PROVISIONAL_PRODUCTION_LINK",
                "baseline": baseline.model_dump(mode="json"),
            },
        )

    async def _review_state_for_checkpoint(
        checkpoint: IncidentCheckpoint,
    ) -> IncidentReviewState:
        if professional_review_workflow is not None:
            persisted = await professional_review_workflow.ledger.get_incident_review_state(
                checkpoint.incident_id
            )
            if persisted is not None:
                return persisted
        if checkpoint.report_ready_at is None or checkpoint.candidate_brf is None:
            raise ValueError("incident has no report-ready human-review state")
        return IncidentReviewState(
            incident_id=checkpoint.incident_id,
            baseline_id=checkpoint.baseline_id,
            state=(
                IncidentState.NEEDS_REVIEW
                if checkpoint.stage is IncidentWorkflowStage.NEEDS_REVIEW
                else IncidentState.REPORT_READY
            ),
            state_version=0,
            report_ready_at=checkpoint.report_ready_at,
            current_candidate_sha256=checkpoint.candidate_brf.sha256,
            blocking_reason=checkpoint.blocking_reason,
            updated_at=checkpoint.updated_at,
        )

    async def _read_json_artifact(ref: ArtifactRef) -> dict[str, object]:
        if incident_workflow is None:
            raise RuntimeError("incident workflow is not configured")
        value = json.loads((await incident_workflow.artifact_store.read(ref)).decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("incident artifact payload must be an object")
        return value

    async def _candidate_preview(checkpoint: IncidentCheckpoint) -> dict[str, str]:
        if incident_workflow is None or checkpoint.candidate_brf is None:
            return {
                "label": "TEXT EVIDENCE PREVIEW ONLY — NOT TACTILE PROOF",
                "text": "Candidate BRF preview is unavailable.",
            }
        try:
            value = await incident_workflow.artifact_store.read(checkpoint.candidate_brf)
            preview = value[:1_200].decode("ascii", errors="replace")
        except (KeyError, OSError, RuntimeError, UnicodeDecodeError, ValueError):
            preview = "Candidate BRF preview is unavailable."
        return {
            "label": "TEXT EVIDENCE PREVIEW ONLY — NOT TACTILE PROOF",
            "text": preview,
        }

    async def _incident_timeline(
        checkpoint: IncidentCheckpoint,
    ) -> list[dict[str, object]]:
        if ledger is None:
            return []
        entries: list[tuple[datetime, dict[str, object]]] = []
        if checkpoint.report_ready_at is not None:
            entries.append(
                (
                    checkpoint.report_ready_at,
                    {
                        "kind": "REPORT_READY",
                        "truth_basis": "DETERMINISTIC_REPORT",
                        "recorded_at": checkpoint.report_ready_at.isoformat(),
                        "state_version": 0,
                    },
                )
            )
        if professional_review_workflow is not None:
            for event in await professional_review_workflow.ledger.list_incident_timeline_events(
                checkpoint.incident_id
            ):
                record: (
                    CandidateApprovalInvalidation
                    | ContainmentConfirmation
                    | OperatorAttestation
                    | ProfessionalDisposition
                    | ProofRecord
                    | ReplacementObservationLink
                    | None
                )
                truth_basis = "HUMAN_ATTESTATION"
                if event.kind is HumanTimelineEventKind.PROFESSIONAL_DISPOSITION:
                    record = await ledger.get_professional_disposition(event.record_id)
                elif event.kind is HumanTimelineEventKind.OPERATOR_ATTESTATION:
                    record = await ledger.get_operator_attestation(event.record_id)
                elif event.kind is HumanTimelineEventKind.CONTAINMENT_CONFIRMATION:
                    record = await ledger.get_containment_confirmation(event.record_id)
                    truth_basis = "READ_ONLY_OBSERVATION_AND_HUMAN_CONFIRMATION"
                elif event.kind is HumanTimelineEventKind.PROOF_RECORD:
                    record = await ledger.get_proof_record(event.record_id)
                    truth_basis = "DEMO_FIXTURE_REVIEW"
                elif event.kind is HumanTimelineEventKind.CANDIDATE_APPROVAL_INVALIDATED:
                    record = await ledger.get_candidate_approval_invalidation(event.record_id)
                    truth_basis = "DETERMINISTIC_CANDIDATE_LINEAGE"
                elif event.kind is HumanTimelineEventKind.REPLACEMENT_OBSERVATION_LINK:
                    record = await ledger.get_replacement_observation_link(event.record_id)
                    truth_basis = "HUMAN_SUBMITTED_EXTERNAL_JOB_PLUS_READ_ONLY_OBSERVATION"
                else:
                    raise ValueError("incident timeline contains an unsupported event kind")
                if record is None:
                    raise ValueError("human timeline references a missing immutable record")
                entries.append(
                    (
                        event.recorded_at,
                        {
                            "kind": event.kind.value,
                            "truth_basis": truth_basis,
                            "recorded_at": event.recorded_at.isoformat(),
                            "state_version": event.state_version,
                            "record": record.model_dump(mode="json"),
                        },
                    )
                )
        baseline = await ledger.get_baseline(checkpoint.baseline_id)
        if baseline is None:
            return [entry for _, entry in sorted(entries, key=lambda value: value[0])]
        observation = await ledger.get_latest_site_observation(
            site_id=baseline.baseline.site_id,
            bridge_id=incident_workflow.bridge_id if incident_workflow is not None else "",
            queue_name=baseline.baseline.queue_name,
        )
        active_job_id = baseline.baseline.scheduler_job_id
        if observation is not None and active_job_id is not None:
            canceled_job = next(
                (
                    job
                    for job in observation.observations
                    if job.scheduler_job_id == active_job_id and job.state is JobState.CANCELED
                ),
                None,
            )
            if canceled_job is not None:
                entries.append(
                    (
                        canceled_job.completed_at or observation.observed_at,
                        {
                            "kind": "QUEUE_CANCELLATION_OBSERVED",
                            "truth_basis": "REAL_READ_ONLY_OBSERVATION",
                            "recorded_at": (
                                canceled_job.completed_at or observation.observed_at
                            ).isoformat(),
                            "observation_id": observation.observation_id,
                            "scheduler_job_id": canceled_job.scheduler_job_id,
                            "device_stop_confirmed": False,
                            "physical_output_isolated": False,
                        },
                    )
                )
        link = await ledger.get_production_link(checkpoint.baseline_id)
        if link is not None:
            receipt = await ledger.get_endpoint_receipt_for_link(
                baseline_id=checkpoint.baseline_id,
                production_link_id=link.link_id,
            )
            if receipt is not None:
                entries.append(
                    (
                        receipt.verified_at,
                        {
                            "kind": "SIMULATED_ENDPOINT_RECEIPT",
                            "truth_basis": receipt.truth_basis,
                            "recorded_at": receipt.verified_at.isoformat(),
                            "scheduler_job_id": receipt.scheduler_job_id,
                            "capture_state": receipt.capture_state.value,
                            "receipt_id": receipt.receipt_id,
                        },
                    )
                )
        return [entry for _, entry in sorted(entries, key=lambda value: value[0])]

    async def _incident_detail(checkpoint: IncidentCheckpoint) -> dict[str, object]:
        if ledger is None:
            raise RuntimeError("ledger is not configured")
        report = (
            await _read_json_artifact(checkpoint.report) if checkpoint.report is not None else None
        )
        source_correction = (
            await _read_json_artifact(checkpoint.source_diff)
            if checkpoint.source_diff is not None
            else None
        )
        packet = (
            await _read_json_artifact(checkpoint.disposition_packet)
            if checkpoint.disposition_packet is not None
            else None
        )
        baseline = await ledger.get_baseline(checkpoint.baseline_id)
        observation = None
        if ledger is not None and baseline is not None and incident_workflow is not None:
            observation = await ledger.get_latest_site_observation(
                site_id=baseline.baseline.site_id,
                bridge_id=incident_workflow.bridge_id,
                queue_name=baseline.baseline.queue_name,
            )
        candidate_manifest: dict[str, object] | None = None
        if checkpoint.candidate_manifest is not None:
            try:
                candidate_manifest = _safe_candidate_manifest_evidence(
                    await _read_json_artifact(checkpoint.candidate_manifest)
                )
            except (KeyError, OSError, RuntimeError, TypeError, UnicodeDecodeError, ValueError):
                candidate_manifest = None
        profile_identity: dict[str, object] | None = None
        try:
            profile = incident_workflow.profile if incident_workflow is not None else None
        except AttributeError:
            profile = None
        if profile is not None and hasattr(profile, "model_dump"):
            profile_identity = profile.model_dump(mode="json")
        review_actions: dict[str, object] = {
            "containment_confirmation": {
                "eligible": False,
                "blocking_reason": "CONTAINMENT_CONFIRMATION_REQUIRED",
            },
            "proof": {"eligible": False, "blocking_reason": "PROOF_NOT_ELIGIBLE"},
            "replacement_observation": {
                "eligible": False,
                "candidate_download_eligible": False,
                "blocking_reason": "REPLACEMENT_NOT_ELIGIBLE",
                "provenance": None,
            },
        }
        if containment_proof_workflow is not None:
            containment, proof = await asyncio.gather(
                containment_proof_workflow.containment_eligibility(
                    incident_id=checkpoint.incident_id
                ),
                containment_proof_workflow.proof_eligibility(incident_id=checkpoint.incident_id),
            )
            review_actions = {
                "containment_confirmation": containment.sanitized_record(),
                "proof": proof.sanitized_record(),
                "replacement_observation": review_actions["replacement_observation"],
            }
        if replacement_observation_workflow is not None:
            replacement = await replacement_observation_workflow.eligibility(
                incident_id=checkpoint.incident_id
            )
            review_actions["replacement_observation"] = replacement.sanitized_record()
        return {
            "checkpoint": checkpoint.model_dump(mode="json"),
            "review_state": (await _review_state_for_checkpoint(checkpoint)).model_dump(
                mode="json"
            ),
            "baseline": baseline.model_dump(mode="json") if baseline is not None else None,
            "source_correction": source_correction,
            "report": report,
            "human_disposition_packet": packet,
            "candidate_manifest": candidate_manifest,
            "profile_identity": profile_identity,
            "candidate_evidence_preview": await _candidate_preview(checkpoint),
            "review_actions": review_actions,
            "current_site_observation": (
                observation.model_dump(mode="json") if observation is not None else None
            ),
        }

    async def _lookup_incident(incident_id: str) -> IncidentCheckpoint | JSONResponse:
        if ledger is None or incident_workflow is None:
            return JSONResponse(status_code=503, content={"status": "BLOCKED"})
        try:
            checkpoint = await ledger.get_incident_checkpoint(incident_id)
        except ValueError:
            return JSONResponse(status_code=404, content={"detail": "incident not found"})
        if checkpoint is None:
            return JSONResponse(status_code=404, content={"detail": "incident not found"})
        return checkpoint

    async def _incident_overview(
        checkpoint: IncidentCheckpoint,
        state: IncidentReviewState,
    ) -> dict[str, object]:
        """Build a compact evidence summary without granting any new authority."""

        source_change_summary = "Open immutable incident detail for source-correction evidence."
        page_impact_summary = "Deterministic page impact is available in incident detail."
        observation_freshness = "No current read-only observation is available."
        watch_highlight: dict[str, object] | None = None
        if checkpoint.report is not None:
            try:
                report = await _read_json_artifact(checkpoint.report)
                semantic = report.get("semantic_assessment")
                if isinstance(semantic, dict) and isinstance(semantic.get("summary"), str):
                    source_change_summary = semantic["summary"]
                impact = report.get("braille_impact")
                if isinstance(impact, dict):
                    changed = impact.get("pages_changed")
                    old_range = impact.get("old_page_range")
                    new_range = impact.get("new_page_range")
                    page_impact_summary = f"Pages changed: {changed}; baseline range {old_range}; candidate range {new_range}."
                    materiality = (
                        semantic.get("materiality") if isinstance(semantic, dict) else None
                    )
                    change_kind = (
                        semantic.get("change_kind") if isinstance(semantic, dict) else None
                    )
                    baseline_count = impact.get("baseline_page_count")
                    candidate_count = impact.get("candidate_page_count")
                    if (
                        isinstance(materiality, str)
                        and isinstance(change_kind, str)
                        and isinstance(baseline_count, int)
                        and not isinstance(baseline_count, bool)
                        and isinstance(candidate_count, int)
                        and not isinstance(candidate_count, bool)
                    ):
                        # The local watch sanitizer independently validates this
                        # closed, structured projection before it reaches the
                        # browser. No source or Gemini free text is included.
                        watch_highlight = {
                            "materiality": materiality,
                            "change_kind": change_kind,
                            "baseline_page_count": baseline_count,
                            "candidate_page_count": candidate_count,
                            "old_page_range": old_range,
                            "new_page_range": new_range,
                            "resynchronized_after_page": impact.get("resynchronized_after_page"),
                        }
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                pass
        baseline = await ledger.get_baseline(checkpoint.baseline_id) if ledger is not None else None
        if ledger is not None and baseline is not None and incident_workflow is not None:
            try:
                observation = await ledger.get_latest_site_observation(
                    site_id=baseline.baseline.site_id,
                    bridge_id=incident_workflow.bridge_id,
                    queue_name=baseline.baseline.queue_name,
                )
                if observation is not None:
                    observed_at = observation.observed_at
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=UTC)
                    age = max(0.0, (datetime.now(UTC) - observed_at).total_seconds())
                    observation_freshness = f"Read-only observation age: {age:.1f} seconds."
            except (AttributeError, RuntimeError, ValueError):
                pass
        if state.state is IncidentState.AWAITING_REPLACEMENT:
            next_safe_action = "Human operator uses the independent production surface, then links a fresh observation."
        elif state.state is IncidentState.REPLACEMENT_OBSERVED:
            next_safe_action = "Replacement is observed only; final verification remains separate."
        elif state.blocking_reason is not None:
            next_safe_action = "Resolve the visible review block through human judgment."
        else:
            next_safe_action = "Review the authoritative incident evidence before any human action."
        return {
            "source_change_summary": source_change_summary,
            "page_impact_summary": page_impact_summary,
            "observation_freshness": observation_freshness,
            "watch_highlight": watch_highlight,
            "next_safe_action": next_safe_action,
        }

    @app.get("/api/v1/incidents")
    async def list_incidents() -> JSONResponse:
        if ledger is None or incident_workflow is None:
            return JSONResponse(status_code=503, content={"status": "BLOCKED"})
        rows: list[dict[str, object]] = []
        for checkpoint in await ledger.list_incident_checkpoints():
            state = await _review_state_for_checkpoint(checkpoint)
            overview = await _incident_overview(checkpoint, state)
            rows.append(
                {
                    "incident_id": checkpoint.incident_id,
                    "baseline_id": checkpoint.baseline_id,
                    "workflow_stage": checkpoint.stage.value,
                    "review_state": state.model_dump(mode="json"),
                    "blocking_reason": checkpoint.blocking_reason.value
                    if checkpoint.blocking_reason is not None
                    else None,
                    "updated_at": checkpoint.updated_at.isoformat(),
                    **overview,
                }
            )
        return JSONResponse(status_code=200, content={"incidents": rows})

    @app.get("/api/v1/incidents/{incident_id}/approved-candidate")
    async def download_approved_candidate(incident_id: str) -> Response:
        """Return only the current proof-approved immutable candidate BRF.

        The route has no artifact URI or filename parameter.  Authentication is
        enforced by the private API middleware before this handler runs.
        """

        if replacement_observation_workflow is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "detail": "replacement observation is not configured",
                },
            )
        try:
            candidate = await replacement_observation_workflow.download_current_candidate(
                incident_id=incident_id
            )
        except ReplacementObservationConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ReplacementObservationRejected, ValueError) as exc:
            reason = (
                exc.reason.value
                if isinstance(exc, ReplacementObservationRejected)
                else BlockingReason.REPLACEMENT_NOT_ELIGIBLE.value
            )
            return JSONResponse(
                status_code=403,
                content={"status": "BLOCKED", "blocking_reason": reason},
            )
        return Response(
            content=candidate.content,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{candidate.filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> JSONResponse:
        checkpoint = await _lookup_incident(incident_id)
        if isinstance(checkpoint, JSONResponse):
            return checkpoint
        return JSONResponse(status_code=200, content=await _incident_detail(checkpoint))

    @app.get("/api/v1/incidents/{incident_id}/timeline")
    async def get_incident_timeline(incident_id: str) -> JSONResponse:
        checkpoint = await _lookup_incident(incident_id)
        if isinstance(checkpoint, JSONResponse):
            return checkpoint
        return JSONResponse(
            status_code=200,
            content={
                "incident_id": checkpoint.incident_id,
                "events": await _incident_timeline(checkpoint),
            },
        )

    @app.post("/api/v1/incidents/{incident_id}/professional-dispositions")
    async def record_professional_disposition(
        incident_id: str,
        payload: ProfessionalDispositionRequest,
        request: Request,
    ) -> JSONResponse:
        if professional_review_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "professional review is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await professional_review_workflow.record_disposition(
                incident_id=incident_id,
                decision=payload.decision,
                selected_role=payload.selected_role,
                expected_state_version=payload.expected_state_version,
                note=payload.note,
                idempotency_key=payload.idempotency_key,
                actor_principal=principal,
            )
        except ProfessionalReviewConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ProfessionalReviewRejected, ValueError) as exc:
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "sanitized_detail": str(exc)},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": result.state.state.value,
                "duplicate": result.duplicate,
                "review_state": result.state.model_dump(mode="json"),
                "professional_disposition": result.disposition.model_dump(mode="json"),
            },
        )

    @app.post("/api/v1/incidents/{incident_id}/operator-attestations")
    async def record_operator_attestation(
        incident_id: str,
        payload: OperatorAttestationRequest,
        request: Request,
    ) -> JSONResponse:
        if professional_review_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "professional review is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await professional_review_workflow.record_operator_attestation(
                incident_id=incident_id,
                attestation_type=payload.attestation_type,
                truth_basis=payload.truth_basis,
                selected_role=payload.selected_role,
                expected_state_version=payload.expected_state_version,
                note=payload.note,
                idempotency_key=payload.idempotency_key,
                actor_principal=principal,
            )
        except ProfessionalReviewConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ProfessionalReviewRejected, ValueError) as exc:
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "sanitized_detail": str(exc)},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": result.state.state.value,
                "duplicate": result.duplicate,
                "review_state": result.state.model_dump(mode="json"),
                "operator_attestation": result.attestation.model_dump(mode="json"),
            },
        )

    @app.post("/api/v1/incidents/{incident_id}/containment-confirmations")
    async def record_containment_confirmation(
        incident_id: str,
        payload: ContainmentConfirmationRequest,
        request: Request,
    ) -> JSONResponse:
        if containment_proof_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "containment proof is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await containment_proof_workflow.record_containment_confirmation(
                incident_id=incident_id,
                halt_disposition_record_id=payload.halt_disposition_record_id,
                site_observation_id=payload.site_observation_id,
                physical_output_isolation_attestation_id=(
                    payload.physical_output_isolation_attestation_id
                ),
                selected_role=payload.selected_role,
                expected_state_version=payload.expected_state_version,
                note=payload.note,
                idempotency_key=payload.idempotency_key,
                actor_principal=principal,
            )
        except ContainmentProofConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ContainmentProofRejected, ValueError) as exc:
            reason = (
                exc.reason.value
                if isinstance(exc, ContainmentProofRejected)
                else BlockingReason.CONTAINMENT_CONFIRMATION_REQUIRED.value
            )
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "blocking_reason": reason},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": result.state.state.value,
                "duplicate": result.duplicate,
                "review_state": result.state.model_dump(mode="json"),
                "containment_confirmation": result.confirmation.model_dump(mode="json"),
            },
        )

    @app.post("/api/v1/incidents/{incident_id}/proof-records")
    async def record_proof(
        incident_id: str,
        payload: ProofRecordRequest,
        request: Request,
    ) -> JSONResponse:
        if containment_proof_workflow is None:
            return JSONResponse(
                status_code=503,
                content={"status": "BLOCKED", "detail": "containment proof is not configured"},
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await containment_proof_workflow.record_proof(
                incident_id=incident_id,
                candidate_sha256=payload.candidate_sha256,
                manifest_sha256=payload.manifest_sha256,
                decision=payload.decision,
                review_basis=payload.review_basis,
                selected_role=payload.selected_role,
                expected_state_version=payload.expected_state_version,
                note=payload.note,
                findings=payload.findings,
                visual_only_uncertainty=payload.visual_only_uncertainty,
                idempotency_key=payload.idempotency_key,
                actor_principal=principal,
            )
        except ContainmentProofConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ContainmentProofRejected, ValueError) as exc:
            reason = (
                exc.reason.value
                if isinstance(exc, ContainmentProofRejected)
                else BlockingReason.PROOF_NOT_ELIGIBLE.value
            )
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "blocking_reason": reason},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": result.state.state.value,
                "duplicate": result.duplicate,
                "review_state": result.state.model_dump(mode="json"),
                "proof_record": result.proof.model_dump(mode="json"),
                "next_human_stage": "AWAITING_HUMAN_SUBMISSION"
                if result.proof.decision is ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION
                else "PROOF_REJECTED_UNRESOLVED",
            },
        )

    @app.post("/api/v1/incidents/{incident_id}/replacement-observation-links")
    async def record_replacement_observation_link(
        incident_id: str,
        payload: ReplacementObservationLinkRequest,
        request: Request,
    ) -> JSONResponse:
        """Append a human link to a fresh bridge observation; never operate a job."""

        if replacement_observation_workflow is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "BLOCKED",
                    "detail": "replacement observation is not configured",
                },
            )
        principal = getattr(request.state, "authenticated_principal", None)
        if not isinstance(principal, str) or not principal:
            return JSONResponse(status_code=401, content={"detail": "verified principal required"})
        try:
            result = await replacement_observation_workflow.record_observation_link(
                incident_id=incident_id,
                candidate_sha256=payload.candidate_sha256,
                candidate_manifest_sha256=payload.candidate_manifest_sha256,
                proof_record_id=payload.proof_record_id,
                scheduler_job_id=payload.scheduler_job_id,
                site_observation_id=payload.site_observation_id,
                selected_role=payload.selected_role,
                expected_state_version=payload.expected_state_version,
                note=payload.note,
                idempotency_key=payload.idempotency_key,
                actor_principal=principal,
            )
        except ReplacementObservationConflict:
            return JSONResponse(
                status_code=409,
                content={"status": "CONFLICT", "blocking_reason": "STALE_STATE_VERSION"},
            )
        except (ReplacementObservationRejected, ValueError) as exc:
            reason = (
                exc.reason.value
                if isinstance(exc, ReplacementObservationRejected)
                else BlockingReason.REPLACEMENT_NOT_ELIGIBLE.value
            )
            return JSONResponse(
                status_code=422,
                content={"status": "NEEDS_REVIEW", "blocking_reason": reason},
            )
        return JSONResponse(
            status_code=200 if result.duplicate else 201,
            content={
                "status": result.state.state.value,
                "duplicate": result.duplicate,
                "review_state": result.state.model_dump(mode="json"),
                "replacement_observation_link": result.link.model_dump(mode="json"),
                "next_human_stage": "OBSERVED_REPLACEMENT_REQUIRES_SEPARATE_VERIFICATION",
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
    production_link_workflow=_runtime.production_link_workflow,
    endpoint_receipt_workflow=_runtime.endpoint_receipt_workflow,
    historical_link_correction_workflow=_runtime.historical_link_correction_workflow,
    telemetry_workflow=_runtime.telemetry_workflow,
    incident_workflow=_runtime.incident_workflow,
    professional_review_workflow=_runtime.professional_review_workflow,
    containment_proof_workflow=_runtime.containment_proof_workflow,
    replacement_observation_workflow=_runtime.replacement_observation_workflow,
    outbox_workflow=_runtime.outbox_workflow,
    automatic_reconciliation_workflow=_runtime.automatic_reconciliation_workflow,
    identity_verifier=_runtime.identity_verifier,
)
