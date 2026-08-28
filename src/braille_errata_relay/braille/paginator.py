"""Fixed geometry pagination with explicit blank-row padding."""

from __future__ import annotations

from dataclasses import dataclass

from braille_errata_relay.domain.models import TranslationProfile

from .formatter import FormattedLine


@dataclass(frozen=True)
class BraillePage:
    number: int
    lines: tuple[FormattedLine, ...]

    @property
    def source_block_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                block_id for line in self.lines for block_id in line.source_block_ids
            )
        )


def paginate(lines: tuple[FormattedLine, ...], profile: TranslationProfile) -> tuple[BraillePage, ...]:
    if not lines:
        return (BraillePage(1, tuple()),)
    pages: list[BraillePage] = []
    for offset in range(0, len(lines), profile.lines_per_page):
        page_lines = list(lines[offset : offset + profile.lines_per_page])
        pages.append(BraillePage(len(pages) + 1, tuple(page_lines)))
    return tuple(pages)

