from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"
SPEC = importlib.util.spec_from_file_location("relay_capture_backend_permissions", MODULE_PATH)
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


@pytest.mark.skipif(os.name == "nt", reason="POSIX capture permissions are enforced in WSL")
def test_capture_evidence_is_not_world_readable_or_traversable(tmp_path: Path) -> None:
    row = b"a   "
    candidate = tmp_path / "candidate.brf"
    candidate.write_bytes(b"\r\n".join((row, b"    ")))
    capture_root = tmp_path / "captures"

    backend.run_backend(
        device_uri=backend.DEVICE_URI,
        job_id_text="42",
        title="BER|INCIDENT|abc|REPLACEMENT",
        input_path=str(candidate),
        capture_root=capture_root,
        cells_per_line=4,
        lines_per_page=2,
        page_delay_seconds=0,
    )

    job_dir = capture_root / "42"
    for directory in (capture_root, job_dir):
        assert stat.S_IMODE(directory.stat().st_mode) & 0o007 == 0
    for filename in ("input.brf", "output.brf", "events.jsonl", "manifest.json"):
        assert stat.S_IMODE((job_dir / filename).stat().st_mode) & 0o007 == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX set-group-ID inheritance is enforced in WSL")
def test_backend_preserves_setup_owned_capture_group_inheritance(tmp_path: Path) -> None:
    row = b"a   "
    candidate = tmp_path / "candidate.brf"
    candidate.write_bytes(b"\r\n".join((row, b"    ")))
    capture_root = tmp_path / "captures"
    capture_root.mkdir(mode=backend.CAPTURE_DIRECTORY_MODE)
    os.chmod(capture_root, backend.CAPTURE_DIRECTORY_MODE)
    capture_group = capture_root.stat().st_gid

    previous_umask = os.umask(0o077)
    try:
        backend.run_backend(
            device_uri=backend.DEVICE_URI,
            job_id_text="43",
            title="BER|INCIDENT|abc|REPLACEMENT",
            input_path=str(candidate),
            capture_root=capture_root,
            cells_per_line=4,
            lines_per_page=2,
            page_delay_seconds=0,
        )
    finally:
        os.umask(previous_umask)

    job_dir = capture_root / "43"
    assert stat.S_IMODE(job_dir.stat().st_mode) == backend.CAPTURE_DIRECTORY_MODE
    assert job_dir.stat().st_mode & stat.S_ISGID
    assert job_dir.stat().st_gid == capture_group
    for filename in ("input.brf", "output.brf", "events.jsonl", "manifest.json"):
        assert (job_dir / filename).stat().st_gid == capture_group
