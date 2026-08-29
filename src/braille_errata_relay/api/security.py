"""OIDC verification and route-level principal allowlists for private Cloud Run."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from fastapi.responses import JSONResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from braille_errata_relay.cloud_settings import CloudSettings


class OidcVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    email: str
    subject: str
    audience: str


class IdentityVerifier(Protocol):
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity: ...


class GoogleOidcVerifier:
    async def verify(self, token: str, *, audience: str) -> VerifiedIdentity:
        try:
            claims = await asyncio.to_thread(
                id_token.verify_oauth2_token,
                token,
                GoogleAuthRequest(),
                audience,
            )
        except (ValueError, OSError) as exc:
            raise OidcVerificationError("OIDC token verification failed") from exc
        issuer = claims.get("iss")
        if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
            raise OidcVerificationError("OIDC issuer is not accepted")
        email = claims.get("email")
        subject = claims.get("sub")
        token_audience = claims.get("aud")
        email_verified = claims.get("email_verified")
        if not isinstance(email, str) or not isinstance(subject, str):
            raise OidcVerificationError("OIDC principal claims are incomplete")
        if token_audience != audience:
            raise OidcVerificationError("OIDC audience does not match")
        if email_verified not in {True, "true"}:
            raise OidcVerificationError("OIDC email is not verified")
        return VerifiedIdentity(email=email, subject=subject, audience=audience)


def _expected_principal(path: str, settings: CloudSettings) -> str | None:
    if path in {"/internal/workspace-events", "/internal/source-jobs"}:
        return settings.source_push_principal_email
    if path == "/internal/site-observations":
        return settings.telemetry_push_principal_email
    if path in {"/internal/drive-reconcile", "/internal/outbox-drain"}:
        return settings.scheduler_principal_email
    if path.startswith("/api/"):
        return settings.demonstrator_principal_email
    return None


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("x-serverless-authorization") or request.headers.get(
        "authorization"
    )
    if not header:
        return None
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def enforce_route_identity(
    request: Request,
    call_next: RequestResponseEndpoint,
    *,
    settings: CloudSettings | None,
    verifier: IdentityVerifier,
) -> Response:
    protected = request.url.path.startswith("/internal/") or request.url.path.startswith("/api/")
    if not protected:
        return await call_next(request)
    if settings is None or not settings.internal_oidc_audience:
        return JSONResponse(
            status_code=503,
            content={"detail": "private route authentication is not configured"},
        )
    expected = _expected_principal(request.url.path, settings)
    if not expected:
        return JSONResponse(
            status_code=403, content={"detail": "route principal is not configured"}
        )
    token = _bearer_token(request)
    if token is None:
        return JSONResponse(
            status_code=401, content={"detail": "authenticated OIDC bearer required"}
        )
    try:
        identity = await verifier.verify(token, audience=settings.internal_oidc_audience)
    except OidcVerificationError:
        return JSONResponse(status_code=401, content={"detail": "OIDC verification failed"})
    if not hmac.compare_digest(identity.email.lower(), expected.lower()):
        return JSONResponse(
            status_code=403, content={"detail": "principal is not allowed on route"}
        )
    request.state.authenticated_principal = identity.email
    return await call_next(request)
