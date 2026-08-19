# ADR-007: MCP Permanently Excluded

## Status
Accepted (Firm / Permanent)

## Context
Model Context Protocol (MCP) is an external tool provider standard. Consideration was given to exposing Astra as an MCP server or calling tools via MCP.

## Decision
**MCP is permanently excluded from Astra's architecture.**
- Astra will never be exposed as an MCP server.
- Astra will never expose or consume tools via MCP.
- Astra interacts with the main agent exclusively via lifecycle hooks (`PostToolUse` and `Stop`).

## Consequences
- **Positive**: Eliminates complex client-server lifecycle management, avoiding circular tool-calling loops and keeping the companion agent model clean.
- **Negative**: None for Astra's supervisor role.
