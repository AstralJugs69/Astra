#!/usr/bin/env python3
"""Emit deterministic BRF identity for a bound Gate 0 translation profile.

The output includes Base64 only for the public synthetic V1/V2 fixtures so a
caller can compare bytes exactly between two isolated runtimes. It never writes
artifacts, contacts CUPS, or operates production equipment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    source_root = parent / "src"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))
        break

from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.profile import load_translation_profile, profile_sha256
from braille_errata_relay.braille.render import render
from braille_errata_relay.domain.models import ArtifactKind, TranslationProfile


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _render_identity(
    *,
    profile: TranslationProfile,
    fixture_root: Path,
) -> dict[str, object]:
    if not profile.is_bound:
        raise ValueError("translation profile has unresolved Liblouis table hashes")
    adapter = LiblouisAdapter()
    brf: dict[str, bytes] = {}
    for version in ("v1", "v2"):
        source_path = fixture_root / "fixtures" / f"source-{version}-hero.md"
        normalized = normalize_source_bytes(source_path.read_bytes(), document_id="biology-vol2")
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
        brf[version] = rendered.brf
    return {
        "schema_version": "gate0-brf-identity.v1",
        "profile_id": profile.profile_id,
        "profile_sha256": profile_sha256(profile),
        "liblouis_version": adapter.version(),
        "table_hashes": [
            {"name": table.name, "sha256": table.sha256} for table in profile.translation_tables
        ],
        "brf_sha256": {version: _sha256(value) for version, value in brf.items()},
        "brf_b64": {
            version: base64.b64encode(value).decode("ascii") for version, value in brf.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        help="a hash-bound translation profile produced from the installed Liblouis tables",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        required=True,
        help="directory containing the public synthetic demo fixtures",
    )
    args = parser.parse_args()
    if importlib.util.find_spec("louis") is None:
        parser.error("upstream Liblouis Python binding is unavailable")
    if not args.profile.is_file():
        parser.error(f"translation profile does not exist: {args.profile}")
    if not (args.fixture_root / "fixtures").is_dir():
        parser.error(f"fixture root does not contain fixtures: {args.fixture_root}")
    profile = load_translation_profile(args.profile)
    print(
        json.dumps(
            _render_identity(profile=profile, fixture_root=args.fixture_root), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
