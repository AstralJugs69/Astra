from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_PATH = ROOT / "infra" / "scripts" / "preflight.py"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("preflight_container_contract", PREFLIGHT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_container_comparison_requires_exact_bytes_and_does_not_build(monkeypatch) -> None:
    preflight = _load_preflight()
    profile = preflight.load_translation_profile(
        ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
    )
    local_outputs = {"v1": b"v1-local-bytes", "v2": b"v2-local-bytes"}
    expected_hashes = {
        version: hashlib.sha256(value).hexdigest() for version, value in local_outputs.items()
    }
    payload = {
        "profile_id": profile.profile_id,
        "profile_sha256": preflight.profile_sha256(profile),
        "table_hashes": preflight._table_identity(profile),
        "brf_b64": {
            version: base64.b64encode(value).decode("ascii")
            for version, value in local_outputs.items()
        },
    }
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: int):
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return preflight.CommandResult(
                True,
                "sha256:" + "a" * 64,
                "sha256:" + "a" * 64,
            )
        assert command[:2] == ["docker", "run"]
        return preflight.CommandResult(True, json.dumps(payload), "")

    monkeypatch.setattr(preflight, "_command", lambda _name: "docker")
    monkeypatch.setattr(preflight, "_render_local_goldens", lambda: (profile, local_outputs))
    monkeypatch.setattr(preflight, "_expected_golden_identity", lambda _profile: expected_hashes)
    monkeypatch.setattr(preflight, "_run", fake_run)

    result, ready = preflight._container_brf_comparison()

    assert ready is True
    assert result["status"] == "PASS"
    assert result["byte_comparison"] == "exact"
    run_command = commands[-1]
    assert "build" not in run_command
    assert run_command[run_command.index("--network") + 1] == "none"
    assert "--read-only" in run_command
    assert "--mount" in run_command


def test_container_comparison_blocks_when_named_image_is_missing(monkeypatch) -> None:
    preflight = _load_preflight()

    monkeypatch.setattr(preflight, "_command", lambda _name: "docker")
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda _command, *, timeout: preflight.CommandResult(False, "", "image not found"),
    )

    result, ready = preflight._container_brf_comparison()

    assert ready is False
    assert result["status"] == "BLOCKED"
    assert "without a build" in result["detail"]


def _cups_evidence() -> dict[str, object]:
    completed_hash = "a" * 64
    terminated_hash = "b" * 64
    check_names = {
        "operator_terminal_submission",
        "full_capture_exact_byte_passthrough",
        "operator_hold_release_cancel",
        "terminated_capture_journal",
        "observer_authorization_denials",
        "observer_filesystem_isolation",
    }
    return {
        "schema_version": "gate0-local-floor-evidence.v1",
        "recorded_at": "2026-08-29T00:00:00Z",
        "queue": "Braille-Embosser-Sim",
        "simulated_endpoint": True,
        "fixture": "demo/expected/v1.brf",
        "full_capture": {
            "state": "COMPLETED",
            "candidate_sha256": completed_hash,
            "backend_received_sha256": completed_hash,
            "captured_output_sha256": completed_hash,
            "terminal_event_sha256": "c" * 64,
            "pages_total": 1,
            "pages_completed": 1,
            "manifest_schema_valid": True,
            "event_chain_valid": True,
        },
        "terminated_capture": {
            "state": "TERMINATED",
            "candidate_sha256": terminated_hash,
            "backend_received_sha256": terminated_hash,
            "captured_output_sha256": None,
            "terminal_event_sha256": "d" * 64,
            "pages_total": 12,
            "pages_completed": 1,
            "manifest_schema_valid": True,
            "event_chain_valid": True,
        },
        "checks": {name: "PASS" for name in check_names},
    }


def test_cups_preflight_passes_only_from_complete_sanitized_evidence(tmp_path: Path) -> None:
    preflight = _load_preflight()
    evidence = tmp_path / "gate0.json"
    evidence.write_text(json.dumps(_cups_evidence()), encoding="utf-8")

    result, ready = preflight._cups_floor_check(evidence)

    assert ready is True
    assert result["status"] == "PASS"
    assert "job" not in json.dumps(result).lower()


def test_cups_preflight_fails_closed_on_inconsistent_capture_hash(tmp_path: Path) -> None:
    preflight = _load_preflight()
    payload = _cups_evidence()
    full_capture = payload["full_capture"]
    assert isinstance(full_capture, dict)
    full_capture["captured_output_sha256"] = "e" * 64
    evidence = tmp_path / "gate0.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result, ready = preflight._cups_floor_check(evidence)

    assert ready is False
    assert result["status"] == "BLOCKED"
