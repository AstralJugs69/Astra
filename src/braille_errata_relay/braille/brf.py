"""Explicit six-dot North American Braille ASCII serialization."""

from __future__ import annotations

import hashlib

from braille_errata_relay.domain.models import TranslationProfile

from .errors import BraillePipelineError
from .formatter import BRAILLE_SPACE
from .paginator import BraillePage


def _dots(value: str) -> int:
    if value == "0":
        return 0
    result = 0
    for dot in value:
        result |= 1 << (int(dot) - 1)
    return result


# This is the explicit mapping represented by the pinned en-us-brf.dis table.
_DISPLAY_DOTS: dict[str, str] = {
    " ": "0",
    **dict(
        zip(
            "abcdefghijklmnopqrstuvwxyz",
            (
                "1",
                "12",
                "14",
                "145",
                "15",
                "124",
                "1245",
                "125",
                "24",
                "245",
                "13",
                "123",
                "134",
                "1345",
                "135",
                "1234",
                "12345",
                "1235",
                "234",
                "2345",
                "136",
                "1236",
                "2456",
                "1346",
                "13456",
                "1356",
            ),
        )
    ),
    "0": "356",
    "1": "2",
    "2": "23",
    "3": "25",
    "4": "256",
    "5": "26",
    "6": "235",
    "7": "2356",
    "8": "236",
    "9": "35",
    "'": "3",
    "@": "4",
    '"': "5",
    ",": "6",
    "*": "16",
    "/": "34",
    "-": "36",
    "^": "45",
    ".": "46",
    ";": "56",
    "<": "126",
    "%": "146",
    ":": "156",
    "[": "246",
    ">": "345",
    "+": "346",
    "_": "456",
    "$": "1246",
    "\\": "1256",
    "?": "1456",
    "!": "2346",
    "#": "3456",
    "&": "12346",
    "(": "12356",
    "]": "12456",
    ")": "23456",
    "=": "123456",
}
BRF_TO_DOTS = {_dots(dots): char for char, dots in _DISPLAY_DOTS.items()}
ALLOWED_BRF_BYTES = frozenset(ord(char) for char in BRF_TO_DOTS.values()) | {10, 13, 12}


def unicode_cells_to_brf(cells: str) -> str:
    output: list[str] = []
    for cell in cells:
        if cell == BRAILLE_SPACE:
            output.append(" ")
            continue
        codepoint = ord(cell)
        if not 0x2800 <= codepoint <= 0x283F:
            raise BraillePipelineError(f"cell is not a six-dot Unicode Braille pattern: {cell!r}")
        try:
            output.append(BRF_TO_DOTS[codepoint - 0x2800])
        except KeyError as exc:
            raise BraillePipelineError(
                f"six-dot pattern has no BRF mapping: U+{codepoint:04X}"
            ) from exc
    return "".join(output)


def serialize_pages(pages: tuple[BraillePage, ...], profile: TranslationProfile) -> bytes:
    rendered_pages: list[bytes] = []
    newline = bytes.fromhex(profile.newline_bytes_hex)
    separator = bytes.fromhex(profile.page_separator_hex)
    if newline not in (b"\n", b"\r\n") or separator != b"\x0c":
        raise BraillePipelineError("profile must use LF/CRLF and form-feed page separators")
    for page in pages:
        if len(page.lines) > profile.lines_per_page:
            raise BraillePipelineError(f"page {page.number} exceeds configured line count")
        rows: list[bytes] = []
        for line in page.lines:
            row = unicode_cells_to_brf(line.unicode_cells)
            if len(row) > profile.cells_per_line:
                raise BraillePipelineError(f"page {page.number} contains an over-width line")
            rows.append(row.ljust(profile.cells_per_line).encode("ascii"))
        rows.extend(
            b" " * profile.cells_per_line for _ in range(profile.lines_per_page - len(rows))
        )
        rendered_pages.append(newline.join(rows))
    result = separator.join(rendered_pages)
    if profile.final_page_separator:
        result += separator
    if any(byte not in ALLOWED_BRF_BYTES for byte in result):
        raise BraillePipelineError("serialized BRF contains a byte outside the declared allowlist")
    return result


def split_exact_brf(data: bytes, profile: TranslationProfile) -> tuple[bytes, ...]:
    separator = bytes.fromhex(profile.page_separator_hex)
    if not data or separator not in data and profile.final_page_separator:
        raise BraillePipelineError("BRF has no complete page")
    if any(byte not in ALLOWED_BRF_BYTES for byte in data):
        raise BraillePipelineError("BRF contains a byte outside the declared allowlist")
    pages = data.split(separator)
    if profile.final_page_separator:
        if pages[-1] != b"":
            raise BraillePipelineError("BRF is missing its configured final page separator")
        pages = pages[:-1]
    elif pages[-1] == b"":
        raise BraillePipelineError("BRF has an unexpected final page separator")
    expected_line_end = bytes.fromhex(profile.newline_bytes_hex)
    for number, page in enumerate(pages, start=1):
        rows = page.split(expected_line_end)
        if len(rows) != profile.lines_per_page:
            raise BraillePipelineError(
                f"page {number} has {len(rows)} lines; expected {profile.lines_per_page}"
            )
        if any(len(row) != profile.cells_per_line for row in rows):
            raise BraillePipelineError(f"page {number} contains an invalid row width")
    return tuple(pages)


def page_hashes(data: bytes, profile: TranslationProfile) -> tuple[str, ...]:
    return tuple(hashlib.sha256(page).hexdigest() for page in split_exact_brf(data, profile))
