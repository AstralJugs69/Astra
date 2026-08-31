"""Loopback-only presentation shell for human review of private Relay APIs.

The browser receives rendered review data only. Short-lived audience-bound
credentials stay on this local server, and this module contains no CUPS client,
device driver, subprocess invocation, or production-control route.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

import google.auth
import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from google.auth import exceptions as google_auth_exceptions
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as UserAdcCredentials
from jinja2 import DictLoader, Environment, select_autoescape
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from braille_errata_relay.domain.models import (
    AttestationType,
    IncidentWorkflowStage,
    ProfessionalDecision,
    ProofDecision,
    TruthBasis,
)
from braille_errata_relay.local_setup import extract_drive_file_id
from braille_errata_relay.presentation.assets import REPORT_JAVASCRIPT, WATCH_JAVASCRIPT
from braille_errata_relay.presentation.view_models import report_view
from braille_errata_relay.presentation.watch import (
    WatchEventTracker,
    heartbeat_event,
    sanitize_watch_snapshot,
    sse_frame,
    upstream_unavailable_event,
    watch_summary,
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
_TOKEN_TRANSPORT_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0)
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

    async def get_bytes(self, path: str) -> tuple[bytes, str]: ...


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
        sleep: Callable[[float], None] = time.sleep,
        transport_retry_delays_seconds: Sequence[float] = (_TOKEN_TRANSPORT_RETRY_DELAYS_SECONDS),
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
        self._sleep = sleep
        self._transport_retry_delays_seconds = tuple(transport_retry_delays_seconds)
        if any(delay < 0 for delay in self._transport_retry_delays_seconds):
            raise ValueError("presentation token retry delays must be non-negative")
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
        token_credentials = None
        for attempt in range(len(self._transport_retry_delays_seconds) + 1):
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
                break
            except google_auth_exceptions.TransportError as exc:
                if attempt >= len(self._transport_retry_delays_seconds):
                    raise PresentationAuthenticationError(
                        "presentation authentication transport retries were exhausted"
                    ) from exc
                self._sleep(self._transport_retry_delays_seconds[attempt])
            except google_auth_exceptions.GoogleAuthError as exc:
                raise PresentationAuthenticationError(
                    "impersonated presentation authentication was not authorized"
                ) from exc
        if token_credentials is None:
            raise PresentationAuthenticationError(
                "presentation authentication returned no token credentials"
            )
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

    async def get_bytes(self, path: str) -> tuple[bytes, str]:
        token = await self.token_provider.token_for(self.audience)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.get(
                f"{self.base_url}{self._path(path)}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise PrivateReviewApiError(response.status_code)
        disposition = response.headers.get("content-disposition")
        if disposition is None:
            raise PrivateReviewApiError(502)
        return response.content, disposition


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
      <h3>Candidate manifest and deterministic tool identity</h3>
      <pre>{{ candidate_manifest }}</pre>
      <pre>{{ profile_identity }}</pre>
      <h3>{{ candidate_evidence_preview.label }}</h3>
      <pre>{{ candidate_evidence_preview.text }}</pre>
      <p>This preview is evidence for a private review conversation. It is not tactile proof and it never turns the candidate into an approved production master.</p>
    </section>
    <section aria-labelledby="current-observation">
      <h2 id="current-observation">Current CUPS observation <span>[REAL]</span></h2>
      <p>Observation age: {{ observation_age }}</p>
      <pre>{{ current_observation }}</pre>
      <p>A scheduler cancellation is not a device-stop or physical-isolation fact.</p>
    </section>
    <section aria-labelledby="human-disposition">
      <h2 id="human-disposition">Professional disposition <span>[HUMAN ATTESTATION]</span></h2>
      {% if error %}
      <p>Professional disposition controls are unavailable until authoritative private review data is loaded.</p>
      {% else %}
      <form method="post" action="/incidents/{{ incident_id }}/professional-dispositions">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="selected_role" value="production_coordinator">
        <input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}">
        <input type="hidden" name="idempotency_key" value="{{ disposition_idempotency_key }}">
        <label>Decision <select name="decision">{% for decision in decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label>
        <label>Note <textarea name="note" maxlength="2000"></textarea></label>
        <button type="submit">Record professional disposition</button>
      </form>
      {% endif %}
      <p>For a halt request, switch to the independent CUPS operator terminal or UI to perform any scheduler action. This screen records no CUPS action.</p>
    </section>
    <section aria-labelledby="operator-attestation">
      <h2 id="operator-attestation">Operator attestation <span>[HUMAN ATTESTATION]</span></h2>
      {% if error %}
      <p>Operator attestation controls are unavailable until authoritative private review data is loaded.</p>
      {% else %}
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
      {% endif %}
    </section>
    <section aria-labelledby="containment-confirmation">
      <h2 id="containment-confirmation">Containment confirmation <span>[HUMAN + READ-ONLY EVIDENCE]</span></h2>
      <pre>{{ containment_evidence }}</pre>
      <p>CUPS state alone never proves device stop or physical-output isolation. The coordinator may record this conclusion only when the authoritative evidence set is eligible.</p>
      {% if review_actions.containment_confirmation.eligible %}
      <form method="post" action="/incidents/{{ incident_id }}/containment-confirmations">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="selected_role" value="production_coordinator">
        <input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}">
        <input type="hidden" name="idempotency_key" value="{{ containment_idempotency_key }}">
        <input type="hidden" name="halt_disposition_record_id" value="{{ review_actions.containment_confirmation.halt_disposition_record_id }}">
        <input type="hidden" name="site_observation_id" value="{{ review_actions.containment_confirmation.site_observation_id }}">
        <input type="hidden" name="physical_output_isolation_attestation_id" value="{{ review_actions.containment_confirmation.physical_output_isolation_attestation_id }}">
        <label>Coordinator note <textarea name="note" maxlength="2000"></textarea></label>
        <button type="submit">Record containment confirmation</button>
      </form>
      {% else %}
      <p>Containment confirmation is unavailable: {{ review_actions.containment_confirmation.blocking_reason }}.</p>
      {% endif %}
    </section>
    <section aria-labelledby="proof-review">
      <h2 id="proof-review">Exact candidate proof gate <span>[DEMO_FIXTURE_REVIEW]</span></h2>
      <p>Fixture review is not independent professional certification. Approval does not submit, link, release, or verify any replacement job.</p>
      {% if review_actions.proof.eligible %}
      <form method="post" action="/incidents/{{ incident_id }}/proof-records">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="candidate_sha256" value="{{ review_actions.proof.provenance.candidate_sha256 }}">
        <input type="hidden" name="manifest_sha256" value="{{ review_actions.proof.provenance.manifest_sha256 }}">
        <input type="hidden" name="review_basis" value="DEMO_FIXTURE_REVIEW">
        <input type="hidden" name="selected_role" value="proofreader">
        <input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}">
        <input type="hidden" name="idempotency_key" value="{{ proof_idempotency_key }}">
        <label>Decision <select name="decision">{% for decision in proof_decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label>
        <label>Note <textarea name="note" maxlength="2000"></textarea></label>
        <label><input type="checkbox" name="visual_only_uncertainty" value="true"> Visual-only uncertainty remains (this blocks approval).</label>
        <button type="submit">Record exact-candidate proof decision</button>
      </form>
      {% else %}
      <p>Proof review is unavailable: {{ review_actions.proof.blocking_reason }}.</p>
      {% endif %}
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


_TEMPLATES["index.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Professional review</title>
<style>
:root{color-scheme:light;--ink:#15233b;--muted:#52627a;--paper:#f5f7fb;--card:#fff;--line:#d7dfeb;--navy:#173f75;--blue:#1769aa;--teal:#027b7b;--amber:#8a5b00;--red:#a02638;--violet:#604da3;--shadow:0 12px 28px rgba(20,42,74,.09)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.skip{position:absolute;left:-999px;top:0}.skip:focus{left:1rem;top:1rem;z-index:3;background:#fff;padding:.6rem 1rem;border:3px solid var(--blue)}.site-header{background:var(--ink);color:#fff;border-bottom:5px solid #2ca5b5}.shell{width:min(1180px,calc(100% - 2rem));margin:auto}.site-header .shell{display:flex;gap:1rem;align-items:center;justify-content:space-between;padding:1rem 0}.brand{font-weight:800;letter-spacing:.015em}.eyebrow{margin:0;color:#bce7ee;font-size:.78rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.hero{padding:2.5rem 0 1.25rem}.hero h1{margin:.25rem 0 .6rem;font-size:clamp(2rem,5vw,3.5rem);line-height:1.08}.lede{max-width:75ch;margin:0;color:var(--muted);font-size:1.1rem}.notice,.empty{background:#fff9df;border-left:5px solid var(--amber);padding:1rem 1.1rem;margin:1rem 0}.summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin:1rem 0 2rem}.summary-card,.incident-card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.summary-card{padding:1rem}.summary-card strong{display:block;font-size:1.7rem}.summary-card span{color:var(--muted);font-size:.9rem}.section-heading{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;margin:1rem 0}.section-heading h2{margin:0;font-size:1.35rem}.section-heading p{margin:0;color:var(--muted)}.incident-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;padding-bottom:3rem}.incident-card{display:block;color:inherit;text-decoration:none;padding:1.1rem;transition:transform .15s ease,box-shadow .15s ease}.incident-card:hover{transform:translateY(-2px)}.incident-card:focus-visible,a:focus-visible,button:focus-visible,select:focus-visible,textarea:focus-visible,input:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}.card-top{display:flex;justify-content:space-between;gap:.75rem;align-items:flex-start}.incident-card h3{margin:0;font-size:1.05rem;overflow-wrap:anywhere}.state{font-weight:800;margin:.75rem 0 .3rem}.meta{color:var(--muted);font-size:.9rem;margin:.25rem 0}.badges{display:flex;flex-wrap:wrap;gap:.35rem}.badge{display:inline-block;border-radius:999px;padding:.18rem .55rem;font-size:.7rem;font-weight:800;letter-spacing:.04em;border:1px solid currentColor}.real{color:var(--teal);background:#e8faf8}.deterministic{color:var(--navy);background:#eaf2ff}.gemini{color:var(--violet);background:#f0edff}.human{color:var(--amber);background:#fff7df}.simulated{color:var(--red);background:#fff0f2}.blocked{color:var(--red);font-weight:800}.footer{border-top:1px solid var(--line);padding:1.5rem 0 2.5rem;color:var(--muted);font-size:.9rem}@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}@media(max-width:640px){.summary-grid{grid-template-columns:1fr}.site-header .shell{align-items:flex-start;flex-direction:column}.hero{padding-top:1.5rem}}
</style></head>
<body><a class="skip" href="#incidents">Skip to incidents</a>
<header class="site-header"><div class="shell"><div><p class="eyebrow">Report-first production overlay</p><div class="brand">Braille Errata Relay</div></div><div class="badges"><a class="badge deterministic" href="/setup/source">REGISTER BASELINE</a><a class="badge real" href="/watch">OPEN LIVE WATCH FLOOR</a>{% if fixture_mode %}<span class="badge simulated">SANITIZED DEMO FIXTURE</span>{% endif %}<span class="badge real">READ-ONLY REVIEW</span><span class="badge human">HUMAN AUTHORITY</span></div></div></header>
<main class="shell"><section class="hero"><p class="eyebrow">Professional review dashboard</p><h1>Evidence before action.</h1><p class="lede">Relay detects and explains a correction, preserves immutable Braille lineage, and records professional evidence. It does not control CUPS, an embosser, or a production device.</p></section>
{% if error %}<p class="notice" role="alert">{{ error }}</p>{% endif %}
<section class="summary-grid" aria-label="Incident summary"><article class="summary-card"><strong>{{ summary.total|default(incidents|length) }}</strong><span>Report-bearing incidents</span></article><article class="summary-card"><strong>{{ summary.blocked|default(0) }}</strong><span>Visible blocked/review outcomes</span></article><article class="summary-card"><strong>Human</strong><span>Disposition, proof, and resubmission remain external</span></article></section>
<section id="incidents"><div class="section-heading"><h2>Current review queue</h2><p>Open an incident to inspect authoritative detail.</p></div><div class="incident-grid">
{% for incident in incidents %}<a class="incident-card" href="/incidents/{{ incident.incident_id }}"><div class="card-top"><h3 title="{{ incident.incident_id }}">Incident {{ incident.incident_id[:12] }}...</h3><div class="badges"><span class="badge deterministic">DETERMINISTIC</span>{% if incident.blocking_reason %}<span class="badge human">REVIEW</span>{% endif %}</div></div><p class="state">{{ incident.review_state.state }}</p>{% if incident.blocking_reason %}<p class="blocked">Block: {{ incident.blocking_reason }}</p>{% endif %}<p class="meta">{{ incident.source_change_summary|default("Open detail for source correction and semantic context.") }}</p><p class="meta">{{ incident.page_impact_summary|default("Page impact is available in immutable detail.") }}</p><p class="meta"><strong>Next safe human action:</strong> {{ incident.next_safe_action|default("Review attributable evidence.") }}</p></a>{% else %}<p class="empty">No report-bearing incidents are available. A quiet queue is not evidence of a completed production action.</p>{% endfor %}
</div></section></main><footer class="footer"><div class="shell">Candidate BRF is not an approved production master. A read-only observation of a replacement job does not prove endpoint completion or physical output.</div></footer></body></html>"""

_TEMPLATES["incident.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Incident review</title>
<style>
:root{color-scheme:light;--ink:#15233b;--muted:#52627a;--paper:#f5f7fb;--card:#fff;--line:#d7dfeb;--navy:#173f75;--blue:#1769aa;--teal:#027b7b;--amber:#8a5b00;--red:#a02638;--violet:#604da3;--shadow:0 12px 28px rgba(20,42,74,.09)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.skip{position:absolute;left:-999px;top:0}.skip:focus{left:1rem;top:1rem;z-index:3;background:#fff;padding:.6rem 1rem;border:3px solid var(--blue)}.site-header{background:var(--ink);color:#fff;border-bottom:5px solid #2ca5b5}.shell{width:min(1180px,calc(100% - 2rem));margin:auto}.site-header .shell{padding:1rem 0}.brand{font-weight:800}.eyebrow{margin:0;color:#bce7ee;font-size:.78rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}main{padding:1.5rem 0 3rem}.back{color:var(--navy);font-weight:700}.status-card,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.status-card{padding:1.25rem;margin:1rem 0 1.25rem;border-left:7px solid var(--navy)}.status-card h1{margin:.2rem 0 .5rem;font-size:clamp(1.7rem,4vw,2.8rem);line-height:1.1}.grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.8fr);gap:1rem}.card{padding:1.15rem;margin-bottom:1rem}.card h2{margin:0 0 .65rem;font-size:1.2rem}.card h3{margin:1rem 0 .45rem;font-size:1rem}.card p{margin:.45rem 0}.badges{display:flex;flex-wrap:wrap;gap:.35rem}.badge{display:inline-block;border-radius:999px;padding:.18rem .55rem;font-size:.7rem;font-weight:800;letter-spacing:.04em;border:1px solid currentColor}.real{color:var(--teal);background:#e8faf8}.deterministic{color:var(--navy);background:#eaf2ff}.gemini{color:var(--violet);background:#f0edff}.human{color:var(--amber);background:#fff7df}.simulated{color:var(--red);background:#fff0f2}.block{color:var(--red);font-weight:800}.notice{background:#fff9df;border-left:5px solid var(--amber);padding:1rem 1.1rem;margin:1rem 0}.role{border:1px solid #e5c66b;background:#fffbeb;padding:.75rem;border-radius:8px;font-size:.92rem}.action{background:#173f75;color:#fff;border:0;border-radius:7px;padding:.65rem .9rem;font:inherit;font-weight:800;cursor:pointer}.action:hover{background:#0f315f}.action[disabled]{background:#9ba8b8;cursor:not-allowed}.download{display:inline-block;background:#0e6576;color:#fff;padding:.65rem .9rem;border-radius:7px;font-weight:800;text-decoration:none}.download:hover{background:#064d5b}dl{display:grid;grid-template-columns:minmax(130px,.42fr) 1fr;gap:.45rem .8rem;margin:0}dt{font-weight:800}dd{margin:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f9;border:1px solid var(--line);padding:.75rem;border-radius:7px;font-size:.84rem}details{margin-top:.75rem}summary{cursor:pointer;font-weight:800}ol{padding-left:1.35rem}li{margin:.55rem 0}.timeline-kind{font-weight:800;overflow-wrap:anywhere;word-break:break-word}.boundary{background:#fff0f2;border:1px solid #efb7c1;border-radius:10px;padding:1rem}.footer{border-top:1px solid var(--line);padding:1.5rem 0 2.5rem;color:var(--muted);font-size:.9rem}label{display:block;font-weight:700;margin:.75rem 0}select,textarea,input[type=number]{display:block;width:100%;margin-top:.25rem;border:1px solid #8797ad;border-radius:6px;padding:.55rem;font:inherit;background:#fff}textarea{min-height:5rem}input[type=hidden]{display:none}a:focus-visible,button:focus-visible,select:focus-visible,textarea:focus-visible,input:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding-top:1rem}}@media(max-width:500px){.shell{width:min(100% - 1rem,1180px)}.status-card,.card{padding:1rem}dl{grid-template-columns:1fr}dt{margin-top:.35rem}}
</style><style>.decision-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin:1rem 0}.decision-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:1rem;box-shadow:var(--shadow)}.decision-card h2{margin:0 0 .3rem;font-size:1rem}.decision-card p{margin:.35rem 0;overflow-wrap:anywhere}.compare{display:grid;grid-template-columns:1fr 1fr;gap:.75rem}.compare div{border-left:4px solid var(--navy);background:#f1f5fb;padding:.65rem}.compare div:last-child{border-left-color:var(--teal)}.progress{display:flex;flex-wrap:wrap;gap:.45rem;margin:.85rem 0}.progress span{border:1px solid var(--line);border-radius:999px;padding:.24rem .52rem;font-size:.75rem;font-weight:800}.progress .current{border-color:var(--navy);background:#eaf2ff;color:var(--navy)}.boundary-strip{background:#edf7f8;border:1px solid #a7d8da;border-radius:10px;padding:.8rem;margin:1rem 0;font-size:.9rem}.boundary-strip strong{color:var(--teal)}@media(max-width:800px){.decision-strip{grid-template-columns:1fr}}@media(max-width:520px){.compare{grid-template-columns:1fr}}</style><style>@media(max-width:800px){.grid{grid-template-columns:minmax(0,1fr)}.grid>*,.card{min-width:0}.card p,.card h2,.card h3,.badge{overflow-wrap:anywhere}}</style></head>
<body><a class="skip" href="#incident-content">Skip to incident</a><header class="site-header"><div class="shell"><p class="eyebrow">Report-first production overlay</p><div class="brand">Braille Errata Relay</div>{% if fixture_mode %}<div class="badges"><span class="badge simulated">SANITIZED DEMO FIXTURE</span></div>{% endif %}</div></header>
<main id="incident-content" class="shell"><p><a class="back" href="/">&larr; All incidents</a></p>{% if error %}<p class="notice" role="alert">{{ error }}</p>{% endif %}
<section class="status-card" aria-labelledby="incident-title"><div class="badges"><span class="badge deterministic">[DETERMINISTIC]</span>{% if review_state.blocking_reason %}<span class="badge human">REVIEW BLOCK</span>{% endif %}</div><h1 id="incident-title">Professional incident review</h1><p><strong>Current state:</strong> {{ review_state.state }}{% if review_state.blocking_reason %} <span class="block">- {{ review_state.blocking_reason }}</span>{% endif %}</p><p><strong>Next safe action:</strong> {{ next_safe_action|default("Review the authoritative evidence below before any independent production action.") }}</p><p>Relay does not control CUPS or the embosser. Candidate approval does not submit a job.</p></section>
<section class="card" aria-labelledby="what-changed"><div class="badges"><span class="badge deterministic">[AUTHORITATIVE STORED SOURCE]</span></div><h2 id="what-changed">What changed?</h2><div class="compare"><div><strong>Old source</strong><p>{{ source_comparison.old }}</p></div><div><strong>New source</strong><p>{{ source_comparison.new }}</p></div></div><div class="progress" aria-label="Durable workflow progress">{% for stage in workflow_stages %}<span{% if stage == workflow_stage %} class="current"{% endif %}>{{ stage }}</span>{% endfor %}</div><p><strong>Persisted activity:</strong> {{ semantic_activity }}</p></section>
<section class="decision-strip" aria-label="Review summary"><article class="decision-card"><h2>Materiality</h2><p>{{ semantic_materiality }}</p></article><article class="decision-card"><h2>Uncertainty</h2><p>{{ uncertainty_summary }}</p></article><article class="decision-card"><h2>Impacted Braille pages</h2><p>{{ impact_summary }}</p></article></section>
<section class="boundary-strip"><strong>Visible boundary:</strong> deterministic source/diff/BRF/page-impact computation; Gemini semantic assessment; real read-only CUPS observation; human records; and the simulated physical endpoint are distinct facts. Nothing here operates a production device.</section>
<div class="grid"><div>
<section class="card" aria-labelledby="source-correction"><div class="badges"><span class="badge deterministic">[DETERMINISTIC]</span></div><h2 id="source-correction">1. Source correction</h2><p>Immutable source-diff evidence is retained for professional review.</p><details><summary>View source correction evidence</summary><pre>{{ source_correction }}</pre></details></section>
<section class="card" aria-labelledby="semantic-summary"><div class="badges"><span class="badge gemini">[GEMINI ASSESSMENT]</span></div><h2 id="semantic-summary">2. Semantic assessment and uncertainty</h2><p>{{ semantic_summary }}</p><h3>Uncertainties requiring human judgment</h3><ul>{% for uncertainty in uncertainties %}<li>{{ uncertainty }}</li>{% else %}<li>No uncertainty was recorded in this assessment.</li>{% endfor %}</ul></section>
<section class="card" aria-labelledby="braille-impact"><div class="badges"><span class="badge deterministic">[DETERMINISTIC]</span></div><h2 id="braille-impact">3. Braille and page impact</h2><dl><dt>Baseline BRF SHA-256</dt><dd title="{{ baseline_brf_sha256 }}">{{ baseline_brf_sha256[:12] }}...</dd><dt>Candidate BRF SHA-256</dt><dd title="{{ candidate_brf_sha256 }}">{{ candidate_brf_sha256[:12] }}...</dd></dl><details><summary>View exact page-impact evidence</summary><pre>{{ braille_impact }}</pre></details></section>
<section class="card" aria-labelledby="current-observation"><div class="badges"><span class="badge real">[REAL]</span><span class="badge real">[REAL QUEUE OBSERVATION]</span></div><h2 id="current-observation">4. Production observation and freshness</h2><p><strong>Observation age:</strong> {{ observation_age }}</p><p>A scheduler cancellation is not a device-stop or physical-isolation fact. An observed replacement is not endpoint completion or physical output.</p><details><summary>View normalized read-only observation</summary><pre>{{ current_observation }}</pre></details></section>
<section class="card" aria-labelledby="candidate-lineage"><div class="badges"><span class="badge deterministic">[DETERMINISTIC]</span><span class="badge human">[DEMO FIXTURE REVIEW]</span></div><h2 id="candidate-lineage">5. Immutable candidate lineage</h2><p><strong>Candidate status:</strong> CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER</p><p>This is an <strong>approved demo-fixture candidate for human-controlled submission</strong>, not a certified production master.</p>{% if review_actions.replacement_observation.candidate_download_eligible %}<p><a class="download" href="/incidents/{{ incident_id }}/approved-candidate">Download exact approved candidate BRF</a></p>{% else %}<p class="block">Candidate download is unavailable: {{ review_actions.replacement_observation.blocking_reason }}.</p>{% endif %}<details><summary>View manifest and tool identity</summary><pre>{{ candidate_manifest }}</pre><pre>{{ profile_identity }}</pre></details><h3>{{ candidate_evidence_preview.label }}</h3><pre>{{ candidate_evidence_preview.text }}</pre><p>This text preview is evidence for discussion, not tactile proof.</p></section>
<section class="card" aria-labelledby="human-disposition"><div class="badges"><span class="badge human">[HUMAN RECORD]</span><span class="badge human">[HUMAN ATTESTATION]</span></div><h2 id="human-disposition">6. Professional disposition</h2>{% if error %}<p>Professional disposition controls are unavailable until authoritative private review data is loaded.</p>{% elif fixture_mode %}<p class="role">Offline fixture: human-record controls are intentionally disabled.</p>{% else %}<form method="post" action="/incidents/{{ incident_id }}/professional-dispositions"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="production_coordinator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ disposition_idempotency_key }}"><div class="role"><strong>Role required:</strong> production coordinator. This form records a disposition only; perform any scheduler action in the independent CUPS/vendor surface.</div><label>Decision <select name="decision">{% for decision in decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label><label>Note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record professional disposition</button></form>{% endif %}</section>
<section class="card" aria-labelledby="operator-attestation"><div class="badges"><span class="badge human">[HUMAN RECORD]</span></div><h2 id="operator-attestation">7. Operator attestation</h2>{% if error %}<p>Operator attestation controls are unavailable until authoritative private review data is loaded.</p>{% elif fixture_mode %}<p class="role">Offline fixture: human-record controls are intentionally disabled.</p>{% else %}<form method="post" action="/incidents/{{ incident_id }}/operator-attestations"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="machine_operator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ attestation_idempotency_key }}"><div class="role"><strong>Role required:</strong> machine operator. An attestation records an attributable fact; it does not operate a device.</div><label>Fact <select name="attestation_type">{% for kind in attestation_types %}<option value="{{ kind }}">{{ kind }}</option>{% endfor %}</select></label><label>Truth basis <select name="truth_basis">{% for basis in truth_bases %}<option value="{{ basis }}">{{ basis }}</option>{% endfor %}</select></label><label>Note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record operator attestation</button></form>{% endif %}</section>
<section class="card" aria-labelledby="containment-confirmation"><div class="badges"><span class="badge human">[HUMAN + READ-ONLY EVIDENCE]</span></div><h2 id="containment-confirmation">8. Containment confirmation</h2><p>CUPS state alone never proves device stop or physical-output isolation.</p>{% if review_actions.containment_confirmation.eligible %}{% if fixture_mode %}<button class="action" disabled>Offline fixture: containment recording disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/containment-confirmations"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="production_coordinator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ containment_idempotency_key }}"><input type="hidden" name="halt_disposition_record_id" value="{{ review_actions.containment_confirmation.halt_disposition_record_id }}"><input type="hidden" name="site_observation_id" value="{{ review_actions.containment_confirmation.site_observation_id }}"><input type="hidden" name="physical_output_isolation_attestation_id" value="{{ review_actions.containment_confirmation.physical_output_isolation_attestation_id }}"><label>Coordinator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record containment confirmation</button></form>{% endif %}{% else %}<p>Containment confirmation is unavailable: {{ review_actions.containment_confirmation.blocking_reason }}.</p>{% endif %}</section>
<section class="card" aria-labelledby="proof-review"><div class="badges"><span class="badge human">[DEMO FIXTURE REVIEW]</span></div><h2 id="proof-review">9. Exact candidate proof gate</h2><p>Fixture review is not independent professional certification. Approval does not submit, link, release, or verify a replacement job.</p>{% if review_actions.proof.eligible %}{% if fixture_mode %}<button class="action" disabled>Offline fixture: proof decision recording disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/proof-records"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="candidate_sha256" value="{{ review_actions.proof.provenance.candidate_sha256 }}"><input type="hidden" name="manifest_sha256" value="{{ review_actions.proof.provenance.manifest_sha256 }}"><input type="hidden" name="review_basis" value="DEMO_FIXTURE_REVIEW"><input type="hidden" name="selected_role" value="proofreader"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ proof_idempotency_key }}"><div class="role"><strong>Role required:</strong> proofreader. The demo fixture label is not independent certification.</div><label>Decision <select name="decision">{% for decision in proof_decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label><label>Note <textarea name="note" maxlength="2000"></textarea></label><label><input type="checkbox" name="visual_only_uncertainty" value="true"> Visual-only uncertainty remains (blocks approval).</label><button class="action" type="submit">Record exact-candidate proof decision</button></form>{% endif %}{% else %}<p>Proof review is unavailable: {{ review_actions.proof.blocking_reason }}.</p>{% endif %}</section>
<section class="card" aria-labelledby="replacement-observation"><div class="badges"><span class="badge real">[REAL QUEUE OBSERVATION]</span><span class="badge human">[HUMAN RECORD]</span></div><h2 id="replacement-observation">10. Replacement observation</h2><p>After independently using the existing CUPS/vendor surface, the machine operator may associate only a fresh read-only observation. This does not prove endpoint completion or physical output.</p>{% if review_actions.replacement_observation.eligible %}{% if fixture_mode %}<div class="role"><strong>Proof-ready offline fixture:</strong> replacement linking is intentionally disabled; no human record can be posted.</div>{% else %}<form method="post" action="/incidents/{{ incident_id }}/replacement-observation-links"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="candidate_sha256" value="{{ review_actions.replacement_observation.provenance.candidate_sha256 }}"><input type="hidden" name="candidate_manifest_sha256" value="{{ review_actions.replacement_observation.provenance.manifest_sha256 }}"><input type="hidden" name="proof_record_id" value="{{ review_actions.replacement_observation.provenance.proof_record_id }}"><input type="hidden" name="site_observation_id" value="{{ current_observation_id|default('') }}"><input type="hidden" name="selected_role" value="machine_operator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ replacement_idempotency_key }}"><div class="role"><strong>Role required:</strong> machine operator. Enter only the scheduler job ID observed by the independent read-only bridge.</div><label>Observed replacement scheduler job ID <input name="scheduler_job_id" type="number" min="1" required></label><label>Operator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record replacement observation link</button></form>{% endif %}{% else %}<p>Replacement observation is unavailable: {{ review_actions.replacement_observation.blocking_reason }}.</p>{% endif %}</section>
</div><aside>
<section class="card" aria-labelledby="timeline"><div class="badges"><span class="badge human">ATTRIBUTABLE TIMELINE</span></div><h2 id="timeline">11. Evidence timeline</h2><ol>{% for event in timeline %}<li><span class="timeline-kind">[{{ event.truth_basis }}] {{ event.kind }}</span><br><span class="meta">{{ event.recorded_at }}</span>{% if event.device_stop_confirmed is defined %}<br><span class="meta">Device stop: {{ event.device_stop_confirmed }}; physical output isolated: {{ event.physical_output_isolated }}</span>{% endif %}</li>{% else %}<li>No attributable events are available.</li>{% endfor %}</ol></section>
<section class="boundary"><div class="badges"><span class="badge simulated">[SIMULATED ENDPOINT]</span><span class="badge simulated">[SIMULATED PHYSICAL ENDPOINT]</span></div><h2>12. System boundary</h2><p>Only the physical endpoint is simulated. CUPS observation is read-only, and all disposition, proof, submission, containment, and final verification authority stays with human professionals.</p><p>Historical blocked incidents are valid fail-closed outcomes.</p></section>
</aside></div></main><footer class="footer"><div class="shell">Candidate BRF is not an approved production master. Relay does not run the production queue or device.</div></footer></body></html>"""


_TEMPLATES["watch.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Live watch floor</title>
<style>
:root{color-scheme:light;--ink:#15233b;--muted:#52627a;--paper:#f4f7fb;--card:#fff;--line:#cfd9e7;--navy:#173f75;--teal:#087878;--amber:#745000;--red:#991d35;--red-bg:#fff0f3;--shadow:0 12px 28px rgba(20,42,74,.09)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(1160px,calc(100% - 2rem));margin:auto}.skip{position:absolute;left:-999px;top:0}.skip:focus{left:1rem;top:1rem;z-index:5;background:#fff;padding:.6rem 1rem;border:3px solid var(--navy)}.site-header{background:var(--ink);color:#fff;border-bottom:5px solid #28aab9}.site-header .shell{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 0}.eyebrow{margin:0;color:#bdebf1;font-size:.76rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.brand{font-weight:900;letter-spacing:.015em}.nav-link{color:#fff;font-weight:800}.hero{padding:2.4rem 0 1.25rem}.hero h1{margin:.25rem 0 .55rem;font-size:clamp(2rem,5vw,3.6rem);line-height:1.06}.lede{max-width:72ch;margin:0;color:var(--muted);font-size:1.08rem}.status-grid,.pipeline,.watch-grid{display:grid;gap:1rem}.status-grid{grid-template-columns:repeat(4,1fr);margin:1.1rem 0}.status-card,.panel,.pipeline-step{background:var(--card);border:1px solid var(--line);border-radius:13px;box-shadow:var(--shadow)}.status-card{padding:1rem}.status-card strong{display:block;font-size:1.05rem}.status-card span{color:var(--muted);font-size:.88rem}.connection{font-weight:900}.connection[data-state=connected]{color:var(--teal)}.connection[data-state=disconnected]{color:var(--red)}.pipeline{grid-template-columns:repeat(6,minmax(0,1fr));margin:1rem 0 1.4rem}.pipeline-step{padding:.8rem;font-size:.87rem}.pipeline-step strong{display:block;color:var(--navy);font-size:.74rem;letter-spacing:.04em}.watch-grid{grid-template-columns:minmax(0,1fr) minmax(280px,.72fr);padding-bottom:2rem}.panel{padding:1.1rem}.panel h2{margin:0 0 .6rem;font-size:1.2rem}.notice,.empty{background:#fff9df;border-left:5px solid #b98400;padding:1rem;margin:1rem 0}.mismatch{background:var(--red-bg);border:3px solid var(--red);border-radius:13px;padding:1rem 1.1rem;margin:1rem 0}.mismatch h2{margin:0;color:var(--red);font-size:clamp(1.2rem,3.5vw,1.8rem);letter-spacing:.02em}.mismatch p{margin:.35rem 0}.controls{display:flex;flex-wrap:wrap;gap:.55rem;margin-top:.8rem}.controls button{appearance:none;background:var(--navy);border:0;border-radius:7px;color:#fff;cursor:pointer;font:inherit;font-weight:800;padding:.58rem .75rem}.controls button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.controls button:focus-visible,a:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}.watch-list{list-style:none;padding:0;margin:0}.watch-incident{border-top:1px solid var(--line);padding:.8rem 0}.watch-incident:first-child{border-top:0}.watch-incident a{display:block;color:var(--navy);font-weight:900;overflow-wrap:anywhere}.watch-incident span{color:var(--muted);display:block;font-size:.9rem}.boundary{background:#edf7f8;border:1px solid #a7d8da;border-radius:10px;padding:1rem}.boundary strong{color:var(--teal)}.footer{border-top:1px solid var(--line);padding:1.4rem 0 2.5rem;color:var(--muted);font-size:.9rem}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}@media(max-width:860px){.status-grid{grid-template-columns:repeat(2,1fr)}.pipeline{grid-template-columns:repeat(3,1fr)}.watch-grid{grid-template-columns:1fr}}@media(max-width:520px){.shell{width:min(100% - 1rem,1160px)}.site-header .shell{align-items:flex-start;flex-direction:column}.status-grid{grid-template-columns:1fr}.pipeline{grid-template-columns:1fr}.hero{padding-top:1.5rem}}
</style><script src="/assets/watch.js" defer></script></head>
<body><a class="skip" href="#watch-floor">Skip to watch floor</a><header class="site-header"><div class="shell"><div><p class="eyebrow">Report-first production overlay</p><div class="brand">Braille Errata Relay {% if fixture_mode %}— SANITIZED DEMO FIXTURE{% endif %}</div></div><a class="nav-link" href="/">Review dashboard</a></div></header>
<main id="watch-floor" class="shell"><section class="hero"><p class="eyebrow">Live production-floor monitor</p><h1>Watching authoritative source</h1><p class="lede">This local monitor reads the existing private review API through the loopback presentation server. It does not submit, hold, cancel, release, restart, or operate CUPS or a production device.</p></section>
{% if error %}<p class="notice" role="status">{{ error }}</p>{% endif %}
<section id="mismatch-alert" class="mismatch" {% if not fixture_alert %}hidden{% endif %} aria-labelledby="mismatch-title"><h2 id="mismatch-title">SOURCE / PRODUCTION MISMATCH — HUMAN REVIEW REQUIRED</h2><p id="mismatch-alert-text">A newly observed durable transition requires human review.</p><div class="controls"><button id="enable-audible-alerts" type="button">Enable audible alerts</button><button id="mute-audible-alerts" class="secondary" type="button">Mute</button><button id="acknowledge-alert-locally" class="secondary" type="button">Acknowledge alert locally</button></div><p><small>Local acknowledgement does not record professional disposition, containment, proof, cancellation, resubmission, or any production action.</small></p></section><p id="watch-alert-live" class="skip" role="alert" aria-live="assertive"></p>
<section class="status-grid" aria-label="Watch monitor status"><article class="status-card"><strong id="watch-connection" class="connection" data-state="connected">{% if fixture_mode %}Connected{% else %}Connecting{% endif %}</strong><span>Loopback event connection</span></article><article class="status-card"><strong id="watch-automatic-cycle" aria-live="polite">{{ watch.automatic_cycle }}</strong><span>Automatic Drive reconciliation</span></article><article class="status-card"><strong>{{ watch.source_label }}</strong><span>Sanitized source display label</span></article><article class="status-card"><strong id="watch-stage">{{ watch.durable_stage }}</strong><span>Current durable stage</span></article></section>
<section class="panel"><h2>Current safe next action</h2><p id="watch-next-action">{{ watch.next_safe_action }}</p></section>
<section class="pipeline" aria-label="Durable incident pipeline"><div class="pipeline-step"><strong>1</strong>Drive revision</div><div class="pipeline-step"><strong>2</strong>Deterministic diff</div><div class="pipeline-step"><strong>3</strong>Liblouis candidate</div><div class="pipeline-step"><strong>4</strong>Page impact</div><div class="pipeline-step"><strong>5</strong>Gemini semantic assessment</div><div class="pipeline-step"><strong>6</strong>Professional report</div></section>
<section class="watch-grid"><section class="panel" aria-labelledby="live-incidents"><h2 id="live-incidents">Durable watch events</h2><ul id="watch-incidents" class="watch-list">{% for incident in snapshot.incidents %}<li class="watch-incident"><a href="/incidents/{{ incident.incident_id }}">Incident {{ incident.incident_id[:12] }}…</a><span>{{ incident.workflow_stage }} — {{ incident.next_safe_action }}</span></li>{% endfor %}</ul><p id="watch-empty" class="empty" {% if snapshot.incidents %}hidden{% endif %}>No incident is currently awaiting review. A quiet queue is not evidence of a completed production action.</p></section><aside class="panel"><h2>Truthful activity</h2><p>Only persisted durable stages are shown. An impact-ready incident says “Next step: semantic assessment”; the monitor never claims Gemini is thinking without a persisted fact.</p><div class="boundary"><strong>Boundary:</strong> deterministic computation, Gemini semantic assessment, real read-only CUPS evidence, human records, and the simulated physical endpoint remain visibly distinct.</div></aside></section>
</main><footer class="footer"><div class="shell">Candidate BRF is not an approved production master. This is a local read-only monitor, not a production control surface.</div></footer></body></html>"""


# The two templates below intentionally supersede the first presentation pass.
# Keeping the string-local templates makes the loopback surface portable while
# retaining the original route and CSRF boundaries.  The visual hierarchy is
# driven by persisted artifacts and eligibility, not by a demo-only state.
_TEMPLATES["watch.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Live watch floor</title>
<style>
:root{color-scheme:light;--ink:#10263f;--muted:#53657b;--paper:#f4f7fb;--card:#fff;--line:#d0dbe8;--navy:#173f75;--teal:#007b78;--amber:#7e5600;--red:#9c2040;--violet:#5d459e;--shadow:0 12px 30px rgba(20,42,74,.09)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(1160px,calc(100% - 2rem));margin:auto}.skip{position:absolute;left:-999px;top:0}.skip:focus{left:1rem;top:1rem;z-index:5;background:#fff;padding:.6rem 1rem;border:3px solid var(--navy)}.site-header{background:var(--ink);color:#fff;border-bottom:5px solid #28aab9}.site-header .shell{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 0}.eyebrow{margin:0;color:#bdebf1;font-size:.76rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.brand{font-weight:900;letter-spacing:.015em}.nav-link{color:#fff;font-weight:800}.hero{padding:2.4rem 0 1.2rem}.hero h1{margin:.25rem 0 .55rem;font-size:clamp(2rem,5vw,3.6rem);line-height:1.06}.lede{max-width:75ch;margin:0;color:var(--muted);font-size:1.08rem}.status-grid,.watch-grid{display:grid;gap:1rem}.status-grid{grid-template-columns:repeat(4,1fr);margin:1.1rem 0}.status-card,.panel,.pipeline-step,.report-hero{background:var(--card);border:1px solid var(--line);border-radius:13px;box-shadow:var(--shadow)}.status-card{padding:1rem}.status-card strong{display:block;font-size:1.02rem}.status-card span{color:var(--muted);font-size:.88rem}.connection{font-weight:900}.connection[data-state=connected]{color:var(--teal)}.connection[data-state=disconnected]{color:var(--red)}.pipeline{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.6rem;margin:1rem 0 1.4rem}.pipeline-step{padding:.8rem;font-size:.84rem;border-left:5px solid #b4c0d0}.pipeline-step strong{display:block;color:var(--navy);font-size:.69rem;letter-spacing:.05em;text-transform:uppercase}.pipeline-step.complete{border-left-color:var(--teal);background:#effaf8}.pipeline-step.current{border-left-color:var(--navy);background:#eaf2ff}.pipeline-step.blocked{border-left-color:var(--red);background:#fff1f4}.pipeline-step.waiting{opacity:.72}.watch-grid{grid-template-columns:minmax(0,1fr) minmax(280px,.72fr);padding-bottom:2rem}.panel,.report-hero{padding:1.1rem}.panel h2,.report-hero h2{margin:0 0 .55rem;font-size:1.2rem}.notice,.empty{background:#fff9df;border-left:5px solid #b98400;padding:1rem;margin:1rem 0}.mismatch{background:#fff0f3;border:3px solid var(--red);border-radius:13px;padding:1rem 1.1rem;margin:1rem 0}.mismatch h2{margin:0;color:var(--red);font-size:clamp(1.2rem,3.5vw,1.8rem);letter-spacing:.02em}.mismatch p{margin:.35rem 0}.controls{display:flex;flex-wrap:wrap;gap:.55rem;margin-top:.8rem}.controls button{appearance:none;background:var(--navy);border:0;border-radius:7px;color:#fff;cursor:pointer;font:inherit;font-weight:800;padding:.58rem .75rem}.controls button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.controls button:focus-visible,a:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}.report-hero{border-left:7px solid var(--violet);margin:1rem 0}.report-hero .kicker{color:var(--violet);font-weight:900;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase}.report-hero a{display:inline-block;background:var(--navy);color:#fff;padding:.58rem .85rem;border-radius:7px;text-decoration:none;font-weight:800;margin-top:.5rem}.watch-list{list-style:none;padding:0;margin:0}.watch-incident{border-top:1px solid var(--line);padding:.8rem 0}.watch-incident:first-child{border-top:0}.watch-incident a{display:block;color:var(--navy);font-weight:900;overflow-wrap:anywhere}.watch-incident span{color:var(--muted);display:block;font-size:.9rem}.boundary{background:#edf7f8;border:1px solid #a7d8da;border-radius:10px;padding:1rem}.boundary strong{color:var(--teal)}.footer{border-top:1px solid var(--line);padding:1.4rem 0 2.5rem;color:var(--muted);font-size:.9rem}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}@media(max-width:860px){.status-grid{grid-template-columns:repeat(2,1fr)}.pipeline{grid-template-columns:repeat(3,1fr)}.watch-grid{grid-template-columns:1fr}}@media(max-width:520px){.shell{width:min(100% - 1rem,1160px)}.site-header .shell{align-items:flex-start;flex-direction:column}.status-grid,.pipeline{grid-template-columns:1fr}.hero{padding-top:1.5rem}}
</style><script src="/assets/watch.js" defer></script></head>
<body><a class="skip" href="#watch-floor">Skip to watch floor</a><header class="site-header"><div class="shell"><div><p class="eyebrow">Report-first production overlay</p><div class="brand">Braille Errata Relay {% if fixture_mode %}— SANITIZED DEMO FIXTURE{% endif %}</div></div><a class="nav-link" href="/">Review dashboard</a></div></header>
<main id="watch-floor" class="shell"><section class="hero"><p class="eyebrow">Live operations view</p><h1>Watching the authoritative source</h1><p class="lede">Background reconciliation is durable and evidence-led. The local browser is read-only: it cannot edit Drive, call Gemini, or control CUPS, an embosser, or another production device.</p></section>
{% if error %}<p class="notice" role="status">{{ error }}</p>{% endif %}
<section id="mismatch-alert" class="mismatch" {% if not fixture_alert %}hidden{% endif %} aria-labelledby="mismatch-title"><h2 id="mismatch-title">SOURCE / PRODUCTION MISMATCH — HUMAN REVIEW REQUIRED</h2><p id="mismatch-alert-text">A newly observed durable transition requires human review.</p><div class="controls"><button id="enable-audible-alerts" type="button">Enable audible alerts</button><button id="mute-audible-alerts" class="secondary" type="button">Mute</button><button id="acknowledge-alert-locally" class="secondary" type="button">Acknowledge alert locally</button></div><p><small>Local acknowledgement creates no professional record and no production action.</small></p></section><p id="watch-alert-live" class="skip" role="alert" aria-live="assertive"></p>
<section class="status-grid" aria-label="Watch monitor status"><article class="status-card"><strong id="watch-connection" class="connection" data-state="connected">{% if fixture_mode %}Connected{% else %}Connecting{% endif %}</strong><span>Loopback event connection</span></article><article class="status-card"><strong id="watch-automatic-cycle" aria-live="polite">{{ watch.automatic_cycle }}</strong><span>Automatic Drive reconciliation</span></article><article class="status-card"><strong>{{ watch.source_label }}</strong><span>Configured source of truth</span></article><article class="status-card"><strong id="watch-stage">{{ watch.stage_label }}</strong><span>Latest durable workflow fact</span></article></section>
<section class="panel"><h2>Current safe next action</h2><p id="watch-next-action">{{ watch.next_safe_action }}</p></section>
<section id="watch-hero" class="report-hero" {% if not watch.hero %}hidden{% endif %} aria-labelledby="watch-hero-title"><p id="watch-hero-kicker" class="kicker">{% if watch.hero and watch.hero.workflow_stage == "NEEDS_REVIEW" %}Material issue detected — safe human review required{% else %}Professional recovery report ready{% endif %}</p><h2 id="watch-hero-title">{% if watch.hero and watch.hero.watch_highlight %}{{ watch.hero.watch_highlight.materiality }} {{ watch.hero.watch_highlight.change_kind|replace("_", " ") }}{% endif %}</h2><p id="watch-hero-status">{% if watch.hero and watch.hero.workflow_stage == "NEEDS_REVIEW" %}Astra completed the bounded investigation and stopped safely for qualified human review.{% elif watch.hero %}The bounded autonomous investigation is complete; a production coordinator can review the recovery report.{% endif %}</p><p id="watch-hero-impact">{% if watch.hero and watch.hero.watch_highlight %}Braille impact: baseline pages {% if watch.hero.watch_highlight.old_page_range %}{{ watch.hero.watch_highlight.old_page_range.start }}–{{ watch.hero.watch_highlight.old_page_range.end }}{% else %}not recorded{% endif %}, candidate pages {% if watch.hero.watch_highlight.new_page_range %}{{ watch.hero.watch_highlight.new_page_range.start }}–{{ watch.hero.watch_highlight.new_page_range.end }}{% else %}not recorded{% endif %} of {{ watch.hero.watch_highlight.candidate_page_count }}.{% if watch.hero.watch_highlight.resynchronized_after_page %} Resynchronized after page {{ watch.hero.watch_highlight.resynchronized_after_page }}.{% endif %}{% endif %}</p><a id="watch-hero-link" href="{% if watch.hero %}/incidents/{{ watch.hero.incident_id }}{% else %}/{% endif %}">Review incident</a></section>
<section id="watch-pipeline" class="pipeline" aria-label="Durable workflow progress"><article class="pipeline-step waiting" data-stage="DETECTED"><strong>1 · Source</strong>Authoritative revision verified</article><article class="pipeline-step waiting" data-stage="DIFF_READY"><strong>2 · Diff</strong>Source correction isolated</article><article class="pipeline-step waiting" data-stage="CANDIDATE_READY"><strong>3 · Braille</strong>Candidate regenerated</article><article class="pipeline-step waiting" data-stage="IMPACT_READY"><strong>4 · Impact</strong>Page impact calculated</article><article class="pipeline-step waiting" data-stage="SEMANTIC_READY"><strong>5 · Gemini</strong>Assessment recorded</article><article class="pipeline-step waiting" data-stage="REPORT_READY"><strong>6 · Report</strong>Professional report ready</article></section>
<section class="watch-grid"><section class="panel" aria-labelledby="live-incidents"><h2 id="live-incidents">Durable review queue</h2><ul id="watch-incidents" class="watch-list">{% for incident in snapshot.incidents %}<li class="watch-incident"><a href="/incidents/{{ incident.incident_id }}">Review incident {{ incident.incident_id[:12] }}…</a><span>{{ incident.workflow_label }} — {{ incident.next_safe_action }}</span></li>{% endfor %}</ul><p id="watch-empty" class="empty" {% if snapshot.incidents %}hidden{% endif %}>No incident is currently awaiting review. The approved baseline remains the current reference until a new authoritative revision is verified.</p></section><aside class="panel"><h2>Truthful activity</h2><p>Only stored workflow facts are shown. “Gemini assessment recorded” means the bounded structured assessment is persisted; this monitor never invents a live model-thinking state.</p><div class="boundary"><strong>Boundary:</strong> deterministic source/Braille calculation, Gemini assessment, read-only production observation, human records, and the simulated physical endpoint are distinct facts.</div></aside></section>
</main><footer class="footer"><div class="shell">Candidate BRF is not an approved production master. This is a local read-only monitor, not a production-control surface.</div></footer></body></html>"""


_TEMPLATES["incident.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Incident decision cockpit</title>
<style>
:root{color-scheme:light;--ink:#10263f;--muted:#53657b;--paper:#f4f7fb;--card:#fff;--line:#d0dbe8;--navy:#173f75;--teal:#007b78;--amber:#7e5600;--red:#9c2040;--violet:#5d459e;--shadow:0 12px 30px rgba(20,42,74,.09)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(1180px,calc(100% - 2rem));margin:auto}.skip{position:absolute;left:-999px;top:0}.skip:focus{left:1rem;top:1rem;z-index:5;background:#fff;padding:.6rem 1rem;border:3px solid var(--navy)}.site-header{background:var(--ink);color:#fff;border-bottom:5px solid #28aab9}.site-header .shell{padding:1rem 0}.brand{font-weight:900}.eyebrow{margin:0;color:#bdebf1;font-size:.76rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}main{padding:1.4rem 0 3rem}.back{color:var(--navy);font-weight:800}.status,.card,.action-card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.status{padding:1.25rem;margin:1rem 0;border-left:7px solid var(--navy)}.status h1{margin:.2rem 0 .5rem;font-size:clamp(1.7rem,4vw,2.8rem);line-height:1.1}.grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(290px,.75fr);gap:1rem}.card,.action-card{padding:1.1rem;margin-bottom:1rem}.card h2,.action-card h2{margin:0 0 .65rem;font-size:1.2rem}.card h3{margin:1rem 0 .4rem;font-size:1rem}.card p{margin:.45rem 0}.badges{display:flex;flex-wrap:wrap;gap:.35rem}.badge{display:inline-block;border-radius:999px;padding:.18rem .55rem;font-size:.7rem;font-weight:800;letter-spacing:.04em;border:1px solid currentColor}.deterministic{color:var(--navy);background:#eaf2ff}.gemini{color:var(--violet);background:#f0edff}.human{color:var(--amber);background:#fff7df}.real{color:var(--teal);background:#e8faf8}.simulated{color:var(--red);background:#fff0f2}.block{color:var(--red);font-weight:800}.notice{background:#fff9df;border-left:5px solid var(--amber);padding:1rem;margin:1rem 0}.compare{display:grid;grid-template-columns:1fr 1fr;gap:.75rem}.compare article{border-left:4px solid var(--navy);background:#f1f5fb;padding:.75rem}.compare article:last-child{border-left-color:var(--teal)}.compare h3{margin:0}.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem;margin:1rem 0}.summary{background:#fff;border:1px solid var(--line);border-radius:10px;padding:.8rem}.summary strong{display:block;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}.progress{display:flex;flex-wrap:wrap;gap:.45rem;margin:.8rem 0}.progress span{border:1px solid var(--line);border-radius:999px;padding:.24rem .52rem;font-size:.75rem;font-weight:800}.progress .complete{border-color:var(--teal);background:#effaf8;color:var(--teal)}.progress .blocked{border-color:var(--red);background:#fff1f4;color:var(--red)}.ripple{margin:0}.ripple-row{display:flex;gap:3px;min-height:1.8rem;margin:.45rem 0}.ripple-segment{display:flex;align-items:center;justify-content:center;min-width:1.5rem;padding:.2rem;color:#14233b;font-size:.75rem;font-weight:800;text-align:center}.ripple-segment.match{background:#dce8f5}.ripple-segment.changed{background:#f5bf4f}.ripple-segment.suffix{background:#ccece5}.ripple figcaption{color:var(--muted);font-size:.92rem}.action-card{border-left:7px solid var(--amber)}.action-card .role{background:#fffbeb;border:1px solid #e5c66b;border-radius:8px;padding:.75rem}.action{background:var(--navy);border:0;border-radius:7px;color:#fff;cursor:pointer;font:inherit;font-weight:800;padding:.65rem .9rem}.action[disabled]{background:#9ba8b8;cursor:not-allowed}.print-link,.download{display:inline-block;background:var(--teal);color:#fff;padding:.65rem .9rem;border-radius:7px;font-weight:800;text-decoration:none}.print-link:hover,.download:hover{background:#06595a}label{display:block;font-weight:700;margin:.75rem 0}select,textarea,input[type=number]{display:block;width:100%;margin-top:.25rem;border:1px solid #8797ad;border-radius:6px;padding:.55rem;font:inherit;background:#fff}textarea{min-height:5rem}input[type=hidden]{display:none}details{margin:.85rem 0;border:1px solid var(--line);border-radius:10px;background:#fff;padding:.75rem}summary{cursor:pointer;font-weight:900}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f9;border:1px solid var(--line);padding:.75rem;border-radius:7px;font-size:.83rem}ol{padding-left:1.3rem}li{margin:.45rem 0}.boundary{background:#edf7f8;border:1px solid #a7d8da;border-radius:10px;padding:1rem}.footer{border-top:1px solid var(--line);padding:1.5rem 0 2.5rem;color:var(--muted);font-size:.9rem}a:focus-visible,button:focus-visible,select:focus-visible,textarea:focus-visible,input:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}@media(max-width:850px){.grid{grid-template-columns:1fr}.summary-grid{grid-template-columns:1fr}.compare{grid-template-columns:1fr}}@media(max-width:520px){.shell{width:min(100% - 1rem,1180px)}.status,.card,.action-card{padding:1rem}}
</style></head>
<body><a class="skip" href="#incident-content">Skip to incident</a><header class="site-header"><div class="shell"><p class="eyebrow">Report-first production overlay</p><div class="brand">Braille Errata Relay{% if fixture_mode %} — SANITIZED DEMO FIXTURE{% endif %}</div></div></header>
{% macro disposition_form() -%}
{% if error %}<p>Professional disposition controls are unavailable until authoritative private review data is loaded.</p>{% elif fixture_mode %}<button class="action" disabled>Offline fixture: disposition recording disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/professional-dispositions"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="production_coordinator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ disposition_idempotency_key }}"><label>Decision <select name="decision">{% for decision in decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label><label>Coordinator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record professional disposition</button></form>{% endif %}
{%- endmacro %}
{% macro operator_form() -%}
{% if error %}<p>Operator attestation controls are unavailable until authoritative private review data is loaded.</p>{% elif fixture_mode %}<button class="action" disabled>Offline fixture: operator attestation disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/operator-attestations"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="machine_operator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ attestation_idempotency_key }}"><label>Attributable fact <select name="attestation_type">{% for kind in attestation_types %}<option value="{{ kind }}">{{ kind }}</option>{% endfor %}</select></label><label>Truth basis <select name="truth_basis">{% for basis in truth_bases %}<option value="{{ basis }}">{{ basis }}</option>{% endfor %}</select></label><label>Operator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record operator attestation</button></form>{% endif %}
{%- endmacro %}
{% macro containment_form() -%}
<p>CUPS state alone never proves device stop or physical-output isolation.</p>{% if review_actions.containment_confirmation.eligible %}{% if fixture_mode %}<button class="action" disabled>Offline fixture: containment recording disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/containment-confirmations"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="selected_role" value="production_coordinator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ containment_idempotency_key }}"><input type="hidden" name="halt_disposition_record_id" value="{{ review_actions.containment_confirmation.halt_disposition_record_id }}"><input type="hidden" name="site_observation_id" value="{{ review_actions.containment_confirmation.site_observation_id }}"><input type="hidden" name="physical_output_isolation_attestation_id" value="{{ review_actions.containment_confirmation.physical_output_isolation_attestation_id }}"><label>Coordinator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record containment confirmation</button></form>{% endif %}{% else %}<p>Containment confirmation is unavailable: {{ review_actions.containment_confirmation.blocking_reason }}.</p>{% endif %}
{%- endmacro %}
{% macro proof_form() -%}
<p>Fixture review is not independent professional certification. Approval does not submit, link, release, or verify a replacement job.</p>{% if review_actions.proof.eligible %}{% if fixture_mode %}<button class="action" disabled>Offline fixture: proof decision recording disabled</button>{% else %}<form method="post" action="/incidents/{{ incident_id }}/proof-records"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="candidate_sha256" value="{{ review_actions.proof.provenance.candidate_sha256 }}"><input type="hidden" name="manifest_sha256" value="{{ review_actions.proof.provenance.manifest_sha256 }}"><input type="hidden" name="review_basis" value="DEMO_FIXTURE_REVIEW"><input type="hidden" name="selected_role" value="proofreader"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ proof_idempotency_key }}"><label>Decision <select name="decision">{% for decision in proof_decisions %}<option value="{{ decision }}">{{ decision }}</option>{% endfor %}</select></label><label>Proof note <textarea name="note" maxlength="2000"></textarea></label><label><input type="checkbox" name="visual_only_uncertainty" value="true"> Visual-only uncertainty remains (blocks approval).</label><button class="action" type="submit">Record exact-candidate proof decision</button></form>{% endif %}{% else %}<p>Proof review is unavailable: {{ review_actions.proof.blocking_reason }}.</p>{% endif %}
{%- endmacro %}
{% macro replacement_form() -%}
<p>After an independent CUPS/vendor submission, a machine operator may link only a fresh read-only observation. This does not prove endpoint completion or physical output.</p>{% if review_actions.replacement_observation.eligible %}{% if fixture_mode %}<div class="role"><strong>Proof-ready offline fixture:</strong> replacement linking is intentionally disabled; no human record can be posted.</div>{% else %}<form method="post" action="/incidents/{{ incident_id }}/replacement-observation-links"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><input type="hidden" name="candidate_sha256" value="{{ review_actions.replacement_observation.provenance.candidate_sha256 }}"><input type="hidden" name="candidate_manifest_sha256" value="{{ review_actions.replacement_observation.provenance.manifest_sha256 }}"><input type="hidden" name="proof_record_id" value="{{ review_actions.replacement_observation.provenance.proof_record_id }}"><input type="hidden" name="site_observation_id" value="{{ current_observation_id|default('') }}"><input type="hidden" name="selected_role" value="machine_operator"><input type="hidden" name="expected_state_version" value="{{ review_state.state_version }}"><input type="hidden" name="idempotency_key" value="{{ replacement_idempotency_key }}"><label>Observed replacement scheduler job ID <input name="scheduler_job_id" type="number" min="1" required></label><label>Operator note <textarea name="note" maxlength="2000"></textarea></label><button class="action" type="submit">Record replacement observation link</button></form>{% endif %}{% else %}<p>Replacement observation is unavailable: {{ review_actions.replacement_observation.blocking_reason }}.</p>{% endif %}
{%- endmacro %}
<main id="incident-content" class="shell"><p><a class="back" href="/">← All incidents</a></p>{% if error %}<p class="notice" role="alert">{{ error }}</p>{% endif %}
<section class="status"><div class="badges"><span class="badge deterministic">DETERMINISTIC REPORT</span><span class="badge human">HUMAN AUTHORITY</span>{% if fixture_mode %}<span class="badge simulated">SANITIZED FIXTURE</span>{% endif %}</div><h1>Professional incident review</h1><p><strong>{{ display.workflow_label }}</strong>{% if review_state.blocking_reason %} <span class="block">— {{ review_state.blocking_reason }}</span>{% endif %}</p><p>Relay explains evidence and records human disposition. It does not control CUPS or an embosser.</p></section>
<section class="card"><div class="badges"><span class="badge deterministic">AUTHORITATIVE SOURCE</span></div><h2>Material correction</h2><div class="compare"><article><h3>Old source</h3><p>{{ source_comparison.old }}</p></article><article><h3>New source</h3><p>{{ source_comparison.new }}</p></article></div><div class="progress" aria-label="Evidence-backed workflow progress">{% for step in display.workflow_progress %}<span class="{{ step.status }}">{{ step.label }}</span>{% endfor %}</div></section>
<section class="summary-grid" aria-label="Incident decision summary"><article class="summary"><strong>Gemini assessment</strong><p>{{ semantic_materiality }} · {{ semantic_change_kind }}</p><p>{{ uncertainty_summary }}</p></article><article class="summary"><strong>[REAL] Read-only production context</strong><p><strong>Report evidence at creation:</strong> {{ report_observation_age }}</p><p><strong>Current monitor record:</strong> {{ current_monitor_summary }}</p></article><article class="summary"><strong>Safe next action</strong><p>{{ next_safe_action }}</p></article></section>
<section class="card"><div class="badges"><span class="badge deterministic">DETERMINISTIC BRAILLE IMPACT</span></div><h2>Small source correction → bounded Braille ripple</h2>{% if display.ripple.available %}<figure class="ripple"><p><strong>{{ display.ripple.headline }}</strong></p><div class="ripple-row" aria-label="Baseline and candidate page ripple">{% for segment in display.ripple.segments %}<span class="ripple-segment {{ segment.kind }}" style="flex: {{ segment.pages }}">{{ segment.label }}</span>{% endfor %}</div><figcaption>Baseline: {{ display.ripple.baseline_total }} pages. Candidate: {{ display.ripple.candidate_total }} pages. {{ display.ripple.resynchronization }}</figcaption></figure>{% else %}<p>{{ display.ripple.headline }}</p>{% endif %}</section>
<section class="card"><div class="badges"><span class="badge gemini">GEMINI STRUCTURED ASSESSMENT</span></div><h2>Why this needs a professional</h2><p>{{ semantic_summary }}</p><h3>Persisted uncertainty</h3><ul>{% for uncertainty in uncertainties %}<li>{{ uncertainty }}</li>{% else %}<li>No uncertainty was recorded in this assessment.</li>{% endfor %}</ul></section>
<section class="action-card" aria-labelledby="current-human-action"><div class="badges"><span class="badge human">CURRENT HUMAN ROLE</span></div><h2 id="current-human-action">{{ display.cockpit.status }}</h2><p class="role"><strong>Required role:</strong> {{ display.cockpit.role }}. <strong>Current action:</strong> {{ display.cockpit.action }}.</p>{% if display.cockpit.form == "disposition" %}{{ disposition_form() }}{% elif display.cockpit.form == "containment" %}{{ containment_form() }}{% elif display.cockpit.form == "proof" %}{{ proof_form() }}{% elif display.cockpit.form == "replacement" %}{{ replacement_form() }}{% else %}<p class="block">No human-record form is currently eligible. Review the visible evidence and blocking reason.</p>{% endif %}<p><a class="print-link" href="/incidents/{{ incident_id }}/report">View / print full incident report</a></p></section>
<details><summary>Audit evidence and later human handoffs</summary><section class="card"><h2>Candidate and tool identity</h2><p><strong>Candidate status:</strong> CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER</p><p>This is an <strong>approved demo-fixture candidate for human-controlled submission</strong>, not a certified production master.</p>{% if review_actions.replacement_observation.candidate_download_eligible %}<p><a class="download" href="/incidents/{{ incident_id }}/approved-candidate">Download exact approved candidate BRF</a></p>{% endif %}<pre>{{ candidate_manifest }}</pre><pre>{{ profile_identity }}</pre><h3>{{ candidate_evidence_preview.label }}</h3><pre>{{ candidate_evidence_preview.text }}</pre></section><section class="card"><h2>Other human workflow gates</h2>{% if display.cockpit.form != "disposition" %}<h3>Professional disposition</h3>{{ disposition_form() }}{% endif %}<h3>[HUMAN ATTESTATION] Operator attestation</h3>{{ operator_form() }}{% if display.cockpit.form != "containment" %}<h3>Containment confirmation</h3>{{ containment_form() }}{% endif %}{% if display.cockpit.form != "proof" %}<h3>Exact candidate proof gate</h3>{{ proof_form() }}{% endif %}{% if display.cockpit.form != "replacement" %}<h3>Replacement observation</h3>{{ replacement_form() }}{% endif %}</section><section class="card"><h2>Immutable evidence</h2><h3>Stored source correction</h3><pre>{{ source_correction }}</pre><h3>Exact page-impact evidence</h3><pre>{{ braille_impact }}</pre><h3>Current read-only observation</h3><pre>{{ current_observation }}</pre><h3>Attributable timeline</h3><ol>{% for event in timeline %}<li><strong>[{{ event.truth_basis }}] {{ event.kind }}</strong><br>{{ event.recorded_at }}</li>{% else %}<li>No attributable events are available.</li>{% endfor %}</ol></section></details>
<section class="boundary"><strong>[SIMULATED ENDPOINT] System boundary:</strong> only the physical endpoint is simulated. Relay does not control CUPS or the embosser. A scheduler observation does not prove device stop, physical isolation, endpoint completion, or final verification. All production actions remain human-owned.</section>
</main><footer class="footer"><div class="shell">Candidate BRF is not an approved production master. Relay does not operate the production queue or device.</div></footer></body></html>"""


_TEMPLATES["report.html"] = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Braille Errata Relay | Printable incident report</title>
<style>
:root{--ink:#10263f;--muted:#53657b;--paper:#f4f7fb;--card:#fff;--line:#d0dbe8;--navy:#173f75;--teal:#007b78;--amber:#7e5600;--violet:#5d459e}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.sheet{width:min(900px,calc(100% - 2rem));margin:2rem auto;background:var(--card);border:1px solid var(--line);padding:2rem;box-shadow:0 12px 30px rgba(20,42,74,.09)}.eyebrow{color:var(--teal);font-size:.78rem;font-weight:900;letter-spacing:.09em;text-transform:uppercase}.meta{color:var(--muted)}h1{font-size:clamp(2rem,5vw,3rem);line-height:1.08;margin:.3rem 0}.card{border-top:2px solid var(--line);padding:1.1rem 0}.card h2{margin:0 0 .55rem}.compare{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}.compare div{padding:.8rem;background:#f1f5fb;border-left:4px solid var(--navy)}.compare div:last-child{border-left-color:var(--teal)}.ripple-row{display:flex;gap:3px;min-height:1.8rem}.ripple-segment{display:flex;align-items:center;justify-content:center;min-width:1.5rem;padding:.2rem;font-size:.75rem;font-weight:800;text-align:center}.match{background:#dce8f5}.changed{background:#f5bf4f}.suffix{background:#ccece5}.callout{background:#fffbeb;border-left:5px solid var(--amber);padding:1rem}.boundary{background:#edf7f8;border:1px solid #a7d8da;padding:1rem}.button{display:inline-block;background:var(--navy);color:#fff;text-decoration:none;padding:.55rem .8rem;border-radius:7px;font-weight:800}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem;background:#f1f4f9;padding:.75rem;border:1px solid var(--line)}details{margin:.8rem 0}summary{font-weight:800;cursor:pointer}@media(max-width:620px){.sheet{width:100%;margin:0;border:0;padding:1rem}.compare{grid-template-columns:1fr}}@media print{body{background:#fff}.sheet{width:100%;margin:0;border:0;box-shadow:none;padding:0}.no-print{display:none}details{display:block}details[open]{display:block}summary{display:none}.card{break-inside:avoid}}
</style><script src="/assets/report.js" defer></script></head>
<body><main class="sheet">{% if error %}<p class="callout" role="alert">{{ error }}</p>{% else %}<p class="eyebrow">Braille Errata Relay · printable incident report{% if fixture_mode %} · sanitized demo fixture{% endif %}</p><h1>{{ display.workflow_label }}</h1><p class="meta">Incident {{ incident_id[:12] }}… · generated from existing immutable report and disposition-packet evidence. Use browser Print / Save as PDF for a local copy.</p><p class="no-print"><a class="button" href="/incidents/{{ incident_id }}">Return to decision cockpit</a> <button id="print-report" type="button">Print / Save as PDF</button></p>
<section class="card"><h2>Executive decision summary</h2><p><strong>Current human role:</strong> {{ display.cockpit.role }}</p><p><strong>Required action:</strong> {{ display.cockpit.action }}</p><p>{{ next_safe_action }}</p>{% if review_state.blocking_reason %}<p class="callout"><strong>Blocking reason:</strong> {{ review_state.blocking_reason }}</p>{% endif %}</section>
<section class="card"><h2>Authoritative source evidence</h2><div class="compare"><div><strong>Old source</strong><p>{{ source_comparison.old }}</p></div><div><strong>New source</strong><p>{{ source_comparison.new }}</p></div></div></section>
<section class="card"><h2>Gemini semantic assessment</h2><p><strong>Materiality / change kind:</strong> {{ semantic_materiality }} · {{ semantic_change_kind }}</p><p>{{ semantic_summary }}</p><p><strong>Uncertainty:</strong> {{ uncertainty_summary }}</p></section>
<section class="card"><h2>Deterministic Braille impact</h2>{% if display.ripple.available %}<p><strong>{{ display.ripple.headline }}</strong></p><div class="ripple-row">{% for segment in display.ripple.segments %}<span class="ripple-segment {{ segment.kind }}" style="flex: {{ segment.pages }}">{{ segment.label }}</span>{% endfor %}</div><p>{{ display.ripple.resynchronization }}</p>{% else %}<p>{{ display.ripple.headline }}</p>{% endif %}</section>
<section class="card"><h2>Read-only production context</h2><p><strong>Report evidence at creation:</strong> {{ report_observation_age }}</p><p><strong>Current monitor record:</strong> {{ current_monitor_summary }}</p><p>This is read-only evidence. It does not prove endpoint completion, physical output, device stop, or closure.</p></section>
<section class="card"><h2>Recommended human response</h2><ol>{% for step in recommended_human_steps %}<li>{{ step }}</li>{% else %}<li>Review the evidence with the qualified human role shown above.</li>{% endfor %}</ol></section>
<section class="boundary"><strong>Authority boundary:</strong> Relay presents source evidence, deterministic Braille analysis, Gemini’s persisted structured assessment, read-only production context, and human records. Candidate BRF is not an approved production master. Relay does not certify Braille, submit a replacement, or control a device.</section>
<details><summary>Audit appendix</summary><h2>Stored source correction</h2><pre>{{ source_correction }}</pre><h2>Page-impact evidence</h2><pre>{{ braille_impact }}</pre><h2>Candidate manifest and profile identity</h2><pre>{{ candidate_manifest }}</pre><pre>{{ profile_identity }}</pre><h2>Attributable timeline</h2><ol>{% for event in timeline %}<li><strong>[{{ event.truth_basis }}] {{ event.kind }}</strong> — {{ event.recorded_at }}</li>{% else %}<li>No attributable events are available.</li>{% endfor %}</ol></details>{% endif %}</main></body></html>"""


_SETUP_STYLE = """
:root{--ink:#15233b;--muted:#52627a;--paper:#f5f7fb;--card:#fff;--line:#d7dfeb;--navy:#173f75;--teal:#027b7b;--amber:#8a5b00;--red:#a02638}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}.head{background:var(--ink);color:#fff;border-bottom:5px solid #2ca5b5}.shell{width:min(960px,calc(100% - 2rem));margin:auto}.head .shell{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:1rem 0}.head a{color:#fff}.brand{font-weight:850}.eyebrow{color:#097579;font-size:.78rem;font-weight:900;letter-spacing:.08em;text-transform:uppercase}.head .eyebrow{color:#bce7ee;margin:0}.hero{padding:2rem 0 1rem}.hero h1{font-size:clamp(2rem,5vw,3.2rem);line-height:1.05;margin:.25rem 0}.lede{color:var(--muted);max-width:72ch}.steps{display:flex;gap:.5rem;flex-wrap:wrap;margin:1rem 0}.step{border:1px solid var(--line);border-radius:999px;padding:.3rem .7rem;font-weight:750;background:#fff}.step.current{background:#e7f5f5;border-color:var(--teal);color:#056466}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1.2rem;margin:1rem 0;box-shadow:0 12px 28px rgba(20,42,74,.08)}.card h2{margin-top:0}.notice{background:#fff9df;border-left:5px solid var(--amber);padding:1rem}.success{background:#e9f8f5;border-left:5px solid var(--teal);padding:1rem}.error{background:#fff0f2;border-left:5px solid var(--red);padding:1rem}label{display:block;font-weight:750;margin:.9rem 0}input,select{display:block;width:100%;margin-top:.3rem;padding:.65rem;border:1px solid #8496ae;border-radius:7px;font:inherit}.choice{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.choice a{display:block;border:1px solid var(--line);border-radius:10px;padding:1rem;color:var(--ink);text-decoration:none;background:#fff}.button,button{display:inline-block;border:0;border-radius:7px;padding:.7rem 1rem;background:var(--navy);color:#fff;font:inherit;font-weight:850;text-decoration:none;cursor:pointer}.secondary{background:#edf3fb;color:var(--navy);border:1px solid var(--navy)}code,pre{background:#eef2f7;border:1px solid var(--line);border-radius:6px;padding:.2rem .4rem;overflow-wrap:anywhere}pre{padding:.8rem;white-space:pre-wrap}.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}.fact{background:#f3f6fa;padding:.75rem;border-radius:8px}.fact strong{display:block}.boundary{border:1px solid #a7d8da;background:#edf7f8;padding:1rem;border-radius:10px;margin:1.5rem 0}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #f2ac32;outline-offset:3px}@media(max-width:650px){.choice,.facts{grid-template-columns:1fr}.head .shell{align-items:flex-start;flex-direction:column}}
"""

_TEMPLATES["setup_source.html"] = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Astra | Register authoritative source</title><style>"""
    + _SETUP_STYLE
    + """</style></head><body><header class="head"><div class="shell"><div><p class="eyebrow">Guided baseline onboarding</p><div class="brand">Braille Errata Relay</div></div><a href="/">Review dashboard</a></div></header><main class="shell"><section class="hero"><p class="eyebrow">Step 1 of 3</p><h1>Choose and verify the authoritative source</h1><p class="lede">Astra reads one explicitly configured Drive file. It never creates the file, changes sharing, or edits its contents.</p></section><div class="steps"><span class="step current">1 Source</span><span class="step">2 Baseline</span><span class="step">3 Monitor</span></div>{% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}{% if result %}<section class="card"><h2>{% if result.matches_configured_source %}Source verified and configured{% else %}Source verified; configuration update required{% endif %}</h2><div class="facts"><div class="fact"><strong>Type</strong>{{ result.source_mime_type }}</div><div class="fact"><strong>Parsed blocks</strong>{{ result.block_count }}</div><div class="fact"><strong>Bytes</strong>{{ result.byte_length }}</div><div class="fact"><strong>Source SHA-256</strong><code>{{ result.source_sha256[:16] }}...</code></div></div>{% if result.matches_configured_source %}<p class="success">The private runtime can read this exact configured file. Continue to deterministic baseline registration.</p><a class="button" href="/setup/baseline">Continue to baseline</a>{% else %}<p class="notice"><strong>Human configuration step required.</strong> The runtime can read this file, but Cloud Run is configured for another source. Run the generated command in your own authenticated terminal, wait for the private revision, then verify again. Astra will not modify cloud configuration from this page.</p><pre>{{ update_command }}</pre>{% endif %}</section>{% endif %}<section class="card"><h2>1. Create or upload the source</h2><div class="choice"><a href="https://docs.new" target="_blank" rel="noopener"><strong>Native Google Doc</strong><br>Create a document in Google Docs. Astra exports it read-only as Markdown for the strict parser.</a><div><strong>Markdown file</strong><br>Create a UTF-8 <code>.md</code> file and upload it to Google Drive. Keep it under the configured byte limit.</div></div></section><section class="card"><h2>2. Share read-only</h2><p>In Drive, share the file with this runtime identity as <strong>Viewer</strong>:</p><pre>{{ setup.runtime_service_account_email }}</pre><p>Do not make the file public. Viewer access is sufficient.</p></section><section class="card"><h2>3. Verify access and format</h2><form method="post" action="/setup/source/verify"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><label>Google Drive or Docs URL<input name="source_reference" required maxlength="2048" placeholder="https://docs.google.com/document/d/.../edit"></label><label>Source type<select name="mime_type"><option value="application/vnd.google-apps.document">Native Google Doc</option><option value="text/markdown">Drive-hosted Markdown (.md)</option></select></label><button type="submit">Verify read-only source</button></form></section><section class="boundary"><strong>Authority boundary:</strong> verification performs metadata and byte reads only. It does not create Docs, grant permissions, alter Cloud Run, start the scheduler, register a production job, or touch CUPS.</section></main></body></html>"""
)

_TEMPLATES["setup_baseline.html"] = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Astra | Register baseline</title><style>"""
    + _SETUP_STYLE
    + """</style></head><body><header class="head"><div class="shell"><div><p class="eyebrow">Guided baseline onboarding</p><div class="brand">Braille Errata Relay</div></div><a href="/setup/source">Back to source</a></div></header><main class="shell"><section class="hero"><p class="eyebrow">Step 2 of 3</p><h1>Generate and register the deterministic baseline</h1><p class="lede">Astra will authoritatively refetch the configured source, normalize it, translate with the pinned Liblouis profile, store immutable evidence, and register a demo-fixture baseline.</p></section><div class="steps"><span class="step">1 Source</span><span class="step current">2 Baseline</span><span class="step">3 Monitor</span></div>{% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}{% if not verified %}<p class="notice">Verify the currently configured source before registration. <a href="/setup/source">Return to source setup.</a></p>{% else %}<section class="card"><h2>Baseline identity</h2><form method="post" action="/setup/baseline"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><label>External production reference<input name="production_id" required maxlength="512" placeholder="BIOLOGY-VOLUME-2-DEMO"></label><label>Production site<input name="site_id" required maxlength="512" value="{{ setup.site_id or '' }}"></label><label>Observed queue name<input name="queue_name" required maxlength="512" value="{{ setup.queue_name or '' }}"></label><button type="submit">Initialize source and register baseline</button></form><p>This may take a few seconds. Retrying the same form is safe and idempotent for this browser session.</p></section>{% endif %}<section class="notice"><strong>What this does not do:</strong> it does not approve a real production master, submit a CUPS job, link an existing production job, enable automatic reconciliation, or operate an embosser.</section></main></body></html>"""
)

_TEMPLATES["baseline_monitor.html"] = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><title>Astra | Baseline monitor</title><style>"""
    + _SETUP_STYLE
    + """</style></head><body><header class="head"><div class="shell"><div><p class="eyebrow">Guided baseline onboarding</p><div class="brand">Braille Errata Relay</div></div><a href="/">Review dashboard</a></div></header><main class="shell"><section class="hero"><p class="eyebrow">Step 3 of 3</p><h1>Baseline registered</h1><p class="lede">This page refreshes every 15 seconds and shows only durable, monitor-safe baseline facts.</p></section><div class="steps"><span class="step">1 Source</span><span class="step">2 Baseline</span><span class="step current">3 Monitor</span></div>{% if error %}<p class="error" role="alert">{{ error }}</p>{% else %}<p class="success"><strong>Registration successful.</strong> Astra generated deterministic BRF evidence and registered the baseline without performing a production action.</p><section class="card"><h2>{{ baseline.production_id }}</h2><div class="facts"><div class="fact"><strong>Status</strong>{{ baseline.status }}</div><div class="fact"><strong>State version</strong>{{ baseline.state_version }}</div><div class="fact"><strong>Site / queue</strong>{{ baseline.site_id }} / {{ baseline.queue_name }}</div><div class="fact"><strong>Created</strong>{{ baseline.created_at }}</div><div class="fact"><strong>Baseline ID</strong><code>{{ baseline.baseline_id[:16] }}...</code></div><div class="fact"><strong>BRF SHA-256</strong><code>{{ baseline.approved_brf_sha256[:16] }}...</code></div></div></section><section class="card"><h2>What happens next</h2><ol><li>A qualified human uses the existing production surface if they choose to submit this baseline.</li><li>Astra accepts only fresh, unambiguous read-only observation evidence to link that human-submitted job.</li><li>Source edits are detected through the configured Drive change feed when the authorized automation cycle runs.</li></ol><p><a class="button" href="/watch">Open live watch floor</a> <a class="button secondary" href="/setup/source">Register another source</a></p></section>{% endif %}<section class="boundary"><strong>Authority boundary:</strong> the baseline is a demo-generated fixture, not a certified production master. No CUPS/device action, endpoint completion, or physical output is claimed.</section></main></body></html>"""
)


def _templates() -> Environment:
    return Environment(
        loader=DictLoader(_TEMPLATES),
        autoescape=select_autoescape(("html", "xml"), default_for_string=True),
    )


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _pretty(value: object) -> str:
    return json.dumps(value if value is not None else {}, indent=2, sort_keys=True)


def _next_safe_action(review_state: Mapping[str, object]) -> str:
    """Render a human instruction, never a production-control instruction."""

    state = review_state.get("state")
    if state == "AWAITING_REPLACEMENT":
        return "Use the independent production surface, then link a fresh read-only observation."
    if state == "REPLACEMENT_OBSERVED":
        return "Observed replacement evidence is recorded; final verification remains separate."
    if state == "AWAITING_PROOF":
        return "A proofreader must review the exact candidate before any human submission."
    if state == "CONTAINMENT_IN_PROGRESS":
        return "Complete attributable containment evidence; scheduler state alone is insufficient."
    if review_state.get("blocking_reason"):
        return "Resolve the visible block through human review before changing any production workflow."
    return "Review the authoritative evidence and select the next human-owned action."


def _source_comparison(source_correction: Mapping[str, object]) -> dict[str, str]:
    """Summarize stored source-diff blocks without deriving new source facts."""

    def side(name: str) -> str:
        raw_blocks = source_correction.get(name)
        if not isinstance(raw_blocks, list) or not raw_blocks:
            return "No stored block is available for this side of the correction."
        block = raw_blocks[0]
        if not isinstance(block, Mapping):
            return "Stored source evidence is malformed."
        raw_kind = block.get("kind")
        raw_text = block.get("text")
        kind = raw_kind if isinstance(raw_kind, str) else "block"
        text = raw_text if isinstance(raw_text, str) else ""
        compact = " ".join(text.split())
        if len(compact) > 260:
            compact = f"{compact[:257]}..."
        return compact if compact else f"{kind.capitalize()} contains no stored text"

    return {"old": side("old_blocks"), "new": side("new_blocks")}


def _impact_summary(impact: Mapping[str, object]) -> str:
    """Summarize persisted deterministic impact fields for the top-level cards."""

    old_range = _mapping(impact.get("old_page_range"))
    new_range = _mapping(impact.get("new_page_range"))

    def display(page_range: Mapping[str, object]) -> str:
        start = page_range.get("start")
        end = page_range.get("end")
        if isinstance(start, int) and isinstance(end, int):
            return f"{start}" if start == end else f"{start}–{end}"
        return "none recorded"

    changed = impact.get("pages_changed")
    changed_label = "Changed" if changed is True else "No page-byte change recorded"
    return f"{changed_label}; baseline pages {display(old_range)}, candidate pages {display(new_range)}."


def _semantic_activity(workflow_stage: object) -> str:
    """Describe only persisted state; never infer active model work."""

    if workflow_stage == "SEMANTIC_READY":
        return "Gemini semantic assessment complete."
    if workflow_stage == "IMPACT_READY":
        return "Next step: semantic assessment."
    if workflow_stage == "CANDIDATE_READY":
        return "Next step: deterministic page-impact calculation."
    if workflow_stage == "DIFF_READY":
        return "Next step: deterministic candidate generation."
    if workflow_stage == "REPORT_READY":
        return "Professional report is ready for human review."
    if workflow_stage == "NEEDS_REVIEW":
        return "Human review is required before the workflow can proceed."
    return "Watching for the next persisted workflow stage."


def _current_monitor_summary(observation: Mapping[str, object]) -> str:
    """Describe current read-only evidence without presenting it as report repair."""

    observation_id = observation.get("observation_id")
    observed_at = observation.get("observed_at")
    if isinstance(observation_id, str) and isinstance(observed_at, str):
        return "A newer read-only monitor record is available in the audit appendix."
    return "No current monitor record is available beyond the report evidence."


def _form_error(status_code: int, message: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><p role="alert">{message}</p>', status_code=status_code)


def create_presentation_app(
    settings: PresentationSettings,
    *,
    api_client: PrivateReviewApi | None = None,
    watch_poll_seconds: float = 2.0,
    watch_heartbeat_seconds: float = 12.0,
    watch_monotonic_clock: Callable[[], float] = time.monotonic,
    watch_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FastAPI:
    """Create the local review server; the launcher always binds 127.0.0.1."""

    if watch_poll_seconds <= 0:
        raise ValueError("watch polling interval must be positive")
    if not 10.0 <= watch_heartbeat_seconds <= 15.0:
        raise ValueError("watch heartbeat interval must remain between 10 and 15 seconds")

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

    @app.middleware("http")
    async def private_review_security_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; connect-src 'self'; "
            "style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def render(name: str, **context: object) -> HTMLResponse:
        return HTMLResponse(templates.get_template(name).render(**context))

    async def watch_snapshot() -> dict[str, object]:
        """Combine private read-only incident and durable automation state.

        Incident availability is the requirement for the watch floor.  A
        status-only failure is represented locally as an unavailable automation
        card so the browser never mistakes its own polling for a Drive check.
        """

        incidents = await api.get_json("/api/v1/incidents")
        try:
            automation = await api.get_json("/api/v1/automation-status")
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            automation = None
        return sanitize_watch_snapshot(incidents, automation=automation)

    def csrf_token(request: Request) -> str:
        token = request.session.get("csrf_token")
        if isinstance(token, str) and token:
            return token
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
        return token

    def is_local_form_origin(value: str | None) -> bool:
        if value is None:
            return False
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() == "http"
            and parsed.hostname == "127.0.0.1"
            and port == settings.port
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    def is_local_incident_referer(value: str | None) -> bool:
        if value is None:
            return False
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError:
            return False
        path_parts = parsed.path.rstrip("/").split("/")
        return (
            parsed.scheme.casefold() == "http"
            and parsed.hostname == "127.0.0.1"
            and port == settings.port
            and parsed.username is None
            and parsed.password is None
            and len(path_parts) == 3
            and path_parts[:2] == ["", "incidents"]
            and re.fullmatch(r"[0-9a-f]{64}", path_parts[2]) is not None
            and parsed.path
            in {
                f"/incidents/{path_parts[2]}",
                f"/incidents/{path_parts[2]}/",
            }
            and not parsed.query
            and not parsed.fragment
        )

    def is_local_setup_referer(value: str | None) -> bool:
        if value is None:
            return False
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() == "http"
            and parsed.hostname == "127.0.0.1"
            and port == settings.port
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") in {"/setup/source", "/setup/baseline"}
            and not parsed.query
            and not parsed.fragment
        )

    def require_local_form(request: Request, csrf: str) -> HTMLResponse | None:
        if request.headers.get("host") != f"127.0.0.1:{settings.port}":
            return _form_error(403, "Local review requests must use the loopback host.")
        origin = request.headers.get("origin")
        if not is_local_form_origin(origin):
            opaque_or_absent_origin = origin is None or origin.strip().casefold() == "null"
            same_origin_metadata = request.headers.get("sec-fetch-site") == "same-origin"
            local_referer = is_local_incident_referer(
                request.headers.get("referer")
            ) or is_local_setup_referer(request.headers.get("referer"))
            if not (opaque_or_absent_origin and same_origin_metadata and local_referer):
                return _form_error(403, "The local review form origin was not accepted.")
        expected = request.session.get("csrf_token")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, csrf):
            return _form_error(403, "The local review form token was not accepted.")
        return None

    async def setup_status() -> dict[str, object]:
        return await api.get_json("/api/v1/setup/source")

    @app.get("/setup/source", response_class=HTMLResponse)
    async def setup_source(request: Request) -> HTMLResponse:
        try:
            setup = await setup_status()
            error = None
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            setup = {"runtime_service_account_email": "Unavailable"}
            error = "Private source setup data is unavailable. Check authentication and retry."
        request.session.pop("source_verified_configured", None)
        request.session.pop("source_verification_sha256", None)
        request.session.pop("baseline_setup_idempotency_key", None)
        request.session.pop("baseline_setup_fingerprint", None)
        return render(
            "setup_source.html",
            setup=setup,
            result=None,
            update_command=None,
            error=error,
            csrf_token=csrf_token(request),
        )

    @app.post("/setup/source/verify", response_class=HTMLResponse)
    async def setup_source_verify(
        request: Request,
        csrf_token: str = Form(...),
        source_reference: str = Form(..., min_length=1, max_length=2048),
        mime_type: str = Form(...),
    ) -> HTMLResponse:
        rejection = require_local_form(request, csrf_token)
        if rejection is not None:
            return rejection
        if mime_type not in {
            "text/markdown",
            "application/vnd.google-apps.document",
        }:
            return _form_error(422, "Choose one of the supported source types.")
        try:
            file_id = extract_drive_file_id(source_reference)
            setup, result = await asyncio.gather(
                setup_status(),
                api.post_json(
                    "/api/v1/setup/source-verifications",
                    {"file_id": file_id, "mime_type": mime_type},
                ),
            )
        except ValueError:
            return _form_error(422, "Enter a valid Google Drive or Google Docs URL.")
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
        ):
            return _form_error(
                422,
                "The private runtime could not read and parse that source. Confirm Viewer sharing and the selected source type.",
            )
        matches = result.get("matches_configured_source") is True
        request.session["source_verified_configured"] = matches
        source_hash = result.get("source_file_id_sha256")
        if matches and isinstance(source_hash, str):
            request.session["source_verification_sha256"] = source_hash
        else:
            request.session.pop("source_verification_sha256", None)
        update_command = None
        if not matches:
            project = str(setup.get("project_id") or "YOUR_PROJECT_ID")
            region = str(setup.get("cloud_run_region") or "YOUR_REGION")
            update_command = (
                "gcloud run services update braille-errata-relay "
                f'--project="{project}" --region="{region}" '
                f'--update-env-vars="DRIVE_FILE_ID={file_id},DRIVE_SOURCE_MIME_TYPE={mime_type}"'
            )
        return render(
            "setup_source.html",
            setup=setup,
            result=result,
            update_command=update_command,
            error=None,
            csrf_token=csrf_token,
        )

    @app.get("/setup/baseline", response_class=HTMLResponse)
    async def setup_baseline(request: Request) -> HTMLResponse:
        try:
            setup = await setup_status()
            error = None
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            setup = {}
            error = "Private baseline setup data is unavailable."
        return render(
            "setup_baseline.html",
            setup=setup,
            verified=request.session.get("source_verified_configured") is True,
            error=error,
            csrf_token=csrf_token(request),
        )

    @app.post("/setup/baseline")
    async def setup_baseline_register(
        request: Request,
        csrf_token: str = Form(...),
        production_id: str = Form(..., min_length=1, max_length=512),
        site_id: str = Form(..., min_length=1, max_length=512),
        queue_name: str = Form(..., min_length=1, max_length=512),
    ) -> Response:
        rejection = require_local_form(request, csrf_token)
        if rejection is not None:
            return rejection
        if request.session.get("source_verified_configured") is not True:
            return _form_error(409, "Verify the configured source before registration.")
        registration_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    request.session.get("source_verification_sha256"),
                    production_id.strip(),
                    site_id.strip(),
                    queue_name.strip(),
                ],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        idempotency_key = request.session.get("baseline_setup_idempotency_key")
        if (
            not isinstance(idempotency_key, str)
            or request.session.get("baseline_setup_fingerprint") != registration_fingerprint
        ):
            idempotency_key = secrets.token_urlsafe(32)
            request.session["baseline_setup_idempotency_key"] = idempotency_key
            request.session["baseline_setup_fingerprint"] = registration_fingerprint
        try:
            result = await api.post_json(
                "/api/v1/setup/baselines",
                {
                    "production_id": production_id.strip(),
                    "site_id": site_id.strip(),
                    "queue_name": queue_name.strip(),
                    "idempotency_key": idempotency_key,
                },
            )
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            return _form_error(
                422,
                "Baseline registration did not complete. No production action was performed; verify the source and retry.",
            )
        baseline = _mapping(result.get("baseline"))
        baseline_id = baseline.get("baseline_id")
        if not isinstance(baseline_id, str) or re.fullmatch(r"[0-9a-f]{64}", baseline_id) is None:
            return _form_error(502, "Baseline registration returned an invalid monitor identity.")
        request.session.pop("baseline_setup_idempotency_key", None)
        request.session.pop("baseline_setup_fingerprint", None)
        return RedirectResponse(f"/baselines/{baseline_id}", status_code=303)

    @app.get("/baselines/{baseline_id}", response_class=HTMLResponse)
    async def baseline_monitor(baseline_id: str) -> HTMLResponse:
        if re.fullmatch(r"[0-9a-f]{64}", baseline_id) is None:
            return _form_error(404, "Baseline not found.")
        try:
            baseline = await api.get_json(f"/api/v1/setup/baselines/{baseline_id}")
            error = None
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            baseline = {}
            error = "Baseline monitor data is temporarily unavailable."
        return render("baseline_monitor.html", baseline=baseline, error=error)

    @app.get("/assets/watch.js")
    async def watch_javascript() -> Response:
        """Serve the watch logic from this same loopback origin only."""

        return Response(WATCH_JAVASCRIPT, media_type="application/javascript")

    @app.get("/assets/report.js")
    async def report_javascript() -> Response:
        """Serve only a same-origin browser-print affordance."""

        return Response(REPORT_JAVASCRIPT, media_type="application/javascript")

    @app.get("/watch", response_class=HTMLResponse)
    async def watch_floor() -> HTMLResponse:
        try:
            snapshot = await watch_snapshot()
            error: str | None = None
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            TypeError,
            ValueError,
        ):
            snapshot = sanitize_watch_snapshot({"incidents": []})
            error = "Private review data is temporarily unavailable; retrying locally."
        return render(
            "watch.html",
            snapshot=snapshot,
            watch=watch_summary(snapshot, suppress_existing_results=True),
            error=error,
            fixture_mode=False,
            fixture_alert=False,
        )

    @app.get("/events")
    async def watch_events(request: Request, max_events: int | None = None) -> Response:
        """Stream sanitized durable transitions to a loopback browser via SSE."""

        if request.headers.get("host") != f"127.0.0.1:{settings.port}":
            return PlainTextResponse("Loopback watch requests only.", status_code=403)
        if max_events is not None and not 1 <= max_events <= 3:
            return PlainTextResponse("Invalid watch event bound.", status_code=400)

        async def event_stream() -> AsyncIterator[str]:
            tracker = WatchEventTracker()
            emitted = 0
            last_heartbeat = watch_monotonic_clock()
            upstream_unavailable = False
            while True:
                if await request.is_disconnected():
                    return
                try:
                    snapshot = await watch_snapshot()
                    transitions = tracker.observe(snapshot)
                    upstream_unavailable = False
                except (
                    httpx.HTTPError,
                    PrivateReviewApiError,
                    PresentationAuthenticationError,
                    TypeError,
                    ValueError,
                ):
                    transitions = () if upstream_unavailable else (upstream_unavailable_event(),)
                    upstream_unavailable = True
                for event in transitions:
                    yield sse_frame(event, retry_milliseconds=2000 if emitted == 0 else None)
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                now = watch_monotonic_clock()
                if now - last_heartbeat >= watch_heartbeat_seconds:
                    yield sse_frame(heartbeat_event())
                    emitted += 1
                    last_heartbeat = now
                    if max_events is not None and emitted >= max_events:
                        return
                await watch_sleep(watch_poll_seconds)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

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
            return render(
                "index.html",
                incidents=(),
                summary={"total": 0, "blocked": 0},
                error="Private review data is unavailable.",
                fixture_mode=False,
            )
        incidents = payload.get("incidents")
        rows = incidents if isinstance(incidents, list) else ()
        blocked = sum(
            1
            for incident in rows
            if isinstance(incident, dict) and incident.get("blocking_reason") is not None
        )
        return render(
            "index.html",
            incidents=rows,
            summary={"total": len(rows), "blocked": blocked},
            error=None,
            csrf_token=csrf_token(request),
            fixture_mode=False,
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
                review_state={
                    "state": "BLOCKED",
                    "blocking_reason": "PRIVATE_REVIEW_DATA_UNAVAILABLE",
                },
                source_correction="No private review data is available.",
                semantic_summary="No private review data is available.",
                uncertainties=(),
                braille_impact="{}",
                baseline_brf_sha256="Unavailable",
                candidate_brf_sha256="Unavailable",
                candidate_manifest="{}",
                profile_identity="{}",
                candidate_evidence_preview={
                    "label": "TEXT EVIDENCE PREVIEW ONLY — NOT TACTILE PROOF",
                    "text": "Candidate BRF preview is unavailable.",
                },
                observation_age="Unavailable",
                current_observation="{}",
                current_observation_id="",
                containment_evidence="{}",
                review_actions={
                    "containment_confirmation": {
                        "eligible": False,
                        "blocking_reason": "PRIVATE_REVIEW_DATA_UNAVAILABLE",
                    },
                    "proof": {
                        "eligible": False,
                        "blocking_reason": "PRIVATE_REVIEW_DATA_UNAVAILABLE",
                    },
                    "replacement_observation": {
                        "eligible": False,
                        "candidate_download_eligible": False,
                        "blocking_reason": "PRIVATE_REVIEW_DATA_UNAVAILABLE",
                        "provenance": None,
                    },
                },
                timeline=(),
                decisions=tuple(decision.value for decision in ProfessionalDecision),
                attestation_types=tuple(kind.value for kind in AttestationType),
                truth_bases=tuple(basis.value for basis in TruthBasis),
                proof_decisions=tuple(decision.value for decision in ProofDecision),
                csrf_token=csrf_token(request),
                disposition_idempotency_key=secrets.token_urlsafe(24),
                attestation_idempotency_key=secrets.token_urlsafe(24),
                containment_idempotency_key=secrets.token_urlsafe(24),
                proof_idempotency_key=secrets.token_urlsafe(24),
                replacement_idempotency_key=secrets.token_urlsafe(24),
                fixture_mode=False,
                next_safe_action="Private review data is unavailable; no action form is enabled.",
                source_comparison={
                    "old": "Private review data is unavailable.",
                    "new": "Private review data is unavailable.",
                },
                workflow_stage="DETECTED",
                workflow_stages=tuple(stage.value for stage in IncidentWorkflowStage),
                semantic_activity="Private review data is unavailable.",
                semantic_materiality="Unavailable",
                semantic_change_kind="Unavailable",
                uncertainty_summary="Unavailable",
                impact_summary="Unavailable",
                report_observation_age="Unavailable",
                current_monitor_summary="Unavailable",
                recommended_human_steps=(),
                display=report_view(
                    stage="DETECTED",
                    checkpoint={},
                    review_state={"state": "BLOCKED"},
                    review_actions={},
                    impact={},
                ),
                error="Private review data is unavailable.",
            )
        report = _mapping(detail.get("report"))
        packet = _mapping(detail.get("human_disposition_packet"))
        semantic = _mapping(report.get("semantic_assessment"))
        # Older private API responses intentionally remain readable as a
        # read-only review surface.  Missing eligibility is never treated as
        # permission to render a human action form.
        containment_action: dict[str, object] = {
            "eligible": False,
            "blocking_reason": "CONTAINMENT_CONFIRMATION_REQUIRED",
        }
        containment_action.update(
            _mapping(_mapping(detail.get("review_actions")).get("containment_confirmation"))
        )
        proof_action: dict[str, object] = {
            "eligible": False,
            "blocking_reason": "PROOF_NOT_ELIGIBLE",
            "provenance": None,
        }
        proof_action.update(_mapping(_mapping(detail.get("review_actions")).get("proof")))
        replacement_action: dict[str, object] = {
            "eligible": False,
            "candidate_download_eligible": False,
            "blocking_reason": "REPLACEMENT_NOT_ELIGIBLE",
            "provenance": None,
        }
        replacement_action.update(
            _mapping(_mapping(detail.get("review_actions")).get("replacement_observation"))
        )
        review_actions = {
            "containment_confirmation": containment_action,
            "proof": proof_action,
            "replacement_observation": replacement_action,
        }
        candidate_evidence_preview: dict[str, object] = {
            "label": "TEXT EVIDENCE PREVIEW ONLY — NOT TACTILE PROOF",
            "text": "Candidate BRF preview is unavailable.",
        }
        candidate_evidence_preview.update(_mapping(detail.get("candidate_evidence_preview")))
        checkpoint = _mapping(detail.get("checkpoint"))
        workflow_stage = checkpoint.get("stage")
        if not isinstance(workflow_stage, str):
            workflow_stage = "DETECTED"
        braille_impact = _mapping(report.get("braille_impact"))
        uncertainties_value = semantic.get("uncertainties", ())
        uncertainty_count = (
            len(uncertainties_value) if isinstance(uncertainties_value, (list, tuple)) else 0
        )
        recommended_steps_value = report.get("recommended_human_steps", ())
        recommended_steps = (
            tuple(step for step in recommended_steps_value if isinstance(step, str))
            if isinstance(recommended_steps_value, (list, tuple))
            else ()
        )
        return render(
            "incident.html",
            incident_id=incident_id,
            review_state=_mapping(detail.get("review_state")),
            source_correction=_pretty(detail.get("source_correction")),
            semantic_summary=semantic.get("summary", "No semantic summary is available."),
            uncertainties=semantic.get("uncertainties", ()),
            braille_impact=_pretty(braille_impact),
            baseline_brf_sha256=packet.get("baseline_brf_sha256", "Unavailable"),
            candidate_brf_sha256=_mapping(packet.get("candidate_brf")).get("sha256", "Unavailable"),
            candidate_manifest=_pretty(detail.get("candidate_manifest")),
            profile_identity=_pretty(detail.get("profile_identity")),
            candidate_evidence_preview=candidate_evidence_preview,
            observation_age=packet.get("observation_age_seconds", "Unavailable"),
            current_observation=_pretty(detail.get("current_site_observation")),
            current_observation_id=_mapping(detail.get("current_site_observation")).get(
                "observation_id", ""
            ),
            containment_evidence=_pretty(containment_action),
            review_actions=review_actions,
            timeline=_mapping(timeline_payload).get("events", ()),
            decisions=tuple(decision.value for decision in ProfessionalDecision),
            attestation_types=tuple(kind.value for kind in AttestationType),
            truth_bases=tuple(basis.value for basis in TruthBasis),
            proof_decisions=tuple(decision.value for decision in ProofDecision),
            csrf_token=csrf_token(request),
            disposition_idempotency_key=secrets.token_urlsafe(24),
            attestation_idempotency_key=secrets.token_urlsafe(24),
            containment_idempotency_key=secrets.token_urlsafe(24),
            proof_idempotency_key=secrets.token_urlsafe(24),
            replacement_idempotency_key=secrets.token_urlsafe(24),
            fixture_mode=False,
            next_safe_action=_next_safe_action(_mapping(detail.get("review_state"))),
            source_comparison=_source_comparison(_mapping(detail.get("source_correction"))),
            workflow_stage=workflow_stage,
            workflow_stages=tuple(stage.value for stage in IncidentWorkflowStage),
            semantic_activity=_semantic_activity(workflow_stage),
            semantic_materiality=semantic.get("materiality", "Not recorded"),
            semantic_change_kind=semantic.get("change_kind", "Not recorded"),
            uncertainty_summary=(
                "No uncertainty was recorded."
                if uncertainty_count == 0
                else f"{uncertainty_count} persisted uncertainty item(s) require human judgment."
            ),
            impact_summary=_impact_summary(braille_impact),
            report_observation_age=packet.get("observation_age_seconds", "Unavailable"),
            current_monitor_summary=_current_monitor_summary(
                _mapping(detail.get("current_site_observation"))
            ),
            recommended_human_steps=recommended_steps,
            display=report_view(
                stage=workflow_stage,
                checkpoint=checkpoint,
                review_state=_mapping(detail.get("review_state")),
                review_actions=review_actions,
                impact=braille_impact,
            ),
            error=None,
        )

    @app.get("/incidents/{incident_id}/report", response_class=HTMLResponse)
    async def printable_incident_report(incident_id: str) -> HTMLResponse:
        """Render existing immutable incident evidence for local browser print.

        This route deliberately performs only the same private GETs as the
        decision cockpit. It has no model, Drive, CUPS, or human-record call.
        """

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
                "report.html",
                incident_id=incident_id,
                fixture_mode=False,
                error="Private review data is unavailable; no report is rendered.",
            )
        report = _mapping(detail.get("report"))
        packet = _mapping(detail.get("human_disposition_packet"))
        semantic = _mapping(report.get("semantic_assessment"))
        checkpoint = _mapping(detail.get("checkpoint"))
        workflow_stage = checkpoint.get("stage")
        if not isinstance(workflow_stage, str):
            workflow_stage = "DETECTED"
        review_state = _mapping(detail.get("review_state"))
        raw_actions = _mapping(detail.get("review_actions"))
        review_actions = {
            "containment_confirmation": _mapping(raw_actions.get("containment_confirmation")),
            "proof": _mapping(raw_actions.get("proof")),
            "replacement_observation": _mapping(raw_actions.get("replacement_observation")),
        }
        braille_impact = _mapping(report.get("braille_impact"))
        uncertainties = semantic.get("uncertainties", ())
        uncertainty_count = len(uncertainties) if isinstance(uncertainties, (list, tuple)) else 0
        recommended_value = report.get("recommended_human_steps", ())
        recommended_steps = (
            tuple(step for step in recommended_value if isinstance(step, str))
            if isinstance(recommended_value, (list, tuple))
            else ()
        )
        current_observation = _mapping(detail.get("current_site_observation"))
        return render(
            "report.html",
            incident_id=incident_id,
            review_state=review_state,
            source_comparison=_source_comparison(_mapping(detail.get("source_correction"))),
            semantic_materiality=semantic.get("materiality", "Not recorded"),
            semantic_change_kind=semantic.get("change_kind", "Not recorded"),
            semantic_summary=semantic.get("summary", "No semantic summary is available."),
            uncertainty_summary=(
                "No uncertainty was recorded."
                if uncertainty_count == 0
                else f"{uncertainty_count} persisted uncertainty item(s) require human judgment."
            ),
            report_observation_age=packet.get("observation_age_seconds", "Unavailable"),
            current_monitor_summary=_current_monitor_summary(current_observation),
            next_safe_action=_next_safe_action(review_state),
            recommended_human_steps=recommended_steps,
            source_correction=_pretty(detail.get("source_correction")),
            braille_impact=_pretty(braille_impact),
            candidate_manifest=_pretty(detail.get("candidate_manifest")),
            profile_identity=_pretty(detail.get("profile_identity")),
            timeline=_mapping(timeline_payload).get("events", ()),
            display=report_view(
                stage=workflow_stage,
                checkpoint=checkpoint,
                review_state=review_state,
                review_actions=review_actions,
                impact=braille_impact,
            ),
            fixture_mode=False,
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

    @app.post("/incidents/{incident_id}/containment-confirmations")
    async def submit_containment_confirmation(
        incident_id: str,
        request: Request,
        csrf_token_value: str = Form(alias="csrf_token"),
        halt_disposition_record_id: str = Form(),
        site_observation_id: str = Form(),
        physical_output_isolation_attestation_id: str = Form(),
        selected_role: str = Form(),
        expected_state_version: int = Form(),
        note: str = Form(default=""),
        idempotency_key: str = Form(),
    ) -> Response:
        rejected = require_local_form(request, csrf_token_value)
        if rejected is not None:
            return rejected
        exact_hashes = (
            halt_disposition_record_id,
            site_observation_id,
            physical_output_isolation_attestation_id,
        )
        if selected_role != "production_coordinator" or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None for value in exact_hashes
        ):
            return _form_error(422, "The selected containment evidence is invalid.")
        try:
            await api.post_json(
                f"/api/v1/incidents/{incident_id}/containment-confirmations",
                {
                    "halt_disposition_record_id": halt_disposition_record_id,
                    "site_observation_id": site_observation_id,
                    "physical_output_isolation_attestation_id": (
                        physical_output_isolation_attestation_id
                    ),
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
                409,
                "Containment confirmation was not recorded. Reload the incident before retrying.",
            )
        return RedirectResponse(f"/incidents/{incident_id}", status_code=303)

    @app.post("/incidents/{incident_id}/proof-records")
    async def submit_proof_record(
        incident_id: str,
        request: Request,
        csrf_token_value: str = Form(alias="csrf_token"),
        candidate_sha256: str = Form(),
        manifest_sha256: str = Form(),
        decision: str = Form(),
        review_basis: str = Form(),
        selected_role: str = Form(),
        expected_state_version: int = Form(),
        note: str = Form(default=""),
        visual_only_uncertainty: bool = Form(default=False),
        idempotency_key: str = Form(),
    ) -> Response:
        rejected = require_local_form(request, csrf_token_value)
        if rejected is not None:
            return rejected
        if (
            selected_role != "proofreader"
            or review_basis != "DEMO_FIXTURE_REVIEW"
            or decision not in {item.value for item in ProofDecision}
            or re.fullmatch(r"[0-9a-f]{64}", candidate_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
        ):
            return _form_error(422, "The selected exact-candidate proof decision is invalid.")
        try:
            await api.post_json(
                f"/api/v1/incidents/{incident_id}/proof-records",
                {
                    "candidate_sha256": candidate_sha256,
                    "manifest_sha256": manifest_sha256,
                    "decision": decision,
                    "review_basis": review_basis,
                    "selected_role": selected_role,
                    "expected_state_version": expected_state_version,
                    "note": note,
                    "findings": [],
                    "visual_only_uncertainty": visual_only_uncertainty,
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
                409,
                "Proof was not recorded. Reload the incident before retrying.",
            )
        return RedirectResponse(f"/incidents/{incident_id}", status_code=303)

    @app.get("/incidents/{incident_id}/approved-candidate")
    async def download_approved_candidate(incident_id: str, request: Request) -> Response:
        """Proxy a fixed immutable candidate download without exposing a Cloud token."""

        if request.headers.get("host") != f"127.0.0.1:{settings.port}":
            return _form_error(404, "Candidate download is available only through local review.")
        get_bytes = getattr(api, "get_bytes", None)
        if not callable(get_bytes):
            return _form_error(404, "Approved candidate download is unavailable.")
        try:
            content, disposition = await get_bytes(
                f"/api/v1/incidents/{incident_id}/approved-candidate"
            )
        except (
            httpx.HTTPError,
            PrivateReviewApiError,
            PresentationAuthenticationError,
            ValueError,
        ):
            return _form_error(403, "The current approved candidate is unavailable.")
        if (
            re.fullmatch(
                r'attachment; filename="braille-errata-relay-[0-9a-f]{12}-[0-9a-f]{12}\.brf"',
                disposition,
            )
            is None
        ):
            return _form_error(502, "Candidate download identity was not accepted.")
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/incidents/{incident_id}/replacement-observation-links")
    async def submit_replacement_observation_link(
        incident_id: str,
        request: Request,
        csrf_token_value: str = Form(alias="csrf_token"),
        candidate_sha256: str = Form(),
        candidate_manifest_sha256: str = Form(),
        proof_record_id: str = Form(),
        scheduler_job_id: int = Form(),
        site_observation_id: str = Form(),
        selected_role: str = Form(),
        expected_state_version: int = Form(),
        note: str = Form(default=""),
        idempotency_key: str = Form(),
    ) -> Response:
        rejected = require_local_form(request, csrf_token_value)
        if rejected is not None:
            return rejected
        hashes = (candidate_sha256, candidate_manifest_sha256, proof_record_id, site_observation_id)
        if (
            selected_role != "machine_operator"
            or scheduler_job_id < 1
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes)
        ):
            return _form_error(422, "The selected replacement observation evidence is invalid.")
        try:
            await api.post_json(
                f"/api/v1/incidents/{incident_id}/replacement-observation-links",
                {
                    "candidate_sha256": candidate_sha256,
                    "candidate_manifest_sha256": candidate_manifest_sha256,
                    "proof_record_id": proof_record_id,
                    "scheduler_job_id": scheduler_job_id,
                    "site_observation_id": site_observation_id,
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
                409,
                "Replacement observation was not recorded. Reload the incident before retrying.",
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

    token_provider = GoogleAudienceTokenProvider(
        target_principal=settings.impersonate_service_account,
        audience=settings.audience,
    )
    asyncio.run(token_provider.token_for(settings.audience))
    api_client = CloudRunPrivateReviewApi(
        base_url=settings.api_base_url,
        audience=settings.audience,
        token_provider=token_provider,
    )
    uvicorn.run(
        create_presentation_app(settings, api_client=api_client),
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
