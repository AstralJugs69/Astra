"""Strict UTF-8 and Markdown normalization with carried source-block lineage."""

from __future__ import annotations

import hashlib
import unicodedata
from difflib import SequenceMatcher

from braille_errata_relay.contracts.canonical_json import canonical_json_bytes
from braille_errata_relay.domain.models import NormalizedSource, SourceBlock

from .errors import UnsupportedContentError
from .parser import parse_markdown


def _carry_block_ids(
    previous: NormalizedSource,
    current: tuple[SourceBlock, ...],
) -> tuple[SourceBlock, ...]:
    """Carry allocated IDs across edits without deriving IDs from mutable text.

    Exact block matches anchor insertion/deletion. Remaining same-kind blocks
    are paired by nearest structural position so a heading rename or a changed
    paragraph retains its allocated ID. Newly inserted blocks receive fresh
    opaque IDs.
    """

    old = previous.blocks
    old_tokens = [(block.kind.value, block.text) for block in old]
    new_tokens = [(block.kind.value, block.text) for block in current]
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    pairs: dict[int, int] = {}

    matcher = SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for old_index, new_index in zip(range(old_start, old_end), range(new_start, new_end)):
            pairs[new_index] = old_index
            matched_old.add(old_index)
            matched_new.add(new_index)

    for new_index, new_block in enumerate(current):
        if new_index in matched_new:
            continue
        candidates = [
            old_index
            for old_index, old_block in enumerate(old)
            if old_index not in matched_old and old_block.kind == new_block.kind
        ]
        if not candidates:
            continue
        old_index = min(candidates, key=lambda index: (abs(index - new_index), index))
        pairs[new_index] = old_index
        matched_old.add(old_index)
        matched_new.add(new_index)

    used_ids = {block.block_id for block in old}
    numeric_ids = [
        int(block.block_id.removeprefix("block-"))
        for block in old
        if block.block_id.startswith("block-") and block.block_id.removeprefix("block-").isdigit()
    ]
    next_id = max(numeric_ids, default=0) + 1

    carried: list[SourceBlock] = []
    for new_index, block in enumerate(current):
        carried_old_index: int | None = pairs.get(new_index)
        if carried_old_index is not None:
            block_id = old[carried_old_index].block_id
        else:
            while f"block-{next_id:06d}" in used_ids:
                next_id += 1
            block_id = f"block-{next_id:06d}"
            used_ids.add(block_id)
            next_id += 1
        carried.append(block.model_copy(update={"block_id": block_id, "ordinal": new_index}))
    return tuple(carried)


def normalize_source_bytes(
    raw: bytes,
    *,
    document_id: str,
    previous: NormalizedSource | None = None,
) -> NormalizedSource:
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
    parsed_blocks = parse_markdown(canonical_text)
    blocks = _carry_block_ids(previous, parsed_blocks) if previous is not None else parsed_blocks
    block_text = "\n\n".join(block.text for block in blocks)
    canonical_blocks = [{"kind": block.kind.value, "text": block.text} for block in blocks]
    digest = hashlib.sha256(canonical_json_bytes(canonical_blocks)).hexdigest()
    return NormalizedSource(
        document_id=document_id,
        blocks=blocks,
        normalized_text=block_text,
        normalized_source_sha256=digest,
    )
