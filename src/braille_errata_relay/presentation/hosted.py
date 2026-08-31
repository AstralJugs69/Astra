"""Public GET-only dashboard backed by the private Relay review API."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import cast

from fastapi import FastAPI
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from braille_errata_relay.presentation.app import (
    AudienceTokenProvider,
    CloudRunPrivateReviewApi,
    PresentationAuthenticationError,
    PresentationSettings,
    create_presentation_app,
)


class GoogleAmbientAudienceTokenProvider(AudienceTokenProvider):
    """Mint cached ID tokens from the dashboard's attached Cloud Run identity."""

    def __init__(self, *, audience: str, monotonic_clock: Callable[[], float] = time.monotonic):
        self._audience = audience
        self._monotonic_clock = monotonic_clock
        self._cache: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    async def token_for(self, audience: str) -> str:
        if audience != self._audience:
            raise ValueError("hosted dashboard rejected an unexpected audience")
        async with self._lock:
            if self._cache is not None and self._cache[1] > self._monotonic_clock():
                return self._cache[0]
            try:
                token = await asyncio.to_thread(
                    google_id_token.fetch_id_token, GoogleAuthRequest(), audience
                )
            except google_auth_exceptions.GoogleAuthError as exc:
                raise PresentationAuthenticationError(
                    "attached public dashboard identity could not mint a private API token"
                ) from exc
            if not token:
                raise PresentationAuthenticationError("attached identity returned no token")
            self._cache = (token, self._monotonic_clock() + 240.0)
            return cast(str, token)


def create_hosted_app() -> FastAPI:
    audience = os.environ.get("RELAY_PRESENTATION_AUDIENCE", "")
    settings = PresentationSettings(
        api_base_url=os.environ.get("RELAY_PRESENTATION_API_URL", ""),
        audience=audience,
        session_secret=os.environ.get("RELAY_PRESENTATION_SESSION_SECRET", ""),
        impersonate_service_account=os.environ.get(
            "RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT",
            "relay-public-reader@placeholder-project.iam.gserviceaccount.com",
        ),
        port=int(os.environ.get("PORT", "8080")),
        hosted_read_only=True,
        public_origin=os.environ.get("RELAY_PRESENTATION_PUBLIC_ORIGIN") or None,
        source_document_url=os.environ.get("RELAY_PUBLIC_SOURCE_URL") or None,
        repository_url=os.environ.get(
            "RELAY_REPOSITORY_URL", "https://github.com/AstralJugs69/Astra"
        ),
    )
    token_provider = GoogleAmbientAudienceTokenProvider(audience=audience)
    api = CloudRunPrivateReviewApi(
        base_url=settings.api_base_url,
        audience=audience,
        token_provider=token_provider,
    )
    return create_presentation_app(settings, api_client=api)


app = create_hosted_app()
