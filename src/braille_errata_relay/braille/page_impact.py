"""Exact page-prefix/suffix impact calculation; Gemini is not involved."""

from __future__ import annotations

from dataclasses import dataclass

from braille_errata_relay.domain.models import BrailleImpact, PageRange

from .brf import page_hashes, split_exact_brf


@dataclass(frozen=True)
class PageImpactResult:
    impact: BrailleImpact
    old_pages: tuple[bytes, ...]
    new_pages: tuple[bytes, ...]
    old_page_hashes: tuple[str, ...]
    new_page_hashes: tuple[str, ...]


def _range_or_none(start: int, end: int) -> PageRange | None:
    return PageRange(start=start, end=end) if start <= end else None


def compare_brf(
    old_brf: bytes,
    new_brf: bytes,
    profile,
    *,
    baseline_artifact_sha256: str,
    candidate_artifact_sha256: str,
) -> PageImpactResult:
    old_pages = split_exact_brf(old_brf, profile)
    new_pages = split_exact_brf(new_brf, profile)
    prefix = 0
    while prefix < min(len(old_pages), len(new_pages)) and old_pages[prefix] == new_pages[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(old_pages) - prefix
        and suffix < len(new_pages) - prefix
        and old_pages[len(old_pages) - 1 - suffix] == new_pages[len(new_pages) - 1 - suffix]
    ):
        suffix += 1
    old_range = _range_or_none(prefix + 1, len(old_pages) - suffix)
    new_range = _range_or_none(prefix + 1, len(new_pages) - suffix)
    changed = old_range is not None or new_range is not None
    resync = (len(new_pages) - suffix) if suffix else None
    impact = BrailleImpact(
        baseline_artifact_sha256=baseline_artifact_sha256,
        candidate_artifact_sha256=candidate_artifact_sha256,
        old_page_range=old_range,
        new_page_range=new_range,
        resynchronized_after_page=resync,
        candidate_page_count=len(new_pages),
        baseline_page_count=len(old_pages),
        pages_changed=changed,
    )
    return PageImpactResult(
        impact=impact,
        old_pages=old_pages,
        new_pages=new_pages,
        old_page_hashes=page_hashes(old_brf, profile),
        new_page_hashes=page_hashes(new_brf, profile),
    )

