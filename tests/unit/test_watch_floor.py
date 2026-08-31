from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from braille_errata_relay.presentation.app import (
    PresentationSettings,
    PrivateReviewApiError,
    create_presentation_app,
)
from braille_errata_relay.presentation.assets import WATCH_JAVASCRIPT
from braille_errata_relay.presentation.screenshot_fixture import create_screenshot_fixture_app
from braille_errata_relay.presentation.watch import (
    WatchEventTracker,
    heartbeat_event,
    sanitize_watch_snapshot,
    sse_frame,
)

AUDIENCE = "https://private-relay.example.test"
DEMONSTRATOR_IDENTITY = "relay-demonstrator@project-12345.iam.gserviceaccount.com"
FIRST_ID = "1" * 64
SECOND_ID = "2" * 64


def _overview(
    *,
    incident_id: str = FIRST_ID,
    stage: str = "IMPACT_READY",
    review_state: str = "ASSESSING",
    blocking_reason: str | None = None,
) -> dict[str, object]:
    return {
        "incidents": [
            {
                "incident_id": incident_id,
                "workflow_stage": stage,
                "review_state": {"state": review_state},
                "blocking_reason": blocking_reason,
                "baseline_id": "private-baseline-must-not-cross-the-browser-boundary",
                "source_change_summary": "private source content must not cross the browser boundary",
                "private_principal": "private@example.test",
            }
        ]
    }


def _automation(
    *,
    state: str = "IDLE",
    outcome: str | None = "COMPLETED",
    status: str | None = "COMPLETED",
    source_change_detected: bool = False,
    content_equivalent_replay: bool = False,
    source_investigation_pending: bool = False,
) -> dict[str, object]:
    return {
        "schema_version": "automation-cycle-status.v1",
        "state": state,
        "last_outcome": outcome,
        "last_status": status,
        "last_completed_at": "2026-08-31T12:00:00+00:00",
        "source_change_detected": source_change_detected,
        "content_equivalent_replay": content_equivalent_replay,
        "source_investigation_pending": source_investigation_pending,
        "source_unavailable": False,
        "outbox": {
            "leased": 0,
            "completed": 0,
            "retried": 0,
            "dead_letter_possible": 0,
        },
        "last_error_code": None,
        "private_drive_file_id": "private-file-must-not-cross-browser-boundary",
        "private_receipt_id": "a" * 64,
    }


class OverviewApi:
    def __init__(
        self,
        *responses: dict[str, object] | Exception,
        automation_responses: Sequence[dict[str, object] | Exception] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._automation_responses = list(automation_responses or (_automation(),))
        self.calls = 0
        self.paths: list[str] = []

    async def get_json(self, path: str) -> dict[str, object]:
        if path == "/api/v1/incidents":
            response = self._responses[min(self.calls_for(path), len(self._responses) - 1)]
        elif path == "/api/v1/automation-status":
            response = self._automation_responses[
                min(self.calls_for(path), len(self._automation_responses) - 1)
            ]
        else:
            raise AssertionError(f"unexpected watch API path: {path}")
        self.calls += 1
        self.paths.append(path)
        if isinstance(response, Exception):
            raise response
        return response

    def calls_for(self, path: str) -> int:
        return sum(candidate == path for candidate in self.paths)

    async def post_json(self, _path: str, _payload: Mapping[str, object]) -> dict[str, object]:
        raise AssertionError("watch floor must not post to the private API")

    async def get_bytes(self, _path: str) -> tuple[bytes, str]:
        raise AssertionError("watch floor must not download an artifact")


def _client(
    api: OverviewApi,
    *,
    watch_poll_seconds: float = 2.0,
    watch_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> TestClient:
    app = create_presentation_app(
        PresentationSettings(
            api_base_url=AUDIENCE,
            audience=AUDIENCE,
            session_secret="s" * 32,
            impersonate_service_account=DEMONSTRATOR_IDENTITY,
        ),
        api_client=api,
        watch_poll_seconds=watch_poll_seconds,
        watch_sleep=watch_sleep,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_watch_snapshot_drops_private_fields_and_invalid_rows() -> None:
    payload = _overview()
    payload["incidents"] = [
        *payload["incidents"],  # type: ignore[operator]
        {"incident_id": "not-a-hash", "workflow_stage": "NEEDS_REVIEW"},
    ]

    snapshot = sanitize_watch_snapshot(
        payload,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        automation=_automation(source_change_detected=True),
    )
    rendered = str(snapshot)

    assert snapshot["source_label"] == "Authoritative source"
    assert snapshot["checked_at"] == "2026-08-30T00:00:00+00:00"
    assert snapshot["incidents"] == [
        {
            "incident_id": FIRST_ID,
            "workflow_stage": "IMPACT_READY",
            "workflow_label": "Braille page impact calculated",
            "review_state": "ASSESSING",
            "blocking_reason": None,
            "next_safe_action": "Next step: semantic assessment.",
        }
    ]
    for forbidden in ("private-baseline", "private source", "private@example"):
        assert forbidden not in rendered
    assert snapshot["automation"] == {
        "state": "IDLE",
        "last_outcome": "COMPLETED",
        "last_status": "COMPLETED",
        "last_completed_at": "2026-08-31T12:00:00+00:00",
        "source_change_detected": True,
        "content_equivalent_replay": False,
        "source_investigation_pending": False,
        "source_unavailable": False,
        "outbox": {
            "leased": 0,
            "completed": 0,
            "retried": 0,
            "dead_letter_possible": 0,
        },
        "last_error_code": None,
    }
    assert "private-file" not in rendered
    assert "private_receipt" not in rendered


def test_watch_snapshot_keeps_only_closed_report_highlights_and_sorts_by_latest_timestamp() -> None:
    first = _overview(incident_id=FIRST_ID, stage="REPORT_READY", review_state="REPORT_READY")
    second = _overview(incident_id=SECOND_ID, stage="REPORT_READY", review_state="REPORT_READY")
    first["incidents"][0].update(  # type: ignore[index]
        {
            "updated_at": "2026-08-31T12:00:00+00:00",
            "watch_highlight": {
                "materiality": "MATERIAL",
                "change_kind": "FACTUAL_CORRECTION",
                "baseline_page_count": 46,
                "candidate_page_count": 46,
                "old_page_range": {"start": 24, "end": 24},
                "new_page_range": {"start": 24, "end": 24},
                "resynchronized_after_page": 24,
                "semantic_summary": "private model prose must not cross the browser boundary",
            },
        }
    )
    second["incidents"][0].update(  # type: ignore[index]
        {
            "updated_at": "2026-08-31T12:01:00+00:00",
            "watch_highlight": {
                "materiality": "MATERIAL",
                "change_kind": "FACTUAL_CORRECTION",
                "baseline_page_count": 46,
                "candidate_page_count": 46,
                "old_page_range": {"start": 24, "end": 24},
                "new_page_range": {"start": 24, "end": 24},
                "resynchronized_after_page": 24,
            },
        }
    )

    snapshot = sanitize_watch_snapshot(
        {"incidents": [first["incidents"][0], second["incidents"][0]]},  # type: ignore[index]
        observed_at=datetime(2026, 8, 31, 12, 2, tzinfo=UTC),
        automation=_automation(),
    )

    assert [row["incident_id"] for row in snapshot["incidents"]] == [SECOND_ID, FIRST_ID]
    assert snapshot["incidents"][0]["watch_highlight"] == {
        "materiality": "MATERIAL",
        "change_kind": "FACTUAL_CORRECTION",
        "baseline_page_count": 46,
        "candidate_page_count": 46,
        "old_page_range": {"start": 24, "end": 24},
        "new_page_range": {"start": 24, "end": 24},
        "resynchronized_after_page": 24,
    }
    assert "semantic_summary" not in str(snapshot)


def test_watch_tracker_deduplicates_historical_snapshots_and_emits_one_new_alert() -> None:
    first = sanitize_watch_snapshot(_overview())
    second = sanitize_watch_snapshot(
        _overview(
            incident_id=SECOND_ID,
            stage="NEEDS_REVIEW",
            review_state="NEEDS_REVIEW",
            blocking_reason="SEMANTIC_REVIEW_REQUIRED",
        )
    )
    second["incidents"] = [*first["incidents"], *second["incidents"]]  # type: ignore[operator]

    tracker = WatchEventTracker()
    initial = tracker.observe(first)
    unchanged = tracker.observe(first)
    new_incident = tracker.observe(second)
    duplicate = tracker.observe(second)
    reconnect = WatchEventTracker().observe(second)

    assert [event.name for event in initial] == ["snapshot"]
    assert all(event.name != "review_required" for event in initial)
    assert unchanged == ()
    assert [event.name for event in new_incident] == [
        "snapshot",
        "incident_detected",
        "review_required",
    ]
    assert new_incident[0].payload == {"initial": False, "snapshot": second}
    assert duplicate == ()
    assert [event.name for event in reconnect] == ["snapshot"]
    assert all(event.name != "review_required" for event in reconnect)


def test_watch_tracker_emits_durable_stage_transition_once() -> None:
    tracker = WatchEventTracker()
    tracker.observe(sanitize_watch_snapshot(_overview(stage="IMPACT_READY")))
    transitioned = tracker.observe(
        sanitize_watch_snapshot(_overview(stage="REPORT_READY", review_state="REPORT_READY"))
    )
    repeated = tracker.observe(
        sanitize_watch_snapshot(_overview(stage="REPORT_READY", review_state="REPORT_READY"))
    )

    assert [event.name for event in transitioned] == [
        "snapshot",
        "stage_changed",
        "report_ready",
        "review_required",
    ]
    assert transitioned[0].payload["initial"] is False
    assert repeated == ()


def test_watch_tracker_emits_a_durable_automation_transition_once_without_alert() -> None:
    tracker = WatchEventTracker()
    first = sanitize_watch_snapshot(_overview(), automation=_automation())
    changed = sanitize_watch_snapshot(
        _overview(),
        automation=_automation(source_change_detected=True),
    )

    initial = tracker.observe(first)
    transitioned = tracker.observe(changed)
    repeated = tracker.observe(changed)

    assert [event.name for event in initial] == ["snapshot"]
    assert [event.name for event in transitioned] == ["snapshot", "automation_cycle"]
    assert transitioned[0].payload == {"initial": False, "snapshot": changed}
    assert transitioned[1].payload == {"automation": changed["automation"]}
    assert repeated == ()


def test_watch_sse_endpoint_has_framing_security_headers_and_same_origin_asset() -> None:
    client = _client(OverviewApi(_overview()))

    page = client.get("/watch")
    stream = client.get("/events?max_events=1")
    asset = client.get("/assets/watch.js")

    assert page.status_code == 200
    assert 'src="/assets/watch.js"' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert "private-baseline" not in page.text
    assert "Completed; no new source content requiring investigation" in page.text
    assert "2026-08-31T12:00:00+00:00" not in page.text
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["x-accel-buffering"] == "no"
    assert stream.text.startswith("retry: 2000\nevent: snapshot\ndata: ")
    assert stream.text.endswith("\n\n")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("application/javascript")
    assert 'new window.EventSource("/events")' in asset.text
    assert "watch-automatic-cycle" in asset.text


def test_watch_sse_refreshes_sanitized_summary_before_later_transition_events() -> None:
    async def no_wait(_seconds: float) -> None:
        return None

    client = _client(
        OverviewApi(
            _overview(stage="IMPACT_READY"),
            _overview(stage="REPORT_READY", review_state="REPORT_READY"),
        ),
        watch_poll_seconds=0.001,
        watch_sleep=no_wait,
    )

    response = client.get("/events?max_events=2")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    initial = json.loads(frames[0].split("data: ", maxsplit=1)[1])
    refreshed = json.loads(frames[1].split("data: ", maxsplit=1)[1])

    assert response.status_code == 200
    assert frames[0].startswith("retry: 2000\nevent: snapshot\n")
    assert frames[1].startswith("event: snapshot\n")
    assert initial["initial"] is True
    assert refreshed["initial"] is False
    assert refreshed["snapshot"]["incidents"][0]["workflow_stage"] == "REPORT_READY"
    assert (
        refreshed["snapshot"]["incidents"][0]["next_safe_action"]
        == "Review the report and record only an attributable human disposition."
    )


def test_watch_sse_upstream_error_is_sanitized_and_heartbeat_framing_is_explicit() -> None:
    client = _client(OverviewApi(PrivateReviewApiError(503)))

    response = client.get("/events?max_events=1")
    heartbeat = sse_frame(heartbeat_event(checked_at=datetime(2026, 8, 30, tzinfo=UTC)))

    assert response.status_code == 200
    assert "event: upstream_unavailable" in response.text
    assert "temporarily unavailable" in response.text
    assert "private Relay API request returned" not in response.text
    assert heartbeat == (
        'event: heartbeat\ndata: {"checked_at":"2026-08-30T00:00:00+00:00",'
        '"schema_version":"watch-heartbeat.v1"}\n\n'
    )


def test_status_only_failure_keeps_the_watch_floor_available_and_sanitized() -> None:
    api = OverviewApi(
        _overview(),
        automation_responses=[PrivateReviewApiError(503)],
    )
    client = _client(api)

    page = client.get("/watch")
    events = client.get("/events?max_events=1")

    assert page.status_code == 200
    assert events.status_code == 200
    assert "Automatic status temporarily unavailable" in page.text
    assert "private Relay API request returned" not in page.text
    assert api.paths == [
        "/api/v1/incidents",
        "/api/v1/automation-status",
        "/api/v1/incidents",
        "/api/v1/automation-status",
    ]


def test_watch_client_controls_are_opt_in_and_do_not_issue_cloud_mutations() -> None:
    source = WATCH_JAVASCRIPT

    assert 'getElementById("enable-audible-alerts")' in source
    assert 'getElementById("acknowledge-alert-locally")' in source
    assert "audibleEnabled = true" in source
    assert "acknowledged = true" in source
    assert 'new window.EventSource("/events")' in source
    assert 'events.addEventListener("snapshot", (event) => {' in source
    assert "renderSnapshot(JSON.parse(event.data))" in source
    assert "payload.initial" not in source
    assert "fetch(" not in source
    assert ".post(" not in source
    assert "window.open" not in source
    assert "10000" in source
    assert "60000" in source


def test_sanitized_fixture_exposes_get_only_watch_alert_and_event_sample() -> None:
    client = TestClient(create_screenshot_fixture_app(), base_url="http://127.0.0.1:8877")

    watch = client.get("/watch")
    events = client.get("/events")

    assert watch.status_code == 200
    assert "SANITIZED DEMO FIXTURE" in watch.text
    assert "Connected" in watch.text
    assert "SOURCE / PRODUCTION MISMATCH — HUMAN REVIEW REQUIRED" in watch.text
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: review_required" in events.text
    assert client.post("/watch").status_code in {404, 405}


def test_watch_endpoint_is_read_only_with_no_background_mutation_calls() -> None:
    api = OverviewApi(_overview())
    client = _client(api)

    response = client.get("/events?max_events=1")
    asyncio.run(asyncio.sleep(0))

    assert response.status_code == 200
    assert api.calls == 2
    assert api.paths == ["/api/v1/incidents", "/api/v1/automation-status"]
