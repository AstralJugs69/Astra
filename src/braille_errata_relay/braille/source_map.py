"""Canonical source-to-page mapping derived from the same rendered pages."""

from __future__ import annotations

from braille_errata_relay.braille.paginator import BraillePage
from braille_errata_relay.contracts.canonical_json import canonical_sha256


def build_source_map(pages: tuple[BraillePage, ...]) -> dict[str, object]:
    return {
        "schema_version": "source-map.v1",
        "pages": [
            {
                "number": page.number,
                "lines": [
                    {
                        "line": index + 1,
                        "source_block_ids": list(line.source_block_ids),
                    }
                    for index, line in enumerate(page.lines)
                ],
                "source_block_ids": list(page.source_block_ids),
            }
            for page in pages
        ],
    }


def source_map_sha256(source_map: dict[str, object]) -> str:
    return canonical_sha256(source_map)
