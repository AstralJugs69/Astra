from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_setup_script_repairs_partial_state_without_granting_observer_spool_access() -> None:
    script = (ROOT / "infra" / "wsl" / "setup_cups_gate0.sh").read_text(encoding="utf-8")

    assert 'QUEUE="Braille-Embosser-Sim"' in script
    assert 'DEVICE_URI="relay-capture://demo-embosser"' in script
    assert 'remove_from_group "$OBSERVER" "$CUPS_GROUP"' in script
    assert 'remove_from_group "$OBSERVER" "$AUDIT_GROUP"' in script
    assert 'remove_from_group "$OPERATOR" "$CUPS_GROUP"' in script
    assert 'usermod -a -G "$AUDIT_GROUP" "$OPERATOR"' in script
    assert "printer-op-policy=relay-observer" in script
    assert 'cupsd -t -c "$CANDIDATE_CONF"' in script
    assert "ROLLBACK_ARMED=true" in script
    assert 'restore_file "$PRINTERS_CONF" "printers.conf"' in script
    assert "assert_observer_isolated" in script
    assert "activate_cups_configuration" in script
    assert "if systemctl reload cups; then" in script
    assert (
        'systemctl restart cups || fail "could not restart CUPS after configuration validation"'
        in script
    )
    assert "activate_cups_configuration\nassert_installed_state" in script
    assert "usermod -a -G lp relay-observer" not in script


def test_observer_filesystem_verifier_covers_all_capture_evidence() -> None:
    verifier = (ROOT / "infra" / "wsl" / "verify_observer_filesystem_access.sh").read_text(
        encoding="utf-8"
    )

    for label in (
        "captured input BRF",
        "captured output BRF",
        "capture journal",
        "capture manifest",
        "CUPS spool",
    ):
        assert label in verifier


def test_cups_policy_allows_system_setup_reads_but_not_observer_mutations() -> None:
    policy = (ROOT / "config" / "cups" / "relay-observer-policy.conf").read_text(encoding="utf-8")
    mutation_start = policy.index("<Limit Create-Job")
    mutation = policy[mutation_start : policy.index("</Limit>", mutation_start)]

    assert "Require user relay-observer relay-operator @SYSTEM" in policy
    assert "Require user relay-operator @SYSTEM" in mutation
    assert "relay-observer" not in mutation


def test_setup_installs_root_controlled_slow_capture_timing() -> None:
    script = (ROOT / "infra" / "wsl" / "setup_cups_gate0.sh").read_text(encoding="utf-8")
    timing_config = (ROOT / "config" / "cups" / "relay-capture.conf").read_text(encoding="utf-8")

    assert 'CAPTURE_CONFIG_DEST="/etc/cups/relay-capture.conf"' in script
    assert 'backup_file "$CAPTURE_CONFIG_DEST" "relay-capture.conf"' in script
    assert (
        'install -o root -g "$CUPS_GROUP" -m 0640 "$CAPTURE_CONFIG" "$CAPTURE_CONFIG_DEST"'
        in script
    )
    assert "root:$CUPS_GROUP:640" in script
    assert "RELAY_PAGE_DELAY_SECONDS=5.0" in timing_config
    assert '"$(stat -c \'%U:%G:%a\' "$CAPTURE_ROOT")" == "lp:$AUDIT_GROUP:2750"' in script


def test_setup_validates_capture_timing_before_installation() -> None:
    script = (ROOT / "infra" / "wsl" / "setup_cups_gate0.sh").read_text(encoding="utf-8")

    assert "validate_capture_timing_config" in script
    assert 'python3 "$BACKEND" --validate-runtime-config "$CAPTURE_CONFIG"' in script
    assert "require_command python3" in script


def test_liblouis_wsl_installer_is_pinned_and_verifies_real_translation() -> None:
    installer = (ROOT / "infra" / "wsl" / "setup_liblouis_3_38.sh").read_text(encoding="utf-8")
    environment = (ROOT / "infra" / "wsl" / "liblouis_env.sh").read_text(encoding="utf-8")

    assert 'LIBLOUIS_VERSION="3.38.0"' in installer
    assert 'LIBLOUIS_COMMIT="07c61e58cfb8814f6842c7212063f829288638c1"' in installer
    assert 'git -C "$SOURCE_DIR" rev-parse HEAD' in installer
    assert 'cd "$SOURCE_DIR"\n./autogen.sh' in installer
    assert (
        'install -m 0644 "$SOURCE_DIR/python/README.md" "$BUILD_DIR/python/README.md"' in installer
    )
    assert '--target "$PYTHON_TARGET" "$BUILD_DIR/python"' in installer
    assert '--target "$PYTHON_TARGET" "$SOURCE_DIR/python"' not in installer
    assert "UEB_TABLE_SHA256=" in installer
    assert "BRF_TABLE_SHA256=" in installer
    assert "louis.translateString" in installer
    assert "PYTHONPATH=" in environment
    assert "LIBLOUIS_TABLEPATH=" in environment


def test_cups_runbook_uses_the_verified_wsl_system_bindings() -> None:
    runbook = (ROOT / "infra" / "wsl" / "README.md").read_text(encoding="utf-8")

    assert "python3 infra/wsl/verify_cups_gate0.py" in runbook
    assert "python3 infra/wsl/verify_capture_evidence.py" in runbook
    assert "uv run python infra/wsl/verify_cups_gate0.py" not in runbook
    assert "uv run python infra/wsl/verify_capture_evidence.py" not in runbook


def test_send_document_probe_completes_the_real_pycups_stream() -> None:
    verifier = (ROOT / "infra" / "wsl" / "verify_cups_gate0.py").read_text(encoding="utf-8")

    start = verifier.index("def _send_document_denial_probe")
    end = verifier.index("\n\ndef main", start)
    probe = verifier[start:end]

    assert "connection.startDocument(" in probe
    assert "connection.writeRequestData(" in probe
    assert "connection.finishDocument(" in probe
    assert "cups.IPP_NOT_AUTHENTICATED" in probe
    assert probe.index("connection.startDocument(") < probe.index("connection.writeRequestData(")
    assert probe.index("connection.writeRequestData(") < probe.index("connection.finishDocument(")
