# Braille Errata Relay

Braille Errata Relay is a report-first overlay for late corrections in an
existing Braille production workflow. It preserves approved baseline lineage,
regenerates a deterministic candidate with pinned Liblouis, calculates exact
BRF page impact, adds a bounded Gemini semantic assessment, correlates fresh
read-only production evidence, and prepares an immutable packet for a human
professional.

It is not a Braille publishing platform, a work-order system of record, or a
production control surface. A candidate BRF is not an approved production
master. The Relay never submits, holds, releases, cancels, restarts, pauses, or
otherwise mutates CUPS, an embosser, or another production device. Professional
disposition, operator containment, physical-output isolation, proof approval,
and replacement submission remain separate human-owned facts.

## Implemented slice

Slice 2.2 implements the report-first path for Stories 1 and 2 plus the
smallest active-production human-review seam for Story 3:

```text
durable Drive revision
  -> approved demo baseline lineage
  -> deterministic full candidate BRF
  -> deterministic page impact
  -> bounded semantic assessment
  -> fresh read-only site evidence
  -> human-only containment recommendation
  -> immutable incident report and disposition packet
```

The implementation includes:

- an idempotent baseline workflow that records external work-order reference
  `WO-DEMO-001`, exact Drive source lineage, bound translation profile, immutable
  artifacts, and the explicit `DEMO_FIXTURE_APPROVED` label;
- a resumable, optimistic-version incident workflow with stable incident identity
  and create-only source, normalized source, BRF, manifest, impact, semantic,
  report, and disposition-packet artifacts;
- real Liblouis 3.38.0 translation with pinned table hashes, deterministic 40 x
  25 pagination, six-dot BRF serialization, and full-volume replacement review;
- semantic execution claims that reuse the first persisted valid result, recover
  expired leases, keep attempt telemetry separate, and fail closed on invalid or
  ungrounded citations;
- schema-validated site-observation ingestion with an exact canonical hash,
  sequence and previous-hash continuity, timestamp checks, and installation,
  bridge, and queue allowlists;
- deterministic production correlation that requires exact job, queue, title,
  artifact hash, freshness, and unambiguous state before offering a precise
  recommendation;
- a bounded scheduler drain for the durable Drive outbox with leases, retries,
  backoff, dead-letter thresholds, and restart recovery; and
- immutable active `RECEIVED` endpoint acceptance evidence, separate from a
  terminal capture manifest, so exact bytes can verify an active human-submitted
  baseline job without claiming completion;
- append-only advisory production-link supersession that preserves the prior
  historical link while switching only the active baseline pointer;
- authenticated coordinator disposition and operator-attestation routes with
  optimistic human-state versions, deterministic replay, and an append-only
  attributable timeline; and
- a loopback-only, server-rendered presentation shell with signed strict
  sessions, per-form CSRF/origin checks, server-side short-lived Cloud Run ID
  tokens, and no CUPS/device-control surface.

The slice intentionally stops after human disposition and containment
attestation records. There is no proof approval, replacement submission,
notification-delivery claim, production-device control, or final closure in
this repository slice.

## Interfaces

The private FastAPI application exposes:

- `POST /api/v1/baselines` and `GET /api/v1/baselines/{baseline_id}` for the
  demonstrator baseline seam;
- `GET /api/v1/incidents`, `GET /api/v1/incidents/{incident_id}`, and
  `GET /api/v1/incidents/{incident_id}/timeline` for the professional-review
  view;
- `POST /api/v1/incidents/{incident_id}/professional-dispositions` and
  `POST /api/v1/incidents/{incident_id}/operator-attestations` for attributable
  human records only;
- `POST /internal/drive-reconcile` for the source principal;
- `POST /internal/site-observations` for the telemetry principal;
- `POST /internal/outbox-drain` for the scheduler principal; and
- `GET /health` and `GET /readyz`, with readiness requiring the exact installed
  Liblouis version, table hashes, and a real translation smoke test. Local
  `/healthz` remains available for container checks, but Cloud Run reserves some
  paths ending in `z`, so live callers use `/health`.

The narrow CLI records only evidence and advisory lineage. It cannot submit or
mutate a production job:

```text
braille-relay register-demo-baseline \
  --service-url SERVICE_URL \
  --audience OIDC_AUDIENCE \
  --file-id DRIVE_FILE_ID \
  --revision-id DRIVE_REVISION_ID
```

`braille-relay supersede-baseline-production` appends a new advisory production
link for an independently human-submitted job. It does not submit, hold,
release, cancel, or otherwise control that job. The local presentation shell is
started separately with `python -m braille_errata_relay.presentation.app`; it
always binds only `127.0.0.1`.

## Verification

Run each check independently from the repository root so one success cannot hide
an earlier failure:

```text
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
docker build --tag braille-errata-relay:slice-2-report-first .
docker run --rm --network none --read-only --cap-drop ALL \
  --entrypoint python braille-errata-relay:slice-2-report-first \
  -m braille_errata_relay.container_smoke
```

For the real WSL Liblouis golden check, first load the installed pinned runtime:

```text
source infra/wsl/liblouis_env.sh
export RELAY_LIBLOUIS_PROFILE="$PWD/work/translation-profile.bound.json"
python3 -m pytest tests/golden/test_golden_render.py -q -p no:cacheprovider
python3 -m pytest tests/golden/test_golden_render.py -q -p no:cacheprovider
```

The local CUPS simulator remains the Gate 0 production-floor harness. CUPS
scheduling, operator lifecycle actions, observer authorization denials, captured
bytes, and journal hashes are real; only the physical embossing endpoint is
simulated. No mutating CUPS operation is exposed through the application or the
read-only bridge.

## Evidence and live status

Sanitized Slice 2 evidence is recorded in
[`demo/evidence/report-first.json`](demo/evidence/report-first.json) and validated
by [`schemas/report-first-evidence.v1.json`](schemas/report-first-evidence.v1.json).
That historical blocked record and the accepted Gate 0 evidence remain unchanged.
Sanitized Slice 2.1 closure evidence is recorded separately in
[`demo/evidence/report-first-live-closure.json`](demo/evidence/report-first-live-closure.json)
and validated by
[`schemas/report-first-live-closure-evidence.v1.json`](schemas/report-first-live-closure-evidence.v1.json).
Slice 2.2 adds a separate, sanitized
[`demo/evidence/active-professional-review.json`](demo/evidence/active-professional-review.json)
record. It truthfully records implementation and verification state while the
new active Story 3 live walkthrough remains `NOT_RUN`; it does not claim any
human disposition, CUPS cancellation, endpoint completion, or attestation.

The frozen container was built, its real Liblouis output matched WSL byte for
byte, and a private Frankfurt revision was deployed by immutable image digest.
Authenticated live `/health` and `/readyz` checks pass. A fresh read-only CUPS
observation linked the exact independently submitted baseline job without Relay
device control. The same-file corrected Drive revision then produced one durable
report-first incident, candidate, semantic assessment, report, and human
disposition packet. The workflow stopped visibly at `NEEDS_REVIEW`; it did not
claim professional disposition, containment, proof, replacement, or notification.

The first scheduler attempt exposed a missing Firestore composite index and
returned HTTP 500. After the index became ready and same-revision transaction
recovery was deployed, one guarded recovery invocation returned HTTP 200 in both
Scheduler and Uvicorn logs. Replaying the identical Drive revision converged on
the same receipt and outbox identity, and an authenticated empty drain leased no
messages. The scheduler is paused after evidence capture, temporary IAM grants
are absent, Cloud Run remains private, and Slice 2.1 has no remaining blocker.

## Live production-link handoff

The local Gate 0 harness keeps human CUPS authority separate from Relay code:

1. A human operator independently submits a held raw baseline job to the local
   simulator with the canonical title and approved BRF bytes.
2. The human runs `infra/gcp/link_local_baseline_job.ps1` with the baseline ID
   and scheduler job ID. The harness uses only read-only CUPS Get operations,
   admits the resulting hash-chained observation, and creates the immutable
   production link.
3. If a prior local observation was known not to have reached telemetry and has
   become stale, the explicit `-ArchiveUnpublishedLocalJournal` recovery switch
   preserves it under ignored `work/` state before starting a fresh chain.
4. Only after the immutable link passes may a human execute one bounded scheduler
   run for the durable V2 source event. The scheduler is paused again immediately
   after the evidence is collected.

Neither the harness nor the Cloud Run service submits, releases, cancels, holds,
restarts, pauses, or otherwise mutates a CUPS job.

## Adapter boundaries

Google Drive and CUPS are MVP adapters, not assumptions about every production
facility. Gemini/ADK performs semantic assessment only. Deterministic code owns
source normalization, translation, BRF bytes, pagination, hashes, page impact,
policy recommendations, state transitions, and verification. An incompatible
external baseline profile fails closed as `INCOMPATIBLE_BASELINE_PROFILE` rather
than being compared with a Relay-generated candidate.
