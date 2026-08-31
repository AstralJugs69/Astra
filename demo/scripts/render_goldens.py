"""Generate reproducible V1/V2 BRF goldens from a bound Liblouis profile."""

from __future__ import annotations

import argparse
import sys
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.fixtures.demo_volume_source import build_demo_volume


def _render_pair(
    *,
    profile_path: Path,
    output: Path,
    document_id: str,
    sources: dict[str, bytes],
) -> None:
    profile = load_translation_profile(profile_path)
    adapter = LiblouisAdapter()
    output.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for version, source_bytes in sources.items():
        normalized = normalize_source_bytes(source_bytes, document_id=document_id)
        rendered[version] = render(
            normalized,
            profile,
            adapter,
            source_revision_id=f"drive:fixture:{document_id}:{version}",
            source_sha256=normalized.normalized_source_sha256,
            artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            generator_build={"profile_sha256": profile_sha256(profile)},
        )
        (output / f"{version}.brf").write_bytes(rendered[version].brf)
        (output / f"{version}-manifest.json").write_bytes(
            canonical_json_bytes(rendered[version].manifest.model_dump(mode="json")) + b"\n"
        )
        (output / f"{version}-source-map.json").write_bytes(
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
        normalize_source_bytes(sources["v1"], document_id=document_id),
        normalize_source_bytes(sources["v2"], document_id=document_id),
    )
    impact = {
        "schema_version": "page-impact.v1",
        "source_changed_block_ids": list(source_diff.changed_block_ids),
        "impact": comparison.impact.model_dump(mode="json"),
        "old_page_sha256": list(comparison.old_page_hashes),
        "new_page_sha256": list(comparison.new_page_hashes),
    }
    (output / "page-impact.json").write_bytes(canonical_json_bytes(impact) + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "demo" / "expected")
    parser.add_argument(
        "--volume-only",
        action="store_true",
        help="Render only the sizeable synthetic demo volume into demo/expected/demo-volume.",
    )
    args = parser.parse_args()
    if not args.volume_only:
        _render_pair(
            profile_path=args.profile,
            output=args.output,
            document_id="biology-vol2",
            sources={
                version: (ROOT / "demo" / "fixtures" / f"source-{version}-hero.md").read_bytes()
                for version in ("v1", "v2")
            },
        )
    volume_output = args.output / "demo-volume"
    _render_pair(
        profile_path=args.profile,
        output=volume_output,
        document_id="synthetic-cellular-systems-field-guide",
        sources={version: build_demo_volume(version) for version in ("v1", "v2")},
    )
    print(f"PASS: rendered deterministic demo-volume goldens into {volume_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
