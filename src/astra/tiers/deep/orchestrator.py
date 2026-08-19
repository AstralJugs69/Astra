"""Deep Tier reasoning orchestrator.

Assembles bounded evidence packets, selects the appropriate reasoning engine,
and applies routing policies.
"""

from typing import List, Optional
from pydantic import BaseModel, Field
import structlog

from astra.domain.evidence import EvidenceSource, assemble_evidence_packet
from astra.domain.events import AstraEvent, EventType
from astra.domain.evidence_ports import EvidenceRetriever
from astra.domain.model_ports import CostMetadata
from astra.domain.reasoning_ports import EngineResult
from astra.domain.routing import RoutingMode, determine_routing
from astra.domain.signals import Signal, SignalType
from astra.domain.trajectory import EvidenceRef, TrajectoryState
from astra.engines.bugfix.verifier import BugfixVerifier
from astra.engines.reasoning.alternatives import AlternativeRanker
from astra.engines.reasoning.critic import ReasoningCritic
from astra.integration.antigravity.response_format import AssistPayload

logger = structlog.get_logger(__name__)


class DeepInvestigationResult(BaseModel):
    """Result of a deep tier investigation."""

    engine_result: EngineResult
    assist_payload: AssistPayload
    total_cost: CostMetadata


class DeepTierOrchestrator:
    """Orchestrates bounded evidence gathering and engine invocation."""

    def __init__(
        self,
        evidence_retriever: EvidenceRetriever,
        bugfix_verifier: BugfixVerifier,
        reasoning_critic: ReasoningCritic,
        alternative_ranker: AlternativeRanker,
        default_routing_mode: RoutingMode = RoutingMode.RETURN_TO_MAIN_AGENT,
        token_budget: int = 4000,
    ):
        self.evidence_retriever = evidence_retriever
        self.bugfix_verifier = bugfix_verifier
        self.reasoning_critic = reasoning_critic
        self.alternative_ranker = alternative_ranker
        self.routing_mode = default_routing_mode
        self.token_budget = token_budget

    async def investigate(
        self,
        event: AstraEvent,
        state: TrajectoryState,
        triggering_signal: Optional[Signal] = None,
    ) -> DeepInvestigationResult:
        """Executes a bounded deep reasoning investigation."""
        # 1. Determine evidence requests based on trigger and trajectory history
        evidence_requests: List[EvidenceRef] = []
        if triggering_signal:
            evidence_requests.extend(triggering_signal.evidence_refs)

        if event.transcript_ref:
            evidence_requests.append(
                EvidenceRef(
                    source_type=EvidenceSource.TRANSCRIPT_SLICE.value,
                    locator=event.transcript_ref,
                    summary="Transcript slice",
                )
            )

        # Include latest verification history so Verifier has test evidence
        if state.verification_history:
            for idx, ver in enumerate(state.verification_history[-3:]):
                evidence_requests.append(
                    EvidenceRef(
                        source_type=EvidenceSource.TEST_OUTPUT.value,
                        locator=f"verification_{idx}_{ver.command[:30]}",
                        summary=f"Command: {ver.command}\nOutcome: {ver.outcome.value}\nOutput:\n{ver.summary}",
                        timestamp=ver.timestamp,
                    )
                )

        # 2. Retrieve raw evidence items via adapter
        raw_items = await self.evidence_retriever.retrieve(
            requests=evidence_requests,
            workspace_path=event.workspace_path,
        )

        # 3. Assemble bounded evidence packet purely
        actions_summary = ", ".join(
            f"{a.tool_name}({a.arguments_summary[:40]})" for a in state.actions_taken[-5:]
        )
        ver_status = (
            f"Latest verification: {state.latest_verification.command} -> {state.latest_verification.outcome.value}"
            if state.latest_verification
            else "No verification run"
        )
        trajectory_summary = f"Actions: {actions_summary}\nVerification: {ver_status}\nFailures: {state.failure_count}"

        packet = assemble_evidence_packet(
            task=state.task or "Complete the assigned development task accurately",
            trajectory_summary=trajectory_summary,
            candidate_items=raw_items,
            token_budget=self.token_budget,
        )

        # 4. Engine Selection
        is_verification_trigger = (
            event.event_type == EventType.STOP
            or (triggering_signal and triggering_signal.type in [
                SignalType.PREMATURE_TERMINATION,
                SignalType.REPEATED_VERIFICATION_FAILURE,
                SignalType.UNSUPPORTED_SUCCESS_CLAIM,
            ])
        )

        if is_verification_trigger:
            engine_result = await self.bugfix_verifier.run(packet, state)
        else:
            engine_result = await self.reasoning_critic.run(packet, state)

        total_cost = engine_result.bounded_cost

        # 5. Apply Routing Policy
        routing_decision = determine_routing(
            engine_result=engine_result,
            configured_mode=self.routing_mode,
        )

        # 6. Optional Alternative Ranker execution if routed
        if routing_decision.should_execute_alternative_ranker:
            alt_result = await self.alternative_ranker.run(packet, state)
            engine_result.alternatives = alt_result.alternatives
            total_cost.model_calls += alt_result.bounded_cost.model_calls
            total_cost.tokens_in += alt_result.bounded_cost.tokens_in
            total_cost.tokens_out += alt_result.bounded_cost.tokens_out
            total_cost.latency_ms += alt_result.bounded_cost.latency_ms

            routing_decision = determine_routing(
                engine_result=engine_result,
                configured_mode=self.routing_mode,
            )

        assist_payload = AssistPayload(
            message=routing_decision.formatted_message,
            evidence_refs=engine_result.evidence_citations,
            confidence=engine_result.confidence,
            critique=engine_result.critique,
        )

        return DeepInvestigationResult(
            engine_result=engine_result,
            assist_payload=assist_payload,
            total_cost=total_cost,
        )
