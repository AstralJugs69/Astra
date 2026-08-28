from __future__ import annotations

import pytest

from braille_errata_relay.braille.diff import diff_sources, evidence_span_ids
from braille_errata_relay.braille.errors import UnsupportedContentError
from braille_errata_relay.braille.normalize import normalize_source_bytes


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
