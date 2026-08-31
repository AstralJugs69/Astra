from __future__ import annotations

import pytest

from braille_errata_relay.braille.diff import diff_sources, evidence_span_ids
from braille_errata_relay.braille.errors import UnsupportedContentError
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.domain.models import MAX_SOURCE_BLOCK_CHARACTERS


def test_normalization_is_line_ending_and_trailing_space_invariant() -> None:
    first = normalize_source_bytes(b"# Section\r\n\r\nA sentence.  \r\n", document_id="fixture")
    second = normalize_source_bytes(b"# Section\n\nA sentence.\n", document_id="fixture")
    assert first == second
    assert first.blocks[1].block_id == "block-000002"


def test_changed_block_has_stable_section_id_and_bounded_evidence() -> None:
    old = normalize_source_bytes(b"# Biology\n\nThe nucleus stores DNA.\n", document_id="x")
    new = normalize_source_bytes(b"# Biology\n\nThe mitochondrion stores DNA.\n", document_id="x")
    source_diff = diff_sources(old, new)
    assert source_diff.changed_block_ids == ("block-000002",)
    assert evidence_span_ids(source_diff) == ("old:block-000002", "new:block-000002")


def test_inserting_a_paragraph_does_not_change_later_stable_blocks() -> None:
    old = normalize_source_bytes(
        b"# Biology\n\nThe nucleus stores DNA.\n\nThe cell divides.\n",
        document_id="x",
    )
    new = normalize_source_bytes(
        b"# Biology\n\nAn inserted note.\n\nThe nucleus stores DNA.\n\nThe cell divides.\n",
        document_id="x",
        previous=old,
    )

    assert [block.block_id for block in new.blocks] == [
        "block-000001",
        "block-000004",
        "block-000002",
        "block-000003",
    ]
    source_diff = diff_sources(old, new)
    assert source_diff.changed_block_ids == ("block-000004",)
    assert source_diff.old_blocks == ()
    assert [block.block_id for block in source_diff.new_blocks] == ["block-000004"]


def test_normalized_source_hash_includes_block_kind() -> None:
    heading = normalize_source_bytes(b"# Title\n", document_id="heading")
    paragraph = normalize_source_bytes(b"Title\n", document_id="paragraph")

    assert heading.normalized_text == paragraph.normalized_text == "Title"
    assert heading.normalized_source_sha256 != paragraph.normalized_source_sha256


def test_google_docs_paragraph_above_legacy_boundary_is_supported() -> None:
    paragraph = "A" * 553

    normalized = normalize_source_bytes(paragraph.encode(), document_id="google-doc")

    assert normalized.blocks[0].text == paragraph


def test_source_paragraph_above_semantic_evidence_boundary_fails_cleanly() -> None:
    paragraph = "A" * (MAX_SOURCE_BLOCK_CHARACTERS + 1)

    with pytest.raises(
        UnsupportedContentError,
        match=f"source paragraph exceeds {MAX_SOURCE_BLOCK_CHARACTERS} characters",
    ):
        normalize_source_bytes(paragraph.encode(), document_id="oversized")


@pytest.mark.parametrize(
    "raw",
    [
        b"# Biology\n\n| old | new |\n| --- | --- |\n",
        b"# Biology\n\n- a list item\n",
        b"# Biology\n\n[unsafe link](https://example.invalid)\n",
        b"# Biology\n\n```\ncode\n```\n",
        b"\xef\xbb\xbf# Biology\n\nText.\n",
    ],
)
def test_unsupported_content_fails_closed(raw: bytes) -> None:
    with pytest.raises(UnsupportedContentError):
        normalize_source_bytes(raw, document_id="unsupported")
