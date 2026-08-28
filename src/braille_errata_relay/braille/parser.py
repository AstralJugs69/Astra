"""Strict parser for the supported heading/paragraph Markdown subset."""

from __future__ import annotations

import re
from collections import defaultdict

from braille_errata_relay.domain.models import SourceBlock, SourceBlockKind

from .errors import UnsupportedContentError

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)$")
_ORDERED_LIST = re.compile(r"^\s*\d+[.)][ \t]+")
_UNORDERED_LIST = re.compile(r"^\s*[-+*][ \t]+")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return value or "section"


def _reject_inline_or_block_markup(line: str, line_number: int) -> None:
    if (
        "|" in line
        or "`" in line
        or "<" in line
        or ">" in line
        or re.search(r"!\[[^\]]*\]\([^)]*\)", line)
        or re.search(r"\[[^\]]+\]\([^)]*\)", line)
        or _ORDERED_LIST.match(line)
        or _UNORDERED_LIST.match(line)
        or line.startswith(">")
        or line.startswith("    ")
        or line.startswith("\t")
    ):
        raise UnsupportedContentError(f"unsupported Markdown structure at line {line_number}")


def parse_markdown(text: str) -> tuple[SourceBlock, ...]:
    lines = text.split("\n")
    blocks: list[SourceBlock] = []
    pending: list[tuple[int, str]] = []
    section = "document"
    section_counts: defaultdict[str, int] = defaultdict(int)
    paragraph_counts: defaultdict[str, int] = defaultdict(int)

    def flush_paragraph() -> None:
        nonlocal pending
        if not pending:
            return
        start_line = pending[0][0]
        value = " ".join(part for _, part in pending).strip()
        if not value:
            pending = []
            return
        paragraph_counts[section] += 1
        block_id = f"{section}/p-{paragraph_counts[section]:03d}"
        blocks.append(
            SourceBlock(
                block_id=block_id,
                kind=SourceBlockKind.PARAGRAPH,
                text=value,
                ordinal=len(blocks),
            )
        )
        pending = []

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip(" \t")
        if not line:
            flush_paragraph()
            continue
        if line.startswith("---") and set(line) == {"-"}:
            raise UnsupportedContentError(f"horizontal rules are unsupported at line {line_number}")
        if line.startswith("#"):
            flush_paragraph()
            match = _HEADING.match(line)
            if match is None or not match.group(2).strip() or match.group(2).rstrip().endswith("#"):
                raise UnsupportedContentError(f"invalid heading at line {line_number}")
            _reject_inline_or_block_markup(match.group(2), line_number)
            base = _slug(match.group(2).strip())
            section_counts[base] += 1
            section = base if section_counts[base] == 1 else f"{base}-{section_counts[base]}"
            blocks.append(
                SourceBlock(
                    block_id=f"heading/{section}",
                    kind=SourceBlockKind.HEADING,
                    text=match.group(2).strip(),
                    ordinal=len(blocks),
                )
            )
            continue
        _reject_inline_or_block_markup(line, line_number)
        pending.append((line_number, line))

    flush_paragraph()
    if not blocks:
        raise UnsupportedContentError("source contains no supported heading or paragraph blocks")
    return tuple(blocks)

