"""Pure reasoning engine ports and output schemas.

Zero I/O, zero framework imports. Defines the protocol for Astra reasoning engines
(Bugfix Verifier, Reasoning Critic, Alternative Ranker).
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Protocol
from pydantic import BaseModel, Field

from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata
from astra.domain.trajectory import EvidenceRef, TrajectoryState


class CritiqueType(str, Enum):
    UNSUPPORTED_ASSUMPTION = "unsupported_assumption"
    MISSING_EVIDENCE = "missing_evidence"
    MISSING_ALTERNATIVE = "missing_alternative"
    INVALID_INFERENCE = "invalid_inference"
    PREMATURE_CONVERGENCE = "premature_convergence"
    CONTRADICTION = "contradiction"
    INSUFFICIENT_VERIFICATION = "insufficient_verification"


class CritiqueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CritiquePayload(BaseModel):
    """Structured reasoning critique output."""

    type: CritiqueType
    severity: CritiqueSeverity
    claim_under_review: str
    supporting_observation: str
    why_problematic: str
    missing_information: str
    suggested_next_action: str


class RankedAlternative(BaseModel):
    """A ranked alternative solution approach."""

    rank: int
    title: str
    description: str
    rationale: str
    risk_assessment: str
    complexity: str  # "low", "medium", "high"


class EngineVerdict(str, Enum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    CRITIQUE_ONLY = "critique_only"
    ALTERNATIVES_RANKED = "alternatives_ranked"


class EngineResult(BaseModel):
    """Result of an engine reasoning pass."""

    engine_name: str
    verdict: EngineVerdict
    critique: Optional[CritiquePayload] = None
    alternatives: Optional[List[RankedAlternative]] = None
    confidence: float = 1.0
    evidence_citations: List[EvidenceRef] = Field(default_factory=list)
    bounded_cost: CostMetadata = Field(default_factory=CostMetadata)
    routing_recommendation: str = "return_to_main_agent"


class Engine(Protocol):
    """Protocol for reasoning engines."""

    async def run(
        self,
        evidence: EvidencePacket,
        state: TrajectoryState,
    ) -> EngineResult:
        """Executes a bounded single-pass reasoning assessment."""
        ...
