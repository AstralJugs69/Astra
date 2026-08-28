"""Record safe Gate 0 diagnostics without pretending an unavailable seam passed."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.readiness import check_liblouis_readiness

PROFILE = ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
EVIDENCE = ROOT / "demo" / "evidence" / "preflight.json"


def _command(name: str) -> str | None:
    return shutil.which(name)


def _probe(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).replace("\x00", "").strip()
    return result.returncode == 0, output[-500:]


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    with part.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(path)


def _liblouis_check() -> tuple[dict[str, object], bool]:
    try:
        profile = load_translation_profile(PROFILE)
    except (OSError, ValueError) as exc:
        return {"status": "BLOCKED", "detail": f"profile invalid: {exc}"}, False
    report = check_liblouis_readiness(profile)
    detail = "; ".join(report.checks) or (report.reason or "no checks recorded")
    if report.reason:
        detail = f"{report.reason}; {detail}"
    return {
        "status": "PASS" if report.ready else "BLOCKED",
        "detail": detail,
    }, report.ready


def collect() -> dict[str, object]:
    python_supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    wsl_ok, wsl_detail = _probe(["wsl.exe", "--status"])
    docker_ok, docker_detail = _probe(["docker", "version", "--format", "{{.Server.Version}}"])
    gcloud = _command("gcloud")
    louis_installed = importlib.util.find_spec("louis") is not None
    cups_tools = all(_command(name) for name in ("lp", "lpstat", "cancel"))
    liblouis_result, liblouis_ready = _liblouis_check()
    raw_and_policy_exercised = False
    return {
        "schema_version": "preflight.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "checks": {
            "python_3_11_or_3_12": {
                "status": "PASS" if python_supported else "BLOCKED",
                "detail": sys.version,
            },
            "adk_gemini_structured_output": {
                "status": "BLOCKED",
                "detail": (
                    "No deployed private Cloud Run smoke test was exercised; local gcloud "
                    "credential/log writes are permission-denied."
                ),
            },
            "liblouis_profile": liblouis_result,
            "cups_raw_passthrough_and_policy": {
                "status": "PASS" if raw_and_policy_exercised else "BLOCKED",
                "detail": (
                    f"WSL={wsl_detail or 'unavailable'}; Docker={docker_detail or 'unavailable'}; "
                    f"native CUPS tools={'present' if cups_tools else 'missing'}; "
                    "exact-byte passthrough and negative authorization tests were not exercised"
                ),
            },
            "drive_same_file_detection": {
                "status": "BLOCKED",
                "detail": (
                    "No DRIVE_FILE_ID is configured and no Workspace Events/change-feed refetch "
                    "was exercised."
                ),
            },
        },
        "safe_diagnostics": {
            "gcloud_binary": gcloud or "missing",
            "louis_binding_installed": louis_installed,
            "liblouis_readiness": liblouis_ready,
            "wsl_status_command_succeeded": wsl_ok,
            "docker_server_reachable": docker_ok,
            "cups_tools_present": cups_tools,
            "secrets_logged": False,
        },
    }


def main() -> int:
    evidence = collect()
    _atomic_write_json(EVIDENCE, evidence)
    checks = evidence["checks"]
    assert isinstance(checks, dict)
    for name, result in checks.items():
        assert isinstance(result, dict)
        print(f"[{result['status']}] {name}: {result['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
