"""Sanitized, loopback-only watch-floor event primitives.

The presentation server polls the existing private, read-only incident list on
behalf of a local browser.  This module deliberately knows nothing about
credentials, CUPS, Drive, or human-record mutations.  It reduces a private
incident overview to the small, stable subset the watch floor needs and emits
deduplicated transition facts for Server-Sent Events.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from braille_errata_relay.domain.models import IncidentWorkflowStage

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
    return {
        "incident_id": incident_id,
        "workflow_stage": stage,
        "review_state": state,
        "blocking_reason": blocking_reason,
        "next_safe_action": _next_safe_action(
            stage=stage,
            review_state=state,
            blocking_reason=blocking_reason,
        ),
    }


def sanitize_watch_snapshot(
    payload: Mapping[str, object],
    *,
    observed_at: datetime | None = None,
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
    incidents.sort(key=lambda incident: str(incident["incident_id"]))
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
    }


def watch_summary(snapshot: Mapping[str, object]) -> dict[str, str]:
    """Build only display-safe summary fields for the watch template."""

    raw_incidents = snapshot.get("incidents")
    incidents = raw_incidents if isinstance(raw_incidents, list) else []
    if not incidents:
        return {
            "source_label": WATCH_SOURCE_LABEL,
            "durable_stage": "WATCHING",
            "next_safe_action": "No incident is awaiting review. Continue watching authoritative source.",
            "last_successful_check": str(snapshot.get("checked_at", "Unavailable")),
        }
    first = incidents[0] if isinstance(incidents[0], Mapping) else {}
    return {
        "source_label": WATCH_SOURCE_LABEL,
        "durable_stage": _safe_enum(first.get("workflow_stage"), default="DETECTED"),
        "next_safe_action": str(
            first.get(
                "next_safe_action",
                "Review authoritative evidence; Relay performs no production action.",
            )
        ),
        "last_successful_check": str(snapshot.get("checked_at", "Unavailable")),
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
                    "review_state",
                    "blocking_reason",
                    "next_safe_action",
                )
                if key in row
            }
        }

    def observe(self, snapshot: Mapping[str, object]) -> tuple[WatchEvent, ...]:
        """Observe one sanitized snapshot and report only new durable facts."""

        current = self._by_id(snapshot)
        if self._previous is None:
            self._previous = current
            return (
                WatchEvent(
                    name="snapshot",
                    payload={"initial": True, "snapshot": dict(snapshot)},
                ),
            )

        events: list[WatchEvent] = []
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
        return tuple(events)
