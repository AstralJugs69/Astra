"""Stable block-aware source diff with bounded context."""

from __future__ import annotations

from braille_errata_relay.domain.models import NormalizedSource, SourceDiff


def diff_sources(old: NormalizedSource, new: NormalizedSource) -> SourceDiff:
    old_by_id = {block.block_id: block for block in old.blocks}
    new_by_id = {block.block_id: block for block in new.blocks}
    ordered_ids = [block.block_id for block in new.blocks] + [
        block.block_id for block in old.blocks if block.block_id not in new_by_id
    ]
    changed_ids = tuple(
        block_id
        for block_id in ordered_ids
        if block_id not in old_by_id
        or block_id not in new_by_id
        or old_by_id[block_id] != new_by_id[block_id]
    )
    changed_set = set(changed_ids)
    old_changed = tuple(block for block in old.blocks if block.block_id in changed_set)
    new_changed = tuple(block for block in new.blocks if block.block_id in changed_set)

    context_ids: set[str] = set()
    for blocks in (old.blocks, new.blocks):
        for index, block in enumerate(blocks):
            if block.block_id in changed_set:
                for neighbor in blocks[max(0, index - 1) : index + 2]:
                    if neighbor.block_id not in changed_set:
                        context_ids.add(neighbor.block_id)
    context = tuple(block for block in new.blocks + old.blocks if block.block_id in context_ids)
    return SourceDiff(
        old_source_sha256=old.normalized_source_sha256,
        new_source_sha256=new.normalized_source_sha256,
        changed_block_ids=changed_ids,
        old_blocks=old_changed,
        new_blocks=new_changed,
        context_blocks=context,
    )


def evidence_span_ids(source_diff: SourceDiff) -> tuple[str, ...]:
    return tuple(
        [f"old:{block.block_id}" for block in source_diff.old_blocks]
        + [f"new:{block.block_id}" for block in source_diff.new_blocks]
    )
