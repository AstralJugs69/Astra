"""Narrow operator CLI for authenticated report-first cloud routes."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import google.auth
import httpx
from google.auth import exceptions as google_auth_exceptions
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import ValidationError

from braille_errata_relay.application.baseline_registration import (
    baseline_registration_idempotency_key,
)
from braille_errata_relay.application.production_link import (
    production_link_idempotency_key,
    production_link_supersession_idempotency_key,
)
from braille_errata_relay.local_setup import (
    LocalRelayConfig,
    doctor_json,
    run_doctor,
    write_local_config,
)


def _add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--impersonate-service-account")


def _identity_token(*, audience: str, impersonate_service_account: str | None) -> str:
    request = GoogleAuthRequest()
    if impersonate_service_account is None:
        return cast(
            str,
            id_token.fetch_id_token(request, audience),  # type: ignore[no-untyped-call]
        )
    source, _ = google.auth.default()
    target = impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
        source_credentials=source,
        target_principal=impersonate_service_account,
        target_scopes=("https://www.googleapis.com/auth/cloud-platform",),
        lifetime=300,
    )
    token_credentials = impersonated_credentials.IDTokenCredentials(  # type: ignore[no-untyped-call]
        target_credentials=target,
        target_audience=audience,
        include_email=True,
    )
    token_credentials.refresh(request)
    if not token_credentials.token:
        raise RuntimeError("identity token impersonation returned no token")
    return cast(str, token_credentials.token)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="braille-relay")
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser(
        "register-demo-baseline",
        help="Register immutable demo baseline lineage; does not submit production work.",
    )
    _add_auth_arguments(baseline)
    baseline.add_argument("--production-id", default="WO-DEMO-001")
    baseline.add_argument("--file-id", required=True)
    baseline.add_argument("--revision-id", required=True)
    baseline.add_argument("--site-id", default="demo-site")
    baseline.add_argument("--queue-name", default="Braille-Embosser-Sim")
    link = commands.add_parser(
        "link-baseline-production",
        help="Create a provisional advisory link for a human-submitted baseline job.",
    )
    _add_auth_arguments(link)
    link.add_argument("--baseline-id", required=True)
    link.add_argument("--scheduler-job-id", required=True, type=int)
    link.add_argument("--expected-state-version", type=int, default=0)
    link.add_argument("--idempotency-key")
    supersede = commands.add_parser(
        "supersede-baseline-production",
        help="Append an advisory replacement for a human-submitted baseline job; never controls CUPS.",
    )
    _add_auth_arguments(supersede)
    supersede.add_argument("--baseline-id", required=True)
    supersede.add_argument("--supersedes-production-link-id", required=True)
    supersede.add_argument("--scheduler-job-id", required=True, type=int)
    supersede.add_argument("--expected-state-version", type=int, required=True)
    supersede.add_argument("--idempotency-key")
    telemetry = commands.add_parser(
        "publish-site-observation",
        help="Publish one completed read-only observation JSON; never accepts commands.",
    )
    _add_auth_arguments(telemetry)
    telemetry.add_argument("--observation", required=True, type=Path)
    init_local = commands.add_parser(
        "init-local-config",
        help="Write non-secret local presentation configuration; never provisions cloud or devices.",
    )
    init_local.add_argument("--project-id")
    init_local.add_argument("--region", default="europe-west3")
    init_local.add_argument("--drive-source")
    init_local.add_argument("--source-mime-type", default="text/markdown")
    init_local.add_argument("--site-id")
    init_local.add_argument("--queue-name")
    init_local.add_argument("--bridge-id")
    init_local.add_argument("--demonstrator-principal")
    init_local.add_argument("--telemetry-principal")
    init_local.add_argument("--relay-api-url")
    init_local.add_argument("--relay-audience")
    init_local.add_argument("--output", type=Path, default=Path(".env"))
    init_local.add_argument("--force", action="store_true")
    init_local.add_argument("--interactive", action="store_true")
    doctor = commands.add_parser(
        "doctor",
        help="Report non-mutating local prerequisites without exposing configuration values.",
    )
    doctor.add_argument("--config", type=Path, default=Path(".env"))
    doctor.add_argument("--check-drive", action="store_true")
    doctor.add_argument("--check-wsl-cups", action="store_true")
    return parser


def _register_demo_baseline(args: argparse.Namespace) -> int:
    idempotency_key = baseline_registration_idempotency_key(
        production_id=args.production_id,
        source_file_id=args.file_id,
        source_revision_id=args.revision_id,
        translation_profile_id="demo-ueb-40x25-v1",
        approval_label="DEMO_FIXTURE_APPROVED",
        site_id=args.site_id,
        queue_name=args.queue_name,
    )
    payload = {
        "production_id": args.production_id,
        "production_id_origin": "EXTERNAL_REFERENCE",
        "source": {
            "provider": "google_drive",
            "file_id": args.file_id,
            "revision_id": args.revision_id,
        },
        "artifact_origin": "DEMO_GENERATED_FIXTURE",
        "approved_brf_sha256": None,
        "approval_label": "DEMO_FIXTURE_APPROVED",
        "translation_profile_id": "demo-ueb-40x25-v1",
        "site_id": args.site_id,
        "queue_name": args.queue_name,
        "idempotency_key": idempotency_key,
    }
    token = _identity_token(
        audience=args.audience,
        impersonate_service_account=args.impersonate_service_account,
    )
    response = httpx.post(
        f"{args.service_url.rstrip('/')}/api/v1/baselines",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if response.status_code not in {200, 201}:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "http_status": response.status_code,
                    "detail": "baseline registration was not accepted",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    body = response.json()
    record = body.get("record", {})
    baseline = record.get("baseline", {}) if isinstance(record, dict) else {}
    print(
        json.dumps(
            {
                "status": body.get("status"),
                "duplicate": body.get("duplicate"),
                "baseline_id": (
                    baseline.get("baseline_id") if isinstance(baseline, dict) else None
                ),
                "approved_brf_sha256": (
                    baseline.get("approved_brf_sha256") if isinstance(baseline, dict) else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def _link_baseline_production(args: argparse.Namespace) -> int:
    idempotency_key = args.idempotency_key or production_link_idempotency_key(
        baseline_id=args.baseline_id,
        scheduler_job_id=args.scheduler_job_id,
        expected_state_version=args.expected_state_version,
    )
    token = _identity_token(
        audience=args.audience,
        impersonate_service_account=args.impersonate_service_account,
    )
    response = httpx.post(
        (f"{args.service_url.rstrip('/')}/api/v1/baselines/{args.baseline_id}/production-links"),
        json={
            "schema_version": "baseline-production-link-request.v1",
            "scheduler_job_id": args.scheduler_job_id,
            "expected_state_version": args.expected_state_version,
            "idempotency_key": idempotency_key,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if response.status_code not in {200, 201}:
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "http_status": response.status_code,
                    "blocking_reason": body.get("blocking_reason"),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    body = response.json()
    link = body.get("production_link", {})
    baseline_record = body.get("baseline", {})
    baseline = baseline_record.get("baseline", {}) if isinstance(baseline_record, dict) else {}
    print(
        json.dumps(
            {
                "status": body.get("status"),
                "duplicate": body.get("duplicate"),
                "link_id": link.get("link_id") if isinstance(link, dict) else None,
                "scheduler_job_id": (
                    link.get("scheduler_job_id") if isinstance(link, dict) else None
                ),
                "baseline_state_version": (
                    baseline.get("state_version") if isinstance(baseline, dict) else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def _supersede_baseline_production(args: argparse.Namespace) -> int:
    idempotency_key = args.idempotency_key or production_link_supersession_idempotency_key(
        baseline_id=args.baseline_id,
        supersedes_production_link_id=args.supersedes_production_link_id,
        scheduler_job_id=args.scheduler_job_id,
        expected_state_version=args.expected_state_version,
    )
    token = _identity_token(
        audience=args.audience,
        impersonate_service_account=args.impersonate_service_account,
    )
    response = httpx.post(
        (
            f"{args.service_url.rstrip('/')}/api/v1/baselines/{args.baseline_id}"
            "/production-link-supersessions"
        ),
        json={
            "schema_version": "baseline-production-link-supersession-request.v1",
            "scheduler_job_id": args.scheduler_job_id,
            "expected_state_version": args.expected_state_version,
            "idempotency_key": idempotency_key,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if response.status_code not in {200, 201}:
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "http_status": response.status_code,
                    "blocking_reason": body.get("blocking_reason"),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    body = response.json()
    link = body.get("production_link", {})
    baseline_record = body.get("baseline", {})
    baseline = baseline_record.get("baseline", {}) if isinstance(baseline_record, dict) else {}
    print(
        json.dumps(
            {
                "status": body.get("status"),
                "duplicate": body.get("duplicate"),
                "link_id": link.get("link_id") if isinstance(link, dict) else None,
                "supersedes_production_link_id": (
                    link.get("supersedes_production_link_id") if isinstance(link, dict) else None
                ),
                "scheduler_job_id": (
                    link.get("scheduler_job_id") if isinstance(link, dict) else None
                ),
                "baseline_state_version": (
                    baseline.get("state_version") if isinstance(baseline, dict) else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def _publish_site_observation(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.observation.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "detail": f"observation JSON is invalid: {type(exc).__name__}",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    if not isinstance(payload, dict):
        print(
            json.dumps({"status": "BLOCKED", "detail": "observation must be an object"}),
            file=sys.stderr,
        )
        return 1
    token = _identity_token(
        audience=args.audience,
        impersonate_service_account=args.impersonate_service_account,
    )
    response = httpx.post(
        f"{args.service_url.rstrip('/')}/internal/site-observations",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if response.status_code != 200:
        try:
            rejected_body = response.json()
        except ValueError:
            rejected_body = {}
        body = rejected_body if isinstance(rejected_body, dict) else {}
        result: dict[str, object] = {
            "status": "BLOCKED",
            "http_status": response.status_code,
        }
        blocking_reason = body.get("blocking_reason")
        if isinstance(blocking_reason, str):
            result["blocking_reason"] = blocking_reason
        sanitized_detail = body.get("sanitized_detail")
        if isinstance(sanitized_detail, str):
            result["detail"] = sanitized_detail
        print(
            json.dumps(result, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    body = response.json()
    print(
        json.dumps(
            {
                "status": body.get("status"),
                "observation_id": body.get("observation_id"),
                "duplicate": body.get("duplicate"),
            },
            sort_keys=True,
        )
    )
    return 0


def _interactive_value(*, prompt: str, existing: str | None, default: str | None = None) -> str:
    if existing:
        return existing
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default or ""


def _init_local_config(args: argparse.Namespace) -> int:
    """Collect non-secret settings and write the ignored local file only once."""

    interactive = bool(args.interactive)
    values = {
        "google_cloud_project": _interactive_value(
            prompt="Google Cloud project ID",
            existing=args.project_id,
        )
        if interactive
        else args.project_id,
        "cloud_run_region": _interactive_value(
            prompt="Cloud Run region",
            existing=args.region,
            default="europe-west3",
        )
        if interactive
        else args.region,
        "drive_file_id": _interactive_value(
            prompt="Authoritative Drive file URL or file ID",
            existing=args.drive_source,
        )
        if interactive
        else args.drive_source,
        "drive_source_mime_type": args.source_mime_type,
        "site_id": _interactive_value(prompt="Site ID", existing=args.site_id)
        if interactive
        else args.site_id,
        "queue_name": _interactive_value(prompt="CUPS queue name", existing=args.queue_name)
        if interactive
        else args.queue_name,
        "local_bridge_id": _interactive_value(prompt="Local bridge ID", existing=args.bridge_id)
        if interactive
        else args.bridge_id,
        "demonstrator_principal_email": args.demonstrator_principal,
        "telemetry_principal_email": args.telemetry_principal,
        "relay_api_url": args.relay_api_url,
        "relay_audience": args.relay_audience,
    }
    try:
        config = LocalRelayConfig.model_validate(values)
        preview = {
            key: "configured" if value else "not configured"
            for key, value in {
                "google_cloud_project": config.google_cloud_project,
                "cloud_run_region": config.cloud_run_region,
                "drive_source": config.drive_file_id,
                "site_id": config.site_id,
                "queue_name": config.queue_name,
                "local_bridge_id": config.local_bridge_id,
                "demonstrator_principal": config.demonstrator_principal_email,
                "telemetry_principal": config.telemetry_principal_email,
                "relay_api_url": config.relay_api_url,
                "relay_audience": config.relay_audience,
            }.items()
        }
        print(json.dumps({"status": "PREVIEW", "configuration": preview}, sort_keys=True))
        write_local_config(path=args.output, config=config, force=bool(args.force))
    except (FileExistsError, OSError, ValidationError, ValueError):
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "detail": "local configuration was not written; correct non-secret values or use --force",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "detail": "non-secret local configuration written; use browser-based gcloud login for authentication",
            },
            sort_keys=True,
        )
    )
    return 0


def _doctor(args: argparse.Namespace) -> int:
    checks = run_doctor(
        config_path=args.config,
        check_drive=bool(args.check_drive),
        check_wsl_cups=bool(args.check_wsl_cups),
    )
    print(doctor_json(checks))
    return 1 if any(check.status == "BLOCKED" for check in checks) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init-local-config":
            return _init_local_config(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "register-demo-baseline":
            return _register_demo_baseline(args)
        if args.command == "link-baseline-production":
            return _link_baseline_production(args)
        if args.command == "supersede-baseline-production":
            return _supersede_baseline_production(args)
        if args.command == "publish-site-observation":
            return _publish_site_observation(args)
        raise AssertionError("unreachable command")
    except google_auth_exceptions.GoogleAuthError:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "detail": (
                        "identity token issuance failed; verify the temporary "
                        "service-account-scoped Token Creator grant"
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
