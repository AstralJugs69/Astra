"""Pure evidence budgeting and object models.

Zero I/O, zero framework imports. Represents bounded evidence packets assembled for Deep Tier engines.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from astra.domain.trajectory import ReasoningCheckpoint


class EvidenceSource(str, Enum):
    TRANSCRIPT_SLICE = "TRANSCRIPT_SLICE"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    CHANGED_FILE_SLICE = "CHANGED_FILE_SLICE"
    TEST_OUTPUT = "TEST_OUTPUT"
    WEB_SEARCH_RESULT = "WEB_SEARCH_RESULT"
    PRIOR_INTERVENTION = "PRIOR_INTERVENTION"


class EvidenceItem(BaseModel):
    """An individual piece of evidence fetched from workspace or session context."""

    id: str
    source: EvidenceSource
    reference: str  # e.g., file path, line range, turn index
    content: str = ""
    relevance_score: float = 1.0
    timestamp: int = 0
    provenance: str = ""
    token_estimate: int = 0


class EvidencePacket(BaseModel):
    """Bounded, prioritized packet of evidence supplied to a reasoning engine."""

    task: Optional[str] = None
    trajectory_summary: str = ""
    checkpoint: Optional[ReasoningCheckpoint] = None
    items: List[EvidenceItem] = Field(default_factory=list)
    token_budget: int = 4000
    token_used: int = 0


def estimate_tokens(text: str) -> int:
    """Heuristic token estimator (approx 4 characters per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def assemble_evidence_packet(
    task: Optional[str],
    trajectory_summary: str,
    candidate_items: List[EvidenceItem],
    token_budget: int = 4000,
    checkpoint: Optional[ReasoningCheckpoint] = None,
) -> EvidencePacket:
    """Pure assembler: prioritizes, deduplicates, and clamps candidate items to token budget."""
    base_cost = estimate_tokens(task or "") + estimate_tokens(trajectory_summary)
    remaining_budget = max(0, token_budget - base_cost)

    # Deduplicate candidate items by (source, reference)
    seen_refs = set()
    unique_items: List[EvidenceItem] = []
    for item in candidate_items:
        key = (item.source, item.reference)
        if key not in seen_refs:
            seen_refs.add(key)
            # Ensure item has token estimate computed
            if item.token_estimate <= 0:
                item.token_estimate = estimate_tokens(item.content)
            unique_items.append(item)

    # Sort by relevance_score descending
    sorted_items = sorted(unique_items, key=lambda x: x.relevance_score, reverse=True)

    packed_items: List[EvidenceItem] = []
    accumulated_tokens = base_cost

    for item in sorted_items:
        if item.token_estimate <= remaining_budget:
            packed_items.append(item)
            remaining_budget -= item.token_estimate
            accumulated_tokens += item.token_estimate
        elif remaining_budget > 50:
            # Partial inclusion: truncate content to fit remaining budget
            allowed_chars = remaining_budget * 4
            truncated_item = item.model_copy(deep=True)
            truncated_item.content = item.content[:allowed_chars] + "\n...[truncated to budget]"
            truncated_item.token_estimate = remaining_budget
            packed_items.append(truncated_item)
            accumulated_tokens += remaining_budget
            remaining_budget = 0
            break

    return EvidencePacket(
        task=task,
        trajectory_summary=trajectory_summary,
        checkpoint=checkpoint,
        items=packed_items,
        token_budget=token_budget,
        token_used=accumulated_tokens,
    )
