"""Unit tests for Reasoning Critic engine."""

import pytest
from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import CritiqueSeverity, CritiqueType, EngineVerdict
from astra.domain.trajectory import create_initial_trajectory
from astra.engines.reasoning.critic import ModelCritiqueOutput, ReasoningCritic


class FakeModelProvider:
    def __init__(self, output: ModelCritiqueOutput):
        self.output = output

    async def generate_structured(self, prompt, response_schema, **kwargs):
        return self.output, CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=250, tokens_out=70, latency_ms=500)


@pytest.mark.asyncio
async def test_reasoning_critic_emits_structured_critique():
    fake_output = ModelCritiqueOutput(
        has_weakness=True,
        critique_type=CritiqueType.UNSUPPORTED_ASSUMPTION,
        severity=CritiqueSeverity.HIGH,
        claim_under_review="Cookie is absent because of SameSite setting",
        supporting_observation="Cookie is not set in browser response",
        why_problematic="Does not eliminate domain mismatch or secure flag requirement",
        missing_information="Inspect browser rejection log",
        suggested_next_action="Check network response headers directly",
        confidence=0.9,
    )
    critic = ReasoningCritic(model_provider=FakeModelProvider(fake_output))

    packet = EvidencePacket(task="Fix auth cookie", trajectory_summary="Hypothesis set")
    state = create_initial_trajectory("s1")

    result = await critic.run(packet, state)
    assert result.verdict == EngineVerdict.CRITIQUE_ONLY
    assert result.critique is not None
    assert result.critique.type == CritiqueType.UNSUPPORTED_ASSUMPTION
    assert "SameSite" in result.critique.claim_under_review
