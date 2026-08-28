from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

from relay_bridge.journal import ObservationJournal
from relay_bridge.observation_builder import build_observation, canonical_bytes


def snapshot() -> dict[str, object]:
    observed_at = "2026-08-28T17:00:00+00:00"
    return {
        "queue_name": "Braille-Embosser-Sim",
        "observed_at": observed_at,
        "jobs": [],
        "printer": {
            "printer_state": "idle",
            "printer_state_reasons": [],
            "printer_accepting_jobs": True,
        },
    }


def test_observation_journal_enforces_and_reopens_hash_chain(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "observations.sqlite3")
    first = build_observation(
        site_id="site",
        bridge_id="bridge",
        queue_name="queue",
        sequence=1,
        queue_snapshot=snapshot(),
        previous_sha256=None,
    )
    assert journal.append(1, first["observation_id"], first) is True
    second = build_observation(
        site_id="site",
        bridge_id="bridge",
        queue_name="queue",
        sequence=2,
        queue_snapshot={**snapshot(), "observed_at": "2026-08-28T17:00:03+00:00"},
        previous_sha256=first["observation_id"],
    )
    assert journal.append(2, second["observation_id"], second) is True
    assert journal.verify_chain() == second["observation_id"]
    journal.close()

    reopened = ObservationJournal(tmp_path / "observations.sqlite3")
    assert reopened.verify_chain() == second["observation_id"]
    assert reopened.append(2, second["observation_id"], second) is False
    with pytest.raises(ValueError, match="does not extend"):
        broken_body = {**second, "previous_observation_sha256": None}
        broken_body.pop("observation_id")
        broken_id = hashlib.sha256(canonical_bytes(broken_body)).hexdigest()
        broken = {**broken_body, "observation_id": broken_id}
        reopened.append(3, broken_id, broken)
    reopened.close()


def test_observation_journal_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "observations.sqlite3"
    journal = ObservationJournal(path)
    first = build_observation(
        site_id="site",
        bridge_id="bridge",
        queue_name="queue",
        sequence=1,
        queue_snapshot=snapshot(),
        previous_sha256=None,
    )
    journal.append(1, first["observation_id"], first)
    journal.connection.execute(
        "UPDATE observations SET payload = ? WHERE sequence = 1",
        ('{"observation_id":"' + "a" * 64 + '"}',),
    )
    journal.connection.commit()
    with pytest.raises(ValueError, match="content hash"):
        journal.verify_chain()
    journal.close()
