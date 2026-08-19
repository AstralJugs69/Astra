"""FastAPI application factory for Astra Cloud Run backend service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astra.api.routers import event, health, reason
from astra.infrastructure.observability.logging import configure_logging
from astra.settings import get_settings


def create_app() -> FastAPI:
    """Creates and configures the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, env=settings.env)

    app = FastAPI(
        title="Astra Companion Agent API",
        version="0.1.0",
        description="Companion supervisor agent for Google Antigravity CLI (agy)",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(event.router)
    app.include_router(reason.router)

    return app


app = create_app()
