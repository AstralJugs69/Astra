"""Build the exact site-observation.v1 envelope from normalized bridge data."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JOB_FIELDS = (
    "scheduler_job_id",
    "owner",
    "title",
    "destination",
    "state",
    "state_reasons",
    "observed_at",
    "job_created_at",
    "processing_at",
    "completed_at",
    "impressions_completed",
)
_PRINTER_FIELDS = (
    "printer_state",
    "printer_state_reasons",
    "printer_accepting_jobs",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _require_hex_or_none(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 or null")
    return value


def _copy_exact(mapping: dict[str, Any], fields: tuple[str, ...], label: str) -> dict[str, object]:
    if "attributes" in mapping:
        raise ValueError(f"raw CUPS attributes cannot enter {label}")
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise ValueError(f"{label} is missing normalized fields: {', '.join(missing)}")
    return {field: mapping[field] for field in fields}


def _scheduler_id(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("scheduler job ID cannot be boolean")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError as exc:
            raise TypeError("scheduler job ID must be numeric") from exc
    else:
        raise TypeError("scheduler job ID must be numeric")
    if result <= 0:
        raise ValueError("scheduler job ID must be positive")
    return result


def _normalized_jobs(queue_snapshot: dict[str, Any]) -> list[dict[str, object]]:
    jobs = queue_snapshot.get("jobs")
    if not isinstance(jobs, list):
        raise TypeError("queue snapshot jobs must be a normalized list")
    normalized = []
    for job in jobs:
        if not isinstance(job, dict):
            raise TypeError("queue snapshot contains a non-object job")
        normalized.append(_copy_exact(job, _JOB_FIELDS, "job observation"))
    return sorted(normalized, key=lambda item: _scheduler_id(item["scheduler_job_id"]))


def build_observation(
    *,
    site_id: str,
    bridge_id: str,
    queue_name: str,
    sequence: int,
    queue_snapshot: dict[str, Any],
    previous_sha256: str | None,
) -> dict[str, object]:
    if not site_id or not bridge_id or not queue_name:
        raise ValueError("site, bridge, and queue names are required")
    if sequence <= 0:
        raise ValueError("observation sequence must be positive")
    previous = _require_hex_or_none(previous_sha256, "previous_sha256")
    observed_at = queue_snapshot.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("queue snapshot must contain an observed_at timestamp")
    printer = queue_snapshot.get("printer")
    if not isinstance(printer, dict):
        raise TypeError("queue snapshot must contain normalized printer attributes")
    normalized_printer = _copy_exact(printer, _PRINTER_FIELDS, "printer observation")
    normalized_jobs = _normalized_jobs(queue_snapshot)
    if any(job["observed_at"] != observed_at for job in normalized_jobs):
        raise ValueError("job observation timestamps must match the envelope")
    body: dict[str, object] = {
        "schema_version": "site-observation.v1",
        "site_id": site_id,
        "bridge_id": bridge_id,
        "queue_name": queue_name,
        "sequence": sequence,
        "observed_at": observed_at,
        "observations": normalized_jobs,
        "printer_state": normalized_printer["printer_state"],
        "printer_state_reasons": normalized_printer["printer_state_reasons"],
        "printer_accepting_jobs": normalized_printer["printer_accepting_jobs"],
        "previous_observation_sha256": previous,
        "source": "cups_read_only_observer",
    }
    digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return {**body, "observation_id": digest}
