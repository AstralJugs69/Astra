"""Response formatter translating domain decisions to Antigravity hook stdout JSON."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from astra.domain.events import EventType
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import CritiquePayload
from astra.domain.trajectory import EvidenceRef


class AssistPayload(BaseModel):
    """Structured assist message delivered to the agent."""

    message: str
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    confidence: float = 1.0
    critique: Optional[CritiquePayload] = None


class HookResponseEnvelope(BaseModel):
    """Full HTTP response returned by Astra POST /event."""

    decision: str  # "continue", "allow", "deny", "block_stop"
    reason: Optional[str] = None
    assist: Optional[AssistPayload] = None
    intervention_id: Optional[str] = None
    mode: str = "SHADOW"
    confidence: Optional[float] = None
    bounded_cost: CostMetadata = Field(default_factory=CostMetadata)
    correlation_id: str


def format_antigravity_stdout(
    event_type: EventType,
    response: HookResponseEnvelope,
) -> Dict[str, Any]:
    """Translates Astra's internal decision envelope to Antigravity's expected stdout shape."""
    if event_type == EventType.POST_TOOL_USE:
        # In Antigravity CLI, PostToolUse expects empty dict {}
        return {}

    # For Stop event:
    if response.decision in ["block_stop", "continue"] and response.reason:
        return {
            "decision": "continue",
            "reason": response.reason,
        }

    # Default allow stop
    return {
        "decision": "allow",
    }
