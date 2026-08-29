from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]


def test_human_gate0_runner_is_scoped_to_the_fixed_local_simulator() -> None:
    runner = (ROOT / "infra" / "wsl" / "run_gate0_local_floor.sh").read_text(encoding="utf-8")

    assert 'QUEUE="Braille-Embosser-Sim"' in runner
    assert 'DEVICE_URI="relay-capture://demo-embosser"' in runner
    assert 'CANDIDATE="$ROOT/demo/expected/v1.brf"' in runner
    assert 'INSTALLED_BACKEND="/usr/lib/cups/backend/relay-capture"' in runner
    assert 'sudo -iu "$OPERATOR"' in runner
    assert "verify_capture_evidence.py" in runner
    assert "verify_cups_gate0.py" in runner
    assert "verify_observer_filesystem_access.sh" in runner
    assert "create_open_cups_job.py" in runner
    assert 'EVIDENCE="$ROOT/demo/evidence/gate0-local-floor.json"' in runner
    assert "SUBMIT-LOCAL-TERMINAL" in runner
    assert 'b"\\x0c".join((source,) * 12)' in runner
    assert 'chmod 0711 "$TEMP_ROOT"' in runner
    assert "submit, hold, release, and cancel a slow local test job" in runner
    assert "CLEANUP-LOCAL-AUTH-PROBES" in runner
    assert "cleanup_probe_job" in runner
    assert "already (aborted|canceled|cancelled|completed)" in runner
    assert "schedule_or_accept_terminal" in runner
    assert "wait_for_capture_manifest" in runner
    assert '"--resume-captures"' in runner
    assert "It never infers lineage from queue contents" in runner
    assert 'operator lp -i "$QUEUE-$job_id" -H immediate' in runner
    assert 'grep -Fqi "job is completed and cannot be changed"' in runner
    assert (
        'cmp -s "$ROOT/simulator/cups_backend/relay_capture_backend.py" "$INSTALLED_BACKEND"'
        in runner
    )
    assert "password=" not in runner


def test_gate0_evidence_schema_accepts_only_sanitized_successful_checks() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "gate0-local-floor-evidence.v1.json").read_text(encoding="utf-8")
    )
    valid = {
        "schema_version": "gate0-local-floor-evidence.v1",
        "recorded_at": "2026-08-29T00:00:00+00:00",
        "queue": "Braille-Embosser-Sim",
        "simulated_endpoint": True,
        "fixture": "demo/expected/v1.brf",
        "full_capture": {
            "state": "COMPLETED",
            "candidate_sha256": "a" * 64,
            "backend_received_sha256": "a" * 64,
            "captured_output_sha256": "a" * 64,
            "terminal_event_sha256": "b" * 64,
            "pages_total": 2,
            "pages_completed": 2,
            "manifest_schema_valid": True,
            "event_chain_valid": True,
        },
        "terminated_capture": {
            "state": "TERMINATED",
            "candidate_sha256": "a" * 64,
            "backend_received_sha256": "a" * 64,
            "captured_output_sha256": None,
            "terminal_event_sha256": "c" * 64,
            "pages_total": 2,
            "pages_completed": 1,
            "manifest_schema_valid": True,
            "event_chain_valid": True,
        },
        "checks": {
            "operator_terminal_submission": "PASS",
            "full_capture_exact_byte_passthrough": "PASS",
            "operator_hold_release_cancel": "PASS",
            "terminated_capture_journal": "PASS",
            "observer_authorization_denials": "PASS",
            "observer_filesystem_isolation": "PASS",
        },
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert not list(validator.iter_errors(valid))

    invalid = {**valid, "queue": "some-other-queue"}
    assert list(validator.iter_errors(invalid))
