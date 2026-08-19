"""Stop hook intervention handler.

Enforces verification auditing upon agent termination attempts and applies
anti-loop safety caps.
"""

import uuid
from typing import Optional, Tuple
import structlog

from astra.domain.events import AstraEvent, EventType, VerificationOutcome
from astra.domain.intervention import evaluate_anti_loop_policy, record_intervention
from astra.domain.modes import Mode
from astra.domain.reasoning_ports import EngineVerdict
from astra.domain.signals import Signal, SignalType, compute_failure_signature
from astra.domain.trajectory import TrajectoryState
from astra.integration.antigravity.response_format import HookResponseEnvelope
from astra.tiers.deep.orchestrator import DeepTierOrchestrator

logger = structlog.get_logger(__name__)


class StopHookHandler:
    """Specialized handler for Stop events enforcing verification and anti-loop protection."""

    def __init__(
        self,
        deep_orchestrator: DeepTierOrchestrator,
        max_forced_continuations_per_signature: int = 2,
        anti_loop_cooldown_seconds: float = 30.0,
    ):
        self.deep_orchestrator = deep_orchestrator
        self.max_forced_continuations = max_forced_continuations_per_signature
        self.anti_loop_cooldown = anti_loop_cooldown_seconds

    async def handle(
        self,
        event: AstraEvent,
        state: TrajectoryState,
        triggering_signal: Optional[Signal] = None,
    ) -> Tuple[HookResponseEnvelope, TrajectoryState]:
        """Audits stop attempt, runs deep verifier, and enforces anti-loop caps."""
        if event.event_type != EventType.STOP:
            raise ValueError(f"StopHookHandler called with non-stop event type: {event.event_type}")

        # Check if there are any unverified code modifications
        latest_passing_ver_ts = 0
        for ver in state.verification_history:
            if ver.outcome == VerificationOutcome.PASSED:
                latest_passing_ver_ts = max(latest_passing_ver_ts, ver.timestamp)

        unverified_edits = [
            a for a in state.actions_taken
            if a.tool_name in ["replace_file_content", "write_to_file", "edit_file", "multi_replace_file_content"]
            and a.timestamp > latest_passing_ver_ts
        ]

        has_failing_verification = (
            state.latest_verification is not None
            and state.latest_verification.outcome == VerificationOutcome.FAILED
        )

        # Fast Allow: If no unverified code edits, no failing verification, and no triggering signal:
        # Allow normal non-bugfixing turns or read-only/informational queries immediately!
        if not unverified_edits and not has_failing_verification and not triggering_signal:
            logger.info("stop_allowed_no_unverified_edits", session_id=event.session_id)
            state.current_mode = Mode.SHADOW.value
            return (
                HookResponseEnvelope(
                    decision="allow",
                    reason="No unverified code modifications in progress.",
                    mode=Mode.SHADOW.value,
                    correlation_id=event.correlation_id,
                ),
                state,
            )

        # Run Deep tier verification audit for actual code modifications
        deep_res = await self.deep_orchestrator.investigate(
            event=event,
            state=state,
            triggering_signal=triggering_signal,
        )

        engine_result = deep_res.engine_result
        cost = deep_res.total_cost

        # Case 1: Work is verified -> Allow Stop
        if engine_result.verdict == EngineVerdict.VERIFIED:
            logger.info("stop_allowed_verification_passed", session_id=event.session_id)
            state.current_mode = Mode.SHADOW.value
            return (
                HookResponseEnvelope(
                    decision="allow",
                    reason="Astra verification passed: claimed fix is backed by test results.",
                    mode=Mode.SHADOW.value,
                    bounded_cost=cost,
                    correlation_id=event.correlation_id,
                ),
                state,
            )

        # Case 2: Work is NOT verified -> Audit failure signature and anti-loop policy
        critique = engine_result.critique
        err_text = critique.why_problematic if critique else "Unverified termination attempt"
        sig_hash = compute_failure_signature(err_text)

        anti_loop = evaluate_anti_loop_policy(
            state=state,
            failure_signature_hash=sig_hash,
            max_forced_continuations_per_sig=self.max_forced_continuations,
            cooldown_seconds=self.anti_loop_cooldown,
            current_time_ms=event.received_at,
        )

        if anti_loop.allow_forced_continuation:
            intervention_id = str(uuid.uuid4())
            reason_msg = (
                f"Astra Stop Intervene: {critique.why_problematic if critique else 'Fix requires verification.'}\n"
                f"Required Action: {critique.suggested_next_action if critique else 'Run tests to verify.'}"
            )

            state = record_intervention(
                state=state,
                intervention_id=intervention_id,
                mode=Mode.INTERVENE.value,
                trigger_signal=SignalType.PREMATURE_TERMINATION.value,
                message=reason_msg,
                was_forced_continuation=True,
                failure_signature_hash=sig_hash,
                timestamp_ms=event.received_at,
            )
            state.current_mode = Mode.INTERVENE.value

            logger.info(
                "stop_blocked_forced_continuation",
                session_id=event.session_id,
                intervention_id=intervention_id,
                sig_hash=sig_hash,
                count=anti_loop.current_count + 1,
            )

            return (
                HookResponseEnvelope(
                    decision="block_stop",
                    reason=reason_msg,
                    assist=deep_res.assist_payload,
                    intervention_id=intervention_id,
                    mode=Mode.INTERVENE.value,
                    confidence=engine_result.confidence,
                    bounded_cost=cost,
                    correlation_id=event.correlation_id,
                ),
                state,
            )

        elif anti_loop.action == "surface_to_user":
            logger.warning("stop_anti_loop_cap_reached_surfacing", session_id=event.session_id, sig_hash=sig_hash)
            state.current_mode = Mode.SHADOW.value
            return (
                HookResponseEnvelope(
                    decision="continue",
                    reason=anti_loop.user_surfaced_message,
                    assist=deep_res.assist_payload,
                    mode=Mode.SHADOW.value,
                    bounded_cost=cost,
                    correlation_id=event.correlation_id,
                ),
                state,
            )

        else:
            # Cooldown active -> allow stop
            state.current_mode = Mode.SHADOW.value
            return (
                HookResponseEnvelope(
                    decision="allow",
                    reason="Cooldown active: permitting termination.",
                    mode=Mode.SHADOW.value,
                    bounded_cost=cost,
                    correlation_id=event.correlation_id,
                ),
                state,
            )
