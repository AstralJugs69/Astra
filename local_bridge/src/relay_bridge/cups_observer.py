"""CUPS observer with an intentionally read-only public surface."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class CupsObserverUnavailable(RuntimeError):
    pass


class ReadOnlyCupsObserver:
    """Use only pycups Get operations for one configured queue."""

    def __init__(self, *, server: str = "localhost:631", queue_name: str) -> None:
        try:
            import cups  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CupsObserverUnavailable("pycups is required in the WSL bridge") from exc
        self._connection = cups.Connection(server=server)
        self._queue_name = queue_name

    def queue_snapshot(self) -> dict[str, Any]:
        jobs = self._connection.getJobs(which_jobs="all", my_jobs=False)
        return {
            "queue_name": self._queue_name,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "jobs": [
                {
                    "scheduler_job_id": int(job_id),
                    "attributes": dict(attributes),
                }
                for job_id, attributes in jobs.items()
                if self._is_configured_queue(attributes)
            ],
            "printer": dict(
                self._connection.getPrinterAttributes(
                    self._queue_name,
                    requested_attributes=["printer-state", "printer-state-reasons", "printer-is-accepting-jobs"],
                )
            ),
        }

    def job_snapshot(self, scheduler_job_id: int) -> dict[str, Any]:
        return {
            "scheduler_job_id": scheduler_job_id,
            "attributes": dict(self._connection.getJobAttributes(scheduler_job_id)),
        }

    def _is_configured_queue(self, attributes: dict[str, Any]) -> bool:
        destination = attributes.get("printer-name") or attributes.get("job-printer-uri")
        return destination in (None, self._queue_name) or self._queue_name in str(destination)

