# ADR-016: Context Engine Deferred (Seam Reserved)

## Status
Accepted (Firm)

## Context
A full repository context / semantic graph retrieval engine is a major post-POC subsystem. Attempting to build it during the POC would derail the core reasoning and intervention validation.

## Decision
The full context engine is **deferred post-POC**. In its place, define the pure `EvidenceRetriever` port in `domain/evidence_ports.py` and implement simple slice retrievers (`TranscriptRetriever`, `RepoRetriever`) in `infrastructure/evidence/`.

## Consequences
- **Positive**: Clean seam allowing the future context engine to plug in without altering domain or reasoning engines.
- **Negative**: POC evidence retrieval is limited to transcript slices, file diffs, and bounded web queries.
