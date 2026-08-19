# ADR-012: Evaluation Architecture & Turns-to-Fix Ground Truth

## Status
Accepted (Firm)

## Context
Validating whether Astra improves coding agent performance requires an objective, reproducible metric that cannot be corrupted by Astra's own self-reporting.

## Decision
1. **Turns-to-Fix** is the primary success metric, calculated strictly from Antigravity's `.jsonl` transcript turn boundaries.
2. An intervention resolved in the same interruption is not counted as +1 turn.
3. Every task must be evaluated against a strict **no-Astra baseline** (`hooks.json` absent).
4. Evaluation runs and metrics are stored in a local SQLite database (`evaluation/runs/*.sqlite`), physically isolated from production Firestore.

## Consequences
- **Positive**: Empirical, tamper-proof metric of performance improvements.
