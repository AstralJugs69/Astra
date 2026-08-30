# Braille Errata Relay

Braille Errata Relay is a report-first overlay for late corrections in an
existing Braille production workflow. It preserves approved baseline lineage,
regenerates deterministic candidate BRF with pinned Liblouis, calculates exact
page impact, adds a bounded semantic assessment, correlates fresh read-only
production evidence, and prepares immutable records for human professionals.

It is not a Braille publishing platform, a work-order system of record, or a
production control surface. A candidate BRF is not an approved production
master. The Relay never submits, holds, releases, cancels, restarts, pauses, or
otherwise mutates CUPS, an embosser, or another production device. Human
professionals retain disposition, containment, physical-output isolation,
proof, resubmission, and closure authority.

## Release-candidate scope

Stories 1–3, the Story 4 containment/proof gate, and the minimal Story 5
replacement-observation seam are implemented. The end-to-end overlay is
intentionally bounded at `REPLACEMENT_OBSERVED`: a human independently submits
an approved candidate through the existing production surface, and Relay can
only record fresh read-only evidence of that external job. The presentation
labels the preceding state **awaiting human submission**.

### Implemented

- Idempotent baseline registration with immutable source, BRF, profile, and
  production-link lineage for the explicit `DEMO_FIXTURE_APPROVED` fixture.
- Deterministic source normalization, real pinned Liblouis translation,
  40 × 25 pagination, BRF serialization, manifests, source maps, and exact
  full-volume page impact.
- Bounded semantic assessment: Gemini/ADK assesses semantics only; deterministic
  code owns translation, pagination, hashes, recommendations, and state.
- Read-only site-observation ingestion with canonical hashes, sequence and
  previous-hash continuity, allowlists, exact queue/job/title/hash correlation,
  freshness checks, and fail-closed ambiguity handling.
- Human-only professional disposition and operator-attestation records with
  optimistic state versions, idempotent replay, and append-only timelines.
- Containment confirmation only after an attributable `HALT_REQUESTED`, a
  separate post-halt `PHYSICAL_OUTPUT_ISOLATED` attestation, and a fresh exact
  admitted read-only terminal job observation. Scheduler state alone cannot
  establish containment.
- Exact-candidate proof approval or rejection only from `AWAITING_PROOF`. Each
  proof record binds the BRF bytes, manifest, source revision/hash, translation
  profile/hash, Liblouis version, table hashes, and formatter version. A changed
  candidate invalidates prior proof before it can be reused.
- A private, read-only download for the current proof-approved candidate only.
  Relay re-hashes the immutable BRF before returning it with a fixed safe
  filename and no-store response. It never accepts an arbitrary artifact path
  or represents the candidate as a certified production master.
- An append-only `ReplacementObservationLink` recorded only by a
  `machine_operator`. It verifies the exact approved candidate, current proof,
  original and replacement job distinction, canonical title, site/bridge/queue,
  current fresh canonical observation, unambiguous matching job, state version,
  and idempotency inside the ledger transaction. It advances only
  `AWAITING_REPLACEMENT` to `REPLACEMENT_OBSERVED`.
- A private loopback presentation shell with signed sessions, strict same-origin
  form handling, CSRF protection, server-side credential handling, responsive
  operations views, and no device-control surface.

### Demonstrated, simulated, and blocked

- Historical private-service and local CUPS-harness evidence is preserved under
  `demo/evidence/`. The only simulated component is the physical embossing
  endpoint; CUPS scheduling, capture bytes, and observer authorization checks
  are real when the local Gate 0 harness is run.
- The current historical incident is preserved as
  `CONTAINMENT_IN_PROGRESS` with `SITE_OBSERVATION_STALE`. It has **not** reached
  a containment confirmation, proof approval/rejection, replacement submission,
  final verification, notification, or closure.
- The Story 5 seam was deliberately not executed against the historical live
  incident. It provides no capability to submit, release, cancel, restart, or
  otherwise control a production job.
- Current container golden renders pass. An isolated frozen-lock WSL environment
  ran the real application golden test twice; V1/V2 rendered BRF bytes, profile
  hash, Liblouis version, and table hashes match the current container exactly.

### Deferred by design

There is no Relay-owned replacement submission, endpoint completion claim,
physical final verification, notification delivery, CUPS mutation,
embosser/device control, automatic remediation, or final incident resolution.
`REPLACEMENT_OBSERVED` remains evidence of a scheduler observation only, not
physical output. Google Drive and CUPS remain MVP adapters rather than
assumptions about every production facility.

## Interfaces

The private FastAPI application exposes evidence and human-record routes only:

- `POST /api/v1/baselines` and `GET /api/v1/baselines/{baseline_id}` for the
  demonstrator baseline seam.
- `GET /api/v1/incidents`, `GET /api/v1/incidents/{incident_id}`, and
  `GET /api/v1/incidents/{incident_id}/timeline` for professional review.
- `POST /api/v1/incidents/{incident_id}/professional-dispositions` and
  `POST /api/v1/incidents/{incident_id}/operator-attestations` for attributable
  human records.
- `POST /api/v1/incidents/{incident_id}/containment-confirmations` and
  `POST /api/v1/incidents/{incident_id}/proof-records` for the Story 4 gate.
  These routes record human evidence; they do not control or submit anything.
- `GET /api/v1/incidents/{incident_id}/approved-candidate` provides the current
  proof-approved immutable candidate bytes only; the localhost presentation
  server proxies this private download without exposing a Cloud credential to a
  browser.
- `POST /api/v1/incidents/{incident_id}/replacement-observation-links` records
  a human association to a read-only observed external job. It does not submit,
  hold, release, cancel, restart, verify, or close a job.
- `POST /internal/drive-reconcile`, `POST /internal/site-observations`, and
  `POST /internal/outbox-drain` for the separately authorized Drive, telemetry,
  and Scheduler principals.
- `GET /health` and `GET /readyz`; readiness requires the exact installed
  Liblouis version, table hashes, and a real translation smoke test. Local
  `/healthz` remains available for container checks.

The narrow CLI records evidence and advisory lineage only. For example,
`braille-relay supersede-baseline-production` appends an advisory link for an
independently human-submitted job. It does not submit, hold, release, cancel, or
otherwise control that job.

## Verification

Run each check independently from the repository root so one success cannot
hide an earlier failure:

```text
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
docker build --tag braille-errata-relay:release-candidate-1 .
docker run --rm --network none --read-only --cap-drop ALL \
  --entrypoint python braille-errata-relay:release-candidate-1 \
  -m braille_errata_relay.container_smoke
```

The real WSL verification must use the pinned runtime and an isolated Linux
environment outside the repository. Never install dependencies into WSL's
system Python and never substitute the Windows virtual environment. The
release evidence records whether that current-commit parity check actually ran.

The local CUPS simulator remains the Gate 0 production-floor harness. CUPS
scheduling, operator lifecycle actions, observer authorization denials, captured
bytes, and journal hashes are real; only the physical embossing endpoint is
simulated. No mutating CUPS operation is exposed through the application or the
read-only bridge.

## Evidence and historical status

Evidence is sanitized, schema-validated, and deliberately separated by slice:

- `demo/evidence/report-first.json` and
  `demo/evidence/report-first-live-closure.json` preserve earlier report-first
  and scheduler-recovery evidence.
- `demo/evidence/active-professional-review.json` preserves the Story 3 review
  seam without claiming a completed active-production walkthrough.
- `demo/evidence/slice-2-3-containment-proof-gate.json` records the Story 4
  implementation, toolchain identity, container goldens, and the fail-closed
  historical incident state. Its schema distinguishes matched WSL/container
  *toolchain* identity from rendered-BRF byte parity, and a blocked WSL golden
  run cannot claim parity.
- `demo/screenshots/` contains four committed responsive renders from a
  dedicated GET-only sanitized offline fixture. Its manifest hashes every PNG,
  records the viewport and `SANITIZED_OFFLINE_DEMO_FIXTURE` truth basis, and is
  not mounted by the Cloud Run application.
- `demo/evidence/final-story5-dashboard.json` records this final slice's
  verification and private deployment status without claiming a human
  replacement submission or final closure.

The historical incident remains a review artifact, not a production master or
authorization to act. A cancellation fact, device-stop fact, physical-output
isolation fact, proof fact, and replacement submission fact are all independent
human-owned records.

## Adapter boundaries

Google Drive and CUPS are MVP adapters, not assumptions about every production
facility. Gemini/ADK performs semantic assessment only. Deterministic code owns
source normalization, translation, BRF bytes, pagination, hashes, page impact,
policy recommendations, state transitions, and verification. An incompatible
external baseline profile fails closed as `INCOMPATIBLE_BASELINE_PROFILE` rather
than being compared with a Relay-generated candidate.
