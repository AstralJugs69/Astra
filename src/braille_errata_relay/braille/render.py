"""Pure deterministic orchestration of translation, formatting, pagination, and BRF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactManifest,
    NormalizedSource,
    PageRecord,
    TranslationProfile,
)

from .brf import page_hashes, serialize_pages
from .formatter import format_blocks
from .liblouis_adapter import LiblouisAdapter
from .paginator import BraillePage, paginate
from .profile import profile_sha256, require_bound_profile
from .source_map import build_source_map, source_map_sha256


@dataclass(frozen=True)
class RenderedBraille:
    brf: bytes
    pages: tuple[BraillePage, ...]
    page_records: tuple[PageRecord, ...]
    source_map: dict[str, object]
    manifest: ArtifactManifest


def render(
    normalized_source: NormalizedSource,
    profile: TranslationProfile,
    translator: LiblouisAdapter,
    *,
    source_revision_id: str,
    source_sha256: str,
    artifact_kind: ArtifactKind = ArtifactKind.FULL_CANDIDATE_BRF,
    baseline_manifest_sha256: str | None = None,
    created_at: datetime | None = None,
    generator_build: dict[str, str] | None = None,
) -> RenderedBraille:
    require_bound_profile(profile)
    translated = translator.translate_blocks(normalized_source.blocks, profile)
    formatted = format_blocks(translated, profile)
    pages = paginate(formatted, profile)
    brf = serialize_pages(pages, profile)
    hashes = page_hashes(brf, profile)
    source_map = build_source_map(pages)
    map_hash = source_map_sha256(source_map)
    artifact_sha = __import__("hashlib").sha256(brf).hexdigest()
    profile_sha = profile_sha256(profile)
    page_records = tuple(
        PageRecord(number=page.number, sha256=hashes[index], source_block_ids=page.source_block_ids)
        for index, page in enumerate(pages)
    )
    manifest = ArtifactManifest(
        artifact_kind=artifact_kind,
        artifact_sha256=artifact_sha,
        byte_length=len(brf),
        source_revision_id=source_revision_id,
        source_sha256=source_sha256,
        normalized_source_sha256=normalized_source.normalized_source_sha256,
        baseline_manifest_sha256=baseline_manifest_sha256,
        translation_profile_sha256=profile_sha,
        liblouis_version=profile.liblouis_version,
        formatter_version=profile.formatter_version,
        page_count=len(pages),
        page_sha256=hashes,
        source_map_uri=f"maps/{artifact_sha}.json",
        created_at=created_at or datetime.now(timezone.utc),
        generator_build=generator_build or {},
    )
    # Touch canonical identity here so accidental non-JSON values fail at render time.
    canonical_sha256(manifest.model_dump(mode="json"))
    return RenderedBraille(
        brf=brf,
        pages=pages,
        page_records=page_records,
        source_map=source_map,
        manifest=manifest,
    )

