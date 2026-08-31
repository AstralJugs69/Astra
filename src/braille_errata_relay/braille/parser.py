"""Strict parser for the supported heading/paragraph Markdown subset."""

from __future__ import annotations

import re

from braille_errata_relay.domain.models import (
    MAX_SOURCE_BLOCK_CHARACTERS,
    SourceBlock,
    SourceBlockKind,
)

from .errors import UnsupportedContentError

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)$")
_ORDERED_LIST = re.compile(r"^\s*\d+[.)][ \t]+")
_UNORDERED_LIST = re.compile(r"^\s*[-+*][ \t]+")


def _reject_inline_or_block_markup(line: str, line_number: int) -> None:
    if (
        "|" in line
        or chr(96) in line
        or "<" in line
        or ">" in line
        or re.search(r"!\[[^\]]*\]\([^)]*\)", line)
        or re.search(r"\[[^\]]+\]\([^)]*\)", line)
        or _ORDERED_LIST.match(line)
        or _UNORDERED_LIST.match(line)
        or line.startswith((">", "    ", "\t"))
    ):
        raise UnsupportedContentError(f"unsupported Markdown structure at line {line_number}")


def _new_block_id(ordinal: int) -> str:
    return f"block-{ordinal + 1:06d}"


def parse_markdown(text: str) -> tuple[SourceBlock, ...]:
    """Parse the deliberately small Markdown subset with opaque provisional IDs.

    IDs are document-local allocation handles. normalize_source_bytes may
    replace them with IDs carried forward from the prior normalized revision.
    No persistent identity is derived from heading text or paragraph content.
    """

    lines = text.split("\n")
    blocks: list[SourceBlock] = []
    pending: list[str] = []

    def flush_paragraph() -> None:
        nonlocal pending
        if not pending:
            return
        value = " ".join(pending).strip()
        if value:
            if len(value) > MAX_SOURCE_BLOCK_CHARACTERS:
                raise UnsupportedContentError(
                    f"source paragraph exceeds {MAX_SOURCE_BLOCK_CHARACTERS} characters"
                )
            blocks.append(
                SourceBlock(
                    block_id=_new_block_id(len(blocks)),
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
            heading = match.group(2).strip()
            if len(heading) > MAX_SOURCE_BLOCK_CHARACTERS:
                raise UnsupportedContentError(
                    "source heading exceeds "
                    f"{MAX_SOURCE_BLOCK_CHARACTERS} characters at line {line_number}"
                )
            blocks.append(
                SourceBlock(
                    block_id=_new_block_id(len(blocks)),
                    kind=SourceBlockKind.HEADING,
                    text=heading,
                    ordinal=len(blocks),
                )
            )
            continue
        _reject_inline_or_block_markup(line, line_number)
        pending.append(line)

    flush_paragraph()
    if not blocks:
        raise UnsupportedContentError("source contains no supported heading or paragraph blocks")
    return tuple(blocks)
