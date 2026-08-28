# Braille Errata Relay

Braille Errata Relay is a report-first overlay for a late source correction in
an existing Braille production workflow. It detects and explains a changed
source, regenerates a candidate through pinned Liblouis, calculates exact BRF
page impact, and preserves a human-controlled recovery path.

The relay is not a Braille publishing platform or system of record. It never
submits, holds, cancels, releases, restarts, pauses, or otherwise controls
CUPS, an embosser, or a production device. CUPS queue actions belong to a human
operator on the independent production surface. Only the physical endpoint is
simulated; all candidate bytes, hashes, pagination, and report facts are
deterministic.

## Current implementation slice

The checked-in core includes:

- Python 3.11-3.12 project configuration and FastAPI health/readiness factory;
- closed Pydantic domain models and read-only dependency ports;
- canonical JSON and SHA-256 helpers;
- strict UTF-8, NFC, line-ending, and heading/paragraph Markdown handling;
- versioned UEB Grade 2 profile for 40 cells x 25 lines;
- pinned Liblouis adapter with no substitute translator;
- deterministic wrapping, pagination, explicit six-dot BRF serialization,
  manifests, source maps, and page prefix/suffix impact;
- V1/V2 hero fixtures, unsupported-content tests, incompatible-profile tests,
  authority-negative tests, and a Gate 0-aware golden test.

The profile is intentionally unbound until the real Liblouis 3.38.0 table bundle is installed and hashed. The locked environment has a uv-managed Python 3.12 interpreter. The real Liblouis binding/table profile, a running Docker engine, native CUPS tools, and deployed cloud credentials remain Gate 0 prerequisites; `infra/scripts/preflight.py` records those results.

## Local verification

Use a Python 3.11 or 3.12 environment, install the project with its chosen
lockfile, and run:

```text
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen python infra/scripts/preflight.py
```

The Liblouis golden test is skipped until Gate 0 binds the profile. It must not
be replaced with a fake translator or a saved model response.

## Contract boundaries

Google Drive is the MVP source adapter and CUPS is the MVP scheduler observer;
neither choice claims to describe every Braille facility. Gemini/ADK is limited
to semantic assessment. Deterministic code owns source normalization, BRF
translation/output, pagination, hashes, page impact, policy, and verification.
Candidate BRF bytes are not approved production masters. Professional
disposition, operator containment, physical-output isolation, proof approval,
and replacement submission are separate human-owned facts.

