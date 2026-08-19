"""Unit tests for Alternative Ranker engine."""

import pytest
from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import EngineVerdict, RankedAlternative
from astra.domain.trajectory import create_initial_trajectory
from astra.engines.reasoning.alternatives import AlternativeRanker, ModelAlternativeOutput


class FakeModelProvider:
    def __init__(self, output: ModelAlternativeOutput):
        self.output = output

    async def generate_structured(self, prompt, response_schema, **kwargs):
        return self.output, CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=300, tokens_out=120, latency_ms=600)


@pytest.mark.asyncio
async def test_alternative_ranker_returns_ranked_solutions():
    alts = [
        RankedAlternative(
            rank=1,
            title="Update middleware cookie parser",
            description="Fix header parsing logic in auth middleware",
            rationale="Addresses root cause directly",
            risk_assessment="Low risk",
            complexity="low",
        ),
        RankedAlternative(
            rank=2,
            title="Bypass session cookie with bearer token",
            description="Refactor to Authorization header auth",
            rationale="More resilient architecture",
            risk_assessment="Medium risk",
            complexity="medium",
        ),
    ]
    fake_output = ModelAlternativeOutput(alternatives=alts, confidence=0.85)
    ranker = AlternativeRanker(model_provider=FakeModelProvider(fake_output))

    packet = EvidencePacket(task="Fix session bug", trajectory_summary="Exploring options")
    state = create_initial_trajectory("s1")

    result = await ranker.run(packet, state)
    assert result.verdict == EngineVerdict.ALTERNATIVES_RANKED
    assert len(result.alternatives) == 2
    assert result.alternatives[0].rank == 1
    assert result.alternatives[0].title == "Update middleware cookie parser"
