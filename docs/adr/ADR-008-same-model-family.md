# ADR-008: Same Model Family

## Status
Accepted (Firm)

## Context
Astra evaluates the main agent's reasoning. A question arises whether to use a distinct reasoning model or the same family.

## Decision
Astra uses the **same model family (Gemini)** as the main agent. Fast tier uses a lightweight variant (e.g. Gemini 2.5 Flash), while Deep tier uses an advanced variant (e.g. Gemini 2.5 Pro).

## Consequences
- **Positive**: Consistent reasoning vocabulary, unified Google GenAI SDK integration, streamlined credentialing and pricing.
