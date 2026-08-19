"""Health check endpoint."""

from typing import Any, Dict
from fastapi import APIRouter, Depends

from astra.settings import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    """Health check returning status and configuration summary."""
    return {
        "status": "healthy",
        "service": "astra-backend",
        "environment": settings.env,
        "persistence": settings.persistence_backend,
        "fast_model": settings.fast_model,
        "deep_model": settings.deep_model,
    }
