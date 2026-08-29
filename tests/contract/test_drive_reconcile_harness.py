from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "gcp" / "reconcile_live_drive.ps1"


def test_drive_reconcile_harness_is_narrow_audited_and_cups_free() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "-ExecuteDriveRead" in script
    assert "/internal/drive-reconcile" in script
    assert "cloud-gate0-drive-reconcile.v1" in script
    assert "INTERNAL_SCHEDULER_PRINCIPAL_EMAIL" in script
    assert "roles/iam.serviceAccountTokenCreator" in script
    assert "Test-TokenCreatorGrantPresent" in script
    assert "Invoke-GcloudQuietly" in script
    assert "Temporary token impersonation authority remains." in script
    assert "work\\live-closure\\drive-" in script
    for forbidden in (
        "cups",
        " lp ",
        "cancel",
        "hold",
        "release",
        "restart",
        "print-job",
        "api_key",
        "service-account-key",
    ):
        assert forbidden not in script.casefold()
