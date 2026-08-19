"""POST /reason router for explicit / offline deep tier reasoning requests."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
import structlog

from astra.api.auth import verify_bearer_token
from astra.api.deps import get_deep_orchestrator, get_persistence_store
from astra.domain.events import AstraEvent, EventType
from astra.domain.persistence_ports import TrajectoryStateStore
from astra.domain.reasoning_ports import EngineResult
from astra.domain.signals import Signal, SignalType
from astra.domain.trajectory import TrajectoryState, create_initial_trajectory
from astra.tiers.deep.orchestrator import DeepTierOrchestrator

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["reason"])


class ReasonRequest(BaseModel):
    """Request payload for POST /reason."""

    session_id: str
    task: Optional[str] = None
    trigger_type: str = "EXPLICIT_REASON_REQUEST"
    evidence_hints: List[str] = Field(default_factory=list)


@router.post("/reason", response_model=Dict[str, Any])
async def direct_reason(
    req: ReasonRequest,
    authorized: bool = Depends(verify_bearer_token),
    store: TrajectoryStateStore = Depends(get_persistence_store),
    orchestrator: DeepTierOrchestrator = Depends(get_deep_orchestrator),
    x_correlation_id: str = Header(default=""),
) -> Dict[str, Any]:
    """Executes a deep reasoning pass directly on a session's trajectory."""
    state = await store.load(req.session_id)
    if state is None:
        state = create_initial_trajectory(req.session_id)
        if req.task:
            state.task = req.task

    synthetic_event = AstraEvent(
        event_id=f"reason-{req.session_id}",
        session_id=req.session_id,
        event_type=EventType.STOP,
        received_at=1000,
        correlation_id=x_correlation_id or "direct-reason",
    )

    sig = Signal(
        type=SignalType.PREMATURE_TERMINATION,
        confidence=1.0,
        suggested_mode="INTERVENE",
        rationale=f"Direct reason request: {req.trigger_type}",
    )

    deep_res = await orchestrator.investigate(
        event=synthetic_event,
        state=state,
        triggering_signal=sig,
    )

    return deep_res.engine_result.model_dump(mode="json")
