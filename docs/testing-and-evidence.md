# Astra — testing, evidence, and release chronology

This project separates reproducible verification from operational claims.
Passing a deterministic render test does not prove a physical embosser stopped;
a screenshot does not prove a Drive edit; and a private health check does not
prove a complete source-to-production lifecycle.

Run checks independently from the repository root so one pass cannot conceal
another. Commands below are read-only except for Docker build cache and clearly
labelled local generated output.

## Core local checks

~~~text
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
git diff --check
~~~

These validate the frozen dependency graph, automated contracts, style/type
checks, and whitespace integrity. They do not make a cloud, Drive, CUPS, or
physical-production claim.

## Container readiness

~~~text
docker build --tag braille-errata-relay:final-demo-readiness .
docker run --rm --network none --read-only --cap-drop ALL --entrypoint python braille-errata-relay:final-demo-readiness -m braille_errata_relay.container_smoke
~~~

The shown container command is an installed-runtime readiness smoke: it checks
the packaged profile and Liblouis readiness under constrained container
settings. It does **not** itself run the V1/V2 BRF byte golden comparison.

The current release evidence records a separate repeated container Liblouis
golden comparison. Do not replace that distinction with a stronger claim than
the command actually performs.

## WSL and local production-floor checks

The directly reproducible WSL golden is the deterministic Liblouis V1/V2
render test using the pinned bound profile and real table files. It is not a
full Drive, ADK, Firestore, CUPS, or physical-device integration suite.

Use the [WSL2 CUPS Gate 0 runbook](../infra/wsl/README.md) to:

1. build the pinned Liblouis 3.38.0 binding and validate table identity;
2. run the Unicode six-dot and deterministic BRF checks;
3. inspect and install the human-owned CUPS simulator;
4. exercise real raw CUPS submission/hold/release/cancel actions through the
   separate operator identity;
5. prove the observer cannot mutate CUPS or read spool/capture material;
6. verify exact capture hashes and terminal hash chains through the dedicated
   endpoint auditor.

An unavailable WSL binding, table, profile, frozen project runtime, CUPS
permission, or hardware prerequisite is **BLOCKED**, not a pass. Never replace
such a gap with a mocked translator or weakened observer policy.

## Current release and historical evidence

The evidence directory intentionally preserves historical gates. Read it as a
chronology, not as a set of interchangeable claims.

| Artifact | Status and interpretation |
| --- | --- |
| [gate0-local-floor.json](../demo/evidence/gate0-local-floor.json) | Historical local-floor proof: real CUPS scheduling, raw-byte capture, operator lifecycle, and observer denials. Only the final physical endpoint is simulated. |
| [cloud-gate0.json](../demo/evidence/cloud-gate0.json) | Historical cloud-seam proof: private Cloud Run, Drive change/refetch, Firestore/GCS, and structured Gemini/ADK assessment. |
| [final-story5-dashboard.json](../demo/evidence/final-story5-dashboard.json) | Historical Story 5/dashboard snapshot. It records the then-blocked isolated WSL-runtime state and is not the current release status. |
| [final-demo-readiness.json](../demo/evidence/final-demo-readiness.json) | **Current sanitized release evidence.** It was recorded in Git commit **8569335** at **2026-08-30T19:35:15Z** and records current verification/deployment results. |
| [manifest.json](../demo/screenshots/manifest.json) | Sanitized offline fixture screenshot evidence only. It proves rendering/privacy contracts, never live Drive, Gemini, cloud, CUPS, human action, or endpoint execution. |

The latest final-readiness record contains
**wsl_full_application_golden: PASS**. That is the recorded release
provenance. Earlier Story 5 evidence correctly preserved a then-blocked
isolated-runtime attempt; it must not be read as a contradiction or silently
rewritten. The directly runnable WSL proof described above remains the
deterministic Liblouis V1/V2 render golden, while the container remains the
frozen installed-runtime verification path.

The current final-readiness artifact records these live hero-path actions as
**NOT_RUN** for that release pass:

- Drive edit;
- human CUPS lifecycle;
- professional disposition;
- replacement submission.

They are not passing demonstrations. A future live run may update evidence only
from actual observed execution; never use a fixture or an old historical
record to fill those cells.

## What the latest release evidence records

The current release evidence records passing results for:

- lock consistency, frozen pytest, Ruff lint/format, strict mypy, diff and
  secret checks;
- Docker frozen build and installed-container readiness;
- repeated container Liblouis golden renders;
- WSL Liblouis smoke and the evidence-recorded WSL application golden;
- responsive offline fixture inspection and screenshot sanitization;
- committed-source Cloud Build and private Cloud Run deployment;
- authenticated, read-only private route smoke;
- temporary Token Creator cleanup verification.

It also records that Cloud Run remained private, the scheduler remained paused,
and no public invoker or persistent temporary Token Creator grant was present.

## Evidence handling rules

- Evidence is sanitized and schema-validated before it is tracked.
- It must not contain passwords, OAuth tokens, service-account keys, private
  principals, Drive IDs, private service URLs, machine paths, raw source, raw
  spool content, or captured BRF.
- Immutable hashes are evidence, not anonymity; private artifacts remain behind
  access control.
- A current evidence record may supersede an older verification state, but it
  never changes historical facts.
- A status in the dashboard is not proof of physical containment. Physical
  isolation, proof approval, replacement submission, endpoint completion,
  final verification, and closure remain separate human facts.

For the current release scope and real-versus-simulated boundary, see the root
[README](../README.md). For local CUPS authority, see
[local-floor-and-cups-simulator.md](local-floor-and-cups-simulator.md). For the
video flow, see [live-demo-runbook.md](live-demo-runbook.md).
