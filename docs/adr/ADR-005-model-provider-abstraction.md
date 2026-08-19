# ADR-005: Model Provider Abstraction

## Status
Accepted (Firm)

## Context
Astra calls Gemini models using the Google GenAI / Antigravity SDK (`google-genai`). Domain logic and reasoning engines must remain decoupled from specific SDK classes to allow structured output mocking and future SDK updates.

## Decision
Define a pure `ModelProvider` protocol in `domain/model_ports.py`. Implement concrete `AntigravitySdkProvider` in `infrastructure/model_providers/antigravity_sdk_provider.py`. Inject the provider into engines and tiers via the composition root (`deps.py`).

## Consequences
- **Positive**: Domain and engines are 100% testable with mock providers; token cost accounting and timeouts are centralized in the adapter.
- **Negative**: Adds a thin adapter layer over `google-genai`.
