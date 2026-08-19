"""Pure routing policy for deep tier reasoning outputs."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel

from astra.domain.reasoning_ports import EngineResult


class RoutingMode(str, Enum):
    RETURN_TO_MAIN_AGENT = "return_to_main_agent"
    ASTRA_REASONS_FURTHER = "astra_reasons_further"
    COMBINED = "combined"


class RoutingDecision(BaseModel):
    """Decision on how deep tier results should be surfaced."""

    mode: RoutingMode
    formatted_message: str
    should_execute_alternative_ranker: bool = False


def determine_routing(
    engine_result: EngineResult,
    configured_mode: RoutingMode = RoutingMode.RETURN_TO_MAIN_AGENT,
) -> RoutingDecision:
    """Determines whether to return critique to main agent or perform deeper investigation."""
    if engine_result.critique:
        critique = engine_result.critique
        msg = (
            f"Astra Reasoning Critique [{critique.severity.value.upper()}]: {critique.why_problematic}\n"
            f"- Missing Information: {critique.missing_information}\n"
            f"- Recommended Action: {critique.suggested_next_action}"
        )
        should_rank = (
            configured_mode in [RoutingMode.COMBINED, RoutingMode.ASTRA_REASONS_FURTHER]
            and critique.severity.value == "high"
        )
        return RoutingDecision(
            mode=configured_mode,
            formatted_message=msg,
            should_execute_alternative_ranker=should_rank,
        )

    elif engine_result.alternatives:
        alts_text = "\n".join(
            f"  {a.rank}. {a.title} ({a.complexity} complexity): {a.description}"
            for a in engine_result.alternatives
        )
        msg = f"Astra Alternative Approaches:\n{alts_text}"
        return RoutingDecision(
            mode=configured_mode,
            formatted_message=msg,
            should_execute_alternative_ranker=False,
        )

    return RoutingDecision(
        mode=configured_mode,
        formatted_message="Astra verification: work verified.",
        should_execute_alternative_ranker=False,
    )
