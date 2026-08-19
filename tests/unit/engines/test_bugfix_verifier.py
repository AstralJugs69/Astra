"""Unit tests for Bugfix Verifier engine."""

import pytest
from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import EngineVerdict
from astra.domain.trajectory import create_initial_trajectory
from astra.engines.bugfix.verifier import BugfixVerificationOutput, BugfixVerifier


class FakeModelProvider:
    def __init__(self, output: BugfixVerificationOutput):
        self.output = output

    async def generate_structured(self, prompt, response_schema, **kwargs):
        return self.output, CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=200, tokens_out=50, latency_ms=400)


@pytest.mark.asyncio
async def test_bugfix_verifier_returns_verified():
    fake_output = BugfixVerificationOutput(
        is_verified=True,
        confidence=0.95,
        evidence_soundness_reason="All 10 unit tests executed and passed.",
    )
    verifier = BugfixVerifier(model_provider=FakeModelProvider(fake_output))

    packet = EvidencePacket(task="Fix bug", trajectory_summary="Tests passed")
    state = create_initial_trajectory("s1")

    result = await verifier.run(packet, state)
    assert result.verdict == EngineVerdict.VERIFIED
    assert result.confidence == 0.95
    assert result.critique is None


@pytest.mark.asyncio
async def test_bugfix_verifier_returns_not_verified_with_critique():
    fake_output = BugfixVerificationOutput(
        is_verified=False,
        confidence=0.9,
        evidence_soundness_reason="Code was edited but test suite was not executed.",
        missing_verification="Run pytest tests/unit/",
    )
    verifier = BugfixVerifier(model_provider=FakeModelProvider(fake_output))

    packet = EvidencePacket(task="Fix bug", trajectory_summary="Code edited")
    state = create_initial_trajectory("s1")

    result = await verifier.run(packet, state)
    assert result.verdict == EngineVerdict.NOT_VERIFIED
    assert result.critique is not None
    assert result.critique.type.value == "insufficient_verification"
    assert "Run pytest" in result.critique.missing_information
