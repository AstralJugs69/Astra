# ADR-003: Cloud Run Service Topology

## Status
Accepted (Firm)

## Context
Astra needs a stateless, scalable backend to process events and execute deep reasoning requests.

## Decision
Deploy a **single Cloud Run service (`astra-backend`)** exposing two route groups:
1. `POST /event`: Fast path called on every hook event (`PostToolUse` and `Stop`).
2. `POST /reason`: Explicit deep reasoning path callable internally by the pipeline or directly for evaluation/offline audits.

Microservices (e.g. separating fast vs deep services) are explicitly rejected for the POC as premature overhead.

## Consequences
- **Positive**: Simple single-container deployment, shared domain and model abstractions, zero inter-service network latency.
- **Negative**: Long-running deep reasoning and fast-tier requests share the same process pool, managed via asyncio concurrency and explicit per-request timeouts.
