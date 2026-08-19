# 🏗️ Astra POC — Technical Architecture (Deep Dive)

> Companion document to the Astra — Organized Project Notebook. Read that page first — it is the canonical source of truth. This page is the detailed system/code architecture for the first POC implementation, produced per the Next Session Technical Architecture Prompt. It settles design before implementation begins; it is not a task list and contains no code.
> 

---

# 1. Executive architecture summary

Astra's first POC is a **single stateless Cloud Run service** fronted by **thin, dependency-free local hook scripts** that run inside the user's Antigravity CLI workspace. The service receives normalized `PostToolUse`/`Stop` events, maintains a **compact trajectory state** in Firestore, runs a **cheap rule-based (optionally model-assisted) Fast tier** on every event, and escalates to a **bounded, single-pass Deep tier** only when a signal warrants it. Deep tier reasoning is performed by narrow, purpose-built **engines** (bugfix verification, reasoning critique, alternative ranking) that consume a **bounded evidence packet**, never the raw session.

The codebase is organized as a **ports-and-adapters (hexagonal) architecture**: a pure `domain` core (trajectory state, signal rules, escalation policy, evidence budgeting, anti-loop policy) that knows nothing about Antigravity, HTTP, or any model SDK; an `integration` layer that is the *only* place Antigravity's raw event shape is known; `engines`/`tiers` that implement domain-defined reasoning ports using injected infrastructure; and an `infrastructure` layer of concrete adapters (model provider, Firestore, evidence retrieval). This is deliberately **not** a general-purpose agent framework — there is no plugin system, no open-ended tool loop, and no multi-provider abstraction beyond what the POC needs.

The **firm, already-decided** constraints from the notebook are preserved exactly: Antigravity CLI only, Hooks only (`PostToolUse`, `Stop`), MCP permanently excluded, same model family as the main agent, Zindi-based evaluation with turns-to-fix as the primary metric, and a hard anti-loop requirement. Everything else in this document — language choice, repository layout, persistence technology, service topology, and the concrete escalation/anti-loop parameters — is either a **firm recommendation for this session** or explicitly flagged **experimental, to be tuned by the evaluation harness**.

---

# 2. Architectural goals and constraints

**Hard constraints (already decided, from the notebook):**

- Antigravity CLI (`agy`) is the only POC surface; IDE/2.0 is post-POC.
- Hooks (`PostToolUse`, `Stop`) are the only integration mechanism. MCP is permanently excluded, not deferred.
- Astra uses the same model family as the main agent.
- Astra must **fail open**: if it cannot safely evaluate an event, the main agent must be allowed to stop.
- A robust anti-loop module is a hard POC requirement, not a later refinement.
- Baseline (no-Astra) performance must be established before any improvement claim.
- The submission must use Gemini 3.5+, a Google Agent Framework (the Antigravity SDK), and at least one Google Cloud infrastructure service (Cloud Run).

**Design goals for this session:**

- Concrete enough to start implementation directly from this document.
- Small enough to be realistic for a hackathon POC.
- Modular enough that Shadow→Assist→Intervene, Fast/Deep tiers, the context engine, and benchmark-killing detection can be added later without rewriting the core.
- Explicit, enforceable boundaries so Antigravity-specific detail cannot leak into Astra's reasoning core, and so Astra cannot accidentally grow into a general autonomous agent framework.

---

# 3. Language and technology selection

## Evaluation

| Criterion | Python | TypeScript/Node | Go |
| --- | --- | --- | --- |
| Google/Gemini SDK maturity | Strongest — `google-genai`, Vertex AI, and most Antigravity-adjacent tooling ships Python first | Good, slightly behind Python in examples/coverage | Weakest — thin/community clients only |
| Cloud Run fit | Excellent, first-class | Excellent, first-class | Excellent, best cold-start/footprint |
| Structured data / schema validation | Excellent (Pydantic v2) | Good (zod) | Verbose (manual structs, less runtime validation) |
| Async/event-driven HTTP service | Excellent (FastAPI + asyncio) | Excellent (native event loop) | Excellent (goroutines) |
| Subprocess/hook scripting | Trivial, stdlib-only, ubiquitous | Trivial, needs Node runtime present | Requires a compiled binary per platform |
| Data-science / evaluation harness fit | Excellent — the Zindi tasks are themselves Python/pandas/sklearn territory | Weak — would require a second language for evaluation | Weak, same problem |
| Hackathon development speed | Fastest for this team's likely stack | Fast | Slower — more ceremony for the same behavior |
| Testing ecosystem | Excellent (pytest) | Excellent (vitest/jest) | Good (stdlib testing) but more boilerplate |

**Status: firm recommendation for this session.**

**Decision: Python for the entire POC** — backend service, reasoning engines, hook scripts, and evaluation harness. The deciding factors are (1) the Google Gen AI / Antigravity SDK ecosystem is Python-first, (2) Pydantic v2 gives us free, strict schema validation for every contract this document defines (raw events, normalized events, trajectory state, evidence packets, engine outputs), and (3) the evaluation harness runs against real Zindi data-science tasks, which is Python's home turf — using a second language there would fragment the project for no benefit. Go's advantages (startup latency, static binary) only matter for the *hook script*, and that gap is closed by keeping the hook script stdlib-only (see Section 9); if hook-side latency is later measured to be a real problem, the hook can be swapped for a small Go binary without touching anything else, because the hook's only contract with the rest of the system is the HTTP/JSON wire format (see Section 26, ADR-1).

## Supporting technologies (POC)

- **FastAPI + Uvicorn** — the Cloud Run service. Async-native, integrates directly with Pydantic for request/response validation.
- **Pydantic v2 + pydantic-settings** — every schema in this document (raw event, normalized event, trajectory state, evidence packet, engine result, settings) is a Pydantic model. `pydantic-settings` is the single configuration entry point (Section 25).
- **Antigravity SDK** — Astra's agent/reasoning runtime and model-calling surface, per the notebook's explicit guidance and the hackathon's Google-Agent-Framework requirement. Wrapped behind a `ModelProvider` port (Section 11) so it is never called directly from `domain`, `engines`, or `tiers`.
- **google-cloud-firestore** — trajectory/intervention state persistence (Section 15).
- **structlog** — structured, contextual JSON logging (session_id/correlation_id automatically threaded through every log line). Justified narrowly: Section 19's observability requirements need per-request contextual fields on every line, which stdlib `logging` can do but with materially more boilerplate.
- **httpx** — used only inside `infrastructure/` adapters and in tests (FastAPI's own test client plus mocking outbound calls). Never used inside `domain`.
- **tenacity** — narrow, bounded retries around Firestore transient errors only. Explicitly **not** used around the fail-open boundary itself (Section 21) — retries must never turn a bounded timeout into an unbounded one.
- **pytest, pytest-asyncio, respx** — testing (Section 24).
- **typer** (optional) — CLI ergonomics for the evaluation harness runner.

**Explicitly rejected for the POC:** an ORM (Firestore is used directly with typed (de)serialization helpers — an ORM adds ceremony without benefit over a document store), a message broker/queue (Pub/Sub, Redis) — the POC's request/response flow is synchronous and small enough that a queue would be premature infrastructure, and a second language for hooks or evaluation.

---

# 4. System architecture

```
 USER WORKSPACE (local machine)
┌────────────────────────────────────────────────────────────────────┐
│ Antigravity CLI (agy)                                               │
│   │ PostToolUse / Stop hook events (stdin JSON)                     │
│   ▼                                                                 │
│ hooks/post_tool_use.py , hooks/stop.py   (thin, stdlib-only)        │
│   │ HTTPS POST (+ bearer token)                 ▲ stdout decision   │
└───┼──────────────────────────────────────────────┼──────────────────┘
    │                                              │
    ▼                                              │
═══════════════ CLOUD RUN: astra-backend (stateless) ═══════════════
│ api/routers/event.py , api/routers/reason.py                        │
│   │ fail-open wrapper (timeout/exception → "continue")              │
│   ▼                                                                 │
│ integration/antigravity/normalize.py → domain.events.AstraEvent     │
│   ▼                                                                 │
│ application/pipeline.py                                             │
│   ├─▶ persistence.load(session_id) ───────────────┐                │
│   ├─▶ domain/trajectory.py   (state update)         │  Firestore   │
│   ├─▶ domain/signals.py      (rule detectors)         │ (trajectory,│
│   ├─▶ tiers/fast/assessor.py (optional cheap model)    │ intervention│
│   ├─▶ domain/modes.py        (escalation policy)        │ state)     │
│   ├─▶ domain/intervention.py (anti-loop / budget)         │          │
│   │       │ Assist or Intervene                            │        │
│   │       ▼                                                │        │
│   │  domain/evidence.py → infrastructure/evidence/*         │        │
│   │       ▼                                                │        │
│   │  tiers/deep/orchestrator.py                             │        │
│   │       ▼                                                │        │
│   │  engines/bugfix/verifier.py                             │        │
│   │  engines/reasoning/critic.py                            │        │
│   │  engines/reasoning/alternatives.py                       │        │
│   │       ▼                                                │        │
│   │  infrastructure/model_providers/antigravity_sdk.py ─▶ Gemini    │
│   └─▶ persistence.save(session_id, state) ─────────────────┘        │
│   ▼                                                                 │
│ integration/antigravity/response_format.py → decision JSON          │
═══════════════════════════════════════════════════════════════════
                     │ structured logs (Cloud Logging)
                     ▼
            evaluation/  (external harness, offline/local)
            reads: Astra intervention log + Antigravity transcript
            writes: evaluation/runs/*.sqlite  (isolated store)
```

Antigravity generates events; Astra evaluates them (notebook Section 8 design principle, preserved exactly). One Cloud Run service, two route groups, one Firestore-backed state store, one evaluation harness that observes from outside without touching production state.

---

# 5. Code architecture

The POC uses **ports-and-adapters (hexagonal) architecture**, chosen deliberately over a plain layered MVC-style split because Astra's central risk is *framework contamination of its reasoning core* — Antigravity's hook schema is explicitly unstable (the notebook flags the IDE hook bug as an open, unresolved Google-side issue), and the model provider will plausibly change shape over time. Hexagonal architecture makes both of those swappable without touching `domain`.

Six groupings, each answering one of the "where does X live" questions directly:

| Question | Lives in |
| --- | --- |
| Hook handling | `hooks/` (local, outside the `astra` package entirely) |
| Event normalization | `astra/integration/antigravity/` |
| Trajectory state | `astra/domain/trajectory.py` (schema/logic) + `astra/infrastructure/persistence/` (storage) |
| Signal detection | `astra/domain/signals.py` (rules) + `astra/tiers/fast/` (optional model-assisted classification) |
| Intervention / escalation policy | `astra/domain/modes.py`, `astra/domain/intervention.py` |
| Fast tier | `astra/tiers/fast/` |
| Deep tier | `astra/tiers/deep/` |
| Bugfixing engine | `astra/engines/bugfix/` |
| Reasoning/critique engine | `astra/engines/reasoning/` |
| Evidence-packet construction | `astra/domain/evidence.py` |
| Raw evidence retrieval | `astra/infrastructure/evidence/` |
| Anti-loop logic | `astra/domain/intervention.py` (policy) + `astra/infrastructure/persistence/` (counters) |
| Evaluation instrumentation | `astra/evaluation/` |
| Model/provider abstraction | `astra/domain/model_ports.py` (interface) + `astra/infrastructure/model_providers/` (concrete) |
| Configuration | `astra/settings.py` (single entry point) |
| Tests | `tests/`, mirroring `src/astra/` |

---

# 6. Repository structure

```
astra/
├── README.md
├── pyproject.toml
├── .env.example
├── Dockerfile
├── Makefile
├── docs/
│   ├── adr/                      # one file per Section 26 decision
│   ├── event-model.md
│   └── evidence-schema.md
│
├── hooks/                        # LOCAL — runs in the user's Antigravity workspace
│   ├── README.md                 # install instructions, hooks.json snippet
│   ├── common.py                 # stdin/stdout contract, HTTP call, fail-open default
│   ├── post_tool_use.py          # thin dispatcher for PostToolUse
│   ├── stop.py                   # thin dispatcher for Stop
│   └── hooks.json.example        # registers the two scripts with Antigravity
│
├── src/
│   └── astra/
│       ├── __init__.py
│       ├── settings.py           # single config entry point (Section 25)
│       │
│       ├── api/                  # delivery layer — Cloud Run HTTP surface
│       │   ├── main.py           # app factory
│       │   ├── deps.py           # composition root: wires infra → app → domain
│       │   ├── routers/
│       │   │   ├── event.py      # POST /event
│       │   │   ├── reason.py     # POST /reason
│       │   │   └── health.py
│       │   └── auth.py           # bearer-token check (Section 20)
│       │
│       ├── integration/          # the ONLY place raw Antigravity shape is known
│       │   └── antigravity/
│       │       ├── raw_schema.py       # loose, extra="allow" Pydantic models
│       │       ├── normalize.py        # raw → domain.events.AstraEvent (+ redaction)
│       │       └── response_format.py  # domain.Decision → Antigravity's expected stdout shape
│       │
│       ├── application/          # orchestration / use cases — knows domain, not integration
│       │   ├── pipeline.py       # the Section 11 decision pipeline
│       │   ├── handle_post_tool_use.py
│       │   └── handle_stop.py
│       │
│       ├── domain/               # PURE — zero I/O, zero framework imports
│       │   ├── events.py         # AstraEvent (normalized)
│       │   ├── trajectory.py     # TrajectoryState schema + pure update/reduce functions
│       │   ├── signals.py        # Signal schema + rule-based detectors
│       │   ├── modes.py          # Mode enum + escalation policy
│       │   ├── routing.py        # critique-back vs Astra-reasons-itself policy
│       │   ├── evidence.py       # EvidencePacket schema + budgeting/dedup/prioritization
│       │   ├── intervention.py   # intervention budget + anti-loop policy (pure)
│       │   ├── reasoning_ports.py # Engine protocol (interface only)
│       │   └── model_ports.py    # ModelProvider protocol (interface only)
│       │
│       ├── engines/              # Astra's actual reasoning IP — implements reasoning_ports
│       │   ├── base.py
│       │   ├── bugfix/
│       │   │   └── verifier.py   # Evidence-First Debugging Protocol (notebook Section 13)
│       │   └── reasoning/
│       │       ├── critic.py     # reasoning critique (notebook Section 15)
│       │       └── alternatives.py # multi-solution generation/ranking (model laziness)
│       │
│       ├── tiers/
│       │   ├── fast/
│       │   │   └── assessor.py   # cheap rules first, optional cheap model call
│       │   └── deep/
│       │       └── orchestrator.py # evidence assembly + engine selection + routing
│       │
│       ├── infrastructure/       # concrete adapters — implement domain ports
│       │   ├── model_providers/
│       │   │   └── antigravity_sdk_provider.py
│       │   ├── persistence/
│       │   │   ├── firestore_store.py
│       │   │   └── memory_store.py    # for tests / local dev
│       │   ├── evidence/
│       │   │   ├── transcript_retriever.py
│       │   │   ├── repo_retriever.py   # naive file read — NOT the future context engine
│       │   │   └── web_research.py
│       │   └── observability/
│       │       └── logging.py
│       │
│       └── evaluation/           # external, isolated from production state
│           ├── runner.py
│           ├── metrics.py        # turns-to-fix and secondary metrics
│           ├── storage.py        # separate SQLite/JSONL store
│           ├── report.py
│           └── tasks/
│               ├── reproducible_bugs/
│               └── zindi/
│
├── tests/
│   ├── unit/
│   │   ├── domain/
│   │   ├── engines/
│   │   └── tiers/
│   ├── integration/
│   │   ├── api/
│   │   └── hooks/                # subprocess tests against hooks/*.py
│   ├── e2e/                      # marked, run manually / nightly, real Antigravity CLI
│   └── fixtures/
│       └── hook_payloads/
│           ├── post_tool_use_success.json
│           ├── post_tool_use_error.json
│           └── stop_after_failed_verification.json
│
├── scripts/
│   ├── dev_server.sh
│   ├── install_hooks.sh
│   └── run_eval.sh
│
└── deploy/
    ├── cloudrun_service.yaml
    └── deploy.sh
```

---

# 7. Module-by-module responsibilities

**`hooks/`** — Responsibility: relay stdin JSON to Astra over HTTPS and print Astra's decision to stdout, nothing else. Must contain: stdlib-only Python (`json`, `urllib.request`, `sys`, `time`), the fail-open default, a small local config read (endpoint URL, bearer token, timeout). Must not contain: any signal detection, policy, or reasoning logic, and no dependency on the `astra` package. Depends on: nothing internal — only the documented HTTP/JSON wire contract.

**`api/`** — Responsibility: HTTP delivery, auth, and the fail-open wrapper around the whole pipeline call. Must contain: route handlers, the composition root (`deps.py`) that instantiates concrete infrastructure and injects it into `application`. Must not contain: business logic, Antigravity-shape knowledge (delegates to `integration`), or direct model/Firestore calls. Depends on: `integration`, `application`, `infrastructure` (only in `deps.py`, for wiring).

**`integration/antigravity/`** — Responsibility: the single translation point between Antigravity's actual (unstable) hook payload and Astra's stable internal event model; also owns redaction/truncation policy at ingestion. Must contain: loose raw schemas (`extra="allow"`), pure normalize functions, response formatting back to Antigravity's expected shape. Must not contain: policy or reasoning. Depends on: `domain` only.

**`application/`** — Responsibility: orchestrates the Section 11 decision pipeline — load state, run signals, consult escalation policy, call fast/deep tiers, apply anti-loop, persist, respond. Must not contain: raw Antigravity knowledge, direct SDK/Firestore calls (uses injected ports). Depends on: `domain`, `engines`, `tiers`.

**`domain/`** — Responsibility: everything that is a *decision* rather than an *I/O action* — trajectory state shape and pure transitions, signal rules, escalation policy, evidence budgeting math, intervention/anti-loop policy, and the interfaces (`Protocol` classes) for engines and model providers. Must contain: pure functions and Pydantic schemas only. Must not contain: any import of FastAPI, Firestore, the Antigravity SDK, or `integration`. Depends on: nothing else in `astra` (stdlib + Pydantic only).

**`engines/`** — Responsibility: Astra's actual reasoning intellectual property — the prompts and structured-output contracts for bugfix verification, reasoning critique, and alternative ranking. Must contain: engine classes implementing `domain.reasoning_ports.Engine`, constructed with an injected `ModelProvider` and `EvidenceRetriever`. Must not contain: Antigravity-shape knowledge or direct SDK instantiation (SDK is injected). Depends on: `domain` (ports/schemas).

**`tiers/fast/`** — Responsibility: cheap, frequent assessment. Runs domain rule detectors first; only escalates to a small model call for ambiguous cases. Depends on: `domain`, injected `ModelProvider`.

**`tiers/deep/`** — Responsibility: assembles the evidence packet, selects which engine(s) to run, applies the routing policy (critique-back vs. Astra-reasons-itself vs. combined). Depends on: `domain`, `engines`.

**`infrastructure/`** — Responsibility: every concrete adapter — Antigravity SDK calls, Firestore reads/writes, transcript/file/web evidence retrieval, structured logging. Each adapter implements exactly one `domain` port. Must not contain: business rules. Depends on: `domain` (implements its ports) — nothing else in `astra`.

**`evaluation/`** — Responsibility: run identical tasks with and without Astra, compute turns-to-fix and secondary metrics, store results in a store physically separate from production Firestore. Depends on: `domain` (for metric types), treats the running Astra service as an external black box via its own logs/API.

---

# 8. Dependency boundaries

```
hooks/  ──HTTP/JSON only, no import of astra──▶  api/

api/  ──▶  integration/  ──▶  domain/
api/  ──▶  application/  ──▶  domain/, engines/, tiers/
api/  ──▶  infrastructure/        (composition root wiring ONLY, in deps.py)

engines/  ──▶  domain/            (ports/schemas)
tiers/    ──▶  domain/, engines/

infrastructure/  ──▶  domain/     (implements domain's ports)

evaluation/  ──▶  domain/ (types), api/ (as an external client)

domain/  ──▶  (nothing else in astra; stdlib + Pydantic only)
```

**Allowed**, matching the notebook's own example format: `hook → application → domain`.

**Forbidden**: `domain → Antigravity SDK`, `domain → Firestore`, `domain → integration`, `engines → integration`, `infrastructure → application`, `infrastructure → api`.

The single rule that matters most: **`domain` has zero outward dependencies, and `integration` is the only code in the repository allowed to know Antigravity's raw event shape.** Everything else in Section 22's original questions ("how do we prevent tight coupling to Antigravity" / "how do we prevent this from becoming a general agent framework") follows from enforcing exactly these two rules plus the explicit absence of a plugin system or open-ended tool loop anywhere in `engines/` or `tiers/`.

---

# 9. Antigravity hook integration

**Status: mechanism already decided (notebook, firm) — Hooks only, `PostToolUse` and `Stop`.** The concrete script design below is this session's recommendation.

## What lives in the local hook process vs. the Cloud Run backend

The hook script is kept **maximally thin** for three concrete reasons: (1) it runs on the user's machine during a live Antigravity session, so its own latency is directly additive to the user's perceived hook latency; (2) it must work with zero install friction during hackathon demo setup — stdlib-only means no `pip install` step; (3) it is the outermost fail-open boundary (Section 21) — the fewer moving parts it has, the easier it is to guarantee it always exits cleanly.

The hook script does exactly four things: read stdin JSON, attach a correlation ID, POST it (with a bearer token, over HTTPS) to the Astra Cloud Run endpoint with a hard timeout, and print whatever comes back (or a hardcoded fail-open default) to stdout. **Everything else — parsing Antigravity's actual field names, signal detection, escalation policy, model calls, and evidence retrieval — lives in the Cloud Run backend.**

## Handling

- **Event parsing**: the hook does not parse semantically — it forwards the raw JSON body as-is. Semantic parsing happens once, in `integration/antigravity/normalize.py`.
- **stdin/stdout behavior**: read the full stdin payload, write exactly one JSON object to stdout matching whatever shape Antigravity's hook runner expects for that event type (`response_format.py` on the backend produces this shape; the hook only relays it).
- **Retries/timeouts**: the hook makes **one** HTTP attempt with a short timeout (config default, e.g. a few seconds for `PostToolUse`, longer for `Stop` since Deep tier may run) — no client-side retries, because a retry after a timeout risks doubling hook-side latency for no benefit; the backend's own internal retry budget (Section 21) is where resilience belongs.
- **Malformed input**: if stdin isn't valid JSON, the hook does not attempt to fix it — it logs locally (optional, off by default) and immediately emits the fail-open default.
- **Malformed Astra responses**: if the HTTP response isn't valid JSON or is missing the required `decision` field, same fail-open default.
- **Safe defaults**: the hardcoded fail-open default is `{"decision": "continue"}` for both event types — never invented as "deny"/"block".
- **Correlation/session IDs**: the hook generates a `correlation_id` (UUID) per invocation and forwards Antigravity's own session/conversation ID unchanged; both are echoed in every log line on the backend.
- **Hook-side latency**: measured and logged locally (optional debug file), because this is one of the architectural risks called out in Section 29 and needs empirical data from Milestone 1 before any tuning decision is made.

---

# 10. Event model

**Status: separation is this session's recommendation; the underlying hook events are notebook-firm (`PostToolUse`, `Stop`).**

## Raw Antigravity event (`integration/antigravity/raw_schema.py`)

Loosely typed (`model_config = {"extra": "allow"}`), because Antigravity's exact payload shape is not yet fully documented and is known to be unstable across surfaces (the notebook explicitly flags the IDE hook bug). Captured empirically during Milestone 1 and stored as fixtures in `tests/fixtures/hook_payloads/`.

## Normalized `AstraEvent` (`domain/events.py`)

- `event_id` — UUID generated at normalization time, for tracing.
- `session_id` — Antigravity's own session/conversation ID (Astra never invents its own).
- `event_type` — `POST_TOOL_USE | STOP`.
- `step_index` — optional turn/step index, if Antigravity supplies one.
- `tool` — optional `{name, arguments_summary (truncated + redacted), had_error}`.
- `result_summary` — truncated, redacted summary of the tool result — never the full payload.
- `error` — optional error string.
- `workspace_path` — the working directory reported by Antigravity.
- `transcript_ref` — a **pointer** to the transcript (path/offset), never transcript content.
- `occurred_at` / `received_at` — Antigravity's timestamp (if given) and Astra's own receipt time, for latency measurement.
- `correlation_id` — Astra's own per-HTTP-call tracing ID (distinct from `session_id`).

## Why separate raw from normalized

Four reasons, all load-bearing: (1) insulates `domain`/`application` from upstream schema churn — only `normalize.py` needs to change if Antigravity's shape shifts; (2) it is the single choke point for the privacy redaction policy in Section 18 of the notebook, applied before anything is persisted or sent to a model; (3) it lets every other layer be unit-tested against small, stable `AstraEvent` fixtures instead of fabricated raw JSON; (4) it is the exact seam a future IDE-surface adapter would plug into (a second `raw_schema.py`/`normalize.py` pair, still producing the same `AstraEvent`) without touching anything downstream.

---

# 11. Trajectory state

**Status: schema and lifecycle design are this session's recommendation; "mirror trajectory state, not the context window" is notebook-firm (Section 6).**

## Schema (`domain/trajectory.py`)

- `session_id`, `schema_version`, `state_version` (int, incremented every update — used for optimistic concurrency).
- `task` — durable.
- `current_hypothesis` — durable, latest value only (not a full history — history lives in `evidence_gathered`/observation entries if needed).
- `evidence_gathered: list[EvidenceRef]` — **references**, not content (transcript path+range, file path+range, etc.) — durable.
- `actions_taken: list[ActionRecord]` — compact `{tool_name, outcome_summary, timestamp}` — durable.
- `verification_history: list[VerificationRecord]` — `{status, timestamp, summary}` — durable.
- `failure_count` — **derived** from `verification_history` (consecutive failures), recomputed, not hand-maintained.
- `failure_signatures: dict[str, int]` — hash of failure signature → forced-continuation count, feeds the anti-loop policy — durable.
- `current_mode` — last-known `Mode`, recomputed each pass by the escalation policy but persisted for continuity/display.
- `interventions: list[InterventionRecord]` — durable log, feeds the intervention budget.
- `unresolved_questions: list[str]` — durable.
- `created_at`, `updated_at`.

**Durable vs. derived**: `task`, the compact lists (`evidence_gathered`, `actions_taken`, `verification_history`, `interventions`, `failure_signatures`) and `unresolved_questions` are durable and hand-updated. `failure_count` and `current_mode` are derived/recomputed each pipeline pass. **What must never appear here**: full transcript text, full tool output, full diffs, or full code — those stay in the Evidence layer (Section 17) and are fetched on demand.

## Ownership, lifecycle, concurrency

- One `TrajectoryState` document per Antigravity `session_id`, created lazily on first event.
- Owned end-to-end by `application/pipeline.py`; read/written only through the `TrajectoryStateStore` port.
- **Concurrency**: single-writer-per-session is the normal case (Antigravity events for one session are effectively serial), but `state_version` gives optimistic concurrency via a Firestore transaction for the rare near-simultaneous case; on conflict, retry once with the freshly re-read state, then fail open (log a warning, return `continue` without a full state update) rather than blocking.
- **Recovery after restart**: trivial by construction — Cloud Run is stateless, all state lives in Firestore, so any instance can serve any session.
- **Staleness**: a configurable session TTL (e.g., hours) — a session not updated within the TTL is treated as effectively new on its next event, and its `failure_signatures`/intervention counters reset.
- **Serialization**: the Pydantic model (de)serializes directly to/from a Firestore document; no separate DB schema layer.

---

# 12. Signal detection

**Status: layered design (rules first, model-assisted second) is this session's recommendation; the specific signal examples are notebook-firm illustrations, not an exhaustive firm list.**

Signals are a **layered combination**, not purely rules and not purely model-assisted:

1. **Rule-based detectors** (`domain/signals.py`, pure, no I/O) run on every event: repeated verification failure, same-file-repeated-edit, recurring failure signature, `Stop` immediately after a failed/absent verification (candidate "unsupported success claim"). These are cheap, deterministic, and require no model call.
2. **Model-assisted classification** (`tiers/fast/assessor.py`, not pure — makes an I/O call) runs **only** when the rule layer produces an ambiguous or borderline result — e.g., "obvious missing evidence" is genuinely fuzzy and is a good candidate for a small, cheap model call rather than a brittle rule.

Both layers emit the **same** normalized `Signal` schema (`type`, `confidence`, `evidence_refs`, `suggested_mode`), so the escalation policy in `domain/modes.py` doesn't care which layer produced a given signal. This keeps the door open for more sophisticated detection later (e.g., a learned classifier) without changing anything downstream.

---

# 13. Shadow / Assist / Intervene

**Status: the three modes and their responsibilities are notebook-firm (Section 5). Escalation thresholds are explicitly experimental (notebook: "exact thresholds are an experimental part of the POC").**

**Module ownership**: `domain/modes.py` owns the escalation policy — `decide(current_mode, signals, trajectory_state, config) -> ModeDecision {new_mode, reason, escalate}`. It is a pure function of state + signals + config, so its thresholds can be tuned via configuration without touching any other module.

- **Shadow**: cheap observation and state update only — no model call beyond what the event-processing itself needs. Can start as soon as a task begins.
- **Assist**: a non-authoritative response — `{message, evidence_refs, confidence}` — surfaced to the main agent as injected guidance (missing-evidence pointer, second opinion, or research result). The main agent stays in control.
- **Intervene**: authoritative. On `Stop`, this is a **forced continuation** (`block_stop`) with a `reason` string fed back to the agent. On `PostToolUse`, since the tool call has already executed, Intervene can only inject a high-priority corrective message for the *next* step — it cannot undo the action. **This exact PostToolUse semantic needs empirical confirmation against Antigravity's actual hook-response contract at Milestone 1**, and the decision schema below is written as a superset that degrades gracefully depending on what each event type actually supports.

**Escalation**: `Shadow → Assist` on a first meaningful signal; `Assist → Intervene` on repeated/strong evidence (repeated failure, repeated stop-after-failed-verification) **and** only if the anti-loop/intervention budget allows it (Section 21). All numeric thresholds are configuration values with documented defaults, explicitly flagged experimental — the evaluation harness is what should ultimately justify them, not this document.

---

# 14. Fast tier

**Status: this session's recommendation, within the notebook-firm two-tier design (Section 12 of the notebook).**

- **Input**: `AstraEvent` + current `TrajectoryState`.
- **Processing**: domain rule detectors first (no I/O); only for ambiguous cases, one small model call (same model family, a fast/cheap variant) with a strict structured-output schema.
- **Output**: `list[Signal]` — never a final decision; the escalation policy (Section 13) makes the final call.
- **Context size**: the compact `TrajectoryState` plus the current event only — never the raw transcript.
- **Timeout**: target well under the notebook's ~5 second guidance; on timeout or error, fall back to rule-only output — never assume Intervene on an erroring/ambiguous assessment (fail toward Shadow).
- **Caching**: rule computation is naturally idempotent per event; no LLM response caching needed in the POC.
- **What it must not become**: another full agent. No tool loop, no multi-step reasoning — a single cheap pass, at most one model call.

---

# 15. Deep tier

**Status: triggers and capability list are notebook-firm (Section 12); the internal orchestration/routing design is this session's recommendation, and the notebook explicitly marks the exact routing policy experimental (Section 3/15).**

`tiers/deep/orchestrator.py` is triggered by an Assist/Intervene escalation, an explicit user request, or high-confidence fast-tier signal. It:

1. Assembles an `EvidencePacket` (Section 17) via the evidence port, using an evidence-request hint attached to the triggering `Signal`.
2. Selects which engine(s) to run: `BugfixVerifier` for verification/claimed-fix triggers (a `Stop` following a code change), `ReasoningCritic` for reasoning-quality/model-laziness triggers, optionally followed by `AlternativeRanker` when the critique itself concludes "insufficient alternatives were explored."
3. Applies the **routing policy** (`domain/routing.py`) to decide what happens with the critique: return it to the main agent for a fresh reasoning pass, have Astra perform the next reasoning pass itself, or both in sequence.

**POC default (experimental, my recommendation)**: start with "return critique to the main agent" as the default path. It is the cheapest to evaluate, keeps the main agent in control (matching the philosophy of Assist mode), and avoids conflating "Astra found a problem" with "Astra solved the problem" in the turns-to-fix metric. "Astra reasons itself" and "combined" are implemented behind the same `routing.py` interface and enabled via a config flag once the default path is validated.

**Boundedness**: Deep tier is a **single bounded reasoning pass** per activation — at most one additional evidence round-trip if an engine's structured output explicitly requests more evidence. This is a deliberate, hard bound, not an open-ended ReAct-style loop; "potentially subagent delegation" from the notebook is explicitly deferred, not part of the POC.

**Timeout**: a longer but still bounded budget than Fast tier (configurable); on timeout, fail open exactly as in Section 21 — log it, return no additional intervention, never block the `Stop` hook indefinitely.

---

# 16. Reasoning engines

**Status: which capabilities exist is notebook-firm (Sections 3, 13, 15); the "separate engines behind one interface" choice is this session's recommendation.**

Each capability is a **separate engine class implementing a single shared `Engine` protocol** (`domain/reasoning_ports.py`: `run(evidence_packet, trajectory_state, config) -> EngineResult`), rather than one giant reasoning class with internal branching, and rather than a generic pipeline framework. This was chosen over the alternatives for a concrete reason: bugfix verification, reasoning critique, and alternative ranking have genuinely different prompts, different evidence needs, and different output shapes — forcing them into one class would produce exactly the kind of premature generalization the notebook explicitly warns against, while a full pipeline/workflow framework would be over-engineering for three known capabilities.

- **`engines/bugfix/verifier.py`** — implements the Evidence-First Debugging Protocol (notebook Section 13): audits whether a claimed fix is actually backed by a passing verification step, not merely a code change.
- **`engines/reasoning/critic.py`** — inspects the main agent's reasoning trajectory for unsupported assumptions, missing evidence/tool use, unexplored alternatives, weak inference, premature convergence, or verification that tests the wrong thing (notebook Section 15).
- **`engines/reasoning/alternatives.py`** — given a task and the main agent's first proposed approach, generates and ranks multiple candidate approaches (correctness risk, complexity, fit-to-task) — the model-laziness mitigation, and the notebook's original priority target.

All three are constructed with an injected `ModelProvider` and `EvidenceRetriever` (never instantiate the Antigravity SDK directly), keeping `engines` dependent only on `domain` ports.

---

# 17. Evidence architecture

**Status: "bounded evidence packets, not the full session" is notebook-firm (Sections 6, 10). The concrete object model, POC evidence-source list, and the context-engine seam are this session's recommendation.**

## Objects (`domain/evidence.py`)

- `EvidenceItem` — `{id, source, reference (opaque locator), content (populated only on retrieval), relevance_score, timestamp, provenance, token_estimate}`.
- `EvidenceSource` (POC-scoped enum): `TRANSCRIPT_SLICE`, `TOOL_OUTPUT`, `CHANGED_FILE_SLICE`, `TEST_OUTPUT`, `WEB_SEARCH_RESULT` (Deep tier only), `PRIOR_INTERVENTION` (from the trajectory state's own history). **Not** in the POC: full repository semantic-graph retrieval, arbitrary multi-hop autonomous browsing — that is the future context engine (Section 27).
- `EvidencePacket` — `{task, trajectory_summary, items: list[EvidenceItem], token_budget, token_used}`.

## Retrieval vs. construction split

`domain/evidence.py` contains **pure** functions for prioritization, deduplication, and truncation against a token budget, given a candidate list of `EvidenceItem`s. The actual fetching (I/O) is a `EvidenceRetriever` port implemented by `infrastructure/evidence/*.py` adapters. `tiers/deep/orchestrator.py` decides *what to request* based on the triggering signal (e.g., a verification-failure trigger requests `TEST_OUTPUT` + `CHANGED_FILE_SLICE`, plus whatever is already in `verification_history`); `domain/evidence.py` decides *how to fit what came back into budget*.

**Token budgeting** for the POC is a simple character-count heuristic (not a real tokenizer) — a deliberate simplification, flagged for later replacement with a model-specific tokenizer if budgeting accuracy becomes a measured problem.

## The future context-engine seam

The `EvidenceRetriever` port is exactly where the notebook's proposed post-POC context engine (Section 24 of the notebook) plugs in later: a future `infrastructure/evidence/context_engine_retriever.py` implements the same protocol, and nothing in `domain`, `engines`, or `tiers` needs to change. This is the concrete mechanism behind the notebook's requirement to "leave a clean boundary so the future context engine can eventually plug in."

---

# 18. State/persistence

**Status: this session's recommendation — the notebook specifies requirements ("Cloud Run plus a lightweight store", "local-first for anything sensitive") but not a concrete technology.**

**Decision: Firestore** for `TrajectoryState` and intervention/anti-loop counters. Reasoning: the state is naturally document-shaped (nested lists inside one object per session), lookups are always by a single key (`session_id`), Firestore is serverless and pairs cleanly with stateless Cloud Run (no connection pooling concerns, unlike Cloud SQL), and it directly satisfies restart-recovery (Section 11) since no instance holds state in memory. Cloud SQL/Postgres is not chosen for the POC — it would add connection-pool/proxy setup for a workload that needs no relational queries.

A `memory_store.py` in-memory implementation of the same `TrajectoryStateStore` port exists purely for unit/integration tests and local dev — no test ever touches real Firestore.

**Evaluation data is stored separately** (Section 20) — never in the same Firestore collections as production trajectory state, to keep the "evaluation must not contaminate the agent trajectory" requirement true at the storage level, not just logically.

---

# 19. Cloud Run architecture

**Status: this session's recommendation, consistent with the notebook's own Section 24 sketch (`POST /event`, `POST /reason` on one backend).**

**One service, two route groups** — `astra-backend` on Cloud Run, exposing `/event` and `/reason` from the same FastAPI app. Rejected: separate fast/deep/research microservices — at POC scale this would add network hops and deployment complexity without a corresponding benefit; the fast/deep split already exists as a clean internal module boundary (Section 5) and can be extracted into a separate service later *if* evaluation data shows a real reason to (e.g., very different scaling profiles), without a rewrite, because `tiers/fast` and `tiers/deep` don't depend on `api/` internals.

- **Statelessness**: the service holds no session affinity; all state is in Firestore (Section 18).
- **Auth**: the notebook does not specify this, so it is this session's recommendation — a shared-secret bearer token, configured in the hook's local config and validated in `api/auth.py`, is the minimum viable protection for a public Cloud Run URL that may carry code/transcript content. HTTPS is Cloud Run's default. Stronger per-user/IAM-backed auth is explicitly a future improvement, not needed for a single-developer hackathon POC.
- **Timeouts/concurrency**: Cloud Run's own request timeout is set comfortably above the Deep tier's internal timeout budget (Section 15) so the platform never cuts off a request Astra itself would have failed open on.
- **Deployment**: `min-instances` kept low but non-zero during the hackathon demo window to avoid cold-start latency spikes during judging (a deploy-config tradeoff, not an architecture change).

---

# 20. APIs/contracts

**Status: keeping `POST /event` / `POST /reason` is this session's recommendation, evaluated against and consistent with the notebook's own Section 24 sketch.**

The two-endpoint shape is appropriate and is kept: `/event` is the fast, always-called path (fed by both hooks); `/reason` is the explicit deep-tier path, callable either internally (from the pipeline, when escalation warrants it) or directly (for the evaluation harness or an explicit user "go deeper" request), which cleanly matches the notebook's own distinction between Shadow/cheap observation and Deep-tier investigation.

**`POST /event`** — request: raw Antigravity hook event (event type discriminates `PostToolUse`/`Stop`) plus the correlation ID; internally normalized, run through the full pipeline. Response: `{decision: continue | allow | deny | block_stop, reason, assist: {message, evidence_refs, confidence} | null, intervention_id: str | null}`.

**`POST /reason`** — request: `{session_id, trigger_type, evidence_hints}`. Response: a structured `EngineResult` (verdict/critique points/alternatives, confidence, evidence citations).

This section defines contracts and responsibilities only, per the prompt's explicit instruction — no OpenAPI implementation here.

---

# 21. Anti-loop/safety

**Status: the anti-loop requirement itself, and "Astra must fail open," are both notebook-firm, hard requirements. The concrete cap/cooldown numbers are explicitly experimental.**

## Where the safety boundary lives

There are **two** fail-open boundaries, deliberately at the two outermost edges of the system:

1. **`hooks/common.py`** (local) — any network failure, non-2xx response, timeout, or malformed JSON from the backend results in the hardcoded `{"decision": "continue"}` default. This boundary protects against the backend being unreachable at all.
2. **`api/routers/*.py`** (Cloud Run) — the entire pipeline call is wrapped in a try/except-with-timeout; any internal exception, state-store failure, or timeout **inside** the backend also collapses to `continue`. This boundary protects against internal failures (a crashing engine, a Firestore outage, a malformed model response) still resulting in a decision reaching the hook in time.

Both boundaries independently guarantee the same outcome, so a bug in one does not remove the guarantee.

## Anti-loop module (`domain/intervention.py`, pure policy + persisted counters)

- Tracks forced-continuation counts keyed by a **failure signature** (a hash derived from the recurring error/test-failure content) inside `TrajectoryState.failure_signatures`.
- Enforces a configurable per-signature cap on forced `Stop`-hook continuations.
- Enforces a cooldown between forced continuations.
- Once the cap is hit, the policy returns "do not force again" and the pipeline instead surfaces the unresolved issue to the user rather than continuing to loop — this is a **required** behavior for the POC, not an optional refinement.
- The exact cap and cooldown values are configuration, explicitly flagged **experimental** — they need evaluation-harness data before being trusted.

---

# 22. Evaluation architecture

**Status: turns-to-fix as the primary metric and the requirement to establish a baseline are notebook-firm. The harness/storage design is this session's recommendation.**

**Isolation principle**: the evaluation harness is an **external, one-way observer**. It never writes into `TrajectoryState` or into any prompt Astra's engines see. Metrics are computed post-hoc from three sources that already exist independently of the harness: (1) Antigravity's own transcript/turn count (ground truth for turns-to-fix, avoiding measurement circularity from trusting Astra's self-report), (2) Astra's own structured intervention log (already durable via the observability pipeline, Section 23), and (3) objective external checks (test-suite exit codes, git diff).

**Baseline vs. with-Astra**: baseline runs simply do not register `hooks.json` at all — the cleanest possible "Astra absent" condition, avoiding any doubt about a no-op code path being correct. A secondary, optional diagnostic mode (Astra active but configured to never intervene) is noted as a future refinement to separately isolate "cost of Astra observing" from "value of Astra intervening," but is not required for the POC.

**Storage**: `evaluation/storage.py` uses a local SQLite/JSONL store under `evaluation/runs/`, physically separate from the production Firestore collections — this is the storage-level enforcement of "evaluation must not contaminate the agent trajectory."

**Metrics computed**: turns-to-fix (per the notebook's operational definition — Astra resolving an issue within the same interruption does not count as an extra turn), verification failures, intervention count, time-to-fix, unnecessary changes (where measurable via diff size/count), and model/tool cost (summed from the per-call token accounting in Section 11's model port).

---

# 23. Observability

**Status: this session's recommendation, implementing the notebook's Section 19 field list and Section 18 privacy principles.**

Every pipeline pass emits one structured JSON log line (via `structlog`, `infrastructure/observability/logging.py`) with: `correlation_id`, `session_id`, `event_id`, `event_type`, `trajectory_state_version` (before/after), `signals_detected` (`[{type, confidence}]`), `mode_decision` (from/to), `intervention_id` (if any), `reason`, `latency_ms` broken down by stage (normalize / state-load / fast-tier / deep-tier / state-save / total), `model_calls` (`[{tier, model_name, tokens_in, tokens_out, latency_ms}]`), `evidence_sources_used` (source types + counts only, never content), `failure_signature` (**hashed**, never raw error text), and the anti-loop counter vs. cap.

**Privacy**: redaction happens once, in `integration/antigravity/normalize.py` (Section 10), before anything is persisted or logged — tool arguments/results are truncated and pattern-redacted for common secret shapes by default. A verbose local-dev logging mode exists for debugging but is explicitly disabled in the production deploy config.

---

# 24. Testing architecture

**Status: this session's recommendation.**

- **Unit — `domain/`**: pure-function tests, no mocks needed beyond simple fixtures (trajectory transitions, signal rules, escalation policy, evidence budgeting, anti-loop policy).
- **Unit — `engines/`, `tiers/`**: mocked `ModelProvider` and `EvidenceRetriever` ports; test prompt construction and response parsing (including malformed model output) separately from real model behavior.
- **Integration — `api/`**: FastAPI test client + in-memory store + fake model provider, run against the fixture payloads in `tests/fixtures/hook_payloads/`, asserting the exact response contract.
- **Integration — `hooks/`**: run `hooks/post_tool_use.py`/`stop.py` as real subprocesses against a local test server, explicitly asserting fail-open behavior when: the server is unreachable, the server returns malformed JSON, and the server times out.
- **Trajectory-state tests**: version-conflict handling, staleness/TTL behavior.
- **Signal detector tests**: table-driven, one case per rule.
- **Intervention/anti-loop tests**: cap enforcement, cooldown, and the "surface to user instead of looping" behavior once the cap is hit.
- **API contract tests**: schema validation and error responses.
- **End-to-end**: a small number of tests against a real (or recorded) Antigravity CLI session, marked `@pytest.mark.e2e`, run manually/nightly rather than in default CI, given the notebook's own open question about hook reliability.
- **Evaluation harness tests**: metrics computed correctly against synthetic transcripts/logs (no live LLM required), and storage isolation verified (no cross-write into production Firestore).

Failure-path coverage is explicit and required, not incidental: endpoint unavailable, response-parse failure, missing/first-time state, unexpected/malformed hook payload, repeated failures hitting the anti-loop cap, and `Stop` triggered repeatedly are all first-class test scenarios, not edge cases discovered later.

---

# 25. Configuration

**Status: this session's recommendation.**

A single `Settings` class (`astra/settings.py`, `pydantic-settings`) is the **only** place environment variables are read. It is loaded once, at process start, in the composition root (`api/deps.py`), and every other module receives typed configuration objects via constructor injection — never `os.environ` scattered through `domain`/`engines`/`tiers`. Fields are grouped: `model` (fast/deep model names, timeouts), `thresholds` (escalation thresholds — explicitly documented as experimental/tunable), `intervention` (per-session budget, anti-loop cap, cooldown), `persistence` (Firestore project/collection, or `IN_MEMORY` for tests), `observability` (log level, verbose flag), `feature_flags` (`DEEP_TIER_ENABLED`, `WEB_RESEARCH_ENABLED`, `ROUTING_MODE`), and an environment discriminator (`dev`/`test`/`prod`) that selects sane defaults (dev: in-memory store, verbose logs; prod: Firestore, redacted logs). Local dev uses a gitignored `.env` (with `.env.example` checked in); production secrets (bearer token, any API keys) are sourced from Google Secret Manager via Cloud Run's native integration, not custom code.

---

# 26. Security/privacy considerations

**Status: principles are notebook-firm (Section 18); concrete mechanisms are this session's recommendation.**

| Notebook principle | Concrete POC mechanism |
| --- | --- |
| Local-first where practical | Repo/file evidence retrieval happens by reading the local workspace at request time — nothing is pre-indexed off-machine in the POC |
| Retrieve only necessary context | `EvidenceRetriever` requests are scoped by the triggering signal (Section 17) — never a full-file or full-transcript dump by default |
| Redact secrets | Single choke point in `normalize.py` (Section 10), applied before persistence or model calls |
| Project-level opt-out | Absence of `hooks.json` registration is itself the opt-out — no separate mechanism needed |
| Avoid indexing everything by default | No background indexer exists in the POC at all (that's the deferred context engine) |
| Document what leaves the machine | Only normalized, redacted `AstraEvent`s and on-demand evidence slices ever leave the workspace, over HTTPS, to the Cloud Run service |
| (Not previously specified) Endpoint authentication | Shared-secret bearer token (Section 19) — the minimum needed to avoid an open, unauthenticated public endpoint |

---

# 27. Future extension boundaries

**Status: the notebook already fixes most of this (Section 19, Section 24 of the notebook); this session adds the concrete seams.**

**Implement now (POC)**: hooks (`PostToolUse`, `Stop`) on the CLI surface; Shadow/Assist/Intervene; Fast + Deep tiers; the three engines (bugfix verifier, critic, alternative ranker); bounded evidence packets from the POC-scoped source list; Firestore-backed trajectory state; the anti-loop module; the evaluation harness against reproducible bugs and Zindi tasks.

**Interface now, implementation later**: `EvidenceRetriever` (Section 17) is the seam for the full context engine; `domain.reasoning_ports.Engine` is the seam for a future `BenchmarkIntegrityChecker` engine (post-POC benchmark-killing detection) without touching the pipeline; `integration/antigravity/` is the seam for a second raw-schema/normalize pair if/when IDE-surface hooks start firing reliably — still Hooks-only, never MCP; `domain/model_ports.ModelProvider` is the seam for any future model-provider change.

**Explicitly do not build now**: a full custom context engine (only the seam exists); benchmark-killing detection (only the seam exists); IDE integration (CLI only for the POC); MCP, anywhere, ever — enforced structurally by having no MCP dependency in `pyproject.toml` and recording the decision as ADR-9; cross-platform (non-Antigravity) support; a generalized autonomous-agent/tool-loop framework — Deep tier is a single bounded pass, not a ReAct loop, and there is no plugin system; a large memory/context platform beyond compact trajectory state; sophisticated enterprise infrastructure (multi-tenant auth, queues, multi-region).

---

# 28. Architecture decisions

1. **Language: Python.** Reason: Google/Gemini SDK maturity, Pydantic-based schema validation everywhere, and Zindi-task alignment for the evaluation harness. Alternatives considered: TypeScript/Node (rejected — would fragment the evaluation harness into a second language), Go (rejected — weaker AI-SDK ecosystem; kept as a future option for the hook binary only). **Firm recommendation.**
2. **Repository layout: hexagonal/ports-and-adapters**, with `hooks/` fully outside the `astra` package. Reason: isolates Antigravity-schema instability and model-provider changes from the reasoning core. Alternative considered: a flat layered (MVC-style) structure — rejected, doesn't enforce the domain-purity boundary the risk profile requires. **Firm recommendation.**
3. **Service topology: one Cloud Run service, two route groups (`/event`, `/reason`).** Alternative considered: separate fast/deep/research microservices — rejected as premature; internal module boundary already gives the same separability without network overhead. **Firm recommendation, consistent with notebook Section 24.**
4. **State persistence: Firestore** for trajectory/intervention state; a physically separate local store for evaluation data. Alternative considered: Cloud SQL/Postgres — rejected, adds connection-pooling overhead for a workload with no relational query need. **Recommendation** (notebook specifies requirements, not the technology).
5. **Model abstraction: `ModelProvider` port + Antigravity SDK adapter.** Satisfies the hackathon's Google-Agent-Framework requirement while keeping `domain`/`engines` SDK-agnostic. **Recommendation.**
6. **Hooks-only integration, `PostToolUse` + `Stop`, stdlib-only local scripts.** **Already decided (notebook), firm.**
7. **MCP permanently excluded**, enforced structurally (no dependency, ADR recorded). **Already decided (notebook), firm.**
8. **Same model family for Astra and the main agent.** **Already decided (notebook), firm.**
9. **Fast/Deep tier separation**, concretely as `tiers/fast/` and `tiers/deep/`. Principle **already decided (notebook)**; module boundary is this session's **recommendation.**
10. **Evidence abstraction: bounded `EvidencePacket` via an `EvidenceRetriever` port**, with a POC-scoped source list and a reserved seam for the future context engine. Principle **already decided (notebook)**; concrete design is this session's **recommendation.**
11. **Anti-loop module is required.** **Already decided (notebook), firm.** Concrete cap/cooldown values: **experimental**, to be tuned via the evaluation harness.
12. **Evaluation architecture: external, one-way observer; separate storage; no-hooks-registered baseline.** Turns-to-fix and the baseline requirement **already decided (notebook), firm**; harness/storage design is this session's **recommendation.**
13. **Routing policy default: critique-back-to-main-agent.** **Explicitly experimental (notebook)** — this session's recommended *default*, switchable via config once validated.
14. **Escalation thresholds: configuration, not hardcoded.** **Explicitly experimental (notebook).**
15. **CLI-only demo surface.** **Already decided (notebook), firm.**
16. **Context engine deferred; `EvidenceRetriever` reserved as its plug-in seam.** Deferral **already decided (notebook), firm**; the seam is this session's **recommendation.**
17. **Benchmark-killing detection deferred; `Engine` protocol reserved as its plug-in seam.** **Already decided (notebook), firm.**
18. **Cloud Run endpoint authentication: shared-secret bearer token.** Not previously addressed in the notebook. **New recommendation, this session.**

---

# 29. Risks and mitigations

- **Antigravity hook behavior changing** → `raw_schema.py` (`extra="allow"`) + single-choke-point `normalize.py`; contract tests against captured fixtures catch drift quickly; Milestone 1 is dedicated entirely to proving hook reliability empirically before anything else is built on top of it.
- **Hook latency** → the hook script is kept stdlib-only and single-attempt; if empirically too slow, the architecture already supports a fire-and-forget pivot (return `continue` immediately, deliver Assist guidance on the *next* event) without any redesign, because the event/response contract is already clean.
- **State synchronization** → Firestore transactions + `state_version` optimistic concurrency; documented single-writer-per-session assumption; bounded retry then fail-open on conflict.
- **Cloud Run latency (cold starts)** → deploy-config lever (`min-instances`), not an architecture change, since the service is already fully stateless.
- **Model latency** → hard, tier-specific timeouts enforced in code (Sections 14–15), independent of SDK defaults — a decision is always produced.
- **Malformed hook responses** → the local fail-open boundary (Section 21, `hooks/common.py`) exists specifically for this.
- **Intervention loops** → the anti-loop module (Section 21) is a required, independently tested subsystem, not a policy note.
- **Excessive Astra interventions (noise)** → per-session intervention budget + confidence thresholds; intervention count is a first-class evaluation metric (Section 22), making over-intervention visible and tunable rather than hidden.
- **Context contamination** → the durable/derived split in trajectory state (Section 11) plus on-demand-only evidence fetching (Section 17) prevents Astra from re-accumulating the long-context problem it exists to fix.
- **Over-engineering** → structurally enforced "do not build now" list (Section 27): no plugin system, no open-ended tool loop, bounded single-pass engines.
- **Coupling to Antigravity internals** → the `integration/` isolation layer plus the hard dependency rule (`domain`/`engines`/`tiers` never import `integration`).
- **Evaluation contamination** → physically separate storage (Section 22) and one-way observation, enforced at the storage layer, not just by convention.
- **Difficulty reproducing agent behavior** → fixed task set, fresh workspace per run, and turns-to-fix computed from Antigravity's own transcript (not Astra's self-report), reducing measurement circularity.

---

# 30. Final recommended POC architecture diagram

```
 USER WORKSPACE
┌───────────────────────────────────────────────────────────┐
│ Antigravity CLI (agy) → PostToolUse/Stop → hooks/*.py       │
│ (stdlib-only, one HTTP attempt, hardcoded fail-open default)│
└───────────────────────┬───────────────────────────────────┘
                         │ HTTPS + bearer token
                         ▼
┌──────────────────────── CLOUD RUN: astra-backend ─────────────────────────┐
│ api/ (auth + fail-open wrapper)                                            │
│   → integration/antigravity/ (normalize, redact)                          │
│     → application/pipeline.py                                             │
│         → domain/ (trajectory, signals, modes, routing, evidence,         │
│                     intervention/anti-loop — all pure)                    │
│         → tiers/fast/  (rules, then optional cheap model call)            │
│         → tiers/deep/  (evidence packet → engine selection → routing)     │
│             → engines/bugfix/, engines/reasoning/                         │
│                 → infrastructure/model_providers/ (Antigravity SDK)       │
│         → infrastructure/persistence/ (Firestore: trajectory, anti-loop)  │
│         → infrastructure/evidence/ (transcript, repo, web)                │
│     → integration/antigravity/response_format.py                         │
│ structured logs (structlog) → Cloud Logging                               │
└─────────────────────────────────────────────────────────────────────────┘
                         │ (read-only, offline)
                         ▼
              evaluation/  — separate storage, turns-to-fix
              and secondary metrics, with/without Astra
```

**Dependency direction, restated once more because it is the most important guarantee in this document**: `hooks → api → integration → domain`, and `engines/tiers → domain`, with `infrastructure` implementing `domain`'s ports from the outside. `domain` depends on nothing else in the repository. This is what keeps Astra selectively improving the main agent's reasoning without becoming another always-on agent, another copy of the context window, or an uncontrolled second coding system.

## Architecture Review Note — Completeness Check

The current document is strong as a **POC implementation architecture**, but it should not yet be considered fully complete as an engineering specification. The major runtime components and layering are present, including the separation of API/integration/application/domain/infrastructure concerns and the planned Firestore trajectory state plus separate evaluation store. fileciteturn19file0L6-L8 fileciteturn21file0L6-L8

Before implementation begins, the remaining specification gaps to verify are:

1. **Concrete hook contracts.** Define the exact normalized event schema Astra receives for `PreToolUse`, `PostToolUse`, and `Stop`, including required fields, event IDs, timestamps, session/task IDs, tool identity, exit status, stdout/stderr or equivalent output references, and how missing fields are handled. *(Resolved — see Section 31.1. Note: `PreToolUse` is out of scope per Sections 7A/8/9; only `PostToolUse`/`Stop` apply.)*
2. **End-to-end sequence definitions.** The architecture shows the components, but each critical flow should have one canonical sequence: normal Shadow event, Assist escalation, Stop-hook Intervene, successful verification, failed verification, Astra timeout/failure, and anti-loop exhaustion.
3. **State-transition semantics.** Define the authoritative state machine for Shadow → Assist → Intervene, including legal transitions, idempotency, repeated/out-of-order events, and what resets between tasks/sessions.
4. **Astra decision contract.** Specify the exact input/output contract of the fast tier and deep tier: decision enum, confidence/rationale fields, evidence references, continuation decision, intervention message, and bounded-cost metadata. *(Resolved — see Section 31.2.)*
5. **Timeout and failure semantics.** The architecture should explicitly define per-call timeouts, retries, circuit-breaking, fail-open behavior, and what happens if Firestore or the Astra endpoint is unavailable. The fail-open principle already exists conceptually and should become a formal contract. *(Resolved — see Section 31.3.)*
6. **Concurrency and idempotency.** Multiple hook events may arrive close together. Define ordering guarantees, deduplication keys, optimistic locking/versioning, and how concurrent Astra evaluations against the same trajectory are serialized or reconciled.
7. **Evidence/data contracts.** Define the evidence-packet schema and which artifacts are stored directly versus by reference: diffs, test output, tool output, transcript slices, file snapshots, hypotheses, critique points, and verification results.
8. **Evaluation harness.** The document should specify how with-Astra and without-Astra runs are made comparable, how task seeds/configuration are held constant, how turns-to-fix is counted automatically, and what constitutes a failed or invalid trial. *(Resolved — see Section 31.4.)*
9. **Security/privacy boundaries.** Define what code/tool output leaves the local environment, what is sent to Gemini/Cloud Run/Firestore, secret handling, retention, access control, and redaction rules. This matters particularly because source code and agent trajectories may contain credentials or private data.
10. **Observability.** Add the minimal required telemetry: correlation ID, task/session ID, hook event ID, latency, tier invoked, decision, intervention count, failure mode, token/model cost, and final task outcome. This is needed for debugging Astra itself and for trustworthy POC measurements.
11. **Deployment/runtime topology.** The module architecture is clear, but the actual runtime topology should be explicit: local `agy` hook process → Astra API → reasoning worker/model → Firestore/evaluation store, plus network boundaries and local-vs-cloud responsibilities.
12. **Versioning/configuration.** Specify model version, policy version, prompt version, hook version, and experiment configuration as recorded metadata so results can be reproduced.
13. **POC/non-POC boundaries.** Keep the post-POC context engine, benchmark-killing detector, and future IDE support explicitly outside the implementation dependency graph of the first POC, so the architecture cannot accidentally expand during coding.

### Verdict

**Status: sufficiently complete to understand and begin an architectural spike, but not yet complete enough to treat as a final implementation contract.** The missing material is mostly operational precision rather than a missing major subsystem. Once the event contracts, state machine, critical sequences, failure semantics, evaluation protocol, and security/observability boundaries are written down, the document will be much closer to implementation-ready.

# 28. Reasoning Critique + Event/State/Sequence Design Decisions

This section records the architecture decisions reached in the design discussion before implementation. These are now the working design contracts for the POC; exact thresholds and low-level field names remain tunable where explicitly marked.

## 28.1 Five questions that define the reasoning-critique mechanism

### 1. What constitutes a reasoning checkpoint?

A **reasoning checkpoint** is a structured snapshot of the main agent's current intellectual position, captured when the agent reaches a meaningful commitment rather than on every ordinary turn.

A checkpoint should capture, at minimum:

- current claim or intended conclusion
- current hypothesis
- assumptions
- evidence currently supporting the hypothesis
- alternatives considered, if any
- actions already taken
- unresolved questions
- confidence/uncertainty
- verification state

Typical checkpoint triggers include:

- committing to a root-cause hypothesis
- selecting an implementation strategy
- materially changing direction
- claiming that a root cause has been established
- moving toward a final conclusion
- attempting to terminate after verification

The checkpoint is a **structured state representation**, not a dump of the entire conversation or hidden chain-of-thought. Astra should critique the observable justification/state available to it.

### 2. What triggers critique?

Astra should not deeply critique every reasoning step. Critique is **event-triggered**.

Strong triggers include:

- premature commitment despite multiple plausible approaches
- repeated failure without new evidence
- repeated edits around the same hypothesis
- mismatch between the agent's claim and observed tool/environment state
- reasoning that depends on an unestablished premise
- important alternatives not considered when the decision is consequential
- contradiction between reasoning and evidence
- a termination/success claim without adequate verification
- explicit user feedback that the proposed resolution did not work

The Fast tier should identify inexpensive signals; the Deep tier should perform substantive critique only when warranted.

### 3. What must a critique contain?

A critique is not a generic disagreement and should not simply output another guess. It should identify an **actionable weakness in the justification**.

Canonical critique shape:

```
Critique
├── type
│   ├── unsupported_assumption
│   ├── missing_evidence
│   ├── missing_alternative
│   ├── invalid_inference
│   ├── premature_convergence
│   ├── contradiction
│   └── insufficient_verification
├── severity
├── claim_under_review
├── supporting_observation
├── why_problematic
├── missing_information
└── suggested_next_action
```

A useful critique answers four questions:

1. What is weak or unjustified?
2. Why does that weakness matter?
3. What evidence or reasoning step is missing?
4. What should happen next?

Astra's objective is **not to disagree with the main agent**. The objective is to determine whether the current conclusion is sufficiently justified by the available reasoning and evidence.

Example:

```
Claim:
SameSite is the root cause.

Problem:
Observed evidence shows the cookie is absent but does not establish
why it is absent.

Missing alternatives:
- cookie rejection
- domain/path mismatch
- Secure requirement
- browser policy

Next evidence:
Inspect the browser's cookie rejection reason before changing more configuration.
```

### 4. When does critique return to the main agent vs. Astra reasoning further?

There are two recovery paths.

**Path A — Critique → Main Agent**

The default path. Astra identifies a reasoning weakness and sends the critique back to the main agent. The main agent then reruns its reasoning with the objection exposed.

```
Main reasoning
      ↓
Astra critique
      ↓
Main agent receives critique
      ↓
Main agent re-reasons
      ↓
New plan / conclusion
```

**Path B — Critique → Astra Deep Reasoning**

Used when the critique reveals a substantive unresolved gap and simply asking the main agent to retry would not provide enough new information.

Astra may then:

- investigate additional evidence
- retrieve documentation or repository context
- generate alternative hypotheses/solutions
- compare/rank alternatives
- produce a recommended next investigation or approach
- pass the result to the main agent

```
Main reasoning
      ↓
Astra critique
      ↓
Astra deeper investigation
      ↓
Alternative hypotheses / solutions
      ↓
Ranking / recommendation
      ↓
Main agent
```

Astra should **not** automatically generate alternative solutions on every critique. Alternative generation belongs to the deeper path and should be earned by the severity of the reasoning gap or failure signal.

### 5. What state does Astra maintain about the agent's beliefs?

The trajectory state should represent the **agent's current epistemic/operational position**, not merely a chronological transcript.

At minimum, the trajectory model should distinguish:

- current task
- current hypothesis/claim
- evidence gathered
- assumptions
- alternatives considered
- actions taken
- verification results
- unresolved questions
- failure history/signatures
- Astra mode
- relevant critique history
- intervention budget/anti-loop state

Astra therefore maintains both:

1. **trajectory state** — what the agent currently believes/is doing; and
2. **operating mode** — Shadow, Assist, or Intervene.

These are related but distinct. Mode does not replace belief state.

## 28.2 Reasoning critique depth

Critique should have escalating cognitive cost:

### Level 0 — No critique

Trajectory looks normal. Astra records state and continues.

### Level 1 — Lightweight critique

Fast signal detector identifies a suspicious assumption or decision. Astra can issue a compact objection without substantial investigation.

### Level 2 — Structured critique

Astra explicitly analyzes assumptions, evidence, alternatives, contradictions, and verification gaps, then returns the critique to the main agent.

### Level 3 — Deep critique + investigation

Astra critiques, retrieves additional evidence, generates alternatives where justified, ranks them, and provides a recommendation to the main agent.

The exact thresholds remain experimental and should be tuned against evaluation results.

## 28.3 Event model derived from the reasoning design

The event system should distinguish **main-agent observations** from **Astra actions**.

### Main-agent / environment events

Conceptual event classes:

- `tool_call`
- `tool_result`
- `reasoning_checkpoint`
- `verification_result`
- `failure`
- `termination_attempt`
- `user_feedback`

### Astra events

Conceptual event classes:

- `observation`
- `critique`
- `assistance`
- `intervention`
- `verification_request`
- `reasoning_request`

Exact JSON schemas are defined separately, but the semantic distinction should remain stable.

A `reasoning_checkpoint` should not be generated for every low-value model turn. It represents a meaningful commitment or decision point.

## 28.4 State machine: trajectory state vs. Astra mode

Astra's existing operating-mode machine remains:

```
Shadow
   ↓ signal
Assist
   ↓ insufficient improvement / stronger failure evidence
Intervene
```

However, this is only the **operating-mode state**. The trajectory also needs an independent conceptual state describing the main agent's current position:

```
UNKNOWN
   ↓
HYPOTHESIS
   ↓
INVESTIGATING
   ↓
EVIDENCE_FOUND
   ↓
CONCLUSION
   ↓
VERIFICATION
   ↓
CONFIRMED / CONTRADICTED
```

Astra's mode is selected from the combination of:

- trajectory state
- risk signals
- evidence quality
- failure history
- intervention budget

This separation prevents the architecture from confusing **what the agent believes** with **how aggressively Astra is currently operating**.

## 28.5 Critical sequence diagrams

The POC should be understood through four canonical execution sequences.

### Sequence A — Normal reasoning / observation

```
Main agent event
      ↓
Hook
      ↓
Astra normalization
      ↓
Trajectory state update
      ↓
Fast signal detection
      ↓
No escalation
      ↓
Continue
```

### Sequence B — Standard reasoning critique

```
Main agent reaches reasoning checkpoint
      ↓
Hook event
      ↓
Astra state update
      ↓
Critique trigger
      ↓
Bounded evidence packet
      ↓
Critique engine
      ↓
Critique returned to main agent
      ↓
Main agent re-reasons
```

### Sequence C — Deep critique / investigation

```
Reasoning checkpoint / failure signal
      ↓
Astra critique
      ↓
Substantive unresolved gap
      ↓
Evidence retrieval / research
      ↓
Alternative generation (when justified)
      ↓
Alternative ranking
      ↓
Recommendation
      ↓
Main agent continues
```

### Sequence D — Verification / intervention

```
Main agent attempts termination
      ↓
Stop hook
      ↓
Astra loads trajectory + verification evidence
      ↓
Verification assessment
      ↓
┌───────────────┴───────────────┐
│                               │
Sufficiently verified       Not sufficiently verified
│                               │
Allow stop                     Intervene / continue
                                ↓
                           Anti-loop policy
                                ↓
                     Continue or surface to user
```

These sequences are the working behavioral contract from which concrete event schemas, transition rules, and API contracts should be derived.

## 28.6 Important reasoning-design boundary

Astra is **not a disagreement engine** and is not expected to produce an alternative answer every time the main agent reasons.

Its first responsibility is to identify whether the main agent's current conclusion is justified. Deeper independent reasoning is an escalation path when the critique reveals a substantive gap.

This preserves the broader Astra thesis while preventing the POC from degenerating into a permanently-running second coding agent.

---

# 31. Tier 1 Gap Resolutions — Concrete Specifications

**Status: this session's recommendation, resolving the four gaps triaged as blocking (Gaps 1, 4, 5, 8 from Section 30's completeness check).** Gaps 2, 3, 6, 7, 9–12 remain open per that triage and are not addressed here. Gap 13 was already satisfied by Section 27.

## 31.1 Gap 1 — Concrete hook contracts

**Correction to the original gap statement**: the gap list names `PreToolUse` among the events needing a schema. That is inconsistent with the rest of this document — Sections 7A/8/9 firmly restrict the POC to `PostToolUse` and `Stop` only. This resolution covers those two; `PreToolUse` stays out of scope.

**The actual obstacle**: Antigravity's raw hook payload shape is empirically unverified and explicitly flagged unstable (Section 10). This gap cannot be closed by guessing Antigravity's JSON — it is closed by making `normalize.py`'s behavior fully deterministic for any raw shape it receives, plus a protocol for locking down the real shape at Milestone 1.

### Normalized `AstraEvent` — missing-field handling (extends Section 10)

- `session_id` missing/unparseable → cannot construct a valid `AstraEvent`; pipeline short-circuits to `continue`, logged as `events_dropped_missing_session_id` (kept distinct from a genuinely evaluated `continue`). Blocking.
- `event_type` missing/unrecognized → same treatment, logged as `events_dropped_unknown_event_type`. Blocking.
- `step_index` missing → defaults to `None`; signal detectors fall back to trajectory event count as the ordering key. Non-blocking.
- `tool.name` / `tool.arguments_summary` missing → `tool = None`, `normalization_warnings += ["tool_missing"]`; any rule requiring it (e.g. same-file-repeated-edit) is skipped, never defaulted to false. Non-blocking.
- `result_summary` / `error` → truncated to ~2000 chars and redacted (Section 23 pattern-redactor) before anything else touches them.
- `occurred_at` missing → defaults to `received_at`; a warning is logged so latency metrics can be filtered for synthesized timestamps.
- `workspace_path` / `transcript_ref` missing → `None`; evidence retrieval for that event degrades to trajectory-state-only.

New fields on `AstraEvent`: `raw_schema_version` (e.g. `"antigravity-cli-unverified"` until Milestone 1 locks a real value) and `normalization_warnings: list[str]` — every degradation above is recorded here and surfaced in the Section 23 log line, so degraded-input runs are visible rather than silent.

### Milestone 1 capture protocol

Capture ≥4 raw payload types into `tests/fixtures/hook_payloads/`: successful tool call, failing tool call, `Stop` after a passing verification, `Stop` after a failing/absent one. Hand-write `raw_schema.py` against the captures, then set `raw_schema_version` to a real value. Until captured, `raw_schema.py` stays `extra="allow"` and every field above stays defensive by construction — implementation is not blocked on Antigravity cooperating first.

## 31.2 Gap 4 — Astra decision contract

Fleshes out Section 20's sketch into field-complete shapes.

**`POST /event` response**: `{decision: continue | allow | deny | block_stop, reason: str | null, assist: AssistPayload | null, intervention_id: uuid | null, mode: Shadow|Assist|Intervene, confidence: float | null, bounded_cost: CostMetadata, correlation_id: uuid}`.

**Decision-enum semantics per event type** (flagged in Section 13 as needing empirical confirmation — this is the intended default, to validate at Milestone 1):

- `PostToolUse` → `decision ∈ {continue, allow, deny}`. `deny` is likely unusable in practice since the tool has already executed by the time the hook fires; Intervene on `PostToolUse` resolves to `continue` plus an `assist.message` flagged `priority: "corrective"`, not a real block.
- `Stop` → `decision ∈ {continue, block_stop}`. `allow`/`deny` do not apply here. `block_stop` is the forced continuation and always carries a non-null `reason`.

**`AssistPayload`**: `{message: str, evidence_refs: list[EvidenceRef], confidence: float, critique: CritiquePayload | null}`.

**`CostMetadata`**: `{tier_invoked: none|fast|deep, model_calls: int, tokens_in: int, tokens_out: int, latency_ms: int}` — every response carries this, even a `continue` with no model call (`tier_invoked: "none"`), so cost accounting (needed for Gap 8) never has a gap.

**`POST /reason` response (`EngineResult`)**: `{engine: bugfix_verifier|reasoning_critic|alternative_ranker, verdict: verified|not_verified|critique_only|alternatives_ranked, critique: CritiquePayload | null, alternatives: list[RankedAlternative] | null, confidence: float, evidence_citations: list[EvidenceRef], bounded_cost: CostMetadata, routing_recommendation: return_to_main_agent|astra_reasons_further|combined}`.

**`CritiquePayload`** — already shaped in Section 28.1.3, formalized here as the wire contract: `{type, severity: low|medium|high, claim_under_review, supporting_observation, why_problematic, missing_information, suggested_next_action}`.

**Fast-tier `Signal`** (extends Section 12): `{signal_id: uuid, type: SignalType, confidence: float, source: rule|model, evidence_refs: list[EvidenceRef], suggested_mode: Mode, rationale: str}` — `rationale` is new: a one-line human-readable justification, so an Assist message is never just a bare confidence number.

## 31.3 Gap 5 — Timeout and failure semantics as a formal contract

The two fail-open boundaries already exist as principles (Section 21); this resolves them into numbers and an explicit outer-deadline rule.

| Boundary | Budget (default, tunable via Settings.thresholds) | On failure | Retries |
| --- | --- | --- | --- |
| Hook → backend (PostToolUse) | 3s | {"decision":"continue"} hardcoded | 0 (single attempt, Section 9) |
| Hook → backend (Stop) | 12s (Deep tier may run) | same | 0 |
| API → full pipeline | 2.5s fast-only / 10s deep path, enforced via an outer deadline independent of internal component budgets | continue, reason: "astra_internal_fail_open", mode unchanged | — |
| Fast-tier model call | 2s | Falls back to rule-only signals; pipeline still proceeds | 0 |
| Deep-tier engine pass | 8s total (incl. one optional evidence round-trip) | "No additional intervention," logged, degrades to whatever Fast tier already produced | 0 |
| Firestore read/write | 1.5s/op | See below | 1 retry (2 attempts), transient errors only, 100ms→300ms backoff |

**Why an explicit outer deadline matters**: per-component budgets could in theory stack past the hook's own timeout if something upstream retries. The pipeline enforces one hard ceiling (deadline = hook timeout minus ~400ms margin) around the entire call, independent of what any single stage does internally — this is the piece "fail-open as a principle" was missing.

**Firestore unavailable, read vs. write (previously unaddressed)**:

- **Read fails** → treat the session as effectively new (same path as TTL expiry, Section 11) rather than blocking; logged as `state_load_failed`, kept distinct from a genuine new session so Gap 8's evaluation harness can filter these out as invalid trials.
- **Write fails** → the decision already computed is still returned to the hook (a valid decision is not discarded because persistence failed); the log line gets `state_persisted: false`, and that event is excluded from trajectory-dependent evaluation metrics for the run.

**Degraded mode** (new, lightweight — a full circuit-breaker library is out of scope at POC scale): an in-memory per-instance counter; after N consecutive Firestore failures, skip state read/write entirely for a cooldown window and run Shadow-only/stateless rather than repeatedly eating timeout latency against a down dependency.

## 31.4 Gap 8 — Evaluation harness mechanics

**`TaskSpec`**: `{task_id, category: reproducible_bug|zindi, workspace_seed_ref, prompt: str, verification_command: str, max_turns: int, condition: baseline|with_astra}`.

**Comparability rules**: identical `workspace_seed_ref` per trial, never reused across conditions; byte-identical `prompt` and `max_turns` across both conditions; baseline = `hooks.json` absent entirely (already decided), plus `antigravity_version` and `main_agent_model_version` pinned and recorded in every run manifest so version drift between conditions is visible rather than a silent confound; baseline and with-Astra runs for a given task are run back-to-back with the order logged, to reduce time/model-drift as a confound.

**Turns-to-fix, computed automatically**:

1. Ground truth is Antigravity's own transcript turn boundaries — never Astra's self-report (already decided in the notebook).
2. `turns_to_fix` = turn index of the first turn after which `verification_command` exits 0 and stays 0 on every subsequent check (guards against a fix that passes once then regresses).
3. The notebook's "same-interruption doesn't count as +1" rule is applied by cross-referencing an intervention's `intervention_id` (correlation-linked, Section 23 logs) against the transcript's turn index: if the triggering event and the resulting main-agent action share a turn index, it is not counted; if the main agent only resolves it on a later turn index, it is.
4. Hitting `max_turns` unresolved → outcome `unresolved`, `turns_to_fix: null` — excluded from the mean/median, counted separately in a resolution-rate metric. A timeout is never silently coerced into a number.

**Invalid-trial criteria** (discarded and rerun, never averaged in): workspace seed failed to apply cleanly (setup failure, not a task-solving failure); hook infrastructure itself errored during a with-Astra run (e.g. the Section 31.3 degraded-mode path fired) — both conditions for that `task_id` are discarded and rerun together, not just the broken one; `verification_command` fails on the untouched seed workspace before any agent action (bad task spec, not a result); wall-clock infra hang unrelated to `max_turns`.

**`RunRecord`**: `{run_id, task_id, condition, antigravity_version, main_agent_model_version, started_at, finished_at, turns_to_fix: int|null, outcome: resolved|unresolved|invalid, invalid_reason: str|null, secondary_metrics}`, where `secondary_metrics = {failed_verification_attempts, time_to_fix_seconds, unnecessary_changes, astra_interventions, model_tool_cost}` (the last two populated only for `with_astra`, summed from `CostMetadata`, Section 31.2).