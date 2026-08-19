# ADR-002: Hexagonal / Ports-and-Adapters Architecture

## Status
Accepted (Firm)

## Context
Astra evaluates upstream Antigravity events, which have unverified and evolving JSON schemas. Furthermore, model provider SDKs and persistence backends may evolve. Tight coupling between reasoning logic and Antigravity internals or Google Cloud SDKs would make the codebase brittle.

## Decision
Adopt **Ports-and-Adapters (Hexagonal) Architecture**:
- `domain/`: Pure domain logic with **zero I/O and zero framework imports** (no FastAPI, no Firestore, no `google-genai`, no `httpx`).
- `integration/`: The **only** layer permitted to know Antigravity's raw JSON payload shape.
- `application/`: Orchestrates the decision pipeline using domain entities and ports.
- `infrastructure/`: Implements domain ports for model providers, Firestore persistence, and evidence retrieval.
- `api/`: Delivery layer wrapping application pipeline behind HTTP routes and fail-open handlers.

## Consequences
- **Positive**: Domain models and signal/anti-loop rules are 100% unit-testable in isolation with zero mocks; upstream schema churn is isolated to `integration/antigravity/normalize.py`.
- **Negative**: Requires strict discipline regarding imports; verified via linting/typing tests.
