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

Slice 2 implements the minimum report-first path for Stories 1 and 2:

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
- authenticated baseline, incident, source, telemetry, and scheduler routes with
  principal separation.

The slice intentionally stops at `REPORT_READY` or a visible `NEEDS_REVIEW`.
There is no dashboard, professional-decision submission, proof approval,
replacement submission, notification-delivery claim, or production-device
control in this repository slice.

## Interfaces

The private FastAPI application exposes:

- `POST /api/v1/baselines` and `GET /api/v1/baselines/{baseline_id}` for the
  demonstrator baseline seam;
- `GET /api/v1/incidents/{incident_id}` for immutable report and packet retrieval;
- `POST /internal/drive-reconcile` for the source principal;
- `POST /internal/site-observations` for the telemetry principal;
- `POST /internal/outbox-drain` for the scheduler principal; and
- `GET /healthz` and `GET /readyz`, with readiness requiring the exact installed
  Liblouis version, table hashes, and a real translation smoke test.

The narrow CLI registers only a demo baseline. It cannot submit or mutate a
production job:

```text
braille-relay register-demo-baseline \
  --service-url SERVICE_URL \
  --audience OIDC_AUDIENCE \
  --file-id DRIVE_FILE_ID \
  --revision-id DRIVE_REVISION_ID
```

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

## Evidence and current blocker

Sanitized Slice 2 evidence is recorded in
[`demo/evidence/report-first.json`](demo/evidence/report-first.json) and validated
by [`schemas/report-first-evidence.v1.json`](schemas/report-first-evidence.v1.json).
The historical local and cloud Gate 0 evidence is preserved unchanged.

The frozen container was built, its real Liblouis output matched WSL byte for
byte, and a new private Frankfurt revision was deployed by immutable image
digest. The revision reports ready, but authenticated requests currently receive
an HTTP 404 from the Google edge before reaching Uvicorn. The outbox scheduler is
therefore provisioned but paused and no live report-first route pass is claimed.
Restore a valid Cloud Run default URL route or custom audience, rerun the private
route smoke tests, then unpause and execute the scheduler once.

## Adapter boundaries

Google Drive and CUPS are MVP adapters, not assumptions about every production
facility. Gemini/ADK performs semantic assessment only. Deterministic code owns
source normalization, translation, BRF bytes, pagination, hashes, page impact,
policy recommendations, state transitions, and verification. An incompatible
external baseline profile fails closed as `INCOMPATIBLE_BASELINE_PROFILE` rather
than being compared with a Relay-generated candidate.
