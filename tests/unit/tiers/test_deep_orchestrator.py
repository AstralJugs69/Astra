"""Unit tests for Deep Tier Orchestrator."""

import pytest
from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.domain.evidence import EvidenceItem, EvidenceSource
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import CritiquePayload, CritiqueSeverity, CritiqueType, EngineResult, EngineVerdict
from astra.domain.signals import Signal, SignalType
from astra.domain.trajectory import create_initial_trajectory
from astra.tiers.deep.orchestrator import DeepTierOrchestrator


class FakeEvidenceRetriever:
    async def retrieve(self, requests, workspace_path=None):
        return [
            EvidenceItem(
                id="item-1",
                source=EvidenceSource.TEST_OUTPUT,
                reference="test_app.py",
                content="FAILED test_app - AssertionError",
                relevance_score=0.9,
            )
        ]


class FakeVerifier:
    async def run(self, evidence, state):
        return EngineResult(
            engine_name="bugfix_verifier",
            verdict=EngineVerdict.NOT_VERIFIED,
            critique=CritiquePayload(
                type=CritiqueType.INSUFFICIENT_VERIFICATION,
                severity=CritiqueSeverity.HIGH,
                claim_under_review="Done",
                supporting_observation="Tests failed",
                why_problematic="Unverified",
                missing_information="Run pytest",
                suggested_next_action="Run tests",
            ),
            bounded_cost=CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=100, tokens_out=30, latency_ms=200),
        )


class FakeCritic:
    async def run(self, evidence, state):
        return EngineResult(
            engine_name="reasoning_critic",
            verdict=EngineVerdict.CRITIQUE_ONLY,
            critique=CritiquePayload(
                type=CritiqueType.UNSUPPORTED_ASSUMPTION,
                severity=CritiqueSeverity.MEDIUM,
                claim_under_review="Assumption",
                supporting_observation="Observation",
                why_problematic="Unclear",
                missing_information="More data",
                suggested_next_action="Check logs",
            ),
            bounded_cost=CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=100, tokens_out=30, latency_ms=200),
        )


class FakeRanker:
    async def run(self, evidence, state):
        return EngineResult(
            engine_name="alternative_ranker",
            verdict=EngineVerdict.ALTERNATIVES_RANKED,
            alternatives=[],
            bounded_cost=CostMetadata(tier_invoked="deep", model_calls=1, tokens_in=100, tokens_out=30, latency_ms=200),
        )


@pytest.mark.asyncio
async def test_deep_orchestrator_routes_verification_trigger_to_verifier():
    orchestrator = DeepTierOrchestrator(
        evidence_retriever=FakeEvidenceRetriever(),
        bugfix_verifier=FakeVerifier(),
        reasoning_critic=FakeCritic(),
        alternative_ranker=FakeRanker(),
    )

    event = AstraEvent(
        event_id="e1",
        session_id="s1",
        event_type=EventType.STOP,
        received_at=1000,
        correlation_id="c1",
    )
    state = create_initial_trajectory("s1")
    sig = Signal(
        type=SignalType.PREMATURE_TERMINATION,
        confidence=0.95,
        suggested_mode="INTERVENE",
    )

    res = await orchestrator.investigate(event, state, triggering_signal=sig)
    assert res.engine_result.engine_name == "bugfix_verifier"
    assert res.assist_payload is not None
    assert "Astra Reasoning Critique" in res.assist_payload.message
