# 🌌 Astra: Epistemic Companion Agent for Google Antigravity

[![CI](https://github.com/AstralJugs69/Astra/actions/workflows/ci.yml/badge.svg)](https://github.com/AstralJugs69/Astra)
[![Tests](https://img.shields.io/badge/tests-61%20passed-brightgreen.svg)](https://github.com/AstralJugs69/Astra)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Model](https://img.shields.io/badge/model-gemini--3.7--flash-purple.svg)](https://cloud.google.com/vertex-ai)
[![Architecture](https://img.shields.io/badge/architecture-hexagonal%20%2F%20ports--and--adapters-orange.svg)](docs/adr/ADR-002-hexagonal-architecture.md)

> **Astra** is an autonomous companion supervisor for the **Google Antigravity CLI (`agy`)**. It observes the coding agent via non-invasive lifecycle hooks (`PostToolUse` and `Stop`), maintains a continuous trajectory of the agent's epistemic phase, and selectively injects deep reasoning critique, alternative hypothesis rankings, and bugfix verification audits without getting trapped in infinite loops.

---

## 🏗️ Technical Architecture

Astra operates on a **Hexagonal (Ports-and-Adapters)** architecture where the pure domain logic is strictly isolated from Google Antigravity CLI wire schemas, HTTP routers, and Google Cloud SDKs.

```
                      ANTIGRAVITY CLI (agy)
                                │
                       PostToolUse / Stop
                                │
                                ▼
                    ┌───────────────────────┐
                    │  Event Normalization  │ (integration/antigravity/)
                    └───────────┬───────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │ Trajectory Reducer    │ (domain/trajectory.py)
                    │ Updates actions,      │
                    │ epistemic phase,      │
                    │ evidence pointers,    │
                    │ verification state    │
                    └───────────┬───────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Fast Signal Layer   │ (domain/signals.py & tiers/fast/)
                    │ Evaluates risk rules, │
                    │ thrashing, unverified │
                    │ changes (<5ms)        │
                    └───────────┬───────────┘
                                │
                     Activation warranted?
                      /               \
                    No                 Yes
                    │                   │
                    ▼                   ▼
              Remain in            Construct Structured
             SHADOW Mode           Reasoning Checkpoint
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │    Deep Tier (LLM)    │ (tiers/deep/orchestrator.py)
                            │  Critique / Verify    │ (gemini-3.7-flash on Vertex)
                            └───────────┬───────────┘
                                        │
                              Is deeper investigation /
                              alternatives needed?
                                /               \
                              No                 Yes
                              │                   │
                              │             AlternativeRanker
                              │             (generate & rank 3
                              │              distinct paths)
                              │                   │
                              └─────────┬─────────┘
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │    Astra Decision     │ (domain/modes.py, routing.py)
                            └───────────┬───────────┘
                                 /             \
                            ASSIST          INTERVENE
                          (injected        (Stop hook
                          critique)        block continuation)
                              │                 │
                              ▼                 ▼
                         Main agent         Main agent
                         continues          interrupted
```

---

## 🧭 Dual Independent State Dimensions

Astra maintains two orthogonal dimensions of state throughout a session:

### 1. Epistemic Trajectory Phase (`EpistemicPhase`)
Tracks the evolution of the agent's beliefs and evidence:
$$\text{UNKNOWN} \longrightarrow \text{HYPOTHESIS} \longrightarrow \text{INVESTIGATING} \longrightarrow \text{EVIDENCE\_FOUND} \longrightarrow \text{CONCLUSION} \longrightarrow \text{VERIFYING} \longrightarrow \begin{cases} \text{CONFIRMED} \\ \text{CONTRADICTED} \end{cases}$$

### 2. Astra Companion Mode (`Mode`)
- **`SHADOW`**: Continuous background observation. Updates beliefs silently with 0ms lag on nominal steps.
- **`ASSIST`**: Non-authoritative guidance (critique payload, second opinion, missing evidence pointers) injected into the agent context.
- **`INTERVENE`**: Authoritative enforcement (e.g. `Stop` hook continuation under anti-loop safety bounds) when an unverified fix is claimed.

---

## 🧠 Deep Tier Reasoning Engines

1. **🛡️ `BugfixVerifier`** (`src/astra/engines/bugfix/verifier.py`): Enforces the *Evidence-First Debugging Protocol*. Audits whether claimed fixes are proven by passing test results.
2. **🔍 `ReasoningCritic`** (`src/astra/engines/reasoning/critic.py`): Evaluates structured `ReasoningCheckpoint`s to detect unsupported assumptions, circular reasoning, and premature convergence.
3. **🔀 `AlternativeRanker`** (`src/astra/engines/reasoning/alternatives.py`): Formulates and ranks 3 distinct alternative technical approaches to break out of local minima and model laziness.

---

## ⚡ Key Architectural Features

- **Vertex AI with Project Credits**: Powered by `gemini-3.7-flash` using `google-genai` SDK on Vertex AI (`location="global"`).
- **Dual Fail-Open Guarantee**: Local hook scripts and Cloud Run router independently guarantee fallback to `allow` on any timeout or backend failure.
- **Bounded Evidence Principle**: Consumes token-budgeted `EvidencePacket`s ($4,000$ tokens), never raw sessions.
- **Strict Anti-Loop Safety**: Failure signature hashing caps repeated interventions (max 2 per signature), surfacing to the user instead of looping.
- **Offline Evaluation Harness**: Measures turns-to-fix on real bugfixing and Zindi data-science benchmarks.

---

## 🚀 Quickstart

### 1. Install Dependencies
```bash
git clone https://github.com/AstralJugs69/Astra.git
cd Astra
pip install -e ".[dev]"
```

### 2. Configure Environment
Create a `.env` file (or set environment variables):
```env
ASTRA_USE_VERTEX_AI=true
ASTRA_VERTEX_LOCATION=global
GCP_PROJECT_ID=your-gcp-project-id
FIRESTORE_PROJECT_ID=your-gcp-project-id
ASTRA_FAST_MODEL=gemini-3.7-flash
ASTRA_DEEP_MODEL=gemini-3.7-flash
ASTRA_AUTH_TOKEN=astra-dev-secret-token-change-in-prod
```

Authenticate with Google Cloud Application Default Credentials:
```bash
gcloud auth application-default login
```

### 3. Start Backend Server
```bash
make dev
# or: uvicorn astra.api.main:app --host 0.0.0.0 --port 8080 --reload
```

### 4. Install Hooks into Antigravity Workspace
```bash
# Linux/macOS
./scripts/install_hooks.sh /path/to/your/workspace

# Windows
.\scripts\install_hooks.bat C:\path\to\your\workspace
```

---

## 📊 Live Observability & Log Inspector

Astra outputs clean, human-readable, grep-friendly logs:

```bash
# View last 30 events with color coding
python scripts/tail_logs.py

# Show only tool executions
python scripts/tail_logs.py --tools-only

# Show only interventions and blocked stop hooks
python scripts/tail_logs.py --interventions-only

# Follow live log updates in real-time
python scripts/tail_logs.py --follow
```

---

## 🧪 Testing & Evaluation

```bash
# Run all unit tests (domain purity, engines, tiers, anti-loop)
pytest tests/unit/ -v

# Run integration tests (API contracts, persistence, hooks)
pytest tests/integration/ -v

# Run full E2E lifecycle session tests
pytest tests/e2e/ -v -m e2e

# Run comparative benchmark evaluation harness
python scripts/run_eval.py
```

---

## 📁 Repository Structure

```
Astra/
├── Makefile                          # Development and testing tasks
├── pyproject.toml                    # Dependencies and build configuration
├── Dockerfile                        # Multi-stage production container
├── hooks/                            # Local Antigravity CLI lifecycle hooks
│   ├── common.py                     # Stdlib-only HTTP relay & fail-open default
│   ├── post_tool_use.py              # PostToolUse dispatcher
│   └── stop.py                       # Stop audit dispatcher
├── docs/                             # Architecture & Specs
│   ├── adr/                          # Architecture Decision Records (ADRs 1–18)
│   ├── event-model.md                # Event normalization specification
│   └── evidence-schema.md            # Evidence packet and budgeting specification
├── src/astra/
│   ├── api/                          # FastAPI delivery surface & composition root
│   ├── integration/                  # Raw Antigravity CLI payload normalization
│   ├── application/                  # Pipeline orchestration & hook handlers
│   ├── domain/                       # Pure hexagonal core (0 I/O, zero external deps)
│   ├── engines/                      # BugfixVerifier, ReasoningCritic, AlternativeRanker
│   ├── tiers/                        # Fast tier assessor & Deep tier orchestrator
│   ├── infrastructure/               # Model providers (Vertex AI), Firestore, Evidence
│   └── evaluation/                   # Turns-to-fix metrics & benchmark runner
├── tests/
│   ├── unit/                         # Unit tests mirroring src/
│   ├── integration/                  # API contract & hook fail-open tests
│   ├── e2e/                          # Full session lifecycle E2E tests
│   └── fixtures/                     # Real Antigravity CLI payload fixtures
└── scripts/                          # Dev server, hooks installer, log viewer
```

---

## 📄 License
Apache-2.0. See [LICENSE](LICENSE) for details.