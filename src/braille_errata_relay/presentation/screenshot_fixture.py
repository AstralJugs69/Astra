"""Offline-only, sanitized presentation fixture used to render demo screenshots.

This module is deliberately not mounted by the deployed Relay API.  It contains
synthetic values only, defines GET routes only, and does not call Cloud services,
the bridge, or a production device.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from braille_errata_relay.presentation.app import _next_safe_action, _pretty, _templates
from braille_errata_relay.presentation.watch import (
    WatchEvent,
    sanitize_watch_snapshot,
    sse_frame,
    watch_summary,
)

FIXTURE_TRUTH_BASIS = "SANITIZED_OFFLINE_DEMO_FIXTURE"
REPORT_READY_ID = "1" * 64
BLOCKED_ID = "2" * 64
PROOF_READY_ID = "3" * 64
REPLACEMENT_OBSERVED_ID = "4" * 64
CANDIDATE_SHA256 = "5" * 64
MANIFEST_SHA256 = "6" * 64
OBSERVATION_ID = "7" * 64
PROOF_RECORD_ID = "8" * 64
NOW = datetime(2026, 8, 30, 18, 0, tzinfo=UTC)


def _row(
    *,
    incident_id: str,
    state: str,
    blocking_reason: str | None,
    summary: str,
    next_safe_action: str,
    workflow_stage: str,
) -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "workflow_stage": workflow_stage,
        "review_state": {"state": state, "state_version": 7},
        "blocking_reason": blocking_reason,
        "source_change_summary": summary,
        "page_impact_summary": "One synthetic full-volume candidate page is affected.",
        "next_safe_action": next_safe_action,
    }


def _overview_rows() -> tuple[dict[str, object], ...]:
    return (
        _row(
            incident_id=REPORT_READY_ID,
            state="REPORT_READY",
            blocking_reason=None,
            summary="Synthetic source correction is ready for coordinator review.",
            next_safe_action="Record a professional disposition after reviewing the report.",
            workflow_stage="REPORT_READY",
        ),
        _row(
            incident_id=BLOCKED_ID,
            state="NEEDS_REVIEW",
            blocking_reason="SEMANTIC_REVIEW_REQUIRED",
            summary="Synthetic ambiguity deliberately remains visible and blocked.",
            next_safe_action="Resolve semantic uncertainty with a qualified human review.",
            workflow_stage="NEEDS_REVIEW",
        ),
        _row(
            incident_id=PROOF_READY_ID,
            state="AWAITING_REPLACEMENT",
            blocking_reason=None,
            summary="Exact synthetic candidate has a demo-fixture proof approval.",
            next_safe_action="Use an independent production surface, then observe the job.",
            workflow_stage="REPORT_READY",
        ),
        _row(
            incident_id=REPLACEMENT_OBSERVED_ID,
            state="REPLACEMENT_OBSERVED",
            blocking_reason=None,
            summary="A synthetic external-job observation is recorded without closure.",
            next_safe_action="Separate final verification remains outside this fixture.",
            workflow_stage="REPORT_READY",
        ),
    )


def _detail(incident_id: str) -> dict[str, object]:
    rows: dict[str, dict[str, object]] = {}
    for row in _overview_rows():
        row_incident_id = row["incident_id"]
        if isinstance(row_incident_id, str):
            rows[row_incident_id] = row
    selected = rows.get(incident_id)
    if selected is None:
        raise KeyError(incident_id)
    raw_review_state = selected["review_state"]
    if not isinstance(raw_review_state, dict):
        raise TypeError("sanitized fixture review state must be an object")
    review_state = dict(raw_review_state)
    if selected["blocking_reason"] is not None:
        review_state["blocking_reason"] = selected["blocking_reason"]
    is_proof_ready = incident_id == PROOF_READY_ID
    is_replacement_observed = incident_id == REPLACEMENT_OBSERVED_ID
    candidate_provenance = {
        "candidate_sha256": CANDIDATE_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "proof_record_id": PROOF_RECORD_ID,
        "candidate_label": "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER",
    }
    replacement_action = {
        "eligible": is_proof_ready,
        "candidate_download_eligible": is_proof_ready,
        "blocking_reason": None if is_proof_ready else "REPLACEMENT_NOT_ELIGIBLE",
        "provenance": candidate_provenance if is_proof_ready else None,
    }
    timeline = [
        {
            "kind": "REPORT_READY",
            "truth_basis": "DETERMINISTIC_REPORT",
            "recorded_at": NOW.isoformat(),
        },
        {
            "kind": "PROOF_RECORD",
            "truth_basis": "DEMO_FIXTURE_REVIEW",
            "recorded_at": NOW.isoformat(),
        },
    ]
    if is_replacement_observed:
        timeline.append(
            {
                "kind": "REPLACEMENT_OBSERVATION_LINK",
                "truth_basis": "HUMAN_SUBMITTED_EXTERNAL_JOB_PLUS_READ_ONLY_OBSERVATION",
                "recorded_at": NOW.isoformat(),
            }
        )
    return {
        "incident_id": incident_id,
        "review_state": review_state,
        "source_correction": _pretty(
            {
                "truth_basis": FIXTURE_TRUTH_BASIS,
                "changed_block": "Synthetic paragraph correction for visual review.",
            }
        ),
        "semantic_summary": "Synthetic semantic assessment: human review remains authoritative.",
        "uncertainties": ("Synthetic terminology confirmation remains illustrative.",),
        "braille_impact": _pretty(
            {
                "truth_basis": "DETERMINISTIC_FIXTURE",
                "old_page_range": [1, 1],
                "new_page_range": [1, 1],
                "pages_changed": True,
            }
        ),
        "baseline_brf_sha256": "9" * 64,
        "candidate_brf_sha256": CANDIDATE_SHA256,
        "candidate_manifest": _pretty(
            {
                "schema_version": "artifact-manifest.v1",
                "artifact_sha256": CANDIDATE_SHA256,
                "translation_profile_sha256": "a" * 64,
                "liblouis_version": "3.38.0",
            }
        ),
        "profile_identity": _pretty(
            {
                "profile_id": "demo-ueb-40x25-v1",
                "formatter_version": "relay-formatter.v1",
                "truth_basis": FIXTURE_TRUTH_BASIS,
            }
        ),
        "candidate_evidence_preview": {
            "label": "TEXT EVIDENCE PREVIEW ONLY - NOT TACTILE PROOF",
            "text": "Synthetic six-dot Braille preview remains visual evidence only.",
        },
        "observation_age": "3.0 seconds (synthetic)",
        "current_observation": _pretty(
            {
                "schema_version": "site-observation.v1",
                "truth_basis": FIXTURE_TRUTH_BASIS,
                "queue_name": "Synthetic-Braille-Queue",
                "observed_job": {
                    "scheduler_job_id": 43,
                    "state": "PENDING_HELD" if is_proof_ready else "PROCESSING",
                },
            }
        ),
        "current_observation_id": OBSERVATION_ID,
        "containment_evidence": "{}",
        "review_actions": {
            "containment_confirmation": {
                "eligible": False,
                "blocking_reason": "CONTAINMENT_CONFIRMATION_REQUIRED",
            },
            "proof": {
                "eligible": False,
                "blocking_reason": "PROOF_NOT_ELIGIBLE",
            },
            "replacement_observation": replacement_action,
        },
        "timeline": tuple(timeline),
        "decisions": ("HALT_REQUESTED", "DEFERRED"),
        "attestation_types": ("PHYSICAL_OUTPUT_ISOLATED",),
        "truth_bases": ("HUMAN_ATTESTATION", "SIMULATED_DEMO"),
        "proof_decisions": ("APPROVED_FOR_HUMAN_SUBMISSION", "REJECTED"),
        "csrf_token": "fixture-csrf-not-a-secret",
        "disposition_idempotency_key": "fixture-disposition",
        "attestation_idempotency_key": "fixture-attestation",
        "containment_idempotency_key": "fixture-containment",
        "proof_idempotency_key": "fixture-proof",
        "replacement_idempotency_key": "fixture-replacement",
        "fixture_mode": True,
        "next_safe_action": _next_safe_action(review_state),
        "source_comparison": {
            "old": "paragraph: Synthetic wording before correction.",
            "new": "paragraph: Synthetic wording after correction.",
        },
        "workflow_stage": str(selected["workflow_stage"]),
        "workflow_stages": (
            "DETECTED",
            "DIFF_READY",
            "CANDIDATE_READY",
            "IMPACT_READY",
            "SEMANTIC_READY",
            "REPORT_READY",
            "NEEDS_REVIEW",
        ),
        "semantic_activity": "Gemini semantic assessment complete.",
        "semantic_materiality": "MATERIAL (synthetic fixture)",
        "uncertainty_summary": "1 persisted synthetic uncertainty item requires human judgment.",
        "impact_summary": "Changed; baseline pages 1, candidate pages 1.",
        "error": None,
    }


def create_screenshot_fixture_app() -> FastAPI:
    """Create the offline screenshot app; it has no private Relay API client."""

    templates = _templates()
    app = FastAPI(
        title="Braille Errata Relay sanitized screenshot fixture",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def fixture_security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; connect-src 'self'; "
            "style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def overview() -> HTMLResponse:
        return HTMLResponse(
            templates.get_template("index.html").render(
                incidents=_overview_rows(),
                summary={"total": 4, "blocked": 1},
                error=None,
                fixture_mode=True,
            )
        )

    @app.get("/watch", response_class=HTMLResponse)
    async def watch() -> HTMLResponse:
        snapshot = sanitize_watch_snapshot({"incidents": list(_overview_rows())}, observed_at=NOW)
        return HTMLResponse(
            templates.get_template("watch.html").render(
                snapshot=snapshot,
                watch=watch_summary(snapshot),
                error=None,
                fixture_mode=True,
                fixture_alert=True,
            )
        )

    @app.get("/watch/quiet", response_class=HTMLResponse)
    async def quiet_watch() -> HTMLResponse:
        snapshot = sanitize_watch_snapshot({"incidents": []}, observed_at=NOW)
        return HTMLResponse(
            templates.get_template("watch.html").render(
                snapshot=snapshot,
                watch=watch_summary(snapshot),
                error=None,
                fixture_mode=True,
                fixture_alert=False,
            )
        )

    @app.get("/events")
    async def events() -> Response:
        """A fixed GET-only event sample for the sanitized watch screenshot."""

        snapshot = sanitize_watch_snapshot({"incidents": list(_overview_rows())}, observed_at=NOW)
        payload: dict[str, object] = {
            "incident": {
                "incident_id": BLOCKED_ID,
                "workflow_stage": "NEEDS_REVIEW",
                "review_state": "NEEDS_REVIEW",
                "blocking_reason": "SEMANTIC_REVIEW_REQUIRED",
                "next_safe_action": "Resolve the visible review block through qualified human judgment.",
            }
        }
        body = sse_frame(
            WatchEvent("snapshot", {"initial": True, "snapshot": snapshot}),
            retry_milliseconds=2000,
        ) + sse_frame(WatchEvent("review_required", payload))
        return Response(body, media_type="text/event-stream")

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    async def incident(incident_id: str) -> HTMLResponse:
        try:
            context = _detail(incident_id)
        except KeyError:
            return HTMLResponse(
                "<!doctype html><p>Fixture incident not found.</p>", status_code=404
            )
        return HTMLResponse(templates.get_template("incident.html").render(**context))

    return app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="braille-relay-screenshot-fixture")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise ValueError("fixture port is outside the valid TCP range")
    import uvicorn

    uvicorn.run(
        create_screenshot_fixture_app(),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
