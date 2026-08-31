from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "demo" / "rehearse.ps1"


def test_rehearsal_launcher_has_bounded_modes_and_cleanup() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[ValidateSet('Fixture', 'Live', 'Status', 'Cleanup')]" in text
    assert "[switch]$EnableAutomaticWatch" in text
    assert "Refusing to reuse a pre-existing Token Creator binding." in text
    assert "roles/iam.serviceAccountTokenCreator" in text
    assert "Remove-RecordedIamBinding" in text
    assert "Restore-RecordedSchedulerState" in text
    assert "Stop-RecordedPresentation" in text
    assert "Read-Host" in text


def test_rehearsal_launcher_does_not_gain_production_controls() -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()

    forbidden = (
        "reconcile_live_drive.ps1",
        "cupsdisable",
        "cupsenable",
        "lpmove",
        "lpadmin",
        "lp -d",
        "/professional-dispositions",
        "/operator-attestations",
        "/containment-confirmations",
        "/proof-records",
        "/replacement-observation-links",
    )
    for value in forbidden:
        assert value not in text


def test_rehearsal_launcher_keeps_fixture_visibly_separate() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "braille_errata_relay.presentation.screenshot_fixture" in text
    assert "This mode is visibly synthetic and makes no live-system claim." in text
    assert "http://127.0.0.1:$selectedPort/watch/quiet" in text
