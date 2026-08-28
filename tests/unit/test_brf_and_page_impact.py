from __future__ import annotations

import hashlib

from braille_errata_relay.braille.brf import (
    page_hashes,
    serialize_pages,
    split_exact_brf,
    unicode_cells_to_brf,
)
from braille_errata_relay.braille.formatter import FormattedLine
from braille_errata_relay.braille.page_impact import compare_brf
from braille_errata_relay.braille.paginator import BraillePage
from braille_errata_relay.domain.models import TranslationProfile, TranslationTable


def small_profile() -> TranslationProfile:
    return TranslationProfile(
        profile_id="test-profile",
        liblouis_version="3.38.0",
        translation_tables=(
            TranslationTable(name="en-ueb-g2.ctb", sha256="a" * 64),
            TranslationTable(name="en-us-brf.dis", sha256="b" * 64),
        ),
        cells_per_line=4,
        lines_per_page=2,
    )


def make_brf(rows: tuple[tuple[str, str], ...], profile: TranslationProfile) -> bytes:
    pages = tuple(
        BraillePage(index + 1, tuple(FormattedLine(row, ()) for row in page_rows))
        for index, page_rows in enumerate(rows)
    )
    return serialize_pages(pages, profile)


def test_explicit_unicode_to_brf_mapping_and_geometry() -> None:
    profile = small_profile()
    assert unicode_cells_to_brf("\u2800\u2801\u2803\u2802") == " ab1"
    brf = make_brf((("abcd", "    "),), profile)
    assert brf == b"abcd\r\n    "
    assert len(split_exact_brf(brf, profile)) == 1
    assert page_hashes(brf, profile) == (hashlib.sha256(brf).hexdigest(),)


def test_page_impact_uses_equal_prefix_and_suffix_only() -> None:
    profile = small_profile()
    old = make_brf((("aaaa", "    "), ("bbbb", "    "), ("cccc", "    ")), profile)
    new = make_brf((("aaaa", "    "), ("xxxx", "    "), ("cccc", "    ")), profile)
    result = compare_brf(
        old,
        new,
        profile,
        baseline_artifact_sha256="a" * 64,
        candidate_artifact_sha256="b" * 64,
    )
    assert result.impact.old_page_range is not None
    assert result.impact.old_page_range.as_tuple() == (2, 2)
    assert result.impact.new_page_range is not None
    assert result.impact.new_page_range.as_tuple() == (2, 2)
    assert result.impact.resynchronized_after_page == 2
    assert result.impact.pages_changed is True

