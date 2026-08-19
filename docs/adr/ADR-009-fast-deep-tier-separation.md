# ADR-009: Fast / Deep Tier Separation

## Status
Accepted (Firm)

## Context
Evaluating every tool event with a deep reasoning model would introduce unacceptable latency and token costs.

## Decision
Separate reasoning into two explicit tiers:
1. **Fast Tier (`tiers/fast/`)**: Runs on every event. Rule-based signal detection first (0ms LLM latency), with an optional cheap model call only for borderline cases. Enforces a 2s timeout.
2. **Deep Tier (`tiers/deep/`)**: Runs only when escalated by signals or explicit trigger. Gathers a bounded evidence packet and executes targeted reasoning engines (Bugfix Verifier, Reasoning Critic, Alternative Ranker). Enforces an 8s timeout.

## Consequences
- **Positive**: Near-zero overhead on nominal turns; expensive reasoning is strictly event-triggered.
- **Negative**: Requires careful tuning of Fast-tier escalation signals to avoid missing issues or over-escalating.
