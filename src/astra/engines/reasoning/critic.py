"""Reasoning Critic Engine.

Audits the agent's reasoning trajectory for unsupported assumptions, missing evidence,
unexplored alternatives, weak inferences, and premature convergence.
"""

from typing import Optional
from pydantic import BaseModel, Field
import structlog

from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata, ModelProvider
from astra.domain.reasoning_ports import (
    CritiquePayload,
    CritiqueSeverity,
    CritiqueType,
    EngineResult,
    EngineVerdict,
)
from astra.domain.trajectory import TrajectoryState
from astra.engines.base import BaseEngine

logger = structlog.get_logger(__name__)


class ModelCritiqueOutput(BaseModel):
    """Pydantic schema for LLM structured critique output."""

    has_weakness: bool = Field(description="True if a substantive epistemic or reasoning weakness is identified")
    critique_type: CritiqueType = Field(description="Classification of the flaw")
    severity: CritiqueSeverity = Field(description="Severity of the reasoning flaw")
    claim_under_review: str = Field(description="The specific claim or conclusion made by the agent")
    supporting_observation: str = Field(description="Observable evidence supporting or contradicting the claim")
    why_problematic: str = Field(description="Why this flaw matters or risks failure")
    missing_information: str = Field(description="What evidence, premise, or alternative is missing")
    suggested_next_action: str = Field(description="Concrete recommended next step for the agent")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")


class ReasoningCritic(BaseEngine):
    """Critiques the observable justification and hypothesis evolution in the trajectory."""

    async def run(
        self,
        evidence: EvidencePacket,
        state: TrajectoryState,
    ) -> EngineResult:
        """Runs structured reasoning critique on the agent trajectory."""
        recent_actions = [
            f"- Step {a.step_index or '?'}: Tool={a.tool_name} Args={a.arguments_summary[:100]} Error={a.had_error}"
            for a in state.actions_taken[-8:]
        ]
        actions_str = "\n".join(recent_actions) or "No actions recorded."

        cp = evidence.checkpoint
        phase_str = cp.epistemic_phase.value if cp else state.epistemic_phase.value
        claims_str = ", ".join(cp.active_claims) if cp and cp.active_claims else "None stated"
        assumptions_str = ", ".join(cp.assumptions) if cp and cp.assumptions else "None stated"

        prompt = (
            f"You are the Astra Reasoning Critic auditing an agentic pair-programmer.\n"
            f"Task: {evidence.task or state.task or 'Current coding task'}\n"
            f"Epistemic Phase: {phase_str}\n"
            f"Hypothesis Under Review: {state.current_hypothesis or 'Unstated/Implicit'}\n"
            f"Active Claims: {claims_str}\n"
            f"Assumptions: {assumptions_str}\n\n"
            f"Recent Actions Taken:\n{actions_str}\n\n"
            f"Evidence Packet:\n{evidence.trajectory_summary}\n\n"
            f"Evaluate whether the agent's current conclusion/hypothesis is sufficiently justified.\n"
            f"Identify if there is an unsupported assumption, missing alternative, invalid inference, "
            f"premature convergence, or contradiction with observed tool output."
        )

        try:
            output, cost = await self.model_provider.generate_structured(
                prompt=prompt,
                response_schema=ModelCritiqueOutput,
                model_name=self.model_name,
                timeout_seconds=self.timeout_seconds,
                system_instruction=(
                    "You are a companion reasoning critic. Your goal is NOT to disagree unnecessarily, "
                    "but to identify whether conclusions are rigorously justified by evidence. Be constructive and specific."
                ),
                tier="deep",
            )

            if output.has_weakness:
                critique = CritiquePayload(
                    type=output.critique_type,
                    severity=output.severity,
                    claim_under_review=output.claim_under_review,
                    supporting_observation=output.supporting_observation,
                    why_problematic=output.why_problematic,
                    missing_information=output.missing_information,
                    suggested_next_action=output.suggested_next_action,
                )
                return EngineResult(
                    engine_name="reasoning_critic",
                    verdict=EngineVerdict.CRITIQUE_ONLY,
                    critique=critique,
                    confidence=output.confidence,
                    bounded_cost=cost,
                    routing_recommendation="return_to_main_agent",
                )
            else:
                return EngineResult(
                    engine_name="reasoning_critic",
                    verdict=EngineVerdict.VERIFIED,
                    confidence=output.confidence,
                    bounded_cost=cost,
                    routing_recommendation="return_to_main_agent",
                )

        except Exception as exc:
            logger.error("reasoning_critic_failed", error=str(exc))
            return EngineResult(
                engine_name="reasoning_critic",
                verdict=EngineVerdict.CRITIQUE_ONLY,
                confidence=0.5,
                bounded_cost=CostMetadata(tier_invoked="deep", latency_ms=100),
                routing_recommendation="return_to_main_agent",
            )
