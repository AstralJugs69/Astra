"""Read-only CUPS observation adapter with deterministic attribute normalization.

The bridge exposes only IPP Get operations.  Raw pycups dictionaries never cross
the bridge boundary: every value is reduced to the versioned site-observation
wire contract, and unknown values remain explicitly unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class CupsObserverUnavailable(RuntimeError):
    pass


_JOB_STATE_NAMES = {
    3: "PENDING",
    4: "PENDING_HELD",
    5: "PROCESSING",
    6: "PROCESSING_STOPPED",
    7: "CANCELED",
    8: "ABORTED",
    9: "COMPLETED",
}
_PRINTER_STATE_NAMES = {3: "idle", 4: "processing", 5: "stopped"}
_REQUESTED_JOB_ATTRIBUTES = [
    "job-originating-user-name",
    "job-name",
    "job-printer-uri",
    "printer-name",
    "job-state",
    "job-state-reasons",
    "time-at-creation",
    "time-at-processing",
    "time-at-completed",
    "job-impressions",
    "job-impressions-completed",
]


def _as_text(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    result = str(value).strip()
    return result or default


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _as_reasons(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (tuple, list, set, frozenset)):
        values = list(value)
    else:
        values = [value]
    normalized = {_as_text(item, "") for item in values}
    normalized.discard("")
    return sorted(normalized)


def _as_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _parse_server(server: str) -> tuple[str, int]:
    value = server.strip()
    if not value:
        raise ValueError("CUPS server must not be empty")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            raise ValueError("CUPS IPv6 server is missing its closing bracket")
        host = value[1:closing]
        port_text = value[closing + 1 :]
        port_text = port_text.removeprefix(":")
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator:
            host, port_text = value, "631"
    if not host or not port_text.isdigit():
        raise ValueError("CUPS server must be host[:port]")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("CUPS port must be between 1 and 65535")
    return host, port


def _normalize_job_state(value: Any) -> str:
    try:
        return _JOB_STATE_NAMES.get(int(value), "UNKNOWN")
    except (TypeError, ValueError):
        return "UNKNOWN"


def _normalize_printer_state(value: Any) -> str:
    try:
        return _PRINTER_STATE_NAMES.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _normalize_destination(attributes: dict[str, Any]) -> str:
    value = attributes.get("printer-name")
    if value is None:
        value = attributes.get("job-printer-uri")
    destination = _as_text(value, "")
    if "/printers/" in destination:
        destination = destination.rsplit("/printers/", 1)[1].split("/", 1)[0]
    if not destination:
        raise ValueError("CUPS job destination is missing")
    return destination


def normalize_job_attributes(
    scheduler_job_id: int,
    attributes: dict[str, Any],
    *,
    queue_name: str,
    observed_at: str,
) -> dict[str, object]:
    if scheduler_job_id <= 0:
        raise ValueError("scheduler job ID must be positive")
    return {
        "scheduler_job_id": scheduler_job_id,
        "owner": _as_text(
            attributes.get("job-originating-user-name") or attributes.get("job-owner"),
            "unknown",
        ),
        "title": _as_text(attributes.get("job-name"), "unknown"),
        "destination": _normalize_destination(attributes),
        "state": _normalize_job_state(attributes.get("job-state")),
        "state_reasons": _as_reasons(attributes.get("job-state-reasons")),
        "observed_at": observed_at,
        "job_created_at": _as_timestamp(attributes.get("time-at-creation")),
        "processing_at": _as_timestamp(attributes.get("time-at-processing")),
        "completed_at": _as_timestamp(attributes.get("time-at-completed")),
        "impressions_completed": _as_int(
            attributes.get("job-impressions-completed", attributes.get("job-impressions"))
        ),
    }


def normalize_printer_attributes(attributes: dict[str, Any]) -> dict[str, object]:
    return {
        "printer_state": _normalize_printer_state(attributes.get("printer-state")),
        "printer_state_reasons": _as_reasons(attributes.get("printer-state-reasons")),
        "printer_accepting_jobs": _as_bool(attributes.get("printer-is-accepting-jobs")),
    }


class ReadOnlyCupsObserver:
    """Use only pycups Get operations for one configured queue."""

    def __init__(self, *, server: str = "localhost:631", queue_name: str) -> None:
        try:
            import cups  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CupsObserverUnavailable("pycups is required in the WSL bridge") from exc
        host, port = _parse_server(server)
        self._connection = cups.Connection(host=host, port=port)
        self._queue_name = queue_name

    def queue_snapshot(self) -> dict[str, Any]:
        observed_at = datetime.now(UTC).isoformat()
        try:
            jobs = self._connection.getJobs(
                which_jobs="all",
                my_jobs=False,
                requested_attributes=_REQUESTED_JOB_ATTRIBUTES,
            )
        except TypeError:
            jobs = self._connection.getJobs(which_jobs="all", my_jobs=False)
        normalized_jobs = [
            normalize_job_attributes(
                int(job_id),
                dict(attributes),
                queue_name=self._queue_name,
                observed_at=observed_at,
            )
            for job_id, attributes in sorted((jobs or {}).items(), key=lambda item: int(item[0]))
            if self._is_configured_queue(dict(attributes))
        ]
        printer = self._connection.getPrinterAttributes(
            self._queue_name,
            requested_attributes=[
                "printer-state",
                "printer-state-reasons",
                "printer-is-accepting-jobs",
            ],
        )
        return {
            "queue_name": self._queue_name,
            "observed_at": observed_at,
            "jobs": normalized_jobs,
            "printer": normalize_printer_attributes(dict(printer)),
        }

    def job_snapshot(self, scheduler_job_id: int) -> dict[str, object]:
        if scheduler_job_id <= 0:
            raise ValueError("scheduler job ID must be positive")
        observed_at = datetime.now(UTC).isoformat()
        attributes = dict(self._connection.getJobAttributes(scheduler_job_id))
        normalized = normalize_job_attributes(
            scheduler_job_id,
            attributes,
            queue_name=self._queue_name,
            observed_at=observed_at,
        )
        if normalized["destination"] != self._queue_name:
            raise ValueError("CUPS job destination does not match configured queue")
        return normalized

    def _is_configured_queue(self, attributes: dict[str, Any]) -> bool:
        destination = attributes.get("printer-name") or attributes.get("job-printer-uri")
        if destination is None:
            return False
        try:
            normalized = _normalize_destination(attributes)
        except ValueError:
            return False
        return normalized == self._queue_name
