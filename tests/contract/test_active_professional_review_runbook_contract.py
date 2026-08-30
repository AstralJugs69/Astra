from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _backend() -> ModuleType:
    path = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
    spec = importlib.util.spec_from_file_location("active_review_backend", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_review_timing_command_is_allowlisted_validated_and_restorable(
    tmp_path: Path,
) -> None:
    runner = (ROOT / "infra" / "wsl" / "run_active_professional_review_demo.sh").read_text(
        encoding="utf-8"
    )
    backend = _backend()
    values = re.findall(r"RELAY_PAGE_DELAY_SECONDS=([0-9]+(?:\.[0-9]+)?)", runner)

    assert values == ["60"]
    assert "page_delay_seconds=" not in runner
    delay = float(values[0])
    assert backend.MIN_PAGE_DELAY_SECONDS <= delay <= backend.MAX_PAGE_DELAY_SECONDS
    configuration = tmp_path / "relay-capture.conf"
    configuration.write_text(f"RELAY_PAGE_DELAY_SECONDS={values[0]}\n", encoding="utf-8")
    assert backend.load_page_delay(configuration, require_root_owner=False) == delay
    assert (
        "sudo /usr/lib/cups/backend/relay-capture --validate-runtime-config $CAPTURE_CONFIG"
        in runner
    )
    assert "sudo cp -p $CAPTURE_CONFIG $CAPTURE_BACKUP" in runner
    assert "sudo install -o root -g lp -m 0640 $CAPTURE_BACKUP $CAPTURE_CONFIG" in runner
    assert "sudo rm -f $CAPTURE_BACKUP" in runner
    assert "elif sudo cmp -s config/cups/relay-capture.conf $CAPTURE_CONFIG; then" in runner
    assert "active-review timing backup is missing" in runner


def test_active_review_runbook_labels_host_paths_and_uses_repository_runtimes() -> None:
    runner = (ROOT / "infra" / "wsl" / "run_active_professional_review_demo.sh").read_text(
        encoding="utf-8"
    )

    assert 'WSL_REPO_ROOT="/mnt/c/dev/Astra"' in runner
    assert r"WINDOWS_REPO_ROOT='C:\dev\Astra'" in runner
    assert runner.count("WSL Ubuntu-24.04:") >= 5
    assert runner.count("Windows PowerShell (5.1 or 7):") >= 5
    assert "Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'" in runner
    assert "Set-Location -LiteralPath '/mnt/c/dev/Astra'" not in runner
    assert "cd $WSL_REPO_ROOT" in runner
    assert "cd $ROOT" not in runner
    assert "Prefer PowerShell 7 when it is installed" in runner
    assert "  pwsh.exe -NoProfile" not in runner
    assert "uv run braille-relay" in runner
    assert "uv run python -m braille_errata_relay.presentation.app" in runner


def test_active_review_runbook_names_every_local_authority_and_has_no_ambient_fallback() -> None:
    runner = (ROOT / "infra" / "wsl" / "run_active_professional_review_demo.sh").read_text(
        encoding="utf-8"
    )
    local_floor = (ROOT / "infra" / "wsl" / "run_gate0_local_floor.sh").read_text(encoding="utf-8")
    local_floor_docs = (ROOT / "infra" / "wsl" / "README.md").read_text(encoding="utf-8")
    endpoint_helper = (ROOT / "infra" / "gcp" / "confirm_local_endpoint_receipt.ps1").read_text(
        encoding="utf-8"
    )

    assert 'OPERATOR="relay-operator"' in runner
    assert 'OBSERVER="relay-observer"' in runner
    assert 'ENDPOINT_AUDITOR="relay-endpoint-auditor"' in runner
    assert "sudo -u $OPERATOR -- lp" in runner
    assert "sudo -u $OPERATOR -- cancel" in runner
    assert "sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3" in runner
    assert "--user '$OBSERVER'" in runner
    assert 'CANONICAL_BRIDGE_WORK_ROOT="work/live-bridge"' in runner
    assert 'CANONICAL_BRIDGE_JOURNAL="$CANONICAL_BRIDGE_WORK_ROOT/journal.sqlite3"' in runner
    assert "pending-outbox --journal $CANONICAL_BRIDGE_JOURNAL" in runner
    assert "--journal work/active-review/journal.sqlite3" not in runner
    assert "PUBLISH-INITIAL-OBSERVATION" in runner
    assert "PUBLISH-LATER-OBSERVATION" in runner
    assert "active-review-job-$NEW_JOB_ID-initial-observation.json" in runner
    assert "active-review-job-$NEW_JOB_ID-later-observation.json" in runner
    assert "--user relay-endpoint-auditor --exec" in endpoint_helper
    assert "wsl.exe -d Ubuntu-24.04" in endpoint_helper
    assert "sudo -iu" not in runner
    assert "sudo -iu" not in local_floor
    assert 'sudo -u "$OPERATOR"' in local_floor
    assert 'sudo -u "$OBSERVER" -- python3' in local_floor
    assert 'sudo -u "$ENDPOINT_AUDITOR"' in local_floor
    assert "sudo -iu" not in local_floor_docs
    assert "sudo -u relay-operator -- lp" in local_floor_docs
    assert "sudo -u relay-observer -- python3" in local_floor_docs
    assert "sudo -u relay-endpoint-auditor -- python3" in local_floor_docs


def test_active_review_runbook_has_service_account_scoped_iam_cleanup() -> None:
    runner = (ROOT / "infra" / "wsl" / "run_active_professional_review_demo.sh").read_text(
        encoding="utf-8"
    )

    assert "--human-principal HUMAN_USER_EMAIL" in runner
    assert "roles/iam.serviceAccountTokenCreator" in runner
    assert runner.count("gcloud iam service-accounts add-iam-policy-binding") == 4
    assert runner.count("gcloud iam service-accounts remove-iam-policy-binding") == 4
    assert runner.count("\n  finally {") == 4
    assert "Temporary Token Creator grant remains." in runner
    assert "gcloud projects add-iam-policy-binding" not in runner
    assert "RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT" in runner
    assert runner.count(r"\$targetPrincipal = '$TELEMETRY_IDENTITY'") == 2
    assert runner.count(r"\$targetPrincipal = '$DEMONSTRATOR_IDENTITY'") == 2
    assert runner.count("Refusing to reuse a pre-existing Token Creator grant.") == 4
    assert runner.count("Temporary Token Creator cleanup failed.") == 4
    assert "--impersonate-service-account \\$targetPrincipal" in runner
    assert runner.count("function Wait-ForRelayToken") == 4
    assert runner.count("Start-Sleep -Seconds 5") == 4
    assert (
        runner.count(
            "Local user ADC did not gain the temporary Token Creator permission before the bounded wait expired."
        )
        == 4
    )
    assert r"\$_.role" not in runner
    assert runner.count(r"\$PSItem.role") == 8
