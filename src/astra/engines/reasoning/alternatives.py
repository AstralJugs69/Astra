"""Alternative Ranker Engine (Model-Laziness Mitigation).

Generates and ranks multiple candidate approaches with risk/complexity trade-offs
when critique identifies unexplored solution space.
"""

from typing import List, Optional
from pydantic import BaseModel, Field
import structlog

from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata, ModelProvider
from astra.domain.reasoning_ports import (
    EngineResult,
    EngineVerdict,
    RankedAlternative,
)
from astra.domain.trajectory import TrajectoryState
from astra.engines.base import BaseEngine

logger = structlog.get_logger(__name__)


class ModelAlternativeOutput(BaseModel):
    """Pydantic schema for LLM structured alternative generation."""

    alternatives: List[RankedAlternative] = Field(description="2 to 3 ranked distinct candidate solutions")
    confidence: float = Field(description="Confidence in alternative rankings")


class AlternativeRanker(BaseEngine):
    """Generates and ranks alternative architectural or fix strategies."""

    async def run(
        self,
        evidence: EvidencePacket,
        state: TrajectoryState,
    ) -> EngineResult:
        """Generates ranked solution alternatives."""
        prompt = (
            f"You are the Astra Alternative Ranker.\n"
            f"Task: {evidence.task or state.task or 'Current coding task'}\n"
            f"Current Direction/Hypothesis: {state.current_hypothesis or 'First instinct'}\n\n"
            f"Trajectory Summary:\n{evidence.trajectory_summary}\n\n"
            f"Generate 2 to 3 distinct, actionable alternative strategies to solve this problem.\n"
            f"For each alternative, provide risk assessment, complexity (low/medium/high), and rationale."
        )

        try:
            output, cost = await self.model_provider.generate_structured(
                prompt=prompt,
                response_schema=ModelAlternativeOutput,
                model_name=self.model_name,
                timeout_seconds=self.timeout_seconds,
                system_instruction=(
                    "You are an expert software architect providing multiple distinct alternative solutions. "
                    "Help the agent escape local minima and model laziness."
                ),
            )

            return EngineResult(
                engine_name="alternative_ranker",
                verdict=EngineVerdict.ALTERNATIVES_RANKED,
                alternatives=output.alternatives,
                confidence=output.confidence,
                bounded_cost=cost,
                routing_recommendation="return_to_main_agent",
            )

        except Exception as exc:
            logger.error("alternative_ranker_failed", error=str(exc))
            return EngineResult(
                engine_name="alternative_ranker",
                verdict=EngineVerdict.ALTERNATIVES_RANKED,
                alternatives=[],
                confidence=0.5,
                bounded_cost=CostMetadata(tier_invoked="deep", latency_ms=100),
                routing_recommendation="return_to_main_agent",
            )
