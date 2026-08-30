# Astra — active professional-review demo

## Current release boundary

This is a human-authority procedure and target operating context, not a claim
that the current release automatically completes production recovery. The
implemented Story 5 boundary ends at `REPLACEMENT_OBSERVED` after a human
independently submits an approved replacement and Astra links a fresh,
unambiguous read-only observation. It does not claim endpoint completion, final
physical verification, notification, closure, or a `RESOLVED_BY_HUMAN` state.
For the current release evidence and any `NOT_RUN` live actions, see
[testing-and-evidence.md](testing-and-evidence.md).

The harness is a print-only, human-authority runbook. It never submits, holds,
releases, cancels, or otherwise controls CUPS; changes Drive; changes IAM; or
writes evidence. It pauses between each human action so that endpoint receipt,
professional disposition, CUPS cancellation, later observation, and operator
attestation remain separate facts.

**WSL Ubuntu-24.04:**

```text
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
bash infra/wsl/run_active_professional_review_demo.sh --help
```

Use the exact command printed by the harness after providing the current
baseline and principal values. It requires the three named local identities:
`relay-operator` for CUPS submission/cancellation, `relay-observer` for
read-only observation, and `relay-endpoint-auditor` for fixed-root acceptance
audit.

The active-review sequence continues the single canonical observer journal at
`work/live-bridge/journal.sqlite3`. Before submitting a new job, the runbook
requires its read-only pending-outbox check to be empty. Do not create, delete,
acknowledge, or reuse a separate `work/active-review/journal.sqlite3`: a
parallel journal has a different hash-chain predecessor and Cloud Run must
reject it. Older unadmitted local journals remain preserved evidence for review.

The temporary root-owned timing configuration is strictly:

```text
RELAY_PAGE_DELAY_SECONDS=60
```

It is within the installed 1–60 second limit. Retain
`/etc/cups/relay-capture.conf.active-professional-review.bak` until the printed
restoration step succeeds, including if the walkthrough stops early. If that
backup is absent, the recovery block first verifies that the committed default
timing configuration is already installed. It otherwise stops and directs the
human to reinstall the committed local simulator configuration; it never claims
a restore that did not occur.

**Windows PowerShell 5.1 or 7:**

```powershell
$RepoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location -LiteralPath $RepoRoot
```

Prefer PowerShell 7 when it is installed, but the already-open Windows
PowerShell terminal is supported. Do not type `pwsh.exe` inside an existing
PowerShell terminal when it is not installed.

The presentation shell requires only current-terminal environment variables:
`RELAY_PRESENTATION_API_URL`, `RELAY_PRESENTATION_AUDIENCE`,
`RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT`, and a fresh
`RELAY_PRESENTATION_SESSION_SECRET`. It uses ordinary user ADC to impersonate
the named demonstrator service account and mints short-lived audience-bound ID
tokens on the local server only. It does not accept an API key or a
service-account key file.

The harness prints a service-account-scoped, temporary Token Creator grant
with an unconditional PowerShell `finally` cleanup and post-cleanup policy
check. The telemetry publish, advisory-link append, later telemetry publish,
and local presentation each receive their own temporary grant; the runbook
refuses a pre-existing grant and removes the exact human binding afterward.
Before each protected call, it makes a bounded, token-free probe through the
same local user ADC path as the Relay CLI so IAM propagation cannot be mistaken
for a failed presentation or telemetry operation. For each fresh initial or
later observation, that preflight completes *before* the observer reads CUPS;
the human keeps the short-lived grant open only until the one exact observation
file is published and the `finally` cleanup completes. Continue only when the
CLI returns `status: ACCEPTED`. If it returns `BLOCKED` or `REJECTED`, do not
acknowledge the local outbox, append a production link, edit Drive, or attribute
any CUPS action to that attempted run.
Never grant the role at project scope. Do not record passwords, tokens, private
Drive IDs, raw source, spool files, capture paths, or local credential paths in
evidence.
