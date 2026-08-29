from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from braille_errata_relay.domain.models import SiteObservation

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "local_bridge" / "src" / "relay_bridge" / "observation_builder.py"
BACKEND_PATH = ROOT / "simulator" / "cups_backend" / "relay_capture_backend.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _normalized_snapshot() -> dict[str, object]:
    observed_at = "2026-08-28T17:00:00+00:00"
    return {
        "queue_name": "Braille-Embosser-Sim",
        "observed_at": observed_at,
        "jobs": [
            {
                "scheduler_job_id": 42,
                "owner": "relay-operator",
                "title": "BER|WO-DEMO-001|abc|BASELINE",
                "destination": "Braille-Embosser-Sim",
                "state": "PROCESSING",
                "state_reasons": ["processing-to-device"],
                "observed_at": observed_at,
                "job_created_at": "2026-08-28T16:59:00+00:00",
                "processing_at": observed_at,
                "completed_at": None,
                "impressions_completed": 1,
            }
        ],
        "printer": {
            "printer_state": "processing",
            "printer_state_reasons": ["processing"],
            "printer_accepting_jobs": True,
        },
    }


def test_generated_site_observation_matches_schema_and_domain_contract() -> None:
    builder = _load_module("observation_builder_contract", BRIDGE_PATH)
    payload = builder.build_observation(
        site_id="demo-site",
        bridge_id="bridge-1",
        queue_name="Braille-Embosser-Sim",
        sequence=1,
        queue_snapshot=_normalized_snapshot(),
        previous_sha256=None,
    )
    errors = sorted(_validator("site-observation.v1.json").iter_errors(payload), key=str)
    assert errors == []
    SiteObservation.model_validate(payload)
    assert "attributes" not in json.dumps(payload)


def test_site_observation_rejects_raw_cups_attributes() -> None:
    builder = _load_module("observation_builder_raw_contract", BRIDGE_PATH)
    snapshot = _normalized_snapshot()
    snapshot["jobs"] = [{**snapshot["jobs"][0], "attributes": {"job-state": 5}}]
    with pytest.raises((TypeError, ValueError), match="raw CUPS attributes"):
        builder.build_observation(
            site_id="demo-site",
            bridge_id="bridge-1",
            queue_name="Braille-Embosser-Sim",
            sequence=1,
            queue_snapshot=snapshot,
            previous_sha256=None,
        )


def test_capture_manifest_and_event_chain_match_schema(tmp_path: Path) -> None:
    backend = _load_module("capture_manifest_contract", BACKEND_PATH)
    row = b"a   "
    input_path = tmp_path / "candidate.brf"
    input_path.write_bytes(b"\r\n".join((row, b"    ")))
    capture_root = tmp_path / "captures"
    backend.run_backend(
        device_uri=backend.DEVICE_URI,
        job_id_text="42",
        title="BER|INCIDENT|abc|REPLACEMENT",
        input_path=str(input_path),
        capture_root=capture_root,
        cells_per_line=4,
        lines_per_page=2,
        page_delay_seconds=0,
    )
    job_dir = capture_root / "42"
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    errors = sorted(_validator("capture-manifest.v1.json").iter_errors(manifest), key=str)
    assert errors == []
    first_previous, terminal = backend.verify_event_chain(job_dir / "events.jsonl")
    assert first_previous is None
    assert terminal == manifest["terminal_event_sha256"]
    assert manifest["events_sha256"] == manifest["terminal_event_sha256"]
    assert manifest["completed_at"] == manifest["finished_at"]


def test_sanitized_cloud_gate0_evidence_matches_schema() -> None:
    payload = json.loads(
        (ROOT / "demo" / "evidence" / "cloud-gate0.json").read_text(encoding="utf-8")
    )
    errors = sorted(_validator("cloud-gate0-evidence.v1.json").iter_errors(payload), key=str)
    assert errors == []
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "access_token",
        "id_token",
        "api_key",
        "credentials",
        "raw_cursor",
        "source_content",
    ):
        assert forbidden not in serialized
