"""Bugfix Verification Engine (Evidence-First Debugging Protocol).

Audits whether a claimed fix is backed by an actual passing verification step,
not merely a code change.
"""

from typing import List, Optional
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


class BugfixVerificationOutput(BaseModel):
    """Structured LLM output for bugfix verification audit."""

    is_verified: bool = Field(description="True if fix is definitively proven by passing verification steps")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    evidence_soundness_reason: str = Field(description="Explanation of why verification is sufficient or insufficient")
    missing_verification: Optional[str] = Field(default=None, description="What verification step is missing if unverified")


class BugfixVerifier(BaseEngine):
    """Audits code changes against verification history to prevent premature completion."""

    async def run(
        self,
        evidence: EvidencePacket,
        state: TrajectoryState,
    ) -> EngineResult:
        """Audits verification evidence for the agent's task."""
        evidence_summary_lines = []
        for item in evidence.items:
            evidence_summary_lines.append(
                f"- Source: {item.source.value} | Ref: {item.reference}\n  Content:\n{item.content[:500]}"
            )
        evidence_str = "\n".join(evidence_summary_lines)

        prompt = (
            f"You are the Astra Bugfix Verifier enforcing the Evidence-First Debugging Protocol.\n"
            f"Agent Task: {evidence.task or state.task or 'Solve the assigned development/verification task'}\n\n"
            f"Trajectory Summary:\n{evidence.trajectory_summary}\n\n"
            f"Verification & Workspace Evidence:\n{evidence_str}\n\n"
            f"Audit whether the claimed work/fix is backed by actual passing verification.\n"
            f"- If the evidence shows that a test suite or verification command was executed and passed with zero failures, is_verified must be True.\n"
            f"- If the agent edited code but never ran any verification check, or if tests failed, is_verified must be False."
        )

        try:
            output, cost = await self.model_provider.generate_structured(
                prompt=prompt,
                response_schema=BugfixVerificationOutput,
                model_name=self.model_name,
                timeout_seconds=self.timeout_seconds,
                system_instruction=(
                    "You are a rigorous code verification auditor. Determine whether a fix is "
                    "empirically proven by passing test executions. Be strict and objective."
                ),
            )

            if output.is_verified:
                return EngineResult(
                    engine_name="bugfix_verifier",
                    verdict=EngineVerdict.VERIFIED,
                    confidence=output.confidence,
                    evidence_citations=state.evidence_gathered[-3:],
                    bounded_cost=cost,
                    routing_recommendation="return_to_main_agent",
                )
            else:
                critique = CritiquePayload(
                    type=CritiqueType.INSUFFICIENT_VERIFICATION,
                    severity=CritiqueSeverity.HIGH,
                    claim_under_review="Bug is resolved and verified",
                    supporting_observation=output.evidence_soundness_reason,
                    why_problematic="Agent is completing work without proving the fix works against test suite.",
                    missing_information=output.missing_verification or "Run test suite to verify changes.",
                    suggested_next_action="Execute test verification command before terminating.",
                )
                return EngineResult(
                    engine_name="bugfix_verifier",
                    verdict=EngineVerdict.NOT_VERIFIED,
                    critique=critique,
                    confidence=output.confidence,
                    evidence_citations=state.evidence_gathered[-3:],
                    bounded_cost=cost,
                    routing_recommendation="return_to_main_agent",
                )

        except Exception as exc:
            logger.error("bugfix_verifier_failed", error=str(exc))
            # Fallback on failure
            return EngineResult(
                engine_name="bugfix_verifier",
                verdict=EngineVerdict.NOT_VERIFIED,
                confidence=0.5,
                bounded_cost=CostMetadata(tier_invoked="deep", latency_ms=100),
                routing_recommendation="return_to_main_agent",
            )
