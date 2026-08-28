from __future__ import annotations

from braille_errata_relay.braille.normalize import normalize_source_bytes


def test_heading_rename_and_paragraph_insertion_carry_opaque_ids() -> None:
    old = normalize_source_bytes(
        b"# Biology\n\nThe nucleus stores DNA.\n\nCells need energy.\n",
        document_id="fixture",
    )
    new = normalize_source_bytes(
        b"# Cells\n\nA new introductory sentence.\n\nThe nucleus stores DNA.\n\nCells need energy.\n",
        document_id="fixture",
        previous=old,
    )

    assert new.blocks[0].block_id == old.blocks[0].block_id
    assert new.blocks[2].block_id == old.blocks[1].block_id
    assert new.blocks[3].block_id == old.blocks[2].block_id
    assert new.blocks[1].block_id not in {block.block_id for block in old.blocks}
    assert all(block.block_id.startswith("block-") for block in new.blocks)


def test_changed_block_keeps_its_allocated_id_when_revision_is_supplied() -> None:
    old = normalize_source_bytes(b"# Biology\n\nThe nucleus stores DNA.\n", document_id="fixture")
    new = normalize_source_bytes(
        b"# Biology\n\nThe nucleus stores genetic material.\n",
        document_id="fixture",
        previous=old,
    )
    assert new.blocks[1].block_id == old.blocks[1].block_id
