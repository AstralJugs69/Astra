from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "local_bridge" / "src"))

import relay_bridge.main as bridge_module
from relay_bridge.cups_observer import CupsRequiredJobNotFound
from relay_bridge.journal import ObservationJournal
from relay_bridge.main import DemoArmAlreadyRunning, observe_loop, observe_once, write_json_atomic
from relay_bridge.main import main as bridge_main


def test_password_stdin_is_consumed_once_and_never_reprompted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[object] = []
    fake_cups = types.SimpleNamespace(
        setUser=lambda username: None,
        setPasswordCB=lambda callback: callbacks.append(callback),
    )
    monkeypatch.setitem(sys.modules, "cups", fake_cups)
    monkeypatch.setattr(sys, "stdin", io.StringIO("one-secret-line\n"))

    bridge_module._configure_cups_identity("relay-observer", password_stdin=True)

    assert len(callbacks) == 1
    callback = callbacks[0]
    assert callable(callback)
    assert callback("Password:") == "one-secret-line"
    assert callback("Password:") == ""


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
    monkeypatch.setattr(
        bridge_module,
        "_configure_cups_identity",
        lambda _username, **_kwargs: None,
    )

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


def test_observe_loop_keeps_a_single_canonical_chain_and_reports_completion(
    tmp_path: Path,
) -> None:
    journal = ObservationJournal(tmp_path / "journal.sqlite3")
    status_path = tmp_path / "observer-status.json"
    elapsed = [0.0]
    sleeps: list[float] = []

    def monotonic_clock() -> float:
        return elapsed[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        elapsed[0] += seconds

    try:
        assert (
            observe_loop(
                observer=FakeObserver(),
                journal=journal,
                site_id="demo-site",
                bridge_id="single-pc-bridge",
                queue_name="Braille-Embosser-Sim",
                required_job_id=42,
                interval_seconds=5.0,
                max_runtime_seconds=30.0,
                status_path=status_path,
                monotonic_clock=monotonic_clock,
                sleep=sleep,
            )
            == 0
        )
        pending = journal.pending_outbox()
    finally:
        journal.close()

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert len(pending) == 6
    assert sleeps == [5.0] * 6
    assert status == {
        "schema_version": "demo-observer-status.v1",
        "status": "COMPLETED",
        "required_scheduler_job_id": 42,
        "last_observation_id": pending[-1]["observation_id"],
        "last_sequence": 6,
        "last_observed_at": "2026-08-29T12:00:00+00:00",
    }
    assert not status_path.with_suffix(".json.lock").exists()
    assert [entry["payload"]["sequence"] for entry in pending] == list(range(1, 7))
    assert all(entry["payload"]["observations"][0]["scheduler_job_id"] == 42 for entry in pending)


def test_observe_loop_blocks_without_substituting_a_missing_exact_job(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "journal.sqlite3")
    status_path = tmp_path / "observer-status.json"
    try:
        result = observe_loop(
            observer=MissingRequiredJobObserver(),
            journal=journal,
            site_id="demo-site",
            bridge_id="single-pc-bridge",
            queue_name="Braille-Embosser-Sim",
            required_job_id=99,
            interval_seconds=5.0,
            max_runtime_seconds=30.0,
            status_path=status_path,
        )
        pending = journal.pending_outbox()
    finally:
        journal.close()

    assert result == 3
    assert pending == ()
    assert json.loads(status_path.read_text(encoding="utf-8")) == {
        "schema_version": "demo-observer-status.v1",
        "status": "BLOCKED",
        "blocking_reason": "MISSING_LINEAGE",
        "required_scheduler_job_id": 99,
    }
    assert not status_path.with_suffix(".json.lock").exists()


def test_observe_loop_refuses_a_parallel_session(tmp_path: Path) -> None:
    journal = ObservationJournal(tmp_path / "journal.sqlite3")
    status_path = tmp_path / "observer-status.json"
    lock_path = status_path.with_suffix(".json.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("other-process\n", encoding="ascii")
    try:
        with pytest.raises(DemoArmAlreadyRunning):
            observe_loop(
                observer=FakeObserver(),
                journal=journal,
                site_id="demo-site",
                bridge_id="single-pc-bridge",
                queue_name="Braille-Embosser-Sim",
                required_job_id=42,
                interval_seconds=5.0,
                max_runtime_seconds=30.0,
                status_path=status_path,
            )
        assert journal.pending_outbox() == ()
    finally:
        journal.close()
