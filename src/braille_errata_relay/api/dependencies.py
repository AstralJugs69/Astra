"""Assemble cloud adapters at the API edge; domain modules stay SDK-free."""

from __future__ import annotations

import os
from dataclasses import dataclass

import google.auth
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from pydantic import ValidationError

from braille_errata_relay.adapters.adk_assessor import AdkSemanticAssessor
from braille_errata_relay.adapters.drive import DriveBlobProvider, DriveChangeReconciler
from braille_errata_relay.adapters.firestore_ledger import FirestoreGate0Ledger
from braille_errata_relay.adapters.gcs_artifacts import GcsArtifactStore
from braille_errata_relay.api.security import GoogleOidcVerifier, IdentityVerifier
from braille_errata_relay.application.automatic_reconciliation import (
    AutomaticReconciliationWorkflow,
)
from braille_errata_relay.application.baseline_registration import BaselineRegistrationWorkflow
from braille_errata_relay.application.containment_proof import ContainmentProofWorkflow
from braille_errata_relay.application.drive_gate0 import DRIVE_READONLY_SCOPE, DriveGate0Workflow
from braille_errata_relay.application.endpoint_receipt import (
    EndpointReceiptWorkflow,
    HistoricalLinkCorrectionWorkflow,
)
from braille_errata_relay.application.incident_workflow import IncidentWorkflow
from braille_errata_relay.application.outbox_drain import OutboxDrainWorkflow
from braille_errata_relay.application.production_link import ProductionLinkWorkflow
from braille_errata_relay.application.professional_review import ProfessionalReviewWorkflow
from braille_errata_relay.application.replacement_observation import (
    ReplacementObservationWorkflow,
)
from braille_errata_relay.application.semantic_workflow import IdempotentSemanticWorkflow
from braille_errata_relay.application.telemetry_ingestion import (
    TelemetryAllowlist,
    TelemetryIngestionWorkflow,
)
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.cloud_settings import CloudSettings
from braille_errata_relay.configuration import resolve_config_path


@dataclass(frozen=True)
class RuntimeDependencies:
    settings: CloudSettings | None
    assessor: AdkSemanticAssessor | None
    ledger: FirestoreGate0Ledger | None
    drive_workflow: DriveGate0Workflow | None
    baseline_workflow: BaselineRegistrationWorkflow | None
    production_link_workflow: ProductionLinkWorkflow | None
    endpoint_receipt_workflow: EndpointReceiptWorkflow | None
    historical_link_correction_workflow: HistoricalLinkCorrectionWorkflow | None
    professional_review_workflow: ProfessionalReviewWorkflow | None
    containment_proof_workflow: ContainmentProofWorkflow | None
    replacement_observation_workflow: ReplacementObservationWorkflow | None
    telemetry_workflow: TelemetryIngestionWorkflow | None
    incident_workflow: IncidentWorkflow | None
    outbox_workflow: OutboxDrainWorkflow | None
    automatic_reconciliation_workflow: AutomaticReconciliationWorkflow | None
    identity_verifier: IdentityVerifier


def build_runtime_dependencies() -> RuntimeDependencies:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return RuntimeDependencies(
            settings=None,
            assessor=None,
            ledger=None,
            drive_workflow=None,
            baseline_workflow=None,
            production_link_workflow=None,
            endpoint_receipt_workflow=None,
            historical_link_correction_workflow=None,
            professional_review_workflow=None,
            containment_proof_workflow=None,
            replacement_observation_workflow=None,
            telemetry_workflow=None,
            incident_workflow=None,
            outbox_workflow=None,
            automatic_reconciliation_workflow=None,
            identity_verifier=GoogleOidcVerifier(),
        )
    try:
        settings = CloudSettings.from_env()
    except (ValidationError, ValueError):
        return RuntimeDependencies(
            settings=None,
            assessor=None,
            ledger=None,
            drive_workflow=None,
            baseline_workflow=None,
            production_link_workflow=None,
            endpoint_receipt_workflow=None,
            historical_link_correction_workflow=None,
            professional_review_workflow=None,
            containment_proof_workflow=None,
            replacement_observation_workflow=None,
            telemetry_workflow=None,
            incident_workflow=None,
            outbox_workflow=None,
            automatic_reconciliation_workflow=None,
            identity_verifier=GoogleOidcVerifier(),
        )
    assessor = AdkSemanticAssessor(
        model_id=settings.gemini_model,
        context_char_limit=settings.semantic_context_chars,
        model_timeout_seconds=settings.semantic_model_timeout_seconds,
    )
    ledger = FirestoreGate0Ledger(
        project_id=settings.project_id,
        database=settings.firestore_database,
    )
    drive_workflow = None
    baseline_workflow = None
    production_link_workflow = None
    endpoint_receipt_workflow = None
    historical_link_correction_workflow = None
    professional_review_workflow = None
    containment_proof_workflow = None
    replacement_observation_workflow = None
    telemetry_workflow = None
    incident_workflow = None
    outbox_workflow = None
    automatic_reconciliation_workflow = None
    if (
        settings.drive_file_id
        and settings.artifact_bucket
        and settings.runtime_service_account_email
    ):
        credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
        drive_service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        provider = DriveBlobProvider(
            service=drive_service,
            expected_file_id=settings.drive_file_id,
            supported_mime_type=settings.drive_source_mime_type,
            max_bytes=settings.source_max_bytes,
        )
        artifact_store = GcsArtifactStore(
            bucket_name=settings.artifact_bucket,
            project_id=settings.project_id,
        )
        drive_workflow = DriveGate0Workflow(
            provider=provider,
            reconciler=DriveChangeReconciler(provider=provider),
            artifact_store=artifact_store,
            ledger=ledger,
            runtime_service_account_email=settings.runtime_service_account_email,
        )
        profile_path = resolve_config_path(
            direct_env="TRANSLATION_PROFILE_PATH",
            relative_path="translation_profiles/demo-ueb-40x25-v1.json",
        )
        baseline_workflow = BaselineRegistrationWorkflow(
            ledger=ledger,
            artifact_store=artifact_store,
            profile=load_translation_profile(profile_path),
            translator=LiblouisAdapter(),
        )
        if settings.bridge_id:
            production_link_workflow = ProductionLinkWorkflow(
                ledger=ledger,
                bridge_id=settings.bridge_id,
            )
            incident_workflow = IncidentWorkflow(
                ledger=ledger,
                artifact_store=artifact_store,
                profile=baseline_workflow.profile,
                translator=baseline_workflow.translator,
                semantic_workflow=IdempotentSemanticWorkflow(
                    assessor=assessor,
                    ledger=ledger,
                ),
                bridge_id=settings.bridge_id,
            )
            outbox_workflow = OutboxDrainWorkflow(
                ledger=ledger,
                incident_workflow=incident_workflow,
            )
            automatic_reconciliation_workflow = AutomaticReconciliationWorkflow(
                drive_workflow=drive_workflow,
                outbox_workflow=outbox_workflow,
                ledger=ledger,
            )
            endpoint_receipt_workflow = EndpointReceiptWorkflow(
                ledger=ledger,
                artifact_store=artifact_store,
            )
            historical_link_correction_workflow = HistoricalLinkCorrectionWorkflow(
                ledger=ledger,
            )
            professional_review_workflow = ProfessionalReviewWorkflow(ledger=ledger)
            containment_proof_workflow = ContainmentProofWorkflow(
                ledger=ledger,
                artifact_store=artifact_store,
                profile=baseline_workflow.profile,
                bridge_id=settings.bridge_id,
            )
            replacement_observation_workflow = ReplacementObservationWorkflow(
                ledger=ledger,
                containment_proof_workflow=containment_proof_workflow,
                artifact_store=artifact_store,
            )
    if settings.site_id and settings.bridge_id and settings.cups_queue_name:
        telemetry_workflow = TelemetryIngestionWorkflow(
            ledger=ledger,
            allowlist=TelemetryAllowlist(
                site_id=settings.site_id,
                bridge_id=settings.bridge_id,
                queue_name=settings.cups_queue_name,
            ),
        )
    return RuntimeDependencies(
        settings,
        assessor,
        ledger,
        drive_workflow,
        baseline_workflow,
        production_link_workflow,
        endpoint_receipt_workflow,
        historical_link_correction_workflow,
        professional_review_workflow,
        containment_proof_workflow,
        replacement_observation_workflow,
        telemetry_workflow,
        incident_workflow,
        outbox_workflow,
        automatic_reconciliation_workflow,
        GoogleOidcVerifier(),
    )
