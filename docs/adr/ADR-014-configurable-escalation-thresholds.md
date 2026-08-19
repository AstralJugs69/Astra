# ADR-014: Externalized & Configurable Escalation Thresholds

## Status
Accepted (Experimental)

## Context
Thresholds for signal confidence, escalation from Shadow to Assist to Intervene, and anti-loop cooldowns are hypotheses that must be empirically tuned by the evaluation harness.

## Decision
All escalation thresholds, budgets, and cooldowns are declared as typed parameters in `Settings` (`astra/settings.py`) using `pydantic-settings`. No thresholds may be hardcoded inside `domain/` rules or policies.

## Consequences
- **Positive**: Thresholds can be tuned or swept during evaluation benchmark runs without modifying domain code.
- **Negative**: Requires passing configuration objects into domain reducer/decision functions.
