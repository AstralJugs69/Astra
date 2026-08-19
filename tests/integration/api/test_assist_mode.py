"""Integration tests for Assist mode pipeline execution."""

import pytest
from astra.application.pipeline import DecisionPipeline
from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.domain.evidence import EvidenceItem, EvidenceSource
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import CritiquePayload, CritiqueSeverity, CritiqueType, EngineResult, EngineVerdict
from astra.infrastructure.persistence.memory_store import InMemoryTrajectoryStore
from astra.tiers.deep.orchestrator import DeepTierOrchestrator
from astra.tiers.fast.assessor import FastTierAssessor


class FakeAssessor(FastTierAssessor):
    def __init__(self, signals):
        super().__init__(model_provider=None)
        self.preset_signals = signals

    async def assess(self, event, state, **kwargs):
        from astra.tiers.fast.assessor import FastAssessmentResult
        return FastAssessmentResult(signals=self.preset_signals, cost=CostMetadata(tier_invoked="fast"))


class FakeDeepOrchestrator:
    async def investigate(self, event, state, triggering_signal):
        from astra.integration.antigravity.response_format import AssistPayload
        from astra.tiers.deep.orchestrator import DeepInvestigationResult
        critique = CritiquePayload(
            type=CritiqueType.UNSUPPORTED_ASSUMPTION,
            severity=CritiqueSeverity.MEDIUM,
            claim_under_review="Test claim",
            supporting_observation="Obs",
            why_problematic="Unjustified premise",
            missing_information="Need verification",
            suggested_next_action="Run test suite",
        )
        engine_res = EngineResult(
            engine_name="reasoning_critic",
            verdict=EngineVerdict.CRITIQUE_ONLY,
            critique=critique,
        )
        assist = AssistPayload(
            message="Astra Guidance: Unjustified premise. Run test suite.",
            confidence=0.85,
            critique=critique,
        )
        return DeepInvestigationResult(
            engine_result=engine_res,
            assist_payload=assist,
            total_cost=CostMetadata(tier_invoked="deep", model_calls=1),
        )


@pytest.mark.asyncio
async def test_assist_mode_delivers_structured_guidance():
    store = InMemoryTrajectoryStore()
    from astra.domain.signals import Signal, SignalType
    sig = Signal(
        type=SignalType.SAME_FILE_REPEATED_EDITS,
        confidence=0.8,
        suggested_mode="ASSIST",
        rationale="Thrashing on file",
    )
    assessor = FakeAssessor([sig])
    orchestrator = FakeDeepOrchestrator()

    pipeline = DecisionPipeline(
        state_store=store,
        fast_assessor=assessor,
        deep_orchestrator=orchestrator,
    )

    event = AstraEvent(
        event_id="e-assist-1",
        session_id="session-assist",
        event_type=EventType.POST_TOOL_USE,
        step_index=4,
        tool=ToolCallSummary(name="replace_file_content", arguments_summary="src/app.py"),
        received_at=1000,
        correlation_id="c-assist-1",
    )

    resp = await pipeline.process_event(event)
    assert resp.mode == "ASSIST"
    assert resp.decision == "continue"
    assert resp.assist is not None
    assert "Astra Guidance" in resp.assist.message
    assert resp.assist.critique is not None
