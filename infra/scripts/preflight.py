"""Record safe Gate 0 diagnostics without pretending an unavailable seam passed."""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
EVIDENCE = ROOT / "demo" / "evidence" / "preflight.json"


def _command(name: str) -> str | None:
    found = shutil.which(name)
    return found


def _probe(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).replace("\x00", "").strip()
    return result.returncode == 0, output[-500:]


def collect() -> dict[str, object]:
    try:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile_bound = (
            profile.get("liblouis_version") not in (None, "unresolved")
            and all(table.get("sha256") for table in profile.get("translation_tables", []))
        )
        profile_detail = "profile loaded; table hashes are bound" if profile_bound else "profile loaded; table hashes are unresolved"
    except (OSError, json.JSONDecodeError) as exc:
        profile_bound = False
        profile_detail = str(exc)

    python_supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    wsl_ok, wsl_detail = _probe(["wsl.exe", "--status"])
    docker_ok, docker_detail = _probe(["docker", "version", "--format", "{{.Server.Version}}"])
    gcloud = _command("gcloud")
    louis_installed = importlib.util.find_spec("louis") is not None
    cups_tools = all(_command(name) for name in ("lp", "lpstat", "cancel"))
    return {
        "schema_version": "preflight.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "checks": {
            "python_3_11_or_3_12": {"status": "PASS" if python_supported else "BLOCKED", "detail": sys.version},
            "adk_gemini_structured_output": {
                "status": "BLOCKED",
                "detail": "No deployed private Cloud Run smoke test was exercised; local gcloud credential/log writes are permission-denied.",
            },
            "liblouis_profile": {
                "status": "PASS" if profile_bound and louis_installed else "BLOCKED",
                "detail": profile_detail if not louis_installed else profile_detail,
            },
            "cups_raw_passthrough_and_policy": {
                "status": "PASS" if wsl_ok and docker_ok and cups_tools else "BLOCKED",
                "detail": f"WSL={wsl_detail or 'unavailable'}; Docker={docker_detail or 'unavailable'}; native CUPS tools={'present' if cups_tools else 'missing'}",
            },
            "drive_same_file_detection": {
                "status": "BLOCKED",
                "detail": "No DRIVE_FILE_ID is configured and no Workspace Events/change-feed refetch was exercised.",
            },
        },
        "safe_diagnostics": {
            "gcloud_binary": gcloud or "missing",
            "louis_binding_installed": louis_installed,
            "wsl_status_command_succeeded": wsl_ok,
            "docker_server_reachable": docker_ok,
            "cups_tools_present": cups_tools,
            "secrets_logged": False,
        },
    }


def main() -> int:
    evidence = collect()
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    for name, result in evidence["checks"].items():
        print(f"[{result['status']}] {name}: {result['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

