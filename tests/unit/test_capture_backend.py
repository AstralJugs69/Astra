from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "simulator/cups_backend/relay_capture_backend.py"
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

