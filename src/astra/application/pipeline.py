"""Astra decision pipeline coordinator.

Orchestrates state loading, reducer transitions, fast tier assessment, escalation policy,
anti-loop checks, and state persistence.
"""

import time
import uuid
from typing import Any, Optional
import structlog

from astra.domain.events import AstraEvent, EventType
from astra.domain.intervention import evaluate_anti_loop_policy, record_intervention
from astra.domain.model_ports import CostMetadata
from astra.domain.modes import Mode, decide_mode
from astra.domain.persistence_ports import TrajectoryStateStore
from astra.domain.trajectory import (
    TrajectoryState,
    create_initial_trajectory,
    reduce_trajectory,
)
from astra.integration.antigravity.response_format import AssistPayload, HookResponseEnvelope
from astra.tiers.fast.assessor import FastTierAssessor

logger = structlog.get_logger(__name__)


class DecisionPipeline:
    """Decision pipeline coordinator."""

    def __init__(
        self,
        state_store: TrajectoryStateStore,
        fast_assessor: FastTierAssessor,
        deep_orchestrator: Optional[Any] = None,  # Injected in Milestone 5
        max_session_interventions: int = 5,
        max_forced_continuations_per_signature: int = 2,
        anti_loop_cooldown_seconds: float = 30.0,
    ):
        self.state_store = state_store
        self.fast_assessor = fast_assessor
        self.deep_orchestrator = deep_orchestrator
        self.max_session_interventions = max_session_interventions
        self.max_forced_continuations_per_signature = max_forced_continuations_per_signature
        self.anti_loop_cooldown_seconds = anti_loop_cooldown_seconds

    async def process_event(self, event: AstraEvent) -> HookResponseEnvelope:
        """Processes an incoming normalized AstraEvent through the decision pipeline."""
        start_time = time.perf_counter()

        # 1. Load state from persistence
        state = await self.state_store.load(event.session_id)
        if state is None:
            state = create_initial_trajectory(event.session_id, timestamp_ms=event.received_at)
            logger.info("new_session_initialized", session_id=event.session_id)

        # 2. Update trajectory state purely
        state = reduce_trajectory(state, event)

        # 3. Fast Tier Assessment
        assessment = await self.fast_assessor.assess(event=event, state=state)
        signals = assessment.signals
        total_cost = assessment.cost

        # 4. Escalation Policy
        try:
            current_mode = Mode(state.current_mode)
        except ValueError:
            current_mode = Mode.SHADOW

        mode_decision = decide_mode(
            current_mode=current_mode,
            signals=signals,
            state=state,
            max_session_interventions=self.max_session_interventions,
        )
        state.current_mode = mode_decision.new_mode.value

        decision_str = "continue"
        reason_str = None
        assist_payload = None
        intervention_id = None

        # 5. Handle Mode Action
        if mode_decision.new_mode == Mode.SHADOW:
            decision_str = "continue"
            reason_str = None

        elif mode_decision.new_mode == Mode.ASSIST:
            if self.deep_orchestrator:
                # Deep tier investigation in Milestone 5
                deep_res = await self.deep_orchestrator.investigate(
                    event=event, state=state, triggering_signal=mode_decision.primary_signal
                )
                assist_payload = deep_res.assist_payload
                total_cost = deep_res.total_cost
            else:
                # Fallback rule-based assist
                sig = mode_decision.primary_signal
                assist_payload = AssistPayload(
                    message=sig.rationale if sig else "Astra recommendation: verify recent changes.",
                    confidence=sig.confidence if sig else 0.8,
                )
            decision_str = "continue"

        elif mode_decision.new_mode == Mode.INTERVENE:
            sig = mode_decision.primary_signal
            sig_hash = sig.failure_signature_hash if sig else None

            # 6. Anti-Loop Safety Check
            anti_loop = evaluate_anti_loop_policy(
                state=state,
                failure_signature_hash=sig_hash,
                max_forced_continuations_per_sig=self.max_forced_continuations_per_signature,
                cooldown_seconds=self.anti_loop_cooldown_seconds,
                current_time_ms=event.received_at,
            )

            if anti_loop.allow_forced_continuation:
                intervention_id = str(uuid.uuid4())
                decision_str = "block_stop" if event.event_type == EventType.STOP else "continue"
                reason_str = sig.rationale if sig else "Action requires verification before completion."

                # Record intervention
                state = record_intervention(
                    state=state,
                    intervention_id=intervention_id,
                    mode=Mode.INTERVENE.value,
                    trigger_signal=sig.type.value if sig else "UNKNOWN",
                    message=reason_str,
                    was_forced_continuation=(event.event_type == EventType.STOP),
                    failure_signature_hash=sig_hash,
                    timestamp_ms=event.received_at,
                )
            elif anti_loop.action == "surface_to_user":
                decision_str = "continue"
                reason_str = anti_loop.user_surfaced_message
                logger.warning("anti_loop_cap_reached_surfacing_to_user", session_id=event.session_id, hash=sig_hash)
            else:
                decision_str = "continue"
                reason_str = "Cooldown active; allowing agent stop."

        # 7. Persist state
        await self.state_store.save(state)

        total_latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_cost.latency_ms = total_latency_ms

        logger.info(
            "pipeline_event_processed",
            session_id=event.session_id,
            event_type=event.event_type.value,
            mode=state.current_mode,
            decision=decision_str,
            latency_ms=total_latency_ms,
            correlation_id=event.correlation_id,
        )

        return HookResponseEnvelope(
            decision=decision_str,
            reason=reason_str,
            assist=assist_payload,
            intervention_id=intervention_id,
            mode=state.current_mode,
            confidence=mode_decision.primary_signal.confidence if mode_decision.primary_signal else None,
            bounded_cost=total_cost,
            correlation_id=event.correlation_id,
        )
