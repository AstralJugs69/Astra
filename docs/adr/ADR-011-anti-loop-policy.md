# ADR-011: Hard Anti-Loop Safety Module

## Status
Accepted (Firm)

## Context
If an agent repeatedly fails a verification step, an over-eager supervisor might continuously block `Stop` and force continuation, creating infinite loops, wasted tokens, and high frustration.

## Decision
1. Implement a dedicated `AntiLoopPolicy` in `domain/intervention.py`.
2. Hash error messages / test failures into a stable `failure_signature_hash`.
3. Cap forced continuations per unique signature (default: 2) and enforce minimum cooldowns.
4. When the cap is reached, Astra ceases forced continuations, permits `Stop`, and surfaces the unresolved root cause directly to the user.

## Consequences
- **Positive**: Hard mathematical bound against infinite loops; guarantees graceful fallback.
- **Negative**: Requires hashing logic that ignores trivial dynamic content (timestamps, memory addresses).
