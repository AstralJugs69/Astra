from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "infra" / "wsl" / "audit_endpoint_receipt.py"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
TITLE = "BER|WO-DEMO-001|9b13336e1833|BASELINE"
BASELINE_ID = "a" * 64
LINK_ID = "b" * 64
BRF_SHA256 = "9b13336e1833b9491207f26720eae9a8c9132b75a232acdac1a9f76834baa736"


def _module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path]:
    capture_root = tmp_path / "captures"
    source = ROOT / "demo" / "expected" / "v1.brf"
    backend = _module("endpoint_audit_test_backend", BACKEND_PATH)
    assert (
        backend.run_backend(
            device_uri="relay-capture://demo-embosser",
            job_id_text="19",
            title=TITLE,
            input_path=str(source),
            capture_root=capture_root,
            page_delay_seconds=0,
        )
        == 0
    )
    audit = _module("endpoint_audit_test_module", AUDIT_PATH)
    monkeypatch.setattr(audit, "CAPTURE_ROOT", capture_root)
    return audit, capture_root / "19"


def _audit(audit: ModuleType) -> dict[str, object]:
    return audit.audit(
        baseline_id=BASELINE_ID,
        production_link_id=LINK_ID,
        job_id=19,
        expected_title=TITLE,
        approved_brf_sha256=BRF_SHA256,
        expected_state_version=1,
    )


def _active_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, ModuleType, Path, threading.Event, threading.Thread]:
    capture_root = tmp_path / "captures"
    source = ROOT / "demo" / "expected" / "v1.brf"
    backend = _module("endpoint_audit_active_backend", BACKEND_PATH)
    release = threading.Event()
    started = threading.Event()

    def pause(_seconds: float) -> None:
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(backend.time, "sleep", pause)
    worker = threading.Thread(
        target=backend.run_backend,
        kwargs={
            "device_uri": backend.DEVICE_URI,
            "job_id_text": "19",
            "title": TITLE,
            "input_path": str(source),
            "capture_root": capture_root,
            "page_delay_seconds": 1,
        },
    )
    worker.start()
    assert started.wait(timeout=5)
    audit = _module("endpoint_audit_active_module", AUDIT_PATH)
    monkeypatch.setattr(audit, "CAPTURE_ROOT", capture_root)
    return audit, backend, capture_root / "19", release, worker


def test_fixed_root_auditor_validates_exact_bytes_manifest_and_complete_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, _job_dir = _capture(tmp_path, monkeypatch)

    result = _audit(audit)

    assert result["endpoint_received_sha256"] == BRF_SHA256
    assert result["capture_state"] == "COMPLETED"
    assert result["truth_basis"] == "SIMULATED_DEMO"
    assert result["scheduler_job_id"] == 19


def test_fixed_root_auditor_validates_immutable_active_acceptance_before_terminal_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, _backend, job_dir, release, worker = _active_capture(tmp_path, monkeypatch)
    try:
        result = _audit(audit)
        assert result["schema_version"] == "endpoint-evidence-submission.v2"
        assert result["capture_state"] == "RECEIVED"
        assert result["capture_manifest_sha256"] is None
        assert result["terminal_event_sha256"] is None
        assert isinstance(result["capture_acceptance_sha256"], str)
        assert (job_dir / "capture-acceptance.json").is_file()
        assert not (job_dir / "manifest.json").exists()
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


@pytest.mark.parametrize("target", ["acceptance", "first-event", "input"])
def test_active_acceptance_auditor_rejects_tampering(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, backend, job_dir, release, worker = _active_capture(tmp_path, monkeypatch)
    try:
        if target == "acceptance":
            path = job_dir / "capture-acceptance.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["received_sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
        elif target == "first-event":
            path = job_dir / "events.jsonl"
            values = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(values[0])
            first["event_type"] = "PAGE_COMPLETED"
            first["event_sha256"] = backend.sha256_bytes(
                backend._canonical(
                    {key: value for key, value in first.items() if key != "event_sha256"}
                )
            )
            values[0] = json.dumps(first, sort_keys=True)
            path.write_text("\n".join(values) + "\n", encoding="utf-8")
        else:
            (job_dir / "input.brf").write_bytes(b"x" * 2048)

        with pytest.raises(ValueError):
            _audit(audit)
    finally:
        release.set()
        worker.join(timeout=5)


def test_active_acceptance_is_not_invalidated_by_a_later_mutable_event_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, _backend, job_dir, release, worker = _active_capture(tmp_path, monkeypatch)
    try:
        path = job_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"event_type":"UNTRUSTED_SUFFIX"}\n')
        result = _audit(audit)
        assert result["capture_state"] == "RECEIVED"
    finally:
        release.set()
        worker.join(timeout=5)


def test_interrupted_acceptance_write_never_creates_a_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_root = tmp_path / "captures"
    source = ROOT / "demo" / "expected" / "v1.brf"
    backend = _module("endpoint_audit_interrupted_backend", BACKEND_PATH)

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated acceptance write interruption")

    monkeypatch.setattr(backend, "_atomic_write_json_once", interrupted)
    with pytest.raises(OSError):
        backend.run_backend(
            device_uri=backend.DEVICE_URI,
            job_id_text="19",
            title=TITLE,
            input_path=str(source),
            capture_root=capture_root,
            page_delay_seconds=0,
        )
    job_dir = capture_root / "19"
    assert not (job_dir / "capture-acceptance.json").exists()
    audit = _module("endpoint_audit_interrupted_module", AUDIT_PATH)
    monkeypatch.setattr(audit, "CAPTURE_ROOT", capture_root)
    with pytest.raises(ValueError):
        _audit(audit)


def test_duplicate_acceptance_for_the_same_scheduler_job_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, backend, job_dir, release, worker = _active_capture(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="immutable evidence"):
            backend.run_backend(
                device_uri=backend.DEVICE_URI,
                job_id_text="19",
                title=TITLE,
                input_path=str(ROOT / "demo" / "expected" / "v1.brf"),
                capture_root=job_dir.parent,
                page_delay_seconds=0,
            )
        assert _audit(audit)["capture_state"] == "RECEIVED"
    finally:
        release.set()
        worker.join(timeout=5)


@pytest.mark.parametrize("target", ["manifest", "events"])
def test_fixed_root_auditor_rejects_malformed_or_tampered_capture(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, job_dir = _capture(tmp_path, monkeypatch)
    if target == "manifest":
        manifest_path = job_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["received_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        events_path = job_dir / "events.jsonl"
        records = events_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(records[0])
        first["details"]["job_title"] = "tampered"
        records[0] = json.dumps(first)
        events_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        _audit(audit)


def test_auditor_rejects_wrong_job_title_hash_and_missing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit, _job_dir = _capture(tmp_path, monkeypatch)

    for updates in (
        {"job_id": 20},
        {"expected_title": "wrong"},
        {"approved_brf_sha256": "0" * 64},
    ):
        values: dict[str, object] = {
            "baseline_id": BASELINE_ID,
            "production_link_id": LINK_ID,
            "job_id": 19,
            "expected_title": TITLE,
            "approved_brf_sha256": BRF_SHA256,
            "expected_state_version": 1,
        }
        values.update(updates)
        with pytest.raises((OSError, ValueError)):
            audit.audit(**values)


def test_auditor_has_no_caller_selected_capture_root_or_control_surface() -> None:
    source = AUDIT_PATH.read_text(encoding="utf-8")

    assert 'parser.add_argument("--capture-root"' not in source
    assert 'CAPTURE_ROOT = Path("/var/lib/braille-relay/captures")' in source
    for forbidden in ("cups.Connection", "Print-Job", "Cancel-Job", "subprocess", "os.system"):
        assert forbidden not in source
