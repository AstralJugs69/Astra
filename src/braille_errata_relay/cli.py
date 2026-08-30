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

from braille_errata_relay.application.baseline_registration import (
    baseline_registration_idempotency_key,
)
from braille_errata_relay.application.production_link import (
    production_link_idempotency_key,
    production_link_supersession_idempotency_key,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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
