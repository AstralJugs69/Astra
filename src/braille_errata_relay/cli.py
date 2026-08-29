"""Narrow operator CLI for authenticated report-first cloud routes."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from braille_errata_relay.contracts.canonical_json import canonical_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="braille-relay")
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser(
        "register-demo-baseline",
        help="Register immutable demo baseline lineage; does not submit production work.",
    )
    baseline.add_argument("--service-url", required=True)
    baseline.add_argument("--audience", required=True)
    baseline.add_argument("--production-id", default="WO-DEMO-001")
    baseline.add_argument("--file-id", required=True)
    baseline.add_argument("--revision-id", required=True)
    baseline.add_argument("--site-id", default="demo-site")
    baseline.add_argument("--queue-name", default="Braille-Embosser-Sim")
    return parser


def _register_demo_baseline(args: argparse.Namespace) -> int:
    identity = {
        "production_id": args.production_id,
        "file_id": args.file_id,
        "revision_id": args.revision_id,
        "translation_profile_id": "demo-ueb-40x25-v1",
    }
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
        "idempotency_key": canonical_sha256(identity),
    }
    token = id_token.fetch_id_token(  # type: ignore[no-untyped-call]
        GoogleAuthRequest(), args.audience
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "register-demo-baseline":
        return _register_demo_baseline(args)
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
