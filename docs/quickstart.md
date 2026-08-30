# Quick start

This chapter has two deliberately separate paths. The offline fixture is safe
for any evaluator. The live watch floor requires ordinary Google user ADC and a
pre-existing private Relay deployment; it does not provision anything.

## 1. Get the repository ready — local state only

**PowerShell**

```powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
uv sync --frozen
uv lock --check
```

**WSL/Linux**

```bash
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
uv sync --frozen
uv lock --check
```

## 2. View the offline fixture — no cloud or production action

```text
uv run --frozen python -m braille_errata_relay.presentation.screenshot_fixture --port 8877
```

Open `http://127.0.0.1:8877/watch`. Every route is GET-only and visibly marked
`SANITIZED DEMO FIXTURE`; it cannot reach Drive, Cloud Run, CUPS, a queue, or a
physical endpoint.

## 3. Create local configuration — writes ignored `.env`

```text
uv run --frozen braille-relay init-local-config --interactive
uv run --frozen braille-relay doctor --config .env
```

The initializer accepts a standard Drive URL or direct file ID, but asks for no
password or token. It shows a sanitized configuration preview and refuses to
overwrite `.env` without `--force`.

## 4. Start the live watch floor — starts a local process only

```powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\infra\demo\start_demo.ps1
```

The launcher binds only to `127.0.0.1`, opens `/watch` once, and keeps cloud
credentials on the local server. A browser page receives no private URL or
credential. If its `doctor` result is blocked, resolve the documented
prerequisite before claiming live data is ready.

## 5. Verify the code

```text
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
```

The full container and WSL verification path is in
[testing-and-evidence.md](testing-and-evidence.md).
