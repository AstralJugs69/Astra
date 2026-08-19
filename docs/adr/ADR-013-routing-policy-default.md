# ADR-013: Deep Tier Routing Policy Default (Critique to Main Agent)

## Status
Accepted (Experimental Default)

## Context
When Deep tier generates a critique, Astra has two primary options: (Path A) return the critique to the main agent for re-reasoning, or (Path B) perform deeper independent reasoning / alternative generation itself.

## Decision
Set **Path A ("Critique → Main Agent")** as the default routing policy for the POC.
Path B ("Astra reasons further / ranks alternatives") is enabled via configuration (`ASTRA_ROUTING_MODE=combined` or `astra_reasons_further`) when severe failure escalations warrant it.

## Consequences
- **Positive**: Preserves main-agent autonomy, minimizes extra token costs and latency, cleanly isolates the effect of critique on main agent turns-to-fix.
- **Negative**: If the main agent is incapable of reasoning through the critique, Path B must be triggered via escalation.
