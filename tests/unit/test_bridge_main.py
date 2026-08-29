from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

import relay_bridge.main as bridge_module
from relay_bridge.cups_observer import CupsRequiredJobNotFound
from relay_bridge.journal import ObservationJournal
from relay_bridge.main import main as bridge_main
from relay_bridge.main import observe_once, write_json_atomic


class FakeObserver:
    def queue_snapshot(self, *, required_job_id: int | None = None) -> dict[str, object]:
        if required_job_id not in (None, 42):
            raise ValueError("required job is absent")
        observed_at = "2026-08-29T12:00:00+00:00"
        return {
            "queue_name": "Braille-Embosser-Sim",
            "observed_at": observed_at,
            "jobs": [
                {
                    "scheduler_job_id": 42,
                    "owner": "relay-operator",
                    "title": "BER|WO-DEMO-001|9b13336e1833|BASELINE",
                    "destination": "Braille-Embosser-Sim",
                    "state": "PENDING_HELD",
                    "state_reasons": ["job-hold-until-specified"],
                    "observed_at": observed_at,
                    "job_created_at": observed_at,
                    "processing_at": None,
                    "completed_at": None,
                    "impressions_completed": 0,
                }
            ],
            "printer": {
                "printer_state": "idle",
                "printer_state_reasons": [],
                "printer_accepting_jobs": True,
            },
        }


class MissingRequiredJobObserver(FakeObserver):
    def __init__(self, **_kwargs: object) -> None:
        pass

    def queue_snapshot(self, *, required_job_id: int | None = None) -> dict[str, object]:
        raise CupsRequiredJobNotFound(f"required CUPS job {required_job_id} is unavailable")


def test_observe_once_appends_hash_chain_and_outbox_before_export(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "journal.sqlite3")
    first = observe_once(
        observer=FakeObserver(),
        journal=journal,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
    )
    second = observe_once(
        observer=FakeObserver(),
        journal=journal,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
    )

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert second["previous_observation_sha256"] == first["observation_id"]
    assert [row["observation_id"] for row in journal.pending_outbox()] == [
        first["observation_id"],
        second["observation_id"],
    ]
    journal.close()


def test_observe_once_requires_the_exact_attributable_job(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "journal.sqlite3")

    payload = observe_once(
        observer=FakeObserver(),
        journal=journal,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        required_job_id=42,
    )

    assert payload["observations"][0]["scheduler_job_id"] == 42
    with pytest.raises(ValueError, match="required job is absent"):
        observe_once(
            observer=FakeObserver(),
            journal=journal,
            site_id="demo-site",
            bridge_id="single-pc-bridge",
            queue_name="Braille-Embosser-Sim",
            required_job_id=99,
        )
    pending = journal.pending_outbox()
    assert len(pending) == 1
    assert pending[0]["observation_id"] == payload["observation_id"]
    assert pending[0]["payload"] == payload
    journal.close()


def test_atomic_export_contains_only_complete_canonical_json(tmp_path: Path) -> None:
    destination = tmp_path / "observation.json"
    payload = {"schema_version": "site-observation.v1", "sequence": 1}

    write_json_atomic(destination, payload)

    assert json.loads(destination.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob("*.tmp")) == []


def test_pending_outbox_command_emits_durable_payloads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    journal = ObservationJournal(journal_path)
    payload = observe_once(
        observer=FakeObserver(),
        journal=journal,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        required_job_id=42,
    )
    journal.close()

    assert bridge_main(["pending-outbox", "--journal", str(journal_path)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "bridge-pending-observations.v1"
    assert output["observations"] == [
        {"observation_id": payload["observation_id"], "payload": payload}
    ]


def test_observe_command_reports_missing_lineage_without_queuing_an_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    output_path = tmp_path / "observation.json"
    monkeypatch.setattr(bridge_module, "ReadOnlyCupsObserver", MissingRequiredJobObserver)
    monkeypatch.setattr(bridge_module, "_configure_cups_identity", lambda _username: None)

    exit_code = bridge_main(
        [
            "observe-once",
            "--journal",
            str(journal_path),
            "--output",
            str(output_path),
            "--require-job-id",
            "99",
        ]
    )

    assert exit_code == 3
    assert json.loads(capsys.readouterr().out) == {
        "blocking_reason": "MISSING_LINEAGE",
        "required_scheduler_job_id": 99,
        "status": "BLOCKED",
    }
    assert not output_path.exists()
    journal = ObservationJournal(journal_path)
    try:
        assert journal.pending_outbox() == ()
    finally:
        journal.close()
