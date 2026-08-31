"""Sanitized, loopback-only watch-floor event primitives.

The presentation server polls existing private, read-only incident and
automation-status APIs on behalf of a local browser.  This module deliberately
knows nothing about credentials, CUPS, Drive, or human-record mutations.  It
reduces private responses to the small, stable subset the watch floor needs and
emits deduplicated transition facts for Server-Sent Events.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from braille_errata_relay.domain.models import IncidentWorkflowStage
from braille_errata_relay.presentation.view_models import workflow_label

WATCH_SCHEMA_VERSION = "watch-snapshot.v1"
WATCH_SOURCE_LABEL = "Authoritative source"
QUALIFYING_STAGES = frozenset(
    {
        IncidentWorkflowStage.REPORT_READY.value,
        IncidentWorkflowStage.NEEDS_REVIEW.value,
    }
)
_WORKFLOW_STAGES = frozenset(stage.value for stage in IncidentWorkflowStage)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ENUM = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,127}$")
_AUTOMATION_STATES = frozenset({"NOT_YET_RUN", "RUNNING", "IDLE", "UNAVAILABLE"})
_AUTOMATION_OUTCOMES = frozenset({"COMPLETED", "FAILED"})
_AUTOMATION_STATUSES = frozenset(
    {"COMPLETED", "ALREADY_RUNNING", "SOURCE_UNAVAILABLE", "NEEDS_ATTENTION"}
)
_AUTOMATION_SCHEMA_VERSION = "automation-cycle-status.v1"
_MAX_OUTBOX_COUNT = 100
_MAX_PAGE_COUNT = 100_000
_MATERIALITY_VALUES = frozenset({"MATERIAL", "NOT_MATERIAL", "UNCERTAIN"})
_CHANGE_KIND_VALUES = frozenset(
    {
        "FACTUAL_CORRECTION",
        "INSTRUCTION_CHANGE",
        "NAVIGATION_CHANGE",
        "EDITORIAL_CHANGE",
        "FORMATTING_ONLY",
        "UNKNOWN",
    }
)


@dataclass(frozen=True)
class WatchEvent:
    """A bounded, browser-safe event emitted by the local watch floor."""

    name: str
    payload: dict[str, object]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_enum(value: object, *, default: str) -> str:
    if isinstance(value, str) and _SAFE_ENUM.fullmatch(value) is not None:
        return value
    return default


def _next_safe_action(*, stage: str, review_state: str, blocking_reason: str | None) -> str:
    """Return a local, non-operative instruction derived from durable facts."""

    if blocking_reason is not None or stage == IncidentWorkflowStage.NEEDS_REVIEW.value:
        return "Resolve the visible review block through qualified human judgment."
    if stage == IncidentWorkflowStage.SEMANTIC_READY.value:
        return "Gemini semantic assessment is complete; prepare the professional report."
    if stage == IncidentWorkflowStage.IMPACT_READY.value:
        return "Next step: semantic assessment."
    if stage == IncidentWorkflowStage.REPORT_READY.value:
        return "Review the report and record only an attributable human disposition."
    if review_state == "AWAITING_REPLACEMENT":
        return "Use the independent production surface, then link a fresh read-only observation."
    if review_state == "REPLACEMENT_OBSERVED":
        return "Observed replacement evidence is recorded; final verification remains separate."
    return "Continue monitoring authoritative evidence; Relay performs no production action."


def _normalized_timestamp(value: object) -> tuple[str, float] | None:
    """Accept a bounded RFC-3339-like value and return safe display/sort values."""

    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    normalized = parsed.astimezone(UTC)
    return normalized.isoformat(), normalized.timestamp()


def _page_range(value: object, *, total: int) -> dict[str, int] | None:
    raw = value if isinstance(value, Mapping) else {}
    start = raw.get("start")
    end = raw.get("end")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 1 <= start <= end <= total
    ):
        return {"start": start, "end": end}
    return None


def _sanitize_watch_highlight(value: object) -> dict[str, object] | None:
    """Pass only closed report facts to the browser, never source/model prose."""

    raw = value if isinstance(value, Mapping) else {}
    materiality = raw.get("materiality")
    change_kind = raw.get("change_kind")
    baseline_count = raw.get("baseline_page_count")
    candidate_count = raw.get("candidate_page_count")
    if (
        materiality not in _MATERIALITY_VALUES
        or change_kind not in _CHANGE_KIND_VALUES
        or not isinstance(baseline_count, int)
        or isinstance(baseline_count, bool)
        or not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or not 1 <= baseline_count <= _MAX_PAGE_COUNT
        or not 1 <= candidate_count <= _MAX_PAGE_COUNT
    ):
        return None
    old_range = _page_range(raw.get("old_page_range"), total=baseline_count)
    new_range = _page_range(raw.get("new_page_range"), total=candidate_count)
    if old_range is None and new_range is None:
        return None
    resync = raw.get("resynchronized_after_page")
    safe_resync: int | None = None
    if (
        isinstance(resync, int)
        and not isinstance(resync, bool)
        and 1 <= resync <= max(baseline_count, candidate_count)
    ):
        safe_resync = resync
    return {
        "materiality": materiality,
        "change_kind": change_kind,
        "baseline_page_count": baseline_count,
        "candidate_page_count": candidate_count,
        "old_page_range": old_range,
        "new_page_range": new_range,
        "resynchronized_after_page": safe_resync,
    }


def _sanitize_incident(row: Mapping[str, object]) -> dict[str, object] | None:
    incident_id = row.get("incident_id")
    if not isinstance(incident_id, str) or _HEX_SHA256.fullmatch(incident_id) is None:
        return None
    stage = row.get("workflow_stage")
    if not isinstance(stage, str) or stage not in _WORKFLOW_STAGES:
        return None
    review_state_value = row.get("review_state")
    review_state = review_state_value if isinstance(review_state_value, Mapping) else {}
    state = _safe_enum(review_state.get("state"), default="DETECTED")
    blocking_value = row.get("blocking_reason")
    blocking_reason = (
        _safe_enum(blocking_value, default="REVIEW_EVIDENCE_UNAVAILABLE")
        if blocking_value is not None
        else None
    )
    timestamp = _normalized_timestamp(row.get("updated_at"))
    highlight = _sanitize_watch_highlight(row.get("watch_highlight"))
    sanitized: dict[str, object] = {
        "incident_id": incident_id,
        "workflow_stage": stage,
        "workflow_label": workflow_label(stage),
        "review_state": state,
        "blocking_reason": blocking_reason,
        "next_safe_action": _next_safe_action(
            stage=stage,
            review_state=state,
            blocking_reason=blocking_reason,
        ),
    }
    if timestamp is not None:
        sanitized["updated_at"] = timestamp[0]
        sanitized["_updated_at_sort"] = timestamp[1]
    if highlight is not None:
        sanitized["watch_highlight"] = highlight
    return sanitized


def _sanitize_automation_status(automation: Mapping[str, object] | None) -> dict[str, object]:
    """Reduce one private automation record to a fixed, browser-safe shape."""

    unavailable: dict[str, object] = {
        "state": "UNAVAILABLE",
        "last_outcome": None,
        "last_status": None,
        "last_completed_at": None,
        "source_change_detected": False,
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
    if automation is None or automation.get("schema_version") != _AUTOMATION_SCHEMA_VERSION:
        return unavailable

    state_value = automation.get("state")
    state = state_value if state_value in _AUTOMATION_STATES else "UNAVAILABLE"
    outcome_value = automation.get("last_outcome")
    last_outcome = outcome_value if outcome_value in _AUTOMATION_OUTCOMES else None
    status_value = automation.get("last_status")
    last_status = status_value if status_value in _AUTOMATION_STATUSES else None
    completed_at = automation.get("last_completed_at")
    last_completed_at: str | None = None
    if isinstance(completed_at, str) and len(completed_at) <= 64:
        try:
            parsed = datetime.fromisoformat(completed_at)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
            last_completed_at = parsed.isoformat()
    raw_outbox = automation.get("outbox")
    outbox = raw_outbox if isinstance(raw_outbox, Mapping) else {}

    def safe_count(name: str) -> int:
        value = outbox.get(name)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_OUTBOX_COUNT
        ):
            return value
        return 0

    error_value = automation.get("last_error_code")
    error_code = (
        error_value
        if isinstance(error_value, str) and _SAFE_IDENTIFIER.fullmatch(error_value) is not None
        else None
    )
    return {
        "state": state,
        "last_outcome": last_outcome,
        "last_status": last_status,
        "last_completed_at": last_completed_at,
        "source_change_detected": automation.get("source_change_detected") is True,
        "content_equivalent_replay": automation.get("content_equivalent_replay") is True,
        "source_investigation_pending": automation.get("source_investigation_pending") is True,
        "source_unavailable": automation.get("source_unavailable") is True,
        "outbox": {
            "leased": safe_count("leased"),
            "completed": safe_count("completed"),
            "retried": safe_count("retried"),
            "dead_letter_possible": safe_count("dead_letter_possible"),
        },
        "last_error_code": error_code,
    }


def sanitize_watch_snapshot(
    payload: Mapping[str, object],
    *,
    observed_at: datetime | None = None,
    automation: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Reduce an API response to a deterministic, browser-safe watch snapshot.

    Raw Drive identifiers, source content, semantic model material, queue data,
    credentials, principals, and arbitrary upstream error text are excluded by
    construction.  Invalid rows are ignored rather than forwarded.
    """

    raw_rows = payload.get("incidents")
    if not isinstance(raw_rows, list):
        raise TypeError("private incident overview does not contain an incidents list")
    incidents = [
        sanitized
        for row in raw_rows
        if isinstance(row, Mapping)
        if (sanitized := _sanitize_incident(row)) is not None
    ]

    def sort_key(incident: Mapping[str, object]) -> tuple[float, str]:
        raw_timestamp = incident.get("_updated_at_sort")
        timestamp = raw_timestamp if isinstance(raw_timestamp, float) else float("-inf")
        return -timestamp, str(incident["incident_id"])

    incidents.sort(key=sort_key)
    for incident in incidents:
        incident.pop("_updated_at_sort", None)
    now = observed_at or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    return {
        "schema_version": WATCH_SCHEMA_VERSION,
        "source_label": WATCH_SOURCE_LABEL,
        "checked_at": now.isoformat(),
        "incidents": incidents,
        "automation": _sanitize_automation_status(automation),
    }


def _automation_summary(automation: Mapping[str, object]) -> str:
    state = automation.get("state")
    if state == "UNAVAILABLE":
        return "Automatic status temporarily unavailable"
    if state == "NOT_YET_RUN":
        return "Waiting for the background scheduler"
    if state == "RUNNING":
        return "Checking authoritative Drive source"
    if automation.get("last_outcome") == "FAILED":
        label = "Last automatic cycle failed safely; inspect scheduler and error state"
    elif automation.get("last_status") == "NEEDS_ATTENTION":
        label = "Automatic cycle needs attention"
    elif automation.get("last_status") == "SOURCE_UNAVAILABLE":
        label = "Authoritative source is currently unavailable"
    elif automation.get("source_investigation_pending") is True:
        label = "Drive source content detected; investigation is queued"
    elif automation.get("content_equivalent_replay") is True:
        label = "Drive revision matched existing source bytes; no new investigation"
    elif automation.get("source_change_detected") is True:
        label = "Drive source content detected; durable workflow advanced"
    elif automation.get("last_status") == "COMPLETED":
        label = "Completed; no new source content requiring investigation"
    else:
        label = "Waiting for durable automatic-cycle evidence"
    return label


def watch_summary(
    snapshot: Mapping[str, object], *, suppress_existing_results: bool = False
) -> dict[str, object]:
    """Build only display-safe summary fields for the watch template."""

    raw_incidents = snapshot.get("incidents")
    incidents = raw_incidents if isinstance(raw_incidents, list) else []
    raw_automation = snapshot.get("automation")
    automation = raw_automation if isinstance(raw_automation, Mapping) else {}
    automatic_cycle = _automation_summary(automation)
    # A new browser session must not turn retained historical incidents into a
    # live mismatch alarm.  The SSE tracker separately promotes only a durable
    # transition it observes after the connection is established.
    if suppress_existing_results:
        return {
            "source_label": WATCH_SOURCE_LABEL,
            "durable_stage": "WATCHING",
            "stage_label": "Monitoring authoritative source",
            "next_safe_action": (
                "No newly observed incident is awaiting review. "
                "Continue watching authoritative source."
            ),
            "automatic_cycle": automatic_cycle,
            "hero": None,
        }
    if not incidents:
        return {
            "source_label": WATCH_SOURCE_LABEL,
            "durable_stage": "WATCHING",
            "stage_label": "Monitoring authoritative source",
            "next_safe_action": "No incident is awaiting review. Continue watching authoritative source.",
            "automatic_cycle": automatic_cycle,
            "hero": None,
        }
    first = incidents[0] if isinstance(incidents[0], Mapping) else {}
    stage = _safe_enum(first.get("workflow_stage"), default="DETECTED")
    highlight = first.get("watch_highlight")
    hero = dict(first) if stage in QUALIFYING_STAGES and isinstance(highlight, Mapping) else None
    return {
        "source_label": WATCH_SOURCE_LABEL,
        "durable_stage": stage,
        "stage_label": str(
            first.get("workflow_label", workflow_label(first.get("workflow_stage")))
        ),
        "next_safe_action": str(
            first.get(
                "next_safe_action",
                "Review authoritative evidence; Relay performs no production action.",
            )
        ),
        "automatic_cycle": automatic_cycle,
        # A result hero is evidence-led: no highlight, no judge-facing result.
        # NEEDS_REVIEW remains a strong autonomous outcome when the complete
        # deterministic/semantic report evidence exists.
        "hero": hero,
    }


def sse_frame(event: WatchEvent, *, retry_milliseconds: int | None = None) -> str:
    """Return one RFC-compatible SSE frame using compact canonical JSON."""

    retry = f"retry: {retry_milliseconds}\n" if retry_milliseconds is not None else ""
    encoded = json.dumps(event.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{retry}event: {event.name}\ndata: {encoded}\n\n"


def heartbeat_event(*, checked_at: datetime | None = None) -> WatchEvent:
    now = checked_at or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    return WatchEvent(
        name="heartbeat",
        payload={"schema_version": "watch-heartbeat.v1", "checked_at": now.isoformat()},
    )


def upstream_unavailable_event() -> WatchEvent:
    """Produce a generic failure event without leaking upstream internals."""

    return WatchEvent(
        name="upstream_unavailable",
        payload={
            "schema_version": "watch-upstream-unavailable.v1",
            "message": "Authoritative review data is temporarily unavailable.",
            "retry_after_seconds": 2,
        },
    )


class WatchEventTracker:
    """Deduplicate durable incident transitions for one SSE connection."""

    def __init__(self) -> None:
        self._previous: dict[str, dict[str, object]] | None = None
        self._previous_automation: dict[str, object] | None = None

    @staticmethod
    def _by_id(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
        rows = snapshot.get("incidents")
        if not isinstance(rows, list):
            return {}
        return {
            str(row["incident_id"]): dict(row)
            for row in rows
            if isinstance(row, Mapping) and isinstance(row.get("incident_id"), str)
        }

    @staticmethod
    def _qualifies(row: Mapping[str, object]) -> bool:
        stage = row.get("workflow_stage")
        return (
            isinstance(stage, str)
            and stage in QUALIFYING_STAGES
            or row.get("blocking_reason") is not None
        )

    @staticmethod
    def _incident_payload(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "incident": {
                key: row[key]
                for key in (
                    "incident_id",
                    "workflow_stage",
                    "workflow_label",
                    "review_state",
                    "blocking_reason",
                    "next_safe_action",
                    "watch_highlight",
                )
                if key in row
            }
        }

    def observe(self, snapshot: Mapping[str, object]) -> tuple[WatchEvent, ...]:
        """Observe one sanitized snapshot and report only new durable facts."""

        current = self._by_id(snapshot)
        raw_automation = snapshot.get("automation")
        current_automation = dict(raw_automation) if isinstance(raw_automation, Mapping) else {}
        if self._previous is None:
            self._previous = current
            self._previous_automation = current_automation
            return (
                WatchEvent(
                    name="snapshot",
                    payload={"initial": True, "snapshot": dict(snapshot)},
                ),
            )

        events: list[WatchEvent] = []
        if current_automation != self._previous_automation:
            events.append(WatchEvent("automation_cycle", {"automation": current_automation}))
        for incident_id in sorted(current):
            row = current[incident_id]
            prior = self._previous.get(incident_id)
            if prior is None:
                events.append(WatchEvent("incident_detected", self._incident_payload(row)))
                if self._qualifies(row):
                    events.append(WatchEvent("review_required", self._incident_payload(row)))
                continue

            stage_changed = prior.get("workflow_stage") != row.get("workflow_stage")
            became_qualifying = not self._qualifies(prior) and self._qualifies(row)
            qualifying_changed = self._qualifies(row) and (
                prior.get("workflow_stage") != row.get("workflow_stage")
                or prior.get("blocking_reason") != row.get("blocking_reason")
            )
            if stage_changed:
                events.append(WatchEvent("stage_changed", self._incident_payload(row)))
                if row.get("workflow_stage") == IncidentWorkflowStage.REPORT_READY.value:
                    events.append(WatchEvent("report_ready", self._incident_payload(row)))
            if became_qualifying or qualifying_changed:
                events.append(WatchEvent("review_required", self._incident_payload(row)))
        self._previous = current
        self._previous_automation = current_automation
        # The browser must refresh its compact incident summary before it
        # announces a newly durable transition.  Individual transition events
        # intentionally contain only the small alert payload, so they cannot
        # otherwise update the visible row, stage, and next-safe-action values.
        # This is still a sanitized snapshot and it is emitted only when the
        # durable state actually changed; the initial marker remains true only
        # for a new SSE connection, which prevents historical snapshots from
        # becoming alerts.
        if events:
            events.insert(
                0,
                WatchEvent(
                    name="snapshot",
                    payload={"initial": False, "snapshot": dict(snapshot)},
                ),
            )
        return tuple(events)
