from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

from relay_bridge.journal import ObservationJournal
from relay_bridge.observation_builder import build_observation


def test_observation_and_outbox_are_committed_together(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "observations.sqlite3")
    payload = build_observation(
        site_id="site",
        bridge_id="bridge",
        queue_name="queue",
        sequence=1,
        queue_snapshot={
            "observed_at": "2026-08-28T17:00:00+00:00",
            "jobs": [],
            "printer": {
                "printer_state": "idle",
                "printer_state_reasons": [],
                "printer_accepting_jobs": True,
            },
        },
        previous_sha256=None,
    )

    assert journal.append(1, payload["observation_id"], payload) is True
    pending = journal.pending_outbox()
    assert len(pending) == 1
    assert pending[0]["message_id"] == payload["observation_id"]
    assert pending[0]["payload"] == payload
    assert pending[0]["attempts"] == 0

    assert journal.mark_outbox_published(payload["observation_id"]) is True
    assert journal.mark_outbox_published(payload["observation_id"]) is False
    assert journal.pending_outbox() == ()
    journal.close()
