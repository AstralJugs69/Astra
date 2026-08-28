"""Generate reproducible V1/V2 BRF goldens from a bound Liblouis profile."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from braille_errata_relay.braille.diff import diff_sources
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.page_impact import compare_brf
from braille_errata_relay.braille.profile import load_translation_profile, profile_sha256
from braille_errata_relay.braille.render import render
from braille_errata_relay.contracts.canonical_json import canonical_json_bytes
from braille_errata_relay.domain.models import ArtifactKind

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "demo" / "expected")
    args = parser.parse_args()
    profile = load_translation_profile(args.profile)
    adapter = LiblouisAdapter()
    args.output.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for version in ("v1", "v2"):
        fixture = ROOT / "demo" / "fixtures" / f"source-{version}-hero.md"
        normalized = normalize_source_bytes(fixture.read_bytes(), document_id="biology-vol2")
        rendered[version] = render(
            normalized,
            profile,
            adapter,
            source_revision_id=f"drive:fixture:{version}",
            source_sha256=normalized.normalized_source_sha256,
            artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            generator_build={"profile_sha256": profile_sha256(profile)},
        )
        (args.output / f"{version}.brf").write_bytes(rendered[version].brf)
        (args.output / f"{version}-manifest.json").write_bytes(
            canonical_json_bytes(rendered[version].manifest.model_dump(mode="json")) + b"\n"
        )
        (args.output / f"{version}-source-map.json").write_bytes(
            canonical_json_bytes(rendered[version].source_map) + b"\n"
        )
    comparison = compare_brf(
        rendered["v1"].brf,
        rendered["v2"].brf,
        profile,
        baseline_artifact_sha256=rendered["v1"].manifest.artifact_sha256,
        candidate_artifact_sha256=rendered["v2"].manifest.artifact_sha256,
    )
    source_diff = diff_sources(
        normalize_source_bytes(
            (ROOT / "demo/fixtures/source-v1-hero.md").read_bytes(), document_id="biology-vol2"
        ),
        normalize_source_bytes(
            (ROOT / "demo/fixtures/source-v2-hero.md").read_bytes(), document_id="biology-vol2"
        ),
    )
    impact = {
        "schema_version": "page-impact.v1",
        "source_changed_block_ids": list(source_diff.changed_block_ids),
        "impact": comparison.impact.model_dump(mode="json"),
        "old_page_sha256": list(comparison.old_page_hashes),
        "new_page_sha256": list(comparison.new_page_hashes),
    }
    (args.output / "page-impact.json").write_bytes(canonical_json_bytes(impact) + b"\n")
    print(f"PASS: rendered V1/V2 goldens into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
