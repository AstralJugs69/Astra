from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.render import render
from braille_errata_relay.domain.models import ArtifactKind


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.liblouis
def test_v1_and_v2_repeat_renders_are_byte_identical() -> None:
    if importlib.util.find_spec("louis") is None:
        pytest.skip("Gate 0 blocker: upstream Liblouis binding is unavailable")
    profile = load_translation_profile(ROOT / "config/translation_profiles/demo-ueb-40x25-v1.json")
    if not profile.is_bound:
        pytest.skip("Gate 0 blocker: profile has no resolved Liblouis table hashes")
    adapter = LiblouisAdapter()
    outputs = []
    for name in ("source-v1-hero.md", "source-v2-hero.md"):
        normalized = normalize_source_bytes(
            (ROOT / "demo/fixtures" / name).read_bytes(), document_id="biology-vol2"
        )
        first = render(
            normalized,
            profile,
            adapter,
            source_revision_id=f"drive:fixture:{name}",
            source_sha256=normalized.normalized_source_sha256,
            artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
        )
        second = render(
            normalized,
            profile,
            adapter,
            source_revision_id=f"drive:fixture:{name}",
            source_sha256=normalized.normalized_source_sha256,
            artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
        )
        assert first.brf == second.brf
        assert first.manifest.page_sha256 == second.manifest.page_sha256
        outputs.append(first)
    assert outputs[0].brf != outputs[1].brf

