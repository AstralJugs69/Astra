# ADR-015: CLI Surface Only for POC

## Status
Accepted (Firm)

## Context
Google Antigravity exists as both a CLI (`agy`) and IDE extension (Antigravity 2.0 / IDE). IDE hooks are known to have experimental stability issues upstream.

## Decision
The POC strictly targets the **Antigravity CLI surface (`agy`)**. IDE and editor plugin surfaces are explicitly out of scope for this POC.

## Consequences
- **Positive**: Eliminates IDE plugin packaging complexity, focus on deterministic CLI lifecycle hooks.
- **Negative**: Visual IDE interactions are deferred post-POC.
