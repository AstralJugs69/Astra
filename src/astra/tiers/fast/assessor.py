"""Fast Tier signal assessor.

Orchestrates pure rule detectors first (0ms LLM latency), and falls back to a fast,
lightweight structured model call only for ambiguous cases.
"""

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import structlog

from astra.domain.events import AstraEvent
from astra.domain.model_ports import CostMetadata, ModelProvider
from astra.domain.signals import Signal, SignalType, run_all_rule_detectors
from astra.domain.trajectory import EvidenceRef, TrajectoryState

logger = structlog.get_logger(__name__)


class FastModelSignalOutput(BaseModel):
    """Pydantic schema for structured fast tier model classification."""

    signal_type: str
    confidence: float
    rationale: str
    suggested_mode: str  # "SHADOW", "ASSIST", "INTERVENE"


class FastAssessmentResult(BaseModel):
    """Result of a fast tier assessment pass."""

    signals: List[Signal]
    cost: CostMetadata


class FastTierAssessor:
    """Evaluates events rapidly using rules first, then optional cheap model classification."""

    def __init__(
        self,
        model_provider: Optional[ModelProvider] = None,
        fast_model: str = "gemini-2.5-flash",
        timeout_seconds: float = 2.0,
    ):
        self.model_provider = model_provider
        self.fast_model = fast_model
        self.timeout_seconds = timeout_seconds

    async def assess(
        self,
        event: AstraEvent,
        state: TrajectoryState,
        repeated_failures_threshold: int = 2,
        repeated_edits_threshold: int = 3,
    ) -> FastAssessmentResult:
        """Assesses an event and trajectory state for risk signals."""
        # 1. Run pure rule detectors
        rule_signals = run_all_rule_detectors(
            state=state,
            event=event,
            repeated_failures_threshold=repeated_failures_threshold,
            repeated_edits_threshold=repeated_edits_threshold,
        )

        cost = CostMetadata(tier_invoked="fast", model_calls=0)

        # If definitive high-confidence rule signals detected, return immediately
        if any(s.confidence >= 0.85 for s in rule_signals):
            return FastAssessmentResult(signals=rule_signals, cost=cost)

        # If no rule signals and no model provider configured, return empty
        if not self.model_provider:
            return FastAssessmentResult(signals=rule_signals, cost=cost)

        # 2. Optional model-assisted classification for ambiguous cases
        # Only invoke model if tool call had an error or state has repeated edits
        if event.is_tool_failure or len(state.actions_taken) > 3:
            prompt = (
                f"Assess the following coding agent event for reasoning or verification risks.\n"
                f"Event Type: {event.event_type.value}\n"
                f"Tool: {event.tool.name if event.tool else 'None'}\n"
                f"Arguments: {event.tool.arguments_summary if event.tool else ''}\n"
                f"Error: {event.error or (event.tool.had_error if event.tool else False)}\n"
                f"Recent Actions Count: {len(state.actions_taken)}\n"
                f"Recent Failures: {state.failure_count}\n"
                f"Identify if there is MISSING_EVIDENCE, REPEATED_FAILURE, or UNSUPPORTED_CLAIM."
            )

            try:
                model_output, model_cost = await self.model_provider.generate_structured(
                    prompt=prompt,
                    response_schema=FastModelSignalOutput,
                    model_name=self.fast_model,
                    timeout_seconds=self.timeout_seconds,
                    system_instruction="You are Astra Fast Assessor. Rapidly classify agent risks into structured JSON.",
                    tier="fast",
                )

                if model_output.confidence >= 0.7:
                    try:
                        sig_type = SignalType(model_output.signal_type)
                    except ValueError:
                        sig_type = SignalType.MISSING_EVIDENCE

                    model_signal = Signal(
                        type=sig_type,
                        confidence=model_output.confidence,
                        source="model",
                        suggested_mode=model_output.suggested_mode,
                        rationale=model_output.rationale,
                    )
                    rule_signals.append(model_signal)

                cost = model_cost

            except Exception as exc:
                logger.warning("fast_tier_model_assessment_fallback", error=str(exc))
                # Fall back to rule signals only (fail open)

        return FastAssessmentResult(signals=rule_signals, cost=cost)
