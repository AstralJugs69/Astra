from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "simulator/cups_backend/relay_capture_backend.py"
)
SPEC = importlib.util.spec_from_file_location("relay_capture_backend", MODULE_PATH)
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


def valid_brf() -> bytes:
    row = b"a   "
    return b"\r\n".join((row, b"    "))


def test_backend_accepts_exact_geometry_and_rejects_bad_shape() -> None:
    assert backend.validate_brf(valid_brf(), cells_per_line=4, lines_per_page=2) == (valid_brf(),)
    with pytest.raises(ValueError):
        backend.validate_brf(b"a", cells_per_line=4, lines_per_page=2)


def test_backend_derives_numeric_job_path_and_preserves_capture(tmp_path: Path) -> None:
    input_path = tmp_path / "candidate.brf"
    input_path.write_bytes(valid_brf())
    assert (
        backend.run_backend(
            device_uri=backend.DEVICE_URI,
            job_id_text="42",
            title="BER|INCIDENT|abc|REPLACEMENT",
            input_path=str(input_path),
            capture_root=tmp_path / "captures",
            cells_per_line=4,
            lines_per_page=2,
            page_delay_seconds=0,
        )
        == 0
    )
    manifest = (tmp_path / "captures" / "42" / "manifest.json").read_text(encoding="utf-8")
    assert '"simulated_endpoint": true' in manifest
    assert (tmp_path / "captures" / "42" / "output.brf").read_bytes() == valid_brf()
    with pytest.raises(ValueError):
        backend.run_backend(
            device_uri=backend.DEVICE_URI,
            job_id_text="42/../escape",
            title="ignored",
            input_path=str(input_path),
            capture_root=tmp_path / "captures",
            cells_per_line=4,
            lines_per_page=2,
        )


def test_capture_journal_rejects_tampered_event(tmp_path: Path) -> None:
    journal_path = tmp_path / "events.jsonl"
    journal = backend.CaptureJournal(journal_path)
    journal.append("ACCEPTED", {"scheduler_job_id": 42})
    original = journal_path.read_text(encoding="utf-8")
    journal_path.write_text(original.replace('"ACCEPTED"', '"TAMPERED"', 1), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        backend.verify_event_chain(journal_path)


def test_backend_runtime_timing_config_is_strict(tmp_path: Path) -> None:
    configuration = tmp_path / "relay-capture.conf"

    with pytest.raises(ValueError, match="missing"):
        backend.load_page_delay(configuration, require_root_owner=False)

    configuration.write_text("RELAY_PAGE_DELAY_SECONDS=5.0\n", encoding="utf-8")
    assert backend.load_page_delay(configuration, require_root_owner=False) == 5.0
    assert backend.main(["--validate-runtime-config", str(configuration)]) == 0

    for invalid_content in (
        "RELAY_PAGE_DELAY_SECONDS=0.25\n",
        "RELAY_PAGE_DELAY_SECONDS=5.0\nUNEXPECTED=value\n",
        "RELAY_PAGE_DELAY_SECONDS=5.0\nRELAY_PAGE_DELAY_SECONDS=6.0\n",
    ):
        configuration.write_text(invalid_content, encoding="utf-8")
        with pytest.raises(ValueError):
            backend.load_page_delay(configuration, require_root_owner=False)
        assert backend.main(["--validate-runtime-config", str(configuration)]) == 1


def test_backend_main_uses_cups_shebang_arguments_and_device_uri_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_backend(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(backend, "run_backend", fake_run_backend)
    monkeypatch.setattr(backend, "load_page_delay", lambda *_args, **_kwargs: 5.0)
    monkeypatch.setenv("DEVICE_URI", backend.DEVICE_URI)

    assert (
        backend.main(
            [
                "42",
                "relay-operator",
                "BER|GATE0|terminal",
                "1",
                "raw",
                "/var/spool/cups/d00042-001",
            ]
        )
        == 0
    )
    assert captured["device_uri"] == backend.DEVICE_URI
    assert captured["job_id_text"] == "42"


def test_backend_main_rejects_missing_cups_device_uri(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backend, "load_page_delay", lambda *_args, **_kwargs: 5.0)
    monkeypatch.delenv("DEVICE_URI", raising=False)

    assert backend.main(["42", "operator", "title", "1", "raw"]) == 1
