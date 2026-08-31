from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "demo" / "arm_simulated_baseline.ps1"


def test_simulated_baseline_arming_is_fixed_to_truthful_local_evidence() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    lowered = script.lower()

    assert "ARM-SIMULATED-BASELINE" in script
    assert "Braille-Embosser-Sim" in script
    assert "relay-capture://demo-embosser" in script
    assert "SIMULATED_DEMO" in script
    assert "physical_embosser = 'NOT_USED'" in script
    assert "link_local_baseline_job.ps1" in script
    assert "confirm_local_endpoint_receipt.ps1" in script
    assert "Get-FileHash" in script
    assert "--user relay-operator" in script
    assert " -o raw " in script

    for forbidden in (
        "google.cloud.firestore",
        "collection('baselines')",
        'collection("baselines")',
        "update-env-vars",
        "lpadmin",
        "cancel ",
    ):
        assert forbidden not in lowered


def test_simulated_baseline_arming_has_non_mutating_validation_mode() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    validate = script.index("if ($ValidateOnly)")
    submission = script.index("wsl.exe -d Ubuntu-24.04 --user relay-operator -- `")
    assert validate < submission
    assert "exit 0" in script[validate:submission]
