from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

from relay_bridge.cups_observer import ReadOnlyCupsObserver


class _FakeConnection:
    calls: ClassVar[list[str]] = []

    def __init__(self, *, server: str) -> None:
        self.server = server
        self.__class__.calls = []

    def getJobs(self, **kwargs: Any) -> dict[int, dict[str, Any]]:
        self.__class__.calls.append("getJobs")
        assert kwargs["which_jobs"] == "all"
        return {
            42: {
                "job-originating-user-name": "relay-operator",
                "job-name": "BER|WO-DEMO-001|abc|BASELINE",
                "printer-name": "Braille-Embosser-Sim",
                "job-state": 5,
                "job-state-reasons": ("processing-to-device", "processing-to-device"),
                "time-at-creation": 1_756_465_140,
                "time-at-processing": 1_756_465_141,
                "job-impressions-completed": 1,
            },
            43: {
                "job-name": "unrelated",
                "printer-name": "other-queue",
                "job-state": 3,
            },
        }

    def getPrinterAttributes(self, queue_name: str, **kwargs: Any) -> dict[str, Any]:
        self.__class__.calls.append("getPrinterAttributes")
        assert queue_name == "Braille-Embosser-Sim"
        assert kwargs["requested_attributes"] == [
            "printer-state",
            "printer-state-reasons",
            "printer-is-accepting-jobs",
        ]
        return {
            "printer-state": 4,
            "printer-state-reasons": ["processing"],
            "printer-is-accepting-jobs": 1,
        }

    def getJobAttributes(self, scheduler_job_id: int) -> dict[str, Any]:
        self.__class__.calls.append("getJobAttributes")
        assert scheduler_job_id == 42
        return {
            "job-originating-user-name": "relay-operator",
            "job-name": "title",
            "printer-name": "Braille-Embosser-Sim",
            "job-state": 9,
        }


def test_observer_emits_only_normalized_get_operation_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cups", types.SimpleNamespace(Connection=_FakeConnection))

    observer = ReadOnlyCupsObserver(
        server="localhost:631",
        queue_name="Braille-Embosser-Sim",
    )
    snapshot = observer.queue_snapshot()

    assert [job["scheduler_job_id"] for job in snapshot["jobs"]] == [42]
    assert snapshot["jobs"][0]["state_reasons"] == ["processing-to-device"]
    assert snapshot["printer"] == {
        "printer_state": "processing",
        "printer_state_reasons": ["processing"],
        "printer_accepting_jobs": True,
    }
    assert "attributes" not in snapshot
    assert "attributes" not in repr(snapshot)
    assert _FakeConnection.calls == ["getJobs", "getPrinterAttributes"]

    job = observer.job_snapshot(42)
    assert job["state"] == "COMPLETED"
    assert _FakeConnection.calls == ["getJobs", "getPrinterAttributes", "getJobAttributes"]


def test_observation_builder_rejects_mixed_observation_timestamps() -> None:
    from relay_bridge.observation_builder import build_observation

    snapshot = {
        "observed_at": "2026-08-28T17:00:00+00:00",
        "jobs": [
            {
                "scheduler_job_id": 42,
                "owner": "owner",
                "title": "title",
                "destination": "queue",
                "state": "PROCESSING",
                "state_reasons": [],
                "observed_at": "2026-08-28T16:59:59+00:00",
                "job_created_at": None,
                "processing_at": None,
                "completed_at": None,
                "impressions_completed": None,
            }
        ],
        "printer": {
            "printer_state": "processing",
            "printer_state_reasons": [],
            "printer_accepting_jobs": True,
        },
    }
    with pytest.raises(ValueError, match="timestamps"):
        build_observation(
            site_id="site",
            bridge_id="bridge",
            queue_name="queue",
            sequence=1,
            queue_snapshot=snapshot,
            previous_sha256=None,
        )
