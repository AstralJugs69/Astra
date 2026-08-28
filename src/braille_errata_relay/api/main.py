"""Minimal FastAPI factory for health/readiness and later contract-first routes."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from braille_errata_relay.braille.profile import load_translation_profile


def _default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"


def create_app(*, profile_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Braille Errata Relay", version="0.1.0")
    selected_profile = Path(
        profile_path or os.environ.get("TRANSLATION_PROFILE_PATH", _default_profile_path())
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            profile = load_translation_profile(selected_profile)
        except (OSError, ValueError) as exc:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "PROFILE_INVALID", "detail": str(exc)},
            )
        if not profile.is_bound:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "BRAILLE_ENGINE_NOT_READY"},
            )
        if importlib.util.find_spec("louis") is None:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "BRAILLE_ENGINE_NOT_READY"},
            )
        return JSONResponse(status_code=200, content={"ready": True})

    return app


app = create_app()

