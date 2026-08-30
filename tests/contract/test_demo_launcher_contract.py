from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_demo_launcher_starts_only_loopback_presentation_and_has_no_control_plane_actions() -> None:
    launcher = (ROOT / "infra" / "demo" / "start_demo.ps1").read_text(encoding="utf-8").lower()

    assert "127.0.0.1" in launcher
    assert "/watch" in launcher
    assert "nobrowser" in launcher
    assert "braille-relay doctor" in launcher
    assert "windowstyle hidden" in launcher
    for forbidden in (
        "gcloud iam",
        "add-iam-policy-binding",
        "drive.files",
        "lpadmin",
        "cancel ",
        "lp ",
        "service-account.json",
        "access_token",
        "register-demo-baseline",
        "professional-dispositions",
    ):
        assert forbidden not in launcher
