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
from braille_errata_relay.application.drive_gate0 import DRIVE_READONLY_SCOPE, DriveGate0Workflow
from braille_errata_relay.cloud_settings import CloudSettings


@dataclass(frozen=True)
class RuntimeDependencies:
    settings: CloudSettings | None
    assessor: AdkSemanticAssessor | None
    ledger: FirestoreGate0Ledger | None
    drive_workflow: DriveGate0Workflow | None
    identity_verifier: IdentityVerifier


def build_runtime_dependencies() -> RuntimeDependencies:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return RuntimeDependencies(None, None, None, None, GoogleOidcVerifier())
    try:
        settings = CloudSettings.from_env()
    except (ValidationError, ValueError):
        return RuntimeDependencies(None, None, None, None, GoogleOidcVerifier())
    assessor = AdkSemanticAssessor(
        model_id=settings.gemini_model,
        context_char_limit=settings.semantic_context_chars,
    )
    ledger = FirestoreGate0Ledger(
        project_id=settings.project_id,
        database=settings.firestore_database,
    )
    drive_workflow = None
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
        drive_workflow = DriveGate0Workflow(
            provider=provider,
            reconciler=DriveChangeReconciler(provider=provider),
            artifact_store=GcsArtifactStore(
                bucket_name=settings.artifact_bucket,
                project_id=settings.project_id,
            ),
            ledger=ledger,
            runtime_service_account_email=settings.runtime_service_account_email,
        )
    return RuntimeDependencies(
        settings,
        assessor,
        ledger,
        drive_workflow,
        GoogleOidcVerifier(),
    )
