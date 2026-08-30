"""Loopback-only presentation shell for human review of private Relay APIs.

The browser receives rendered review data only. Short-lived audience-bound
credentials stay on this local server, and this module contains no CUPS client,
device driver, subprocess invocation, or production-control route.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

import google.auth
import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth import exceptions as google_auth_exceptions
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as UserAdcCredentials
from jinja2 import DictLoader, Environment, select_autoescape
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from braille_errata_relay.domain.models import (
    AttestationType,
    ProfessionalDecision,
    TruthBasis,
)


class PrivateReviewApiError(RuntimeError):
    """The local shell cannot safely complete a private Relay API request."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"private Relay API request returned HTTP {status_code}")
        self.status_code = status_code


class PresentationAuthenticationError(RuntimeError):
    """The local presentation shell could not mint a private API credential."""


_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_MAX_TOKEN_CACHE_SECONDS = 270.0
_TOKEN_EXPIRY_SKEW_SECONDS = 30.0
_SERVICE_ACCOUNT_PRINCIPAL = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@"
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_private_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


class AudienceTokenProvider(Protocol):
    async def token_for(self, audience: str) -> str: ...


class PrivateReviewApi(Protocol):
    async def get_json(self, path: str) -> dict[str, object]: ...

    async def post_json(self, path: str, payload: Mapping[str, object]) -> dict[str, object]: ...


class GoogleAudienceTokenProvider:
    """Mint short-lived Cloud Run tokens from ordinary user ADC by impersonation.

    This deliberately rejects service-account key credentials and metadata-based
    credentials. The only accepted source is the developer's ordinary local
    user ADC, which receives a narrowly scoped, removable IAM Credentials grant
    for the configured demonstrator service account.
    """

    def __init__(
        self,
        *,
        target_principal: str,
        audience: str,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if _SERVICE_ACCOUNT_PRINCIPAL.fullmatch(target_principal) is None:
            raise ValueError(
                "presentation impersonation target must be a service-account principal"
            )
        if not _is_private_https_url(audience):
            raise ValueError("presentation audience must be a private HTTPS URL")
        self._target_principal = target_principal
        self._audience = audience
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def token_for(self, audience: str) -> str:
        if audience != self._audience:
            raise ValueError("presentation token provider rejected an unexpected audience")
        async with self._lock:
            cached = self._cache.get(audience)
            if cached is not None and cached[1] > self._monotonic_clock():
                return cached[0]
            value, cache_for_seconds = await asyncio.to_thread(self._mint_token)
            self._cache[audience] = (value, self._monotonic_clock() + cache_for_seconds)
            return value

    def _mint_token(self) -> tuple[str, float]:
        try:
            source, _ = google.auth.default(scopes=(_CLOUD_PLATFORM_SCOPE,))
        except google_auth_exceptions.DefaultCredentialsError as exc:
            raise PresentationAuthenticationError(
                "ordinary local user ADC is required for presentation authentication"
            ) from exc
        if not isinstance(source, UserAdcCredentials):
            raise PresentationAuthenticationError(
                "presentation authentication accepts only ordinary local user ADC"
            )
        try:
            target = impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
                source_credentials=source,
                target_principal=self._target_principal,
                target_scopes=(_CLOUD_PLATFORM_SCOPE,),
                lifetime=300,
            )
            token_credentials = impersonated_credentials.IDTokenCredentials(  # type: ignore[no-untyped-call]
                target_credentials=target,
                target_audience=self._audience,
                include_email=True,
            )
            token_credentials.refresh(GoogleAuthRequest())
        except google_auth_exceptions.GoogleAuthError as exc:
            raise PresentationAuthenticationError(
                "impersonated presentation authentication was not authorized"
            ) from exc
        token = cast(str | None, token_credentials.token)
        expiry = token_credentials.expiry
        if not token or expiry is None:
            raise PresentationAuthenticationError(
                "impersonated presentation authentication returned an incomplete token"
            )
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        else:
            expiry = expiry.astimezone(UTC)
        remaining_seconds = (expiry - self._utc_clock()).total_seconds()
        cache_for_seconds = min(
            _MAX_TOKEN_CACHE_SECONDS, remaining_seconds - _TOKEN_EXPIRY_SKEW_SECONDS
        )
        if cache_for_seconds <= 0:
            raise PresentationAuthenticationError(
                "impersonated presentation authentication returned an expired token"
            )
        return token, cache_for_seconds


class CloudRunPrivateReviewApi:
    """A server-side-only client for the private Cloud Run review API."""

    def __init__(
        self,
        *,
        base_url: str,
        audience: str,
        token_provider: AudienceTokenProvider,
    ) -> None:
        if not base_url.rstrip("/"):
            raise ValueError("private Relay API base URL is required")
        if not audience:
            raise ValueError("private Relay API audience is required")
        self.base_url = base_url.rstrip("/")
        self.audience = audience
        self.token_provider = token_provider

    @staticmethod
    def _path(path: str) -> str:
        if not path.startswith("/api/v1/") or ".." in path.split("/"):
            raise ValueError("presentation requests must target a Relay review API route")
        return path

    async def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        token = await self.token_provider.token_for(self.audience)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.request(
                method,
                f"{self.base_url}{self._path(path)}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise PrivateReviewApiError(response.status_code)
        body = response.json()
        if not isinstance(body, dict):
            raise PrivateReviewApiError(502)
        return cast(dict[str, object], body)

    async def get_json(self, path: str) -> dict[str, object]:
        return await self._request("GET", path)

    async def post_json(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        return await self._request("POST", path, payload)


@dataclass(frozen=True)
class PresentationSettings:
    api_base_url: str
    audience: str
    session_secret: str
    impersonate_service_account: str
    port: int = 8765

    def __post_init__(self) -> None:
        if not _is_private_https_url(self.api_base_url.rstrip("/")):
            raise ValueError("private Relay API URL must be an HTTPS URL")
        if not _is_private_https_url(self.audience):
            raise ValueError("private Relay API audience must be an HTTPS URL")
        if len(self.session_secret) < 32:
            raise ValueError("presentation session secret must contain at least 32 characters")
        if _SERVICE_ACCOUNT_PRINCIPAL.fullmatch(self.impersonate_service_account) is None:
            raise ValueError(
                "presentation impersonation target must be a service-account principal"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("presentation port is outside the valid TCP range")

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


_TEMPLATES = {
    "index.html": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Braille Errata Relay review</title></head>
<body>
  <main>
    <h1>Braille Errata Relay</h1>
    <p>Human review only. Relay does not operate the production queue or device.</p>
    {% if error %}<p role="alert">{{ error }}</p>{% endif %}
    <h2>Report-bearing incidents</h2>
    <ul>
    {% for incident in incidents %}
      <li><a href="/incidents/{{ incident.incident_id }}">{{ incident.incident_id }}</a>
        — {{ incident.review_state.state }}
        {% if incident.blocking_reason %} — block: {{ incident.blocking_reason }}{% endif %}
      </li>
    {% else %}<li>No report-bearing incidents are available.</li>{% endfor %}
    </ul>
  </main>
</body></html>""",
    "incident.html": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Incident review</title></head>
<body>
  <main>
    <p><a href="/">All incidents</a></p>
    <h1>Professional incident review</h1>
    {% if error %}<p role="alert">{{ error }}</p>{% endif %}
    <p><strong>Current state:</strong> {{ review_state.state }}
      {% if review_state.blocking_reason %} (block: {{ review_state.blocking_reason }}){% endif %}</p>
    <p><strong>Candidate status:</strong> CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER</p>

    <section aria-labelledby="source-correction">
      <h2 id="source-correction">Source correction</h2>
      <pre>{{ source_correction }}</pre>
    </section>
    <section aria-labelledby="semantic-summary">
      <h2 id="semantic-summary">Gemini semantic assessment</h2>
      <p>{{ semantic_summary }}</p>
      <h3>Uncertainties</h3><ul>{% for uncertainty in uncertainties %}<li>{{ uncertainty }}</li>{% else %}<li>None recorded.</li>{% endfor %}</ul>
    </section>
    <section aria-labelledby="braille-impact">
      <h2 id="braille-impact">Deterministic Braille impact</h2>
      <pre>{{ braille_impact }}</pre>
      <dl>
        <dt>Baseline BRF SHA-256</dt><dd>{{ baseline_brf_sha256 }}</dd>
        <dt>Candidate BRF SHA-256</dt><dd>{{ candidate_brf_sha256 }}</dd>
      </dl>
    </section>
    <section aria-labelledby="current-observation">
      <h2 id="current-observation">Current CUPS observation <span>[REAL]</span></h2>
      <p>Observation age: {{ observation_age }}</p>
      <pre>{{ current_observation }}</pre>
      <p>A scheduler cancellation is not a device-stop or physical-isolation fact.</p>
    </section>
    <section aria-labelledby="human-disposition">
      <h2 id="human-disposition">Professional disposition <span>[HUMAN ATTESTATION]</span></h2>
      <form method="post" action="/incidents/{{ incident_id }}/professional-dispositions">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="selected_role" value="production_coordinator">
        <input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}">
        <input type="hidden" name="idempotency_key" value="{{ disposition_idempotency_key }}">
        <label>Decision <select name="decision">{% for decision in decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label>
        <label>Note <textarea name="note" maxlength="2000"></textarea></label>
        <button type="submit">Record professional disposition</button>
      </form>
      <p>For a halt request, switch to the independent CUPS operator terminal or UI to perform any scheduler action. This screen records no CUPS action.</p>
    </section>
    <section aria-labelledby="operator-attestation">
      <h2 id="operator-attestation">Operator attestation <span>[HUMAN ATTESTATION]</span></h2>
      <form method="post" action="/incidents/{{ incident_id }}/operator-attestations">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="selected_role" value="machine_operator">
        <input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}">
        <input type="hidden" name="idempotency_key" value="{{ attestation_idempotency_key }}">
        <label>Fact <select name="attestation_type">{% for kind in attestation_types %}<option value="{{ kind }}">{{ kind }}</option>{% endfor %}</select></label>
        <label>Truth basis <select name="truth_basis">{% for basis in truth_bases %}<option value="{{ basis }}">{{ basis }}</option>{% endfor %}</select></label>
        <label>Note <textarea name="note" maxlength="2000"></textarea></label>
        <button type="submit">Record operator attestation</button>
      </form>
    </section>
    <section aria-labelledby="timeline">
      <h2 id="timeline">Incident timeline</h2>
      <ol>{% for event in timeline %}
        <li><strong>[{{ event.truth_basis }}]</strong> {{ event.kind }} at {{ event.recorded_at }}
          {% if event.device_stop_confirmed is defined %} — device stop confirmed: {{ event.device_stop_confirmed }}; physical output isolated: {{ event.physical_output_isolated }}{% endif %}
        </li>
      {% else %}<li>No attributable events are available.</li>{% endfor %}</ol>
    </section>
    <p>[SIMULATED ENDPOINT] endpoint evidence is deliberately distinct from real CUPS observation and human attestation.</p>
  </main>
</body></html>""",
}


def _templates() -> Environment:
    return Environment(
        loader=DictLoader(_TEMPLATES),
        autoescape=select_autoescape(("html", "xml"), default_for_string=True),
    )


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _pretty(value: object) -> str:
    return json.dumps(value if value is not None else {}, indent=2, sort_keys=True)


def _form_error(status_code: int, message: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><p role="alert">{message}</p>', status_code=status_code)


def create_presentation_app(
    settings: PresentationSettings,
    *,
    api_client: PrivateReviewApi | None = None,
) -> FastAPI:
    """Create the local review server; the launcher always binds 127.0.0.1."""

    api = api_client or CloudRunPrivateReviewApi(
        base_url=settings.api_base_url,
        audience=settings.audience,
        token_provider=GoogleAudienceTokenProvider(
            target_principal=settings.impersonate_service_account,
            audience=settings.audience,
        ),
    )
    templates = _templates()
    app = FastAPI(
        title="Braille Errata Relay local professional review", docs_url=None, redoc_url=None
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="strict",
        https_only=False,
    )

    def render(name: str, **context: object) -> HTMLResponse:
        return HTMLResponse(templates.get_template(name).render(**context))

    def csrf_token(request: Request) -> str:
        token = request.session.get("csrf_token")
        if isinstance(token, str) and token:
            return token
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
        return token

    def require_local_form(request: Request, csrf: str) -> HTMLResponse | None:
        if request.headers.get("host") != f"127.0.0.1:{settings.port}":
            return _form_error(403, "Local review requests must use the loopback host.")
        if request.headers.get("origin") != settings.origin:
            return _form_error(403, "The local review form origin was not accepted.")
        expected = request.session.get("csrf_token")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, csrf):
            return _form_error(403, "The local review form token was not accepted.")
        return None

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        try:
            payload = await api.get_json("/api/v1/incidents")
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            ValueError,
        ):
            return render("index.html", incidents=(), error="Private review data is unavailable.")
        incidents = payload.get("incidents")
        return render(
            "index.html",
            incidents=incidents if isinstance(incidents, list) else (),
            error=None,
            csrf_token=csrf_token(request),
        )

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    async def incident(request: Request, incident_id: str) -> HTMLResponse:
        try:
            detail, timeline_payload = await asyncio.gather(
                api.get_json(f"/api/v1/incidents/{incident_id}"),
                api.get_json(f"/api/v1/incidents/{incident_id}/timeline"),
            )
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            ValueError,
        ):
            return render(
                "incident.html",
                incident_id=incident_id,
                review_state={},
                source_correction="No private review data is available.",
                semantic_summary="No private review data is available.",
                uncertainties=(),
                braille_impact="{}",
                baseline_brf_sha256="Unavailable",
                candidate_brf_sha256="Unavailable",
                observation_age="Unavailable",
                current_observation="{}",
                timeline=(),
                decisions=tuple(decision.value for decision in ProfessionalDecision),
                attestation_types=tuple(kind.value for kind in AttestationType),
                truth_bases=tuple(basis.value for basis in TruthBasis),
                csrf_token=csrf_token(request),
                disposition_idempotency_key=secrets.token_urlsafe(24),
                attestation_idempotency_key=secrets.token_urlsafe(24),
                error="Private review data is unavailable.",
            )
        report = _mapping(detail.get("report"))
        packet = _mapping(detail.get("human_disposition_packet"))
        semantic = _mapping(report.get("semantic_assessment"))
        return render(
            "incident.html",
            incident_id=incident_id,
            review_state=_mapping(detail.get("review_state")),
            source_correction=_pretty(detail.get("source_correction")),
            semantic_summary=semantic.get("summary", "No semantic summary is available."),
            uncertainties=semantic.get("uncertainties", ()),
            braille_impact=_pretty(report.get("braille_impact")),
            baseline_brf_sha256=packet.get("baseline_brf_sha256", "Unavailable"),
            candidate_brf_sha256=_mapping(packet.get("candidate_brf")).get("sha256", "Unavailable"),
            observation_age=packet.get("observation_age_seconds", "Unavailable"),
            current_observation=_pretty(detail.get("current_site_observation")),
            timeline=_mapping(timeline_payload).get("events", ()),
            decisions=tuple(decision.value for decision in ProfessionalDecision),
            attestation_types=tuple(kind.value for kind in AttestationType),
            truth_bases=tuple(basis.value for basis in TruthBasis),
            csrf_token=csrf_token(request),
            disposition_idempotency_key=secrets.token_urlsafe(24),
            attestation_idempotency_key=secrets.token_urlsafe(24),
            error=None,
        )

    @app.post("/incidents/{incident_id}/professional-dispositions")
    async def submit_professional_disposition(
        incident_id: str,
        request: Request,
        csrf_token_value: str = Form(alias="csrf_token"),
        decision: str = Form(),
        selected_role: str = Form(),
        expected_state_version: int = Form(),
        note: str = Form(default=""),
        idempotency_key: str = Form(),
    ) -> Response:
        rejected = require_local_form(request, csrf_token_value)
        if rejected is not None:
            return rejected
        if selected_role != "production_coordinator" or decision not in {
            item.value for item in ProfessionalDecision
        }:
            return _form_error(422, "The selected professional disposition is invalid.")
        try:
            await api.post_json(
                f"/api/v1/incidents/{incident_id}/professional-dispositions",
                {
                    "decision": decision,
                    "selected_role": selected_role,
                    "expected_state_version": expected_state_version,
                    "note": note,
                    "idempotency_key": idempotency_key,
                },
            )
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            ValueError,
        ):
            return _form_error(
                409, "The disposition was not recorded. Reload the incident before retrying."
            )
        return RedirectResponse(f"/incidents/{incident_id}", status_code=303)

    @app.post("/incidents/{incident_id}/operator-attestations")
    async def submit_operator_attestation(
        incident_id: str,
        request: Request,
        csrf_token_value: str = Form(alias="csrf_token"),
        attestation_type: str = Form(),
        truth_basis: str = Form(),
        selected_role: str = Form(),
        expected_state_version: int = Form(),
        note: str = Form(default=""),
        idempotency_key: str = Form(),
    ) -> Response:
        rejected = require_local_form(request, csrf_token_value)
        if rejected is not None:
            return rejected
        if (
            selected_role != "machine_operator"
            or attestation_type not in {item.value for item in AttestationType}
            or truth_basis not in {item.value for item in TruthBasis}
        ):
            return _form_error(422, "The selected operator attestation is invalid.")
        try:
            await api.post_json(
                f"/api/v1/incidents/{incident_id}/operator-attestations",
                {
                    "attestation_type": attestation_type,
                    "truth_basis": truth_basis,
                    "selected_role": selected_role,
                    "expected_state_version": expected_state_version,
                    "note": note,
                    "idempotency_key": idempotency_key,
                },
            )
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            ValueError,
        ):
            return _form_error(
                409, "The attestation was not recorded. Reload the incident before retrying."
            )
        return RedirectResponse(f"/incidents/{incident_id}", status_code=303)

    return app


def _settings_from_args(argv: Sequence[str] | None = None) -> PresentationSettings:
    parser = argparse.ArgumentParser(prog="braille-relay-review")
    parser.add_argument("--api-base-url", default=os.environ.get("RELAY_PRESENTATION_API_URL"))
    parser.add_argument("--audience", default=os.environ.get("RELAY_PRESENTATION_AUDIENCE"))
    parser.add_argument(
        "--session-secret",
        default=os.environ.get("RELAY_PRESENTATION_SESSION_SECRET"),
    )
    parser.add_argument(
        "--impersonate-service-account",
        default=os.environ.get("RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT"),
    )
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    return PresentationSettings(
        api_base_url=args.api_base_url or "",
        audience=args.audience or "",
        session_secret=args.session_secret or "",
        impersonate_service_account=args.impersonate_service_account or "",
        port=args.port,
    )


def main(argv: Sequence[str] | None = None) -> int:
    settings = _settings_from_args(argv)
    import uvicorn

    uvicorn.run(
        create_presentation_app(settings),
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
