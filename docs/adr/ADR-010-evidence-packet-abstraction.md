# ADR-010: Evidence Packet Abstraction & Token Budgeting

## Status
Accepted (Firm)

## Context
Passing full session transcripts or large codebase dumps to reasoning models causes context bloating, high latency, and high cost.

## Decision
1. Astra maintains compact `TrajectoryState` with **references** (`EvidenceRef`), never full file or transcript content.
2. When Deep tier triggers, `tiers/deep/orchestrator.py` requests relevant evidence from `EvidenceRetriever` adapters.
3. `domain/evidence.py` prioritizes, deduplicates, and clamps candidate evidence into a bounded `EvidencePacket` within a token budget.

## Consequences
- **Positive**: Bounded context sizes, deterministic latency/cost, and clean separation between evidence fetching and reasoning.
