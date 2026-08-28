"""Strict UTF-8 and Markdown normalization."""

from __future__ import annotations

import hashlib
import unicodedata

from braille_errata_relay.domain.models import NormalizedSource

from .errors import UnsupportedContentError
from .parser import parse_markdown


def normalize_source_bytes(raw: bytes, *, document_id: str) -> NormalizedSource:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise UnsupportedContentError("UTF-8 byte-order marks are not supported")
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsupportedContentError("source is not valid UTF-8") from exc

    line_normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    unicode_normalized = unicodedata.normalize("NFC", line_normalized)
    normalized_lines = [line.rstrip(" \t") for line in unicode_normalized.split("\n")]
    while normalized_lines and not normalized_lines[-1]:
        normalized_lines.pop()
    canonical_text = "\n".join(normalized_lines)
    blocks = parse_markdown(canonical_text)
    block_text = "\n\n".join(block.text for block in blocks)
    digest = hashlib.sha256(block_text.encode("utf-8")).hexdigest()
    return NormalizedSource(
        document_id=document_id,
        blocks=blocks,
        normalized_text=block_text,
        normalized_source_sha256=digest,
    )

