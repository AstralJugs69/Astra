# ADR-006: Hooks-Only Integration Surface

## Status
Accepted (Firm)

## Context
Astra integrates with Google Antigravity CLI (`agy`). The choice of integration mechanism dictates reliability, latency, and agent interaction model.

## Decision
Use **Antigravity lifecycle hooks (`PostToolUse` and `Stop`)** exclusively.
- `PostToolUse`: Fires after a tool completes; used for trajectory state updates and fast signal detection.
- `Stop`: Fires when the main agent attempts to finish; used for verification auditing and potential forced continuations.

## Consequences
- **Positive**: Native Antigravity integration point with no need for process injection or UI hacks.
- **Negative**: Synchronous hook execution pauses the agent loop during evaluation, requiring strict latency budgets and fail-open guarantees.
