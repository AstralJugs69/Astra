"""Unit tests for pure evidence budgeting and packet assembly."""

from astra.domain.evidence import (
    EvidenceItem,
    EvidenceSource,
    assemble_evidence_packet,
    estimate_tokens,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_assemble_evidence_packet_deduplication_and_sorting():
    items = [
        EvidenceItem(
            id="1",
            source=EvidenceSource.TEST_OUTPUT,
            reference="tests/unit/test_app.py",
            content="test failure output line 1\nline 2",
            relevance_score=0.9,
        ),
        # Duplicate of reference
        EvidenceItem(
            id="2",
            source=EvidenceSource.TEST_OUTPUT,
            reference="tests/unit/test_app.py",
            content="older output",
            relevance_score=0.5,
        ),
        EvidenceItem(
            id="3",
            source=EvidenceSource.CHANGED_FILE_SLICE,
            reference="src/app.py:10-20",
            content="def fix(): pass",
            relevance_score=0.8,
        ),
    ]

    packet = assemble_evidence_packet(
        task="Fix the failing test in app",
        trajectory_summary="Agent edited src/app.py",
        candidate_items=items,
        token_budget=1000,
    )

    # Must contain 2 unique items (deduplicated by source + reference)
    assert len(packet.items) == 2
    # Highest relevance must come first
    assert packet.items[0].id == "1"
    assert packet.items[1].id == "3"
    assert packet.token_used <= packet.token_budget


def test_assemble_evidence_packet_clamps_to_budget():
    large_content = "X" * 4000  # ~1000 tokens
    item1 = EvidenceItem(
        id="1",
        source=EvidenceSource.TEST_OUTPUT,
        reference="ref1",
        content=large_content,
        relevance_score=0.9,
    )
    item2 = EvidenceItem(
        id="2",
        source=EvidenceSource.TRANSCRIPT_SLICE,
        reference="ref2",
        content=large_content,
        relevance_score=0.4,
    )

    # Budget strictly 500 tokens
    packet = assemble_evidence_packet(
        task=None,
        trajectory_summary="",
        candidate_items=[item1, item2],
        token_budget=500,
    )

    assert packet.token_used <= 500
    assert len(packet.items) == 1
    assert "...[truncated to budget]" in packet.items[0].content
