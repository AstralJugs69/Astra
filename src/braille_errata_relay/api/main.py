"""Minimal FastAPI factory for health/readiness and later contract-first routes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.readiness import check_liblouis_readiness


def _default_profile_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "translation_profiles"
        / "demo-ueb-40x25-v1.json"
    )


def create_app(
    *,
    profile_path: str | Path | None = None,
    table_root: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Braille Errata Relay", version="0.1.0")
    configured_profile = profile_path or os.environ.get("TRANSLATION_PROFILE_PATH")
    selected_profile = Path(configured_profile or _default_profile_path())
    selected_table_root = table_root or os.environ.get("LIBLOUIS_TABLEPATH")

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
        report = check_liblouis_readiness(profile, table_root=selected_table_root)
        content: dict[str, object] = {
            "ready": report.ready,
            "reason": report.reason,
            "checks": list(report.checks),
        }
        if report.liblouis_version is not None:
            content["liblouis_version"] = report.liblouis_version
        return JSONResponse(status_code=200 if report.ready else 503, content=content)

    return app


app = create_app()
