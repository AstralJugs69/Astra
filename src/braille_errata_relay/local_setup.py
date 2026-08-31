"""Safe, local-only first-run configuration and diagnostic helpers.

This module never creates cloud resources, changes IAM, edits Drive, contacts
CUPS, submits a job, or handles a password or service-account key.  It writes
only an explicitly requested gitignored local environment file and exposes
read-only diagnostics for an evaluator's machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import google.auth
from google.auth import exceptions as google_auth_exceptions
from google.oauth2.credentials import Credentials as UserAdcCredentials
from pydantic import BaseModel, ConfigDict, field_validator

from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.readiness import check_liblouis_readiness

_DRIVE_FILE_ID = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
_IDENTIFIER = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
_PROJECT_IDENTIFIER = "abcdefghijklmnopqrstuvwxyz0123456789-"
_SUPPORTED_DRIVE_SOURCE_MIME_TYPES = frozenset(
    {"text/markdown", "application/vnd.google-apps.document"}
)


def _only_from(value: str, allowed: str, *, min_length: int, max_length: int) -> bool:
    return min_length <= len(value) <= max_length and all(
        character in allowed for character in value
    )


def extract_drive_file_id(value: str) -> str:
    """Accept a direct file ID or a standard HTTPS Google Drive URL safely."""

    candidate = value.strip()
    if _only_from(candidate, _DRIVE_FILE_ID, min_length=10, max_length=200):
        return candidate
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in {"drive.google.com", "docs.google.com"}
        or parsed.fragment
    ):
        raise ValueError("Drive source must be a direct file ID or standard HTTPS Google Drive URL")
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    file_id: str | None = None
    if parsed.hostname == "drive.google.com":
        if len(path_segments) >= 3 and path_segments[:2] == ["file", "d"]:
            file_id = path_segments[2]
        elif path_segments in (["open"], ["uc"]):
            query = parse_qs(parsed.query, keep_blank_values=True)
            values = query.get("id", [])
            if len(values) == 1:
                file_id = values[0]
    elif len(path_segments) >= 3 and path_segments[1] == "d":
        file_id = path_segments[2]
    if file_id is None or not _only_from(file_id, _DRIVE_FILE_ID, min_length=10, max_length=200):
        raise ValueError("Drive URL did not contain one valid file ID")
    return file_id


def _is_private_relay_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


class LocalRelayConfig(BaseModel):
    """Typed, non-secret values necessary to start the local presentation app."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    google_cloud_project: str
    cloud_run_region: str = "europe-west3"
    drive_file_id: str
    drive_source_mime_type: str = "text/markdown"
    site_id: str
    queue_name: str
    local_bridge_id: str
    demonstrator_principal_email: str | None = None
    telemetry_principal_email: str | None = None
    relay_api_url: str | None = None
    relay_audience: str | None = None

    @field_validator("google_cloud_project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not _only_from(normalized, _PROJECT_IDENTIFIER, min_length=6, max_length=30)
            or not normalized[0].isalpha()
            or not normalized[-1].isalnum()
        ):
            raise ValueError("Google Cloud project ID is malformed")
        return normalized

    @field_validator("cloud_run_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        normalized = value.strip()
        if not _only_from(normalized, _PROJECT_IDENTIFIER, min_length=3, max_length=63):
            raise ValueError("Cloud Run region is malformed")
        return normalized

    @field_validator("drive_file_id", mode="before")
    @classmethod
    def validate_drive_file_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("Drive source must be text")
        return extract_drive_file_id(value)

    @field_validator("drive_source_mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in _SUPPORTED_DRIVE_SOURCE_MIME_TYPES:
            raise ValueError(
                "source MIME type must be text/markdown or application/vnd.google-apps.document"
            )
        return normalized

    @field_validator("site_id", "queue_name", "local_bridge_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not _only_from(normalized, _IDENTIFIER, min_length=1, max_length=128):
            raise ValueError(
                "site, queue, and bridge identifiers must use safe portable characters"
            )
        return normalized

    @field_validator("demonstrator_principal_email", "telemetry_principal_email")
    @classmethod
    def validate_principal(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        local, separator, domain = normalized.partition("@")
        if (
            separator != "@"
            or not local
            or not domain.endswith(".iam.gserviceaccount.com")
            or " " in normalized
        ):
            raise ValueError("principal must be a service-account email address")
        return normalized

    @field_validator("relay_api_url", "relay_audience")
    @classmethod
    def validate_relay_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().rstrip("/")
        if not _is_private_relay_url(normalized):
            raise ValueError("private Relay URL must be a credential-free HTTPS origin")
        return normalized


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def sanitized_record(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def local_config_env(config: LocalRelayConfig) -> str:
    """Render a deterministic, non-secret local environment file."""

    values = {
        "GOOGLE_CLOUD_PROJECT": config.google_cloud_project,
        "CLOUD_RUN_REGION": config.cloud_run_region,
        "DRIVE_FILE_ID": config.drive_file_id,
        "DRIVE_SOURCE_MIME_TYPE": config.drive_source_mime_type,
        "SITE_ID": config.site_id,
        "QUEUE_NAME": config.queue_name,
        "LOCAL_BRIDGE_ID": config.local_bridge_id,
        "DEMONSTRATOR_PRINCIPAL_EMAIL": config.demonstrator_principal_email or "",
        "INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL": config.telemetry_principal_email or "",
        "RELAY_API_BASE_URL": config.relay_api_url or "",
        "RELAY_API_AUDIENCE": config.relay_audience or "",
    }
    body = [
        "# Generated by braille-relay init-local-config. This file contains no secret.",
        "# Use browser-based gcloud auth login and gcloud auth application-default login;",
        "# never place passwords, OAuth tokens, or service-account JSON keys in this file.",
    ]
    body.extend(f"{key}={value}" for key, value in values.items())
    return "\n".join(body) + "\n"


def write_local_config(*, path: Path, config: LocalRelayConfig, force: bool) -> None:
    """Write only an explicitly named local file, refusing accidental overwrite."""

    if path.exists() and not force:
        raise FileExistsError("local configuration exists; repeat with --force to replace it")
    if path.name not in {".env", ".env.local"}:
        raise ValueError("local configuration output must be .env or .env.local")
    path.write_text(local_config_env(config), encoding="utf-8", newline="\n")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or not key or not key.replace("_", "").isalnum() or key != key.upper():
            raise ValueError("local environment file contains a malformed key")
        values[key] = value
    return values


def load_local_config(path: Path) -> LocalRelayConfig:
    """Load the generated non-secret configuration without shell evaluation."""

    values = _read_env(path)
    return LocalRelayConfig(
        google_cloud_project=values.get("GOOGLE_CLOUD_PROJECT", ""),
        cloud_run_region=values.get("CLOUD_RUN_REGION", "europe-west3"),
        drive_file_id=values.get("DRIVE_FILE_ID", ""),
        drive_source_mime_type=values.get("DRIVE_SOURCE_MIME_TYPE", "text/markdown"),
        site_id=values.get("SITE_ID", ""),
        queue_name=values.get("QUEUE_NAME", ""),
        local_bridge_id=values.get("LOCAL_BRIDGE_ID", ""),
        demonstrator_principal_email=values.get("DEMONSTRATOR_PRINCIPAL_EMAIL") or None,
        telemetry_principal_email=values.get("INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL") or None,
        relay_api_url=values.get("RELAY_API_BASE_URL") or None,
        relay_audience=values.get("RELAY_API_AUDIENCE") or None,
    )


def _command_available(name: str) -> DoctorCheck:
    return DoctorCheck(
        name=name,
        status="PASS" if shutil.which(name) is not None else "BLOCKED",
        detail="available" if shutil.which(name) is not None else "not found on PATH",
    )


def _ordinary_adc_check() -> DoctorCheck:
    try:
        credentials, _ = google.auth.default()
    except google_auth_exceptions.DefaultCredentialsError:
        return DoctorCheck(
            "ordinary_adc", "BLOCKED", "browser-based application-default login is required"
        )
    if not isinstance(credentials, UserAdcCredentials):
        return DoctorCheck("ordinary_adc", "BLOCKED", "ordinary local user ADC is required")
    return DoctorCheck("ordinary_adc", "PASS", "ordinary local user ADC is available")


def _liblouis_check() -> DoctorCheck:
    try:
        profile = load_translation_profile(
            Path("config") / "translation_profiles" / "demo-ueb-40x25-v1.json"
        )
        report = check_liblouis_readiness(profile)
    except (OSError, ValueError):
        return DoctorCheck("liblouis", "BLOCKED", "pinned profile or local binding is unavailable")
    if report.ready:
        return DoctorCheck(
            "liblouis", "PASS", "pinned version, tables, and translation smoke passed"
        )
    return DoctorCheck("liblouis", "BLOCKED", "pinned Liblouis readiness is unavailable")


def _wsl_cups_check() -> DoctorCheck:
    executable = shutil.which("wsl.exe") or shutil.which("wsl")
    if executable is None:
        return DoctorCheck("wsl_cups", "OPTIONAL", "WSL is not available on this host")
    try:
        result = subprocess.run(
            [executable, "--status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return DoctorCheck("wsl_cups", "OPTIONAL", "WSL status could not be inspected")
    if result.returncode == 0:
        return DoctorCheck(
            "wsl_cups", "PASS", "WSL is available; run the separate human-owned CUPS harness"
        )
    return DoctorCheck("wsl_cups", "OPTIONAL", "WSL is installed but not ready")


def _drive_read_check(config: LocalRelayConfig) -> DoctorCheck:
    """Perform one optional metadata-only Drive request using ordinary ADC."""

    try:
        from googleapiclient.discovery import build  # type: ignore[import-untyped]

        credentials, _ = google.auth.default(
            scopes=("https://www.googleapis.com/auth/drive.readonly",)
        )
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        metadata = (
            service.files()
            .get(
                fileId=config.drive_file_id,
                fields="id,mimeType,trashed,capabilities(canDownload)",
                supportsAllDrives=True,
            )
            .execute()
        )
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("id") != config.drive_file_id
            or metadata.get("mimeType") != config.drive_source_mime_type
            or metadata.get("trashed") is True
            or (
                config.drive_source_mime_type == "application/vnd.google-apps.document"
                and (
                    not isinstance(metadata.get("capabilities"), Mapping)
                    or metadata["capabilities"].get("canDownload") is not True
                )
            )
        ):
            return DoctorCheck("drive_read", "BLOCKED", "configured Drive metadata is not eligible")
    except Exception:  # noqa: BLE001 - Drive client error details can contain private identifiers.
        return DoctorCheck(
            "drive_read", "BLOCKED", "read-only Drive metadata access is unavailable"
        )
    return DoctorCheck("drive_read", "PASS", "read-only Drive metadata access passed")


def run_doctor(
    *,
    config_path: Path,
    check_drive: bool,
    check_wsl_cups: bool,
    command_exists: Callable[[str], str | None] = shutil.which,
) -> tuple[DoctorCheck, ...]:
    """Run non-mutating prerequisite checks with intentionally sanitized output."""

    python_ok = (3, 11) <= sys.version_info[:2] <= (3, 12)
    checks: list[DoctorCheck] = [
        DoctorCheck(
            "python",
            "PASS" if python_ok else "BLOCKED",
            "supported Python 3.11–3.12" if python_ok else "Python 3.11–3.12 is required",
        ),
        DoctorCheck(
            "uv",
            "PASS" if command_exists("uv") is not None else "BLOCKED",
            "available" if command_exists("uv") is not None else "not found on PATH",
        ),
        DoctorCheck(
            "docker",
            "PASS" if command_exists("docker") is not None else "OPTIONAL",
            "available" if command_exists("docker") is not None else "not found on PATH",
        ),
        DoctorCheck(
            "gcloud",
            "PASS" if command_exists("gcloud") is not None else "BLOCKED",
            "available" if command_exists("gcloud") is not None else "not found on PATH",
        ),
        _ordinary_adc_check(),
    ]
    config: LocalRelayConfig | None = None
    try:
        config = load_local_config(config_path)
    except (OSError, ValueError):
        checks.append(
            DoctorCheck(
                "local_configuration", "BLOCKED", "valid local .env configuration is required"
            )
        )
    else:
        checks.append(
            DoctorCheck("local_configuration", "PASS", "valid non-secret local configuration")
        )
        relay_urls_ready = config.relay_api_url is not None and config.relay_audience is not None
        checks.append(
            DoctorCheck(
                "private_relay_url",
                "PASS" if relay_urls_ready else "BLOCKED",
                "credential-free private Relay URL format is configured"
                if relay_urls_ready
                else "RELAY_API_BASE_URL and RELAY_API_AUDIENCE are required",
            )
        )
    checks.append(_liblouis_check())
    if check_wsl_cups:
        checks.append(_wsl_cups_check())
    if check_drive:
        checks.append(
            _drive_read_check(config)
            if config is not None
            else DoctorCheck("drive_read", "BLOCKED", "valid local configuration is required first")
        )
    return tuple(checks)


def doctor_json(checks: tuple[DoctorCheck, ...]) -> str:
    """Serialize only named check outcomes, never values from local config."""

    return json.dumps(
        {
            "schema_version": "local-doctor.v1",
            "checks": [check.sanitized_record() for check in checks],
            "status": "PASS"
            if not any(check.status == "BLOCKED" for check in checks)
            else "BLOCKED",
        },
        sort_keys=True,
    )
