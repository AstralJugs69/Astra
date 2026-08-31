"""Typed environment configuration for the isolated cloud Gate 0 slice."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SUPPORTED_DRIVE_SOURCE_MIME_TYPES = frozenset(
    {"text/markdown", "application/vnd.google-apps.document"}
)


class CloudSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    cloud_run_region: str = Field(min_length=1)
    google_cloud_location: str = Field(min_length=1)
    gemini_model: str = Field(min_length=1)
    drive_file_id: str | None = None
    drive_source_mime_type: str = "text/markdown"
    source_max_bytes: int = Field(default=1_048_576, gt=0)
    semantic_context_chars: int = Field(default=12_000, gt=0, le=12_000)
    semantic_model_timeout_seconds: float = Field(default=90.0, gt=0, le=90.0)
    firestore_database: str = "(default)"
    artifact_bucket: str | None = None
    runtime_service_account_email: str | None = None
    internal_oidc_audience: str | None = None
    source_push_principal_email: str | None = None
    telemetry_push_principal_email: str | None = None
    scheduler_principal_email: str | None = None
    demonstrator_principal_email: str | None = None
    endpoint_evidence_principal_email: str | None = None
    site_id: str | None = None
    bridge_id: str | None = None
    cups_queue_name: str | None = None

    @field_validator("drive_source_mime_type")
    @classmethod
    def validate_drive_source_mime_type(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in _SUPPORTED_DRIVE_SOURCE_MIME_TYPES:
            raise ValueError(
                "DRIVE_SOURCE_MIME_TYPE must be text/markdown or application/vnd.google-apps.document"
            )
        return normalized

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CloudSettings:
        values = env or os.environ
        return cls(
            project_id=values.get("GOOGLE_CLOUD_PROJECT", ""),
            cloud_run_region=values.get("CLOUD_RUN_REGION", ""),
            google_cloud_location=values.get("GOOGLE_CLOUD_LOCATION", ""),
            gemini_model=values.get("GEMINI_MODEL", ""),
            drive_file_id=values.get("DRIVE_FILE_ID") or None,
            drive_source_mime_type=values.get("DRIVE_SOURCE_MIME_TYPE", "text/markdown"),
            source_max_bytes=int(values.get("SOURCE_MAX_BYTES", "1048576")),
            semantic_context_chars=int(values.get("SEMANTIC_CONTEXT_CHARS", "12000")),
            semantic_model_timeout_seconds=float(
                values.get("SEMANTIC_MODEL_TIMEOUT_SECONDS", "90")
            ),
            firestore_database=values.get("FIRESTORE_DATABASE", "(default)"),
            artifact_bucket=values.get("GCS_ARTIFACT_BUCKET") or None,
            runtime_service_account_email=values.get("RUNTIME_SERVICE_ACCOUNT_EMAIL") or None,
            internal_oidc_audience=values.get("INTERNAL_OIDC_AUDIENCE") or None,
            source_push_principal_email=(
                values.get("INTERNAL_SOURCE_PUSH_PRINCIPAL_EMAIL") or None
            ),
            telemetry_push_principal_email=(
                values.get("INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL") or None
            ),
            scheduler_principal_email=(values.get("INTERNAL_SCHEDULER_PRINCIPAL_EMAIL") or None),
            demonstrator_principal_email=values.get("DEMONSTRATOR_PRINCIPAL_EMAIL") or None,
            endpoint_evidence_principal_email=(
                values.get("ENDPOINT_EVIDENCE_PRINCIPAL_EMAIL") or None
            ),
            site_id=values.get("RELAY_SITE_ID") or None,
            bridge_id=values.get("RELAY_BRIDGE_ID") or None,
            cups_queue_name=values.get("RELAY_CUPS_QUEUE_NAME") or None,
        )
