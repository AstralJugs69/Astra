from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_human_armed_monitor_preserves_the_read_only_observer_and_ack_boundary() -> None:
    source = (ROOT / "infra" / "demo" / "arm_fresh_observation.ps1").read_text(encoding="utf-8")
    lowered = source.lower()

    assert "[switch]$arm" in lowered
    assert "[switch]$publisherworker" in lowered
    assert "observe-loop" in source
    assert "pending-outbox" in source
    assert "acknowledge-published" in source
    assert "publish-site-observation" in source
    assert "-u', $ObserverUser" in source
    assert "$ObserverUser = 'relay-observer'" in source
    assert "if ($publishExit -ne 0 -or $publishResult.status -ne 'ACCEPTED'" in source
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "temporary telemetry authorization" in lowered
    for forbidden in (
        "gcloud iam",
        "scheduler jobs run",
        "scheduler jobs resume",
        "scheduler jobs pause",
        "lpadmin",
        "cupsdisable",
        "cupsenable",
        "service-account.json",
        "access_token",
    ):
        assert forbidden not in lowered


def test_preflight_is_limited_to_doctor_and_read_only_cloud_describes() -> None:
    source = (ROOT / "infra" / "demo" / "test_demo_readiness.ps1").read_text(encoding="utf-8")
    lowered = source.lower()

    assert "braille-relay doctor" in lowered
    assert "--check-drive" in lowered
    assert "--check-wsl-cups" in lowered
    assert "run services describe" in lowered
    assert "scheduler jobs describe" in lowered
    assert "fresh_read_only_observation" in lowered
    for forbidden in (
        "gcloud iam",
        "scheduler jobs run",
        "scheduler jobs resume",
        "scheduler jobs pause",
        "scheduler jobs create",
        "scheduler jobs update",
        "publish-site-observation",
        "invoke-restmethod",
        "invoke-webrequest",
        "lpadmin",
        "service-account.json",
    ):
        assert forbidden not in lowered
