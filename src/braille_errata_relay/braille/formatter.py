"""Deterministic word wrapping over translated Unicode Braille cells."""

from __future__ import annotations

from dataclasses import dataclass

from braille_errata_relay.domain.models import TranslationProfile

from .liblouis_adapter import TranslatedBlock

BRAILLE_SPACE = "\u2800"


@dataclass(frozen=True)
class FormattedLine:
    unicode_cells: str
    source_block_ids: tuple[str, ...]


def _wrap(text: str, width: int) -> list[str]:
    text = text.replace(" ", BRAILLE_SPACE).strip(BRAILLE_SPACE)
    if not text:
        return [""]
    words = text.split(BRAILLE_SPACE)
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        candidate = word if not current else current + BRAILLE_SPACE + word
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def format_blocks(
    blocks: tuple[TranslatedBlock, ...], profile: TranslationProfile
) -> tuple[FormattedLine, ...]:
    lines: list[FormattedLine] = []
    for index, block in enumerate(blocks):
        lines.extend(
            FormattedLine(line, (block.block_id,))
            for line in _wrap(block.unicode_cells, profile.cells_per_line)
        )
        if index != len(blocks) - 1:
            lines.append(FormattedLine("", ()))
    return tuple(lines)

