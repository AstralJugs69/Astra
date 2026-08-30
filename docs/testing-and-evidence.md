# Testing and evidence

Run checks independently from repository root. Each is read-only except Docker
image build cache and explicitly labelled local generated outputs.

```text
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
git diff --check
docker build --tag braille-errata-relay:final-demo-readiness .
docker run --rm --network none --read-only --cap-drop ALL --entrypoint python braille-errata-relay:final-demo-readiness -m braille_errata_relay.container_smoke
```

## Gate 0

The real pinned Liblouis/Unicode/table smoke runs where the pinned binding and
tables are installed. The container golden runs twice and compares BRF bytes,
profile identity, table identity, and hashes. WSL full-app golden remains an
honest platform skip until isolated frozen project dependencies are present.

The CUPS local-floor harness is human-operated. It validates real CUPS raw
passthrough and read-only authorization without exposing mutation controls in
the app. Do not label a missing WSL/system package/permission as a pass.

## Screenshots and fixture

`demo/screenshots/manifest.json` contains viewport, SHA-256, truth basis, and
sanitization assertions for the GET-only offline fixture. It is not mounted by
the Cloud Run application. Visual inspection covers desktop and mobile views.

## Sanitized evidence

`demo/evidence/` is ignored by default; only named sanitized, schema-validated
release artifacts are allowlisted. Evidence must not contain passwords, tokens,
credentials, private Drive IDs, private principals, machine-specific paths, or
raw spool/capture data.
