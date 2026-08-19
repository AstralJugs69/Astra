# ADR-001: Language Selection (Python)

## Status
Accepted (Firm)

## Context
Astra requires a language that supports rapid iteration, structured JSON schema validation, first-class Google GenAI / Gemini SDK support, Cloud Run deployment, and data science task evaluation (Zindi benchmarks).

## Decision
Use **Python 3.11+** for the entire POC codebase:
- Backend Cloud Run service (FastAPI + Uvicorn + Pydantic v2)
- Domain core, reasoning engines, and tiers
- Local hook dispatchers (`hooks/*.py`, stdlib-only)
- Evaluation harness and Zindi task benchmarking

## Consequences
- **Positive**: Direct compatibility with `google-genai` / Antigravity SDK, native Pydantic v2 schema validation, single language across service and evaluation harness.
- **Negative**: Local hook script has Python startup overhead (~30-50ms), mitigated by using stdlib-only dependencies in `hooks/`.
