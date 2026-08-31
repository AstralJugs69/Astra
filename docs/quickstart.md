# Astra — quick start

Choose one truthful path. The **offline evaluator path** is safe for any judge
and needs no cloud, Drive, CUPS, credentials, or hardware. The **live evaluator
path** uses a configured private Astra deployment and does not provision cloud
resources or grant production authority.

## 1. Get the repository ready — local state only

**PowerShell**

~~~powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
uv sync --frozen
uv lock --check
~~~

**WSL/Linux**

~~~bash
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
uv sync --frozen
uv lock --check
~~~

## 2. Offline evaluator path — fixture only, no production claim

~~~text
uv run --frozen python -m braille_errata_relay.presentation.screenshot_fixture --port 8877
~~~

Open `http://127.0.0.1:8877/watch/quiet`, then `/watch` and `/`. Every route is
GET-only and visibly marked `SANITIZED DEMO FIXTURE`; it cannot reach Drive,
Cloud Run, CUPS, a queue, or a physical endpoint. It proves the UI and
contracts only, never a live integration.

## 3. Create local configuration — writes ignored `.env`

~~~text
uv run --frozen braille-relay init-local-config --interactive
uv run --frozen braille-relay doctor --config .env
~~~

The initializer accepts a standard Drive URL or direct file ID, asks for no
password or token, shows a sanitized preview, and refuses to overwrite `.env`
without `--force`. It writes local identifiers only; it is not a Cloud Run
deployment template.

## 4. Live evaluator path — configured private environment

Before using this path:

1. follow [Google Cloud, Drive, and local authentication](google-cloud-setup.md);
2. use [fresh-project deployment](fresh-project-deployment.md) if you do not
   already have the private Cloud Run, Firestore, GCS, and runtime identity;
3. optionally set up the human-owned [local CUPS floor](local-floor-and-cups-simulator.md).

Ordinary `cloud-platform` ADC is enough for the local watch and private Cloud
Run access. A local Drive diagnostic is optional and requires a project-owned
OAuth client for `drive.readonly`; the deployed runtime identity performs
read-only Drive reconciliation. For the live hero path, register the matching
accepted baseline, initialize the source once, and explicitly enable the
private automatic scheduler described in
[fresh-project deployment](fresh-project-deployment.md#11-automatic-drive-watch-configure-paused-then-enable-explicitly).
After that, a human Drive edit needs no reconciliation command: the background
cycle detects the revision, validates authoritative bytes, and processes the
next durable investigation record.

Start the local presentation process:

~~~powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\infra\demo\start_demo.ps1
~~~

The launcher binds only to `127.0.0.1`, opens `/watch` once, and keeps cloud
credentials on the local server. A browser page receives no private URL or
credential. The `/watch` view displays durable state while the private service
performs configured automatic reconciliation; it cannot initiate Drive writes,
change CUPS, or create a human record. Separately guarded incident-detail
forms remain available only when a human deliberately opens and submits them.
If `doctor` is blocked, resolve the documented prerequisite before claiming
live data is ready.

### One-command rehearsal wrapper

~~~powershell
# No cloud access; all pages are marked SANITIZED DEMO FIXTURE.
.\infra\demo\rehearse.ps1 -Mode Fixture

# Private read-only dashboard over existing durable evidence.
.\infra\demo\rehearse.ps1 -Mode Live

# Live source-edit take with the existing automatic watch enabled only for the
# lifetime of this terminal session.
.\infra\demo\rehearse.ps1 -Mode Live -EnableAutomaticWatch
~~~

The command opens the relevant loopback page and waits. Press Enter to end the
session and restore temporary access. Use `-Mode Status` to inspect the exact
recorded session, or `-Mode Cleanup` after a forcibly closed terminal. Cleanup
is restricted to the recorded presentation PID, IAM member, service account,
and Scheduler job. It never edits or manually reconciles Drive, records human
disposition, or controls CUPS.

## 5. Verify the code

~~~text
uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check src tests infra/scripts
uv run --frozen ruff format --check src tests infra/scripts
uv run --frozen mypy src/braille_errata_relay
~~~

The container, WSL, historical evidence, and current-release chronology are in
[testing-and-evidence.md](testing-and-evidence.md). For a problem-first video,
use [live-demo-runbook.md](live-demo-runbook.md).
