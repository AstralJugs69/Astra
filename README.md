# Astra — Braille Errata Relay

> **Astra is a production-minded change-control and recovery layer for braille production.** When authoritative source material changes after a braille master has been approved or production has begun, Astra determines which artifacts and observed production work can no longer be trusted, builds an evidence-backed recovery case, and preserves an auditable path to a human-verified replacement.

**Built for the All Things Agentic Hackathon / Taskmaster category with Google
ADK + Gemini, private Cloud Run, Firestore/GCS, Liblouis, and a read-only CUPS
production-observation adapter.**

## The production failure Astra addresses

A braille master was approved yesterday and production has already started. Today,
the authoritative source changes.

That correction may alter contractions, cell count, line wrapping, braille
pagination, or volume boundaries. A professional now has to establish which
approved master is stale, whether an observed production job used it, what
output may be affected, and what must happen next. Regenerating a file alone is
not a sufficient answer.

**Astra autonomously builds the recovery case.** It refetches the authoritative
source bytes, establishes what changed, regenerates a deterministic candidate
BRF, calculates exact page impact, performs bounded semantic assessment,
correlates production evidence, and prepares the incident and recovery record
for the responsible professional.

The hook is simple: **Astra makes it visible when yesterday's approved braille
master can no longer remain the trusted input to today's production decision.**
It does not claim to operate a printer or replace a qualified braille
proofreader.

## In one view

~~~mermaid
flowchart LR
    source["Authoritative source revision"] --> astra["ASTRA<br/>autonomous investigation"]
    astra --> case["Evidence-backed recovery case"]
    case --> gate["Human authority gate"]
    gate --> controls["Existing production controls"]
    controls --> output["Physical production and verification"]
~~~

**Astra autonomously completes investigation and recovery preparation.**
Humans retain authority over professional approval and irreversible
physical-production actions.

## What Astra does autonomously

| Workflow | What Astra completes |
| --- | --- |
| **Detect** | When enabled, a private automatic cycle drains the Drive change feed and performs an authoritative metadata-and-byte refetch. The fetched bytes, not a notification, establish truth. |
| **Understand** | Validates immutable source lineage, derives stable source-block differences, and identifies the accepted source revision that supersedes the baseline. |
| **Regenerate** | Uses a pinned Liblouis profile and deterministic formatter to create a candidate BRF, source map, manifest, hashes, and exact page-impact report. |
| **Assess impact** | Sends only persisted, bounded evidence to Gemini through ADK for semantic assessment; deterministic code owns BRF bytes, hashes, page counts, and state. |
| **Prepare recovery** | Correlates fresh read-only production observations, detects stale or ambiguous evidence, selects a fail-closed workflow state and recommendation, and constructs the report/disposition packet. |
| **Observe replacement** | After an operator independently submits an approved replacement through the existing production surface, associates a fresh unambiguous observation and can reach **REPLACEMENT_OBSERVED**. |

This is a complete workflow, not a chat response. It survives duplicate events,
crashes, stale evidence, and restarts through durable Firestore state,
idempotency keys, leases, immutable GCS artifacts, append-only human records,
and explicit state versions.

## Why this is agentic

Astra is agentic because it autonomously orchestrates a multi-stage operational
investigation across source authority, deterministic braille production,
semantic judgment, production observation, and recovery evidence.

Gemini is deliberately narrow: it makes a structured semantic assessment over
persisted evidence. Deterministic software owns production truth: source
normalization, translation, BRF bytes, profile identity, page impact, hashes,
lineage, recommendations, retry behavior, and state transitions.

> Astra is autonomous everywhere software can safely hold authority, and
> deliberately human-gated where professional or physical production authority
> begins.

The interesting part is not a model clicking a stop button. It is Astra
determining when a previously approved master is no longer trustworthy,
establishing the evidence behind that conclusion, preparing the recovery case,
and knowing exactly where its authority ends.

## End-to-end workflow

The intended live hero path is:

1. Show an approved baseline and its observed production context.
2. Change the prepared authoritative Drive source from V1 to V2 through the
   normal Drive UI.
3. Astra's enabled private automatic cycle detects the revision, refetches the
   source bytes, and builds the candidate BRF and page-impact evidence. No
   reconciliation command is run after the edit.
4. Show the bounded Gemini assessment alongside deterministic source, BRF, and
   production-observation evidence.
5. Show a recovery recommendation and the human professional gate.
6. A human acts through the existing external production controls; Astra later
   observes an independently submitted replacement job and records
   **REPLACEMENT_OBSERVED**.

## Contents

- [Architecture](#technical-architecture)
- [Evidence and release chronology](#evidence-recorded-release-chronology)
- [Quick start](#quick-start-choose-the-truthful-path)
- [Fresh-project deployment](docs/fresh-project-deployment.md)
- [Security and authority](docs/security-and-authority.md)
- [Scope and limits](#scope-and-unclaimed-behavior)

## Deliberate human authority boundary

| Astra owns | Humans own through existing, independent controls |
| --- | --- |
| Source-change detection, authoritative refetch, lineage, deterministic candidate generation, impact analysis, bounded semantic assessment, evidence correlation, state, recommendation, and recovery preparation | Professional disposition, halt/cancel decision and execution, physical-output isolation, tactile proof approval, replacement submission, final physical verification, and closure |

Astra has no CUPS or device-control route. It cannot submit, hold, release,
cancel, restart, pause, or physically stop a production job. Scheduler state
does not prove physical containment. A candidate BRF is not an approved
production master.

This keeps professional braille proofing and physical-production authority
with the people and controls already responsible for them.

## Demonstrated vertical slice: real, simulated, and fixture-only

| Capability | Truthful status |
| --- | --- |
| Liblouis translation, BRF serialization, pagination, source maps, profile/table binding, hashing, and page impact | **Real and reproducible.** Pinned profiles and exact BRF golden checks are part of the verification evidence. |
| Google Drive change/reconcile path | **Real read-only adapter.** When enabled, a private scheduler cycle uses Drive change information plus authoritative byte refetch, then processes one durable outbox record. |
| Gemini / Google ADK semantic assessment | **Real bounded adapter when configured.** It returns structured semantic assessment only; it never owns production facts or device tools. |
| Firestore ledger and GCS artifact lineage | **Real cloud adapters.** They hold durable workflow state and immutable content-addressed evidence, not a publishing system of record. |
| Private Cloud Run deployment and authenticated read-only smoke | **Real.** The final readiness evidence records a private deployment and authenticated GET-only smoke. |
| CUPS scheduling and read-only production observation | **Real in the WSL Gate 0 harness.** The application and bridge remain read-only with respect to CUPS. |
| Physical embossing endpoint | **Simulated only.** The endpoint simulator stands in for the final physical act; CUPS/job observation is not faked. |
| Offline screenshots and local fixture | **Sanitized fixture only.** It proves the UI and contracts, never live Drive, Gemini, cloud, CUPS, professional action, or endpoint execution. |

## Designed for a real production world, without pretending every facility is the same

Braille facilities do not use a universal intake or production stack. Source
material can arrive as NIMAS packages, publisher files, Word, PDF, EPUB, secure
uploads, or scanned physical material. Production can involve Duxbury,
BrailleBlaster, Braille 2000, Liblouis-backed tooling, direct embossers,
network-device queues, or plate/PED workflows. It also includes transcription,
proofreading, work-order release, physical output, finishing, and distribution.

The project researched those operational realities rather than treating
conversion as a toy problem. For example, [American Printing House for the
Blind describes its translation, proofreading, work-order, and production
stages](https://www.aph.org/blog/aph-behind-the-scenes-a-look-at-the-people-and-processes-that-bring-you-braille/);
[National Braille Press documents an end-to-end
production-floor workflow](https://www.nbp.org/ic/nbp/about/aboutus/tour.html);
and the U.S. Department of Education documents the
[NIMAS/NIMAC source path](https://sites.ed.gov/idea/idea-files/questions-and-answers-on-the-national-instructional-materials-accessibility-standard-nimas-aug-9-2021/).

Google Drive and CUPS are the hackathon's reproducible adapters, not claims
about industry-standard intake or production control. The durable architecture
is based on **source authority** and **production observation** boundaries, not
on those two products.

> The implementation uses the smallest complete production topology needed to
> demonstrate the recovery architecture.

The deeper reference workflow, facility role map, sources, and integration
rationale are preserved in
[instruction.md](instruction.md#14-real-production-floor-reference-model).

## Replaceable adapter model

~~~mermaid
flowchart TB
    subgraph inputs["Facility-specific source authority examples"]
        nimas["NIMAS / publisher package<br/>(future adapter)"]
        sharepoint["SharePoint or SFTP<br/>(future adapter)"]
        drive["Google Drive<br/>(demonstrated adapter)"]
    end
    inputs --> authority["Source Authority interface"]
    authority --> core["ASTRA CORE<br/>lineage • deterministic BRF • impact • recovery"]
    core --> observation["Production Observation interface"]
    observation --> cups["CUPS read-only observer<br/>(demonstrated adapter)"]
    observation --> future["Vendor / Windows / plate workflow<br/>(future adapters, not implemented)"]
    cups --> endpoint["Simulated physical endpoint only"]
~~~

Future examples are not integrations or product promises. A new facility should
replace an adapter while preserving Astra's source-lineage, evidence,
professional-authority, and fail-closed recovery semantics.

## Technical architecture

For a Devpost-uploadable version of the authority model, use the static
[architecture diagram (PNG)](docs/assets/astra-architecture-diagram.png) or
its [accessible SVG source](docs/assets/astra-architecture-diagram.svg). It
shows the demonstrated adapters, future adapter boundary, and explicit
no-device-control boundary without claiming unimplemented integrations.

~~~mermaid
flowchart LR
    drive["Authoritative Drive source<br/>(read-only adapter)"] --> cycle["Private automatic cycle"]
    cycle --> reconcile["Change feed + authoritative byte refetch"]
    reconcile --> deterministic["Deterministic diff → Liblouis → BRF → page impact"]
    deterministic --> gemini["Gemini / ADK<br/>semantic assessment only"]
    gemini --> ledger["Firestore state + append-only timeline"]
    deterministic --> artifacts["Immutable GCS artifacts"]
    bridge["WSL read-only CUPS bridge"] --> ledger
    ledger --> api["Private Cloud Run API"]
    api --> local["Loopback presentation server<br/>watch + sanitized SSE"]
    local --> professional["Human professional"]
    professional -. independent existing production surface .-> cups["CUPS / vendor workflow"]
    cups --> endpoint["Simulated physical endpoint only"]
~~~

The architecture deliberately separates:

- **Probabilistic judgment**: Gemini/ADK receives bounded evidence and returns
  structured semantic assessment.
- **Deterministic truth**: Liblouis, formatting, BRF bytes, hashes, profile and
  table identity, source maps, page impact, lineage, policy, and state machine.
- **Durable evidence**: Firestore holds idempotent state and append-only human
  records; GCS stores create-only content-addressed artifacts.
- **Production observation**: the local bridge can read CUPS evidence but has
  no queue or device mutation authority.
- **Human authority**: professional and physical actions remain attributable
  records or independent external operations.

## Delivered workflow boundaries

The implementation stories remain useful engineering scope; the judge-facing
workflow above is the easier way to understand their operational effect.

| Story | Delivered boundary |
| --- | --- |
| 1. Baseline and source | Immutable baseline, accepted source revision, profile lineage, and stable source-block identity. |
| 2. Deterministic impact | Real Liblouis translation, deterministic BRF, manifests, source maps, and exact page impact. |
| 3. Semantic report | Grounded Gemini assessment bounded to persisted source-diff evidence with leases and retry convergence. |
| 4. Containment and proof | Separate professional disposition, operator attestation, containment confirmation, and exact-candidate proof gates. |
| 5. Replacement observation | A machine operator can associate a fresh read-only observation of an independently submitted replacement job; the implemented boundary ends at **REPLACEMENT_OBSERVED**. |

## Repository guide

| Path | Purpose |
| --- | --- |
| [src/braille_errata_relay/braille](src/braille_errata_relay/braille) | Deterministic normalization, Liblouis boundary, pagination, BRF, source map, and impact logic. |
| [src/braille_errata_relay/application](src/braille_errata_relay/application) | Idempotent workflows, retry/recovery behavior, and fail-closed gates. |
| [src/braille_errata_relay/adapters](src/braille_errata_relay/adapters) | Google Drive, Firestore, GCS, and Gemini/ADK adapters. |
| [src/braille_errata_relay/api](src/braille_errata_relay/api) | Private Cloud Run API and route-level identity enforcement. |
| [src/braille_errata_relay/presentation](src/braille_errata_relay/presentation) | Loopback dashboard, live watch SSE, and sanitized offline fixture. |
| [local_bridge](local_bridge) | Read-only CUPS observer and transactional observation journal. |
| [simulator/cups_backend](simulator/cups_backend) | Simulated physical endpoint only. |
| [infra](infra) | Explicit human-run GCP, WSL, CUPS, and demonstration tools. |
| [demo](demo) | Sanitized fixtures, expected BRF artifacts, screenshots, and evidence. |
| [docs/assets](docs/assets) | Submission-ready static architecture diagram (PNG/SVG). |
| [docs](docs) | Setup, deployment, security, test/evidence, and demo chapters. |

## Quick start: choose the truthful path

### Offline evaluator path — five minutes, zero credentials

This is safe for any evaluator. It does not contact Google Cloud, Drive, CUPS,
or a device. It proves only the UI and contracts.

~~~powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
uv sync --frozen
uv run --frozen pytest -q -p no:cacheprovider tests/unit/test_watch_floor.py
uv run --frozen python -m braille_errata_relay.presentation.screenshot_fixture --port 8877
~~~

Open:

- http://127.0.0.1:8877/watch/quiet for the quiet monitoring state.
- http://127.0.0.1:8877/watch for the alert, live-style event, and optional
  local sound control.
- http://127.0.0.1:8877/ for the synthetic incident dashboard.

Every page is visibly marked **SANITIZED DEMO FIXTURE**. The fixture has
GET-only routes; it cannot create a human record, contact an external service,
or operate production equipment.

### Live evaluator path — configured private environment

This path demonstrates actual adapters and requires:

1. a private deployed Cloud Run service;
2. a configured Google Drive source shared read-only with the runtime identity;
3. Firestore/GCS state and artifact resources;
4. ordinary local user ADC for the loopback dashboard;
5. a temporary, service-account-scoped demonstrator Token Creator grant only
   while the local dashboard runs;
6. the optional WSL/CUPS local-floor setup for real queue observation.

Start here:

- [quickstart.md](docs/quickstart.md) for local configuration and fixture/live
  choices;
- [fresh-project-deployment.md](docs/fresh-project-deployment.md) for an
  explicit human-reviewed GCP deployment path;
- [google-cloud-setup.md](docs/google-cloud-setup.md) for credentials, Drive
  access, private Cloud Run, and temporary impersonation rules;
- [local-floor-and-cups-simulator.md](docs/local-floor-and-cups-simulator.md)
  for the human-owned local floor.

The live watch launcher is intentionally read-only:

~~~powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\infra\demo\start_demo.ps1
~~~

It displays durable state while the private service performs configured
background reconciliation. It cannot grant IAM, control CUPS, or make a
professional disposition. Its temporary authentication prerequisite is
explained in the Google Cloud setup guide.

## Configuration and security

The non-destructive initializer writes an ignored local environment file that
contains identifiers only, never a password, OAuth token, or service-account
JSON key:

~~~text
uv run --frozen braille-relay init-local-config --interactive
uv run --frozen braille-relay doctor --config .env
~~~

The security model keeps the browser loopback-only; credentials, private URLs,
Drive IDs, and service-account material never reach browser JavaScript. It
uses signed sessions, CSRF protection, same-origin/CSP protections, sanitized
SSE, audience-bound ID tokens, separate principals, temporary scoped
impersonation, and a read-only CUPS observer. See
[security-and-authority.md](docs/security-and-authority.md) and
[configuration.md](docs/configuration.md).

## Verify the implementation

Run independent checks so one pass cannot conceal another:

~~~text
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
git diff --check
docker build --tag braille-errata-relay:final-demo-readiness .
docker run --rm --network none --read-only --cap-drop ALL --entrypoint python braille-errata-relay:final-demo-readiness -m braille_errata_relay.container_smoke
~~~

The latest recorded release evidence records a passing frozen suite, lock check, Ruff,
strict mypy, Docker build, container readiness, repeated Liblouis golden
renders, WSL Liblouis smoke, an evidence-recorded WSL full-application golden,
responsive fixture inspection, private Cloud Run deployment, authenticated
read-only smoke, and temporary Token Creator cleanup. The directly runnable
WSL golden is the deterministic Liblouis render check; Windows-host skips
remain platform-specific and are not passing mocks. Read the exact chronology
in [testing-and-evidence.md](docs/testing-and-evidence.md).

## Evidence: recorded release chronology

Evidence is sanitized, schema-validated, and deliberately chronological:

| Evidence | Role |
| --- | --- |
| [gate0-local-floor.json](demo/evidence/gate0-local-floor.json) | Historical local CUPS/Liblouis foundation evidence. |
| [cloud-gate0.json](demo/evidence/cloud-gate0.json) | Historical private Cloud Run, Drive, Firestore, GCS, and Gemini/ADK seam evidence. |
| [final-story5-dashboard.json](demo/evidence/final-story5-dashboard.json) | Historical Story 5 and dashboard evidence. |
| [final-demo-readiness.json](demo/evidence/final-demo-readiness.json) | **Latest recorded release evidence.** It supersedes earlier WSL-runtime uncertainty and records the execution boundary of that release. |

Historical snapshots remain valuable because they show the gates crossed, but
they do not override the latest recorded release's facts. Fixture screenshots are
separate from live execution evidence and never stand in for it.

The final-demo-readiness release labels its unexecuted fresh Drive edit, human
CUPS lifecycle, professional disposition, and replacement submission as
**NOT_RUN**. It is not evidence of a later automatic-watch exercise; a new
release record is created only after that path is actually run.

## Record the demo honestly

Use [live-demo-runbook.md](docs/live-demo-runbook.md) for the 4–5 minute
problem-first recording story. The detailed human action/CUPS procedure stays
in [active-professional-review-demo.md](docs/active-professional-review-demo.md).

If a live prerequisite is unavailable, use the offline fixture and say so on
camera. It demonstrates the visual interaction and safety contracts, not live
Drive detection, Gemini execution, CUPS observation, professional action,
replacement submission, or endpoint proof.

## Scope and unclaimed behavior

Astra is not a braille publishing platform, work-order system of record,
inventory, fulfillment, shipping, CRM, or production-device control product.
It does not claim universal source support, tactile-graphics processing,
mathematics/table transcription, or compatibility with all facility software.

The demonstrated topology is intentionally narrow:

- one strict text/Markdown source profile;
- one real Drive source-authority adapter;
- deterministic Liblouis BRF production under a pinned profile;
- one real CUPS observer topology in WSL;
- a simulated final physical embossing endpoint;
- human-controlled professional and physical production authority.

The current boundary is **adapter coverage**. [instruction.md](instruction.md)
and [architecture.md](architecture.md) preserve the grounded target design and
research context. This README and the recorded
[final-demo-readiness.json](demo/evidence/final-demo-readiness.json) define
the evidenced Story 5 boundary: the workflow ends at
**REPLACEMENT_OBSERVED** and makes no final-verification, physical-completion,
notification, or closure claim.

## Further reading

- [Fresh-project Google Cloud deployment](docs/fresh-project-deployment.md)
- [Google Cloud authentication and Drive setup](docs/google-cloud-setup.md)
- [Authoritative Drive source and automatic watch](docs/authoritative-drive-source.md)
- [Testing and evidence chronology](docs/testing-and-evidence.md)
- [Security and authority model](docs/security-and-authority.md)
- [Live demonstration runbook](docs/live-demo-runbook.md)
- [Local CUPS simulator and bridge](docs/local-floor-and-cups-simulator.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Product and production research](instruction.md)
- [Grounded target architecture and implementation context](architecture.md)
