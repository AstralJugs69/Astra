"""Record safe Gate 0 diagnostics without pretending an unavailable seam passed."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from braille_errata_relay.braille.profile import load_translation_profile, profile_sha256
from braille_errata_relay.braille.readiness import check_liblouis_readiness
from braille_errata_relay.domain.models import ArtifactKind, TranslationProfile

PROFILE = Path(
    os.environ.get(
        "RELAY_LIBLOUIS_PROFILE", str(ROOT / "config/translation_profiles/demo-ueb-40x25-v1.json")
    )
)
EVIDENCE = ROOT / "demo" / "evidence" / "preflight.json"
LOCAL_CUPS_EVIDENCE = ROOT / "demo" / "evidence" / "gate0-local-floor.json"
LOCAL_CUPS_SCHEMA = ROOT / "schemas" / "gate0-local-floor-evidence.v1.json"
CLOUD_GATE0_EVIDENCE = ROOT / "demo" / "evidence" / "cloud-gate0.json"
CLOUD_GATE0_SCHEMA = ROOT / "schemas" / "cloud-gate0-evidence.v1.json"
DEFAULT_GATE0_IMAGE = "braille-errata-relay:cloud-gate-0"
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CUPS_CHECKS = {
    "operator_terminal_submission",
    "full_capture_exact_byte_passthrough",
    "operator_hold_release_cancel",
    "terminated_capture_journal",
    "observer_authorization_denials",
    "observer_filesystem_isolation",
}


@dataclass(frozen=True)
class CommandResult:
    succeeded: bool
    stdout: str
    detail: str


def _sanitize_detail(value: str) -> str:
    result = value.replace("\x00", "").strip()
    for private_path in (str(ROOT), str(Path.home())):
        if private_path:
            result = result.replace(private_path, "<local-path>")
    return result[-500:]


def _command(name: str) -> str | None:
    return shutil.which(name)


def _run(command: list[str], *, timeout: int) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(False, "", _sanitize_detail(str(exc)))
    stdout = completed.stdout.replace("\x00", "").strip()
    detail = _sanitize_detail(completed.stdout + completed.stderr)
    return CommandResult(completed.returncode == 0, stdout, detail)


def _probe(command: list[str]) -> tuple[bool, str]:
    result = _run(command, timeout=10)
    return result.succeeded, result.detail


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    with part.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(path)


def _governing_document_hashes() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name in ("instruction.md", "architecture.md"):
        path = ROOT / name
        if path.is_file():
            result[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            result[name] = {"sha256": "MISSING"}
    return result


def _render_local_goldens() -> tuple[TranslationProfile, dict[str, bytes]]:
    if importlib.util.find_spec("louis") is None:
        raise RuntimeError("upstream Liblouis Python binding is unavailable")
    from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
    from braille_errata_relay.braille.normalize import normalize_source_bytes
    from braille_errata_relay.braille.render import render

    profile = load_translation_profile(PROFILE)
    if not profile.is_bound:
        raise RuntimeError("profile table hashes are unresolved")
    adapter = LiblouisAdapter()
    outputs: dict[str, bytes] = {}
    for version in ("v1", "v2"):
        fixture = ROOT / "demo" / "fixtures" / f"source-{version}-hero.md"
        normalized = normalize_source_bytes(fixture.read_bytes(), document_id="biology-vol2")
        rendered = render(
            normalized,
            profile,
            adapter,
            source_revision_id=f"drive:fixture:{version}",
            source_sha256=normalized.normalized_source_sha256,
            artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            generator_build={"profile_sha256": profile_sha256(profile)},
        )
        outputs[version] = rendered.brf
    return profile, outputs


def _expected_golden_identity(profile: TranslationProfile) -> dict[str, str]:
    expected = ROOT / "demo" / "expected"
    result: dict[str, str] = {}
    expected_profile_sha = profile_sha256(profile)
    for version in ("v1", "v2"):
        brf_path = expected / f"{version}.brf"
        manifest_path = expected / f"{version}-manifest.json"
        if not brf_path.is_file() or not manifest_path.is_file():
            raise ValueError(f"missing checked-in {version} golden")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError(f"invalid checked-in {version} manifest")
        if manifest.get("translation_profile_sha256") != expected_profile_sha:
            raise ValueError(f"{version} golden has an incompatible profile identity")
        digest = hashlib.sha256(brf_path.read_bytes()).hexdigest()
        if manifest.get("artifact_sha256") != digest:
            raise ValueError(f"{version} golden manifest hash is inconsistent")
        result[version] = digest
    return result


def _table_identity(profile: TranslationProfile) -> list[dict[str, str | None]]:
    return [{"name": table.name, "sha256": table.sha256} for table in profile.translation_tables]


def _liblouis_check() -> tuple[dict[str, object], bool]:
    try:
        profile = load_translation_profile(PROFILE)
    except (OSError, ValueError) as exc:
        return {"status": "BLOCKED", "detail": f"profile invalid: {type(exc).__name__}"}, False
    report = check_liblouis_readiness(profile)
    detail = "; ".join(report.checks) or (report.reason or "no checks recorded")
    if report.reason:
        detail = f"{report.reason}; {detail}"
    return {
        "status": "PASS" if report.ready else "BLOCKED",
        "detail": detail,
        "profile_id": profile.profile_id,
        "profile_sha256": profile_sha256(profile),
        "liblouis_version": report.liblouis_version or profile.liblouis_version,
        "table_hashes": _table_identity(profile),
        "checks": list(report.checks),
    }, report.ready


def _golden_check() -> tuple[dict[str, object], bool]:
    try:
        profile, outputs = _render_local_goldens()
        repeat_profile, repeat_outputs = _render_local_goldens()
        if profile_sha256(profile) != profile_sha256(repeat_profile) or outputs != repeat_outputs:
            return {"status": "BLOCKED", "detail": "repeat render identity or bytes differ"}, False
        expected_hashes = _expected_golden_identity(profile)
        actual_hashes = {
            version: hashlib.sha256(value).hexdigest() for version, value in outputs.items()
        }
        if actual_hashes != expected_hashes:
            return {"status": "BLOCKED", "detail": "checked-in golden bytes differ"}, False
        return {
            "status": "PASS",
            "detail": "two repeat renders matched checked-in V1/V2 BRF bytes",
            "repeat_runs": 2,
            "profile_id": profile.profile_id,
            "profile_sha256": profile_sha256(profile),
            "table_hashes": _table_identity(profile),
            "brf_sha256": actual_hashes,
        }, True
    except Exception as exc:  # noqa: BLE001 - preflight must fail closed and stay sanitized
        return {"status": "BLOCKED", "detail": f"golden check failed: {type(exc).__name__}"}, False


_CONTAINER_RENDER = r"""
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.profile import load_translation_profile, profile_sha256
from braille_errata_relay.braille.render import render
from braille_errata_relay.domain.models import ArtifactKind

profile = load_translation_profile("/app/config/translation_profiles/demo-ueb-40x25-v1.json")
adapter = LiblouisAdapter()
outputs = {}
for version in ("v1", "v2"):
    fixture = Path("/demo/fixtures") / f"source-{version}-hero.md"
    normalized = normalize_source_bytes(fixture.read_bytes(), document_id="biology-vol2")
    rendered = render(
        normalized,
        profile,
        adapter,
        source_revision_id=f"drive:fixture:{version}",
        source_sha256=normalized.normalized_source_sha256,
        artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        generator_build={"profile_sha256": profile_sha256(profile)},
    )
    outputs[version] = base64.b64encode(rendered.brf).decode("ascii")
print(json.dumps({
    "profile_id": profile.profile_id,
    "profile_sha256": profile_sha256(profile),
    "table_hashes": [
        {"name": table.name, "sha256": table.sha256}
        for table in profile.translation_tables
    ],
    "brf_b64": outputs,
}, sort_keys=True, separators=(",", ":")))
"""


def _installed_container_smoke(image: str) -> dict[str, object]:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--entrypoint",
        "python",
        image,
        "-m",
        "braille_errata_relay.container_smoke",
    ]
    result = _run(command, timeout=45)
    if not result.succeeded:
        raise RuntimeError(f"installed container smoke failed: {result.detail}")
    payload = json.loads(result.stdout.splitlines()[-1])
    if not isinstance(payload, dict):
        raise TypeError("installed container smoke did not return an object")
    expected = {
        "schema_version": "installed-container-smoke.v1",
        "policy_id": "relay-policy.v1",
        "profile_id": "demo-ueb-40x25-v1",
        "readyz_status": 200,
        "ready": True,
        "liblouis_version": "3.38.0",
        "app_src_present": False,
        "package_from_app_src": False,
    }
    if payload != expected:
        raise ValueError("installed container smoke predicates did not all pass")
    return payload


def _container_brf_comparison() -> tuple[dict[str, object], bool]:
    image = os.environ.get("RELAY_GATE0_IMAGE", DEFAULT_GATE0_IMAGE)
    if _command("docker") is None:
        return {
            "status": "BLOCKED",
            "detail": "Docker CLI is unavailable; image was not built",
        }, False
    inspected = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=10)
    image_id = inspected.stdout.strip()
    if not inspected.succeeded or _IMAGE_ID.fullmatch(image_id) is None:
        return {
            "status": "BLOCKED",
            "detail": f"named image is unavailable without a build: {inspected.detail or image}",
        }, False
    try:
        installed_smoke = _installed_container_smoke(image)
        profile, local_outputs = _render_local_goldens()
        expected_hashes = _expected_golden_identity(profile)
        fixture_source = os.environ.get("RELAY_DEMO_MOUNT_SOURCE", str(ROOT / "demo"))
        fixture_mount = f"type=bind,src={fixture_source},dst=/demo,readonly"
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m",
            "--mount",
            fixture_mount,
            "--entrypoint",
            "python",
            image,
            "-c",
            _CONTAINER_RENDER,
        ]
        compared = _run(command, timeout=45)
        if not compared.succeeded:
            return {
                "status": "BLOCKED",
                "detail": f"bounded container comparison failed: {compared.detail}",
            }, False
        payload = json.loads(compared.stdout.splitlines()[-1])
        if not isinstance(payload, dict):
            raise TypeError("container comparison output is not an object")
        if payload.get("profile_id") != profile.profile_id:
            raise ValueError("container profile ID differs from the local profile")
        if payload.get("profile_sha256") != profile_sha256(profile):
            raise ValueError("container profile hash differs from the local profile")
        if payload.get("table_hashes") != _table_identity(profile):
            raise ValueError("container table identity differs from the local profile")
        encoded = payload.get("brf_b64")
        if not isinstance(encoded, dict):
            raise TypeError("container comparison did not return BRF bytes")
        container_outputs = {
            version: base64.b64decode(encoded[version], validate=True)
            for version in ("v1", "v2")
            if isinstance(encoded.get(version), str)
        }
        if set(container_outputs) != {"v1", "v2"} or container_outputs != local_outputs:
            raise ValueError("container BRF bytes differ from the local render")
        hashes = {
            version: hashlib.sha256(value).hexdigest() for version, value in local_outputs.items()
        }
        if hashes != expected_hashes:
            raise ValueError("local BRF bytes differ from checked-in goldens")
        return {
            "status": "PASS",
            "detail": "exact V1/V2 BRF bytes, profile identity, and table hashes matched",
            "image": image,
            "image_id": image_id,
            "profile_id": profile.profile_id,
            "profile_sha256": profile_sha256(profile),
            "table_hashes": _table_identity(profile),
            "brf_sha256": hashes,
            "byte_comparison": "exact",
            "installed_smoke": installed_smoke,
        }, True
    except Exception as exc:  # noqa: BLE001 - a preflight must block rather than infer success
        return {
            "status": "BLOCKED",
            "detail": f"container comparison failed: {type(exc).__name__}",
        }, False


def _capture_evidence_valid(value: object, *, expected_state: str) -> bool:
    if not isinstance(value, dict) or value.get("state") != expected_state:
        return False
    candidate = value.get("candidate_sha256")
    backend = value.get("backend_received_sha256")
    output = value.get("captured_output_sha256")
    terminal = value.get("terminal_event_sha256")
    pages_total = value.get("pages_total")
    pages_completed = value.get("pages_completed")
    if not isinstance(candidate, str) or _SHA256.fullmatch(candidate) is None:
        return False
    if backend != candidate or not isinstance(terminal, str) or _SHA256.fullmatch(terminal) is None:
        return False
    if not isinstance(pages_total, int) or isinstance(pages_total, bool) or pages_total < 1:
        return False
    if (
        not isinstance(pages_completed, int)
        or isinstance(pages_completed, bool)
        or not 0 <= pages_completed <= pages_total
    ):
        return False
    if value.get("manifest_schema_valid") is not True or value.get("event_chain_valid") is not True:
        return False
    if expected_state == "COMPLETED":
        return output == candidate and pages_completed == pages_total
    return output is None


def _cups_floor_check(
    evidence_path: Path = LOCAL_CUPS_EVIDENCE,
    *,
    fixture_path: Path | None = None,
    schema_path: Path = LOCAL_CUPS_SCHEMA,
) -> tuple[dict[str, object], bool]:
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        if next(iter(validator.iter_errors(payload)), None) is not None:
            raise ValueError("local CUPS evidence does not satisfy its complete schema")
        current_fixture = fixture_path or ROOT / "demo" / "expected" / "v1.brf"
        fixture_bytes = current_fixture.read_bytes()
    except (OSError, ValueError, TypeError):
        return {
            "status": "BLOCKED",
            "detail": "sanitized local CUPS Gate 0 evidence is unavailable or invalid",
        }, False
    completed_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    lifecycle_sha256 = hashlib.sha256(b"\x0c".join([fixture_bytes] * 12)).hexdigest()
    checks = payload.get("checks") if isinstance(payload, dict) else None
    full_capture = payload.get("full_capture") if isinstance(payload, dict) else None
    terminated_capture = payload.get("terminated_capture") if isinstance(payload, dict) else None
    valid = (
        isinstance(payload, dict)
        and payload.get("schema_version") == "gate0-local-floor-evidence.v1"
        and payload.get("queue") == "Braille-Embosser-Sim"
        and payload.get("simulated_endpoint") is True
        and payload.get("fixture") == "demo/expected/v1.brf"
        and isinstance(checks, dict)
        and set(checks) == _CUPS_CHECKS
        and all(checks.get(name) == "PASS" for name in _CUPS_CHECKS)
        and _capture_evidence_valid(full_capture, expected_state="COMPLETED")
        and _capture_evidence_valid(terminated_capture, expected_state="TERMINATED")
        and isinstance(full_capture, dict)
        and full_capture.get("candidate_sha256") == completed_sha256
        and full_capture.get("backend_received_sha256") == completed_sha256
        and full_capture.get("captured_output_sha256") == completed_sha256
        and isinstance(terminated_capture, dict)
        and terminated_capture.get("candidate_sha256") == lifecycle_sha256
        and terminated_capture.get("backend_received_sha256") == lifecycle_sha256
        and terminated_capture.get("captured_output_sha256") is None
    )
    if not valid:
        return {
            "status": "BLOCKED",
            "detail": "sanitized local CUPS Gate 0 evidence failed closed validation",
        }, False
    return {
        "status": "PASS",
        "detail": (
            "real CUPS scheduling, exact-byte capture, terminal journal, operator lifecycle, "
            "observer denials, and filesystem isolation were evidenced"
        ),
        "evidence_schema": "gate0-local-floor-evidence.v1",
        "queue": "Braille-Embosser-Sim",
        "simulated_endpoint": True,
    }, True


def _cloud_floor_checks(
    evidence_path: Path = CLOUD_GATE0_EVIDENCE,
    *,
    schema_path: Path = CLOUD_GATE0_SCHEMA,
) -> tuple[dict[str, dict[str, object]], bool]:
    names = {
        "adk_gemini_structured_output": "ADK/Gemini structured-output evidence",
        "cloud_run_private_service": "private Cloud Run evidence",
        "firestore_execution_ledger": "Firestore idempotency evidence",
        "drive_same_file_detection": "Drive same-file revision evidence",
        "gcs_immutable_artifacts": "immutable GCS create/read evidence",
    }

    def blocked(detail: str) -> tuple[dict[str, dict[str, object]], bool]:
        return {
            name: {"status": "BLOCKED", "detail": f"{label} {detail}"}
            for name, label in names.items()
        }, False

    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        if next(iter(validator.iter_errors(payload)), None) is not None:
            raise ValueError("cloud evidence does not satisfy its complete schema")
        expected_v1 = hashlib.sha256(
            (ROOT / "demo" / "fixtures" / "source-v1-hero.md").read_bytes()
        ).hexdigest()
        expected_v2 = hashlib.sha256(
            (ROOT / "demo" / "fixtures" / "source-v2-hero.md").read_bytes()
        ).hexdigest()
    except (OSError, ValueError, TypeError):
        return blocked("is unavailable or invalid")

    assert isinstance(payload, dict)
    service = payload.get("service")
    semantic = payload.get("semantic")
    drive = payload.get("drive")
    firestore_evidence = payload.get("firestore")
    storage = payload.get("storage")
    checks = payload.get("checks")
    if not all(
        isinstance(value, dict)
        for value in (service, semantic, drive, firestore_evidence, storage, checks)
    ):
        return blocked("failed closed structural validation")
    assert isinstance(service, dict)
    assert isinstance(semantic, dict)
    assert isinstance(drive, dict)
    assert isinstance(firestore_evidence, dict)
    assert isinstance(storage, dict)
    assert isinstance(checks, dict)

    check_keys = {
        "private_cloud_run",
        "adk_gemini_structured_output",
        "drive_same_file_refetch",
        "firestore_idempotency",
        "immutable_gcs",
    }
    all_evidence_checks_pass = set(checks) == check_keys and all(
        isinstance(checks.get(name), dict) and checks[name].get("status") == "PASS"
        for name in check_keys
    )
    valid = (
        payload.get("schema_version") == "cloud-gate0-evidence.v1"
        and payload.get("region") == "europe-west3"
        and service.get("name") == "braille-errata-relay"
        and service.get("private") is True
        and isinstance(service.get("image_id"), str)
        and _IMAGE_ID.fullmatch(str(service["image_id"])) is not None
        and isinstance(service.get("image_digest"), str)
        and _IMAGE_ID.fullmatch(str(service["image_digest"])) is not None
        and isinstance(service.get("runtime_identity_sha256"), str)
        and _SHA256.fullmatch(str(service["runtime_identity_sha256"])) is not None
        and semantic.get("status") == "PASS"
        and isinstance(semantic.get("assessment_id"), str)
        and _SHA256.fullmatch(str(semantic["assessment_id"])) is not None
        and isinstance(semantic.get("assessment_sha256"), str)
        and _SHA256.fullmatch(str(semantic["assessment_sha256"])) is not None
        and isinstance(semantic.get("latency_ms"), int)
        and not isinstance(semantic.get("latency_ms"), bool)
        and drive.get("status") == "PASS"
        and drive.get("same_file_id") is True
        and drive.get("v1_sha256") == expected_v1
        and drive.get("v2_sha256") == expected_v2
        and drive.get("start_cursor_sha256") != drive.get("final_cursor_sha256")
        and firestore_evidence.get("status") == "PASS"
        and firestore_evidence.get("duplicate_replay") is True
        and storage.get("status") == "PASS"
        and storage.get("artifact_sha256") == expected_v2
        and storage.get("create_read_match") is True
        and all_evidence_checks_pass
    )
    if not valid:
        return blocked("failed closed invariant validation")

    return {
        "adk_gemini_structured_output": {
            "status": "PASS",
            "detail": "deployed ADK agent returned the closed semantic schema using attached identity",
        },
        "cloud_run_private_service": {
            "status": "PASS",
            "detail": "Frankfurt Cloud Run service rejected unauthenticated access",
        },
        "firestore_execution_ledger": {
            "status": "PASS",
            "detail": "Firestore receipt, execution, cursor, and outbox replay converged idempotently",
        },
        "drive_same_file_detection": {
            "status": "PASS",
            "detail": "Drive change feed advanced and refetched exact V1/V2 bytes for one file identity",
        },
        "gcs_immutable_artifacts": {
            "status": "PASS",
            "detail": "content-addressed GCS create and immediate read/rehash matched V2 bytes",
        },
    }, True


def collect() -> dict[str, object]:
    python_supported = (3, 11) <= sys.version_info[:2] < (3, 13)
    wsl_ok, _wsl_detail = _probe(["wsl.exe", "--status"])
    docker_ok, _docker_detail = _probe(["docker", "version", "--format", "{{.Server.Version}}"])
    gcloud = _command("gcloud")
    louis_installed = importlib.util.find_spec("louis") is not None
    cups_tools = all(_command(name) for name in ("lp", "lpstat", "cancel"))
    liblouis_result, liblouis_ready = _liblouis_check()
    golden_result, golden_ready = _golden_check()
    container_result, container_ready = _container_brf_comparison()
    cups_result, cups_ready = _cups_floor_check()
    cloud_results, cloud_ready = _cloud_floor_checks()
    return {
        "schema_version": "preflight.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "governing_documents": _governing_document_hashes(),
        "checks": {
            "python_3_11_or_3_12": {
                "status": "PASS" if python_supported else "BLOCKED",
                "detail": sys.version,
            },
            "adk_gemini_structured_output": cloud_results["adk_gemini_structured_output"],
            "cloud_run_private_service": cloud_results["cloud_run_private_service"],
            "firestore_execution_ledger": cloud_results["firestore_execution_ledger"],
            "liblouis_profile": liblouis_result,
            "liblouis_golden_repeat": golden_result,
            "container_brf_comparison": container_result,
            "cups_raw_passthrough_and_policy": cups_result,
            "drive_same_file_detection": cloud_results["drive_same_file_detection"],
            "gcs_immutable_artifacts": cloud_results["gcs_immutable_artifacts"],
        },
        "safe_diagnostics": {
            "gcloud_binary": "present" if gcloud else "missing",
            "louis_binding_installed": louis_installed,
            "liblouis_readiness": liblouis_ready,
            "liblouis_golden_repeat": golden_ready,
            "container_brf_comparison": container_ready,
            "cups_raw_passthrough_and_policy": cups_ready,
            "cloud_gate0": cloud_ready,
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
