from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

from relay_bridge.cups_observer import (
    normalize_job_attributes,
    normalize_printer_attributes,
)


def test_cups_job_attributes_are_normalized_without_raw_fields() -> None:
    result = normalize_job_attributes(
        42,
        {
            "job-originating-user-name": " operator ",
            "job-name": " BER|INCIDENT|abc|REPLACEMENT ",
            "job-printer-uri": "ipp://localhost/printers/Braille-Embosser-Sim",
            "job-state": 5,
            "job-state-reasons": "processing-to-device,none",
            "time-at-creation": 1798563540,
            "time-at-processing": 1798563600,
            "job-impressions-completed": 3,
        },
        queue_name="Braille-Embosser-Sim",
        observed_at="2026-08-28T17:00:00+00:00",
    )
    assert result == {
        "scheduler_job_id": 42,
        "owner": "operator",
        "title": "BER|INCIDENT|abc|REPLACEMENT",
        "destination": "Braille-Embosser-Sim",
        "state": "PROCESSING",
        "state_reasons": ["none", "processing-to-device"],
        "observed_at": "2026-08-28T17:00:00+00:00",
        "job_created_at": "2026-12-29T16:59:00+00:00",
        "processing_at": "2026-12-29T17:00:00+00:00",
        "completed_at": None,
        "impressions_completed": 3,
    }
    assert "attributes" not in result


def test_cups_printer_attributes_keep_unknown_acceptance_explicit() -> None:
    assert normalize_printer_attributes(
        {
            "printer-state": 5,
            "printer-state-reasons": ["paused", "connecting-to-device"],
            "printer-is-accepting-jobs": "not-a-boolean",
        }
    ) == {
        "printer_state": "stopped",
        "printer_state_reasons": ["connecting-to-device", "paused"],
        "printer_accepting_jobs": None,
    }
