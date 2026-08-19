# ADR-004: State Persistence with Firestore

## Status
Accepted (Recommendation)

## Context
Trajectory state and anti-loop counters must survive instance restarts in stateless Cloud Run environments. Evaluation data must be kept isolated to avoid trajectory contamination.

## Decision
1. Use **Google Cloud Firestore** as the production document store for `TrajectoryState` documents, keyed by Antigravity `session_id`.
2. Implement an in-memory store adapter (`memory_store.py`) implementing the same `TrajectoryStateStore` protocol for local dev and testing.
3. Keep evaluation benchmark runs in a physically separate local SQLite store (`evaluation/runs/*.sqlite`), never in the production Firestore database.

## Consequences
- **Positive**: Serverless, zero connection pooling overhead, direct document serialization with Pydantic.
- **Negative**: Firestore latency (50-150ms) is handled via optimistic concurrency (`state_version`), bounded retries, and degraded-mode fallback on outage.
