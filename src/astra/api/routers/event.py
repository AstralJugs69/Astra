"""POST /event router with outer fail-open deadline."""

import asyncio
from typing import Any, Dict
from fastapi import APIRouter, Depends, Header
import structlog

from astra.api.auth import verify_bearer_token
from astra.api.deps import get_decision_pipeline
from astra.application.pipeline import DecisionPipeline
from astra.domain.events import EventType
from astra.integration.antigravity.normalize import normalize_antigravity_event
from astra.integration.antigravity.raw_schema import RawHookEnvelope
from astra.integration.antigravity.response_format import (
    HookResponseEnvelope,
    format_antigravity_stdout,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["event"])

OUTER_FAST_DEADLINE_SECONDS = 5.0
OUTER_DEEP_DEADLINE_SECONDS = 15.0


@router.post("/event", response_model=Dict[str, Any])
async def handle_event(
    envelope: RawHookEnvelope,
    authorized: bool = Depends(verify_bearer_token),
    pipeline: DecisionPipeline = Depends(get_decision_pipeline),
    x_correlation_id: str = Header(default=""),
) -> Dict[str, Any]:
    """Receives raw hook events, runs decision pipeline, and returns Antigravity decision stdout."""
    corr_id = envelope.correlation_id or x_correlation_id

    # 1. Normalize raw event
    event, warnings = normalize_antigravity_event(envelope)
    if warnings:
        logger.warning("event_normalization_warnings", correlation_id=corr_id, warnings=warnings)

    if event is None:
        # Cannot proceed without valid session_id / event_type -> fail open immediately
        logger.warning("event_dropped_missing_required_fields", correlation_id=corr_id)
        if "POST" in envelope.event_type.upper():
            return {}
        return {"decision": "continue"}

    # 2. Determine outer timeout ceiling
    deadline = OUTER_DEEP_DEADLINE_SECONDS if event.event_type == EventType.STOP else OUTER_FAST_DEADLINE_SECONDS

    # 3. Execute with fail-open safety wrapper
    try:
        async with asyncio.timeout(deadline):
            response_envelope = await pipeline.process_event(event)

        # Format decision for Antigravity stdout
        stdout_json = format_antigravity_stdout(
            event_type=event.event_type,
            response=response_envelope,
        )
        return stdout_json

    except asyncio.TimeoutError:
        logger.error("pipeline_outer_deadline_exceeded_fail_open", correlation_id=corr_id, deadline=deadline)
        if event.event_type == EventType.POST_TOOL_USE:
            return {}
        return {"decision": "allow", "reason": "astra_internal_fail_open"}

    except Exception as exc:
        logger.error("pipeline_unhandled_exception_fail_open", correlation_id=corr_id, error=str(exc))
        if event.event_type == EventType.POST_TOOL_USE:
            return {}
        return {"decision": "allow", "reason": "astra_internal_fail_open"}
