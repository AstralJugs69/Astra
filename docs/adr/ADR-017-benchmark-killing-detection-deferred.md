# ADR-017: Benchmark-Killing Detection Deferred (Seam Reserved)

## Status
Accepted (Firm)

## Context
Detecting reward-hacking or benchmark-tampering (e.g. modifying test assertions rather than fixing code) is a valuable defense mechanism but secondary to core bugfix verification.

## Decision
Benchmark-integrity checking is **deferred post-POC**. The `Engine` protocol in `domain/reasoning_ports.py` serves as the clean plug-in seam for a future `BenchmarkIntegrityChecker` engine.

## Consequences
- **Positive**: Prevents scope bloat in POC while leaving a clean extension path.
