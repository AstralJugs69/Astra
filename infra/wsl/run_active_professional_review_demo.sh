#!/usr/bin/env bash
# Print-only, human-operated Story 3 demonstration harness.
#
# This harness does not submit, hold, release, cancel, restart, or otherwise
# control CUPS. It also does not change Drive, invoke Cloud Run, modify IAM, or
# write evidence. It prints exact, bounded commands and pauses for the person
# who owns each separate authority.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WSL_REPO_ROOT="$ROOT"
if command -v wslpath >/dev/null 2>&1; then
  WINDOWS_REPO_ROOT="$(wslpath -w "$ROOT")"
else
  WINDOWS_REPO_ROOT='<Windows path to this checkout>'
fi
QUEUE="Braille-Embosser-Sim"
SITE_ID="demo-site"
BRIDGE_ID="single-pc-bridge"
CAPTURE_CONFIG="/etc/cups/relay-capture.conf"
CAPTURE_BACKUP="/etc/cups/relay-capture.conf.active-professional-review.bak"
OPERATOR="relay-operator"
OBSERVER="relay-observer"
ENDPOINT_AUDITOR="relay-endpoint-auditor"
CANONICAL_BRIDGE_WORK_ROOT="work/live-bridge"
CANONICAL_BRIDGE_JOURNAL="$CANONICAL_BRIDGE_WORK_ROOT/journal.sqlite3"

BASELINE_ID=""
SUPERSEDES_LINK_ID=""
BASELINE_STATE_VERSION=""
SERVICE_URL=""
AUDIENCE=""
TELEMETRY_IDENTITY=""
DEMONSTRATOR_IDENTITY=""
HUMAN_PRINCIPAL=""

usage() {
  cat <<'EOF'
Usage:
  bash infra/wsl/run_active_professional_review_demo.sh \
    --baseline-id BASELINE_SHA256 \
    --supersedes-production-link-id OLD_LINK_SHA256 \
    --baseline-state-version CURRENT_VERSION \
    --service-url PRIVATE_CLOUD_RUN_URL \
    --audience PRIVATE_CLOUD_RUN_AUDIENCE \
    --telemetry-identity TELEMETRY_SERVICE_ACCOUNT \
    --demonstrator-identity DEMONSTRATOR_SERVICE_ACCOUNT \
    --human-principal HUMAN_USER_EMAIL

The script is a print-only runbook. It never executes a human CUPS action,
Drive action, Cloud Run mutation, IAM mutation, or timing-profile change. It
computes only the committed fixture hash, prints authority-labelled commands,
and waits for explicit human confirmations.
EOF
}

fail() {
  echo "BLOCKED: $*" >&2
  exit 1
}

confirm() {
  local phrase="$1"
  local action="$2"
  local answer=""
  echo
  echo "HUMAN ACTION REQUIRED: $action"
  read -r -p "Type $phrase after the separate human action is complete: " answer
  [[ "$answer" == "$phrase" ]] || fail "human confirmation was not provided"
}

require_sha256() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]] || fail "$2 must be a lowercase SHA-256 value"
}

require_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "$2 must be a positive integer"
}

require_email() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}$ ]] || {
    fail "$2 is invalid"
  }
}

require_https_url() {
  [[ "$1" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || fail "$2 must be an HTTPS origin"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-id) BASELINE_ID="${2:-}"; shift 2 ;;
    --supersedes-production-link-id) SUPERSEDES_LINK_ID="${2:-}"; shift 2 ;;
    --baseline-state-version) BASELINE_STATE_VERSION="${2:-}"; shift 2 ;;
    --service-url) SERVICE_URL="${2:-}"; shift 2 ;;
    --audience) AUDIENCE="${2:-}"; shift 2 ;;
    --telemetry-identity) TELEMETRY_IDENTITY="${2:-}"; shift 2 ;;
    --demonstrator-identity) DEMONSTRATOR_IDENTITY="${2:-}"; shift 2 ;;
    --human-principal) HUMAN_PRINCIPAL="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

require_sha256 "$BASELINE_ID" "baseline ID"
require_sha256 "$SUPERSEDES_LINK_ID" "superseded production link ID"
require_positive_integer "$BASELINE_STATE_VERSION" "baseline state version"
require_https_url "$SERVICE_URL" "service URL"
require_https_url "$AUDIENCE" "audience"
require_email "$TELEMETRY_IDENTITY" "telemetry identity"
require_email "$DEMONSTRATOR_IDENTITY" "demonstrator identity"
require_email "$HUMAN_PRINCIPAL" "human principal"

V1_BRF="$ROOT/demo/expected/v1.brf"
[[ -f "$V1_BRF" ]] || fail "committed V1 BRF fixture is missing"
V1_SHA256="$(sha256sum "$V1_BRF" | awk '{print $1}')"
TITLE="BER|WO-DEMO-001|${V1_SHA256:0:12}|BASELINE"

cat <<EOF
INFO: active professional-review demo harness
INFO: this is a report-first overlay. Candidate BRF is not an approved production master.
INFO: title: $TITLE
INFO: approved V1 BRF SHA-256: $V1_SHA256
INFO: no command below is executed by this harness.

Step 0 — verify the one canonical local bridge journal before any new CUPS job.
The same bridge, site, and queue must continue its admitted hash chain. Do not
create a second journal for single-pc-bridge. Files under work/active-review
from an earlier rejected attempt are preserved local evidence, not a journal to
acknowledge, delete, or reuse.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  test -f $CANONICAL_BRIDGE_JOURNAL
  sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3 -m relay_bridge.main pending-outbox --journal $CANONICAL_BRIDGE_JOURNAL

Expected result: an empty observations array. If it is non-empty, stop here.
Do not create a new job or acknowledge a message until the existing canonical
outbox has been independently reviewed and admitted or explicitly preserved.

Step 1 — in the Drive browser, restore the watched same-file fixture to V1.
The relay must observe a real revision later; do not use a copied file.
EOF
confirm "CANONICAL-JOURNAL-READY" "the canonical bridge journal had no unpublished observations"
confirm "DRIVE-V1-RESTORED" "the Drive fixture was visibly restored to V1"

cat <<EOF
Step 2 — temporarily slow only the simulated endpoint. Retain the backup until
the final restore step. This is a manual root-owned configuration action.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  sudo cp -p $CAPTURE_CONFIG $CAPTURE_BACKUP
  printf 'RELAY_PAGE_DELAY_SECONDS=60\\n' | sudo tee $CAPTURE_CONFIG >/dev/null
  sudo chown root:lp $CAPTURE_CONFIG
  sudo chmod 0640 $CAPTURE_CONFIG
  sudo /usr/lib/cups/backend/relay-capture --validate-runtime-config $CAPTURE_CONFIG
  sudo systemctl restart cups

The 60-second delay is inside the simulator's committed 1–60 second bound. It
only provides time to observe immutable acceptance evidence; it does not make a
physical embossing claim.
EOF
confirm "SIMULATOR-SLOW" "the human installed and validated the temporary simulator timing profile"

cat <<EOF
Step 3 — prepare the temporary telemetry publisher before any new CUPS work.
This makes identity propagation a preflight, so it cannot make the next fresh
observation stale. Run this in a separate Windows terminal and leave it waiting
at its explicit prompt; it holds only the short-lived, service-account-scoped
temporary grant until the one observation is published.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed, but the already-open Windows
  # PowerShell terminal works too. Do not type pwsh.exe inside that terminal.
  \$ErrorActionPreference = 'Stop'
  Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'
  \$humanPrincipal = '$HUMAN_PRINCIPAL'
  \$targetPrincipal = '$TELEMETRY_IDENTITY'
  \$member = 'user:' + \$humanPrincipal
  \$grantAdded = \$false
  function Wait-ForRelayToken {
    param([string]\$Audience, [string]\$TargetPrincipal)
    \$tokenExitCode = 1
    for (\$attempt = 1; \$attempt -le 24; \$attempt++) {
      Start-Sleep -Seconds 5
      \$previousPreference = \$ErrorActionPreference
      \$ErrorActionPreference = 'Continue'
      try {
        uv run python -c "from braille_errata_relay.cli import _identity_token; _identity_token(audience='\$Audience', impersonate_service_account='\$TargetPrincipal')" *> \$null
        \$tokenExitCode = \$LASTEXITCODE
      }
      finally {
        \$ErrorActionPreference = \$previousPreference
      }
      if (\$tokenExitCode -eq 0) { return }
    }
    throw 'Local user ADC did not gain the temporary Token Creator permission before the bounded wait expired.'
  }
  try {
    if ((gcloud config get-value account).Trim() -ne \$humanPrincipal) { throw 'Active gcloud account does not match the named human principal.' }
    \$before = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$beforeBindings = if (\$null -eq \$before.PSObject.Properties['bindings']) { @() } else { @(\$before.bindings) }
    if (@(\$beforeBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Refusing to reuse a pre-existing Token Creator grant.' }
    gcloud iam service-accounts add-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet
    if (\$LASTEXITCODE -ne 0) { throw 'Temporary telemetry Token Creator grant failed.' }
    \$grantAdded = \$true
    Wait-ForRelayToken -Audience '$AUDIENCE' -TargetPrincipal \$targetPrincipal
    Write-Output 'READY: temporary telemetry identity token path is verified. Leave this terminal open.'
    \$jobId = Read-Host 'After the fresh WSL observation, enter its numeric scheduler job ID'
    if (\$jobId -notmatch '^[1-9][0-9]*\$') { throw 'Scheduler job ID must be positive.' }
    \$observationPath = Join-Path 'work\\live-bridge' ('active-review-job-' + \$jobId + '-initial-observation.json')
    if (-not (Test-Path -LiteralPath \$observationPath -PathType Leaf)) { throw 'Fresh local observation file is absent.' }
    \$publish = Read-Host 'Type PUBLISH-INITIAL-OBSERVATION to publish exactly that fresh read-only file'
    if (\$publish -ne 'PUBLISH-INITIAL-OBSERVATION') { throw 'Initial observation publication was not confirmed.' }
    uv run braille-relay publish-site-observation --service-url '$SERVICE_URL' --audience '$AUDIENCE' --impersonate-service-account \$targetPrincipal --observation \$observationPath
    if (\$LASTEXITCODE -ne 0) { throw 'Telemetry observation publication did not complete.' }
  }
  finally {
    \$removeExit = 0
    if (\$grantAdded) { gcloud iam service-accounts remove-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet; \$removeExit = \$LASTEXITCODE }
    \$after = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$afterBindings = if (\$null -eq \$after.PSObject.Properties['bindings']) { @() } else { @(\$after.bindings) }
    if (@(\$afterBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Temporary Token Creator grant remains.' }
    if (\$removeExit -ne 0) { throw 'Temporary Token Creator cleanup failed.' }
    Write-Output 'PASS: temporary telemetry Token Creator grant removed.'
  }
EOF
confirm "INITIAL-TELEMETRY-READY" "the separate Windows terminal reported READY and is waiting to publish one fresh observation"

cat <<EOF
Step 4 — submit the exact V1 BRF through the independent CUPS operator identity.
This is a human CUPS operation; the runbook does not execute it.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  sudo -u $OPERATOR -- lp -d $QUEUE -o raw -t '$TITLE' demo/expected/v1.brf

Copy only the numeric scheduler job ID from CUPS' response into the next prompt.
EOF
read -r -p "Human-entered new CUPS scheduler job ID: " NEW_JOB_ID
require_positive_integer "$NEW_JOB_ID" "new scheduler job ID"
confirm "BASELINE-SUBMITTED" "the independent relay-operator submitted the exact V1 BRF"

cat <<EOF
Step 5 — take one fresh read-only queue observation while the simulator is still
processing. The observer identity cannot submit, hold, release, or cancel jobs.
This continues the admitted canonical journal; it never reuses an earlier
unadmitted active-review journal.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  test ! -e $CANONICAL_BRIDGE_WORK_ROOT/active-review-job-$NEW_JOB_ID-initial-observation.json || { echo 'BLOCKED: initial observation output path already exists'; exit 1; }
  sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3 -m relay_bridge.main observe-once --server localhost:631 --queue '$QUEUE' --site-id '$SITE_ID' --bridge-id '$BRIDGE_ID' --user '$OBSERVER' --journal $CANONICAL_BRIDGE_JOURNAL --require-job-id '$NEW_JOB_ID' --output $CANONICAL_BRIDGE_WORK_ROOT/active-review-job-$NEW_JOB_ID-initial-observation.json

Return to the waiting Windows terminal. Enter job ID $NEW_JOB_ID, then type
PUBLISH-INITIAL-OBSERVATION. Continue only if the CLI prints status ACCEPTED.
If it prints BLOCKED or REJECTED, do not acknowledge the outbox, append a
production link, edit Drive, or use CUPS for this attempted run.
EOF
confirm "INITIAL-OBSERVATION-PUBLISHED" "the exact fresh observation was accepted by Cloud Run"

cat <<EOF
Step 6 — acknowledge only the observation Cloud Run accepted. This advances the
local outbox; it is not a queue action.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3 -m relay_bridge.main acknowledge-published --journal $CANONICAL_BRIDGE_JOURNAL --observation-id ACCEPTED_OBSERVATION_SHA256
EOF
confirm "ACTIVE-OBSERVED" "the human acknowledged the exact accepted read-only observation"

cat <<EOF
Step 7 — append an advisory superseding link. This changes only the cloud
evidence ledger; it never changes CUPS. The old link remains immutable history.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed; the current Windows PowerShell
  # terminal is also supported.
  \$ErrorActionPreference = 'Stop'
  Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'
  \$humanPrincipal = '$HUMAN_PRINCIPAL'
  \$targetPrincipal = '$DEMONSTRATOR_IDENTITY'
  \$member = 'user:' + \$humanPrincipal
  \$grantAdded = \$false
  function Wait-ForRelayToken {
    param([string]\$Audience, [string]\$TargetPrincipal)
    \$tokenExitCode = 1
    for (\$attempt = 1; \$attempt -le 24; \$attempt++) {
      Start-Sleep -Seconds 5
      \$previousPreference = \$ErrorActionPreference
      \$ErrorActionPreference = 'Continue'
      try {
        uv run python -c "from braille_errata_relay.cli import _identity_token; _identity_token(audience='\$Audience', impersonate_service_account='\$TargetPrincipal')" *> \$null
        \$tokenExitCode = \$LASTEXITCODE
      }
      finally {
        \$ErrorActionPreference = \$previousPreference
      }
      if (\$tokenExitCode -eq 0) { return }
    }
    throw 'Local user ADC did not gain the temporary Token Creator permission before the bounded wait expired.'
  }
  try {
    if ((gcloud config get-value account).Trim() -ne \$humanPrincipal) { throw 'Active gcloud account does not match the named human principal.' }
    \$before = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$beforeBindings = if (\$null -eq \$before.PSObject.Properties['bindings']) { @() } else { @(\$before.bindings) }
    if (@(\$beforeBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Refusing to reuse a pre-existing Token Creator grant.' }
    gcloud iam service-accounts add-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet
    if (\$LASTEXITCODE -ne 0) { throw 'Temporary demonstrator Token Creator grant failed.' }
    \$grantAdded = \$true
    Wait-ForRelayToken -Audience '$AUDIENCE' -TargetPrincipal \$targetPrincipal
    uv run braille-relay supersede-baseline-production --service-url '$SERVICE_URL' --audience '$AUDIENCE' --impersonate-service-account \$targetPrincipal --baseline-id '$BASELINE_ID' --supersedes-production-link-id '$SUPERSEDES_LINK_ID' --scheduler-job-id '$NEW_JOB_ID' --expected-state-version '$BASELINE_STATE_VERSION'
    if (\$LASTEXITCODE -ne 0) { throw 'Advisory production-link supersession did not complete.' }
  }
  finally {
    \$removeExit = 0
    if (\$grantAdded) { gcloud iam service-accounts remove-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet; \$removeExit = \$LASTEXITCODE }
    \$after = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$afterBindings = if (\$null -eq \$after.PSObject.Properties['bindings']) { @() } else { @(\$after.bindings) }
    if (@(\$afterBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Temporary Token Creator grant remains.' }
    if (\$removeExit -ne 0) { throw 'Temporary Token Creator cleanup failed.' }
    Write-Output 'PASS: temporary demonstrator Token Creator grant removed.'
  }

Copy the returned new link ID and baseline_state_version. The new link remains
only advisory until exact received-byte evidence is accepted.
EOF
read -r -p "New production link ID from the append-only response: " NEW_LINK_ID
require_sha256 "$NEW_LINK_ID" "new production link ID"
read -r -p "New baseline state version from the append-only response: " NEW_STATE_VERSION
require_positive_integer "$NEW_STATE_VERSION" "new baseline state version"
confirm "SUPERSESSION-APPENDED" "the human saw the new advisory link and preserved the old link"

cat <<EOF
Step 8 — confirm the separate immutable active acceptance record before the
simulator reaches a terminal manifest. The PowerShell helper crosses to WSL only
as $ENDPOINT_AUDITOR; it cannot control CUPS or read the spool.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed; the current Windows PowerShell
  # terminal is also supported.
  Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'
  & .\infra\gcp\confirm_local_endpoint_receipt.ps1 -BaselineId '$BASELINE_ID' -ProductionLinkId '$NEW_LINK_ID' -SchedulerJobId '$NEW_JOB_ID' -ExpectedJobTitle '$TITLE' -ApprovedBrfSha256 '$V1_SHA256' -CurrentStateVersion '$NEW_STATE_VERSION'

Do not infer completion from this accepted RECEIVED fact. The terminal manifest
remains separate evidence when the simulated endpoint later completes.
EOF
confirm "ACTIVE-RECEIPT-CONFIRMED" "the exact active received-byte receipt was accepted"

cat <<'EOF'
Step 9 — edit the same watched Drive file from V1 to V2 in the Drive browser.
Then deliberately invoke the existing guarded source read and one bounded
scheduler run. Pause the Scheduler again after the bounded run.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed; the current Windows PowerShell
  # terminal is also supported.
  $RepoRoot = (git rev-parse --show-toplevel).Trim()
  Set-Location -LiteralPath $RepoRoot
  & .\infra\gcp\reconcile_live_drive.ps1 -Operation RECONCILE -ExecuteDriveRead
  & .\infra\gcp\run_single_scheduler_closure.ps1 -ExecuteSingleRun

Verify the local review screen shows exactly one report-bearing incident and a
visible blocking reason, if applicable.
EOF
confirm "REPORT-READY" "one report-bearing incident was visible in the review surface"

cat <<EOF
Step 10 — start the loopback review shell with no-key impersonated private API
authentication. Leave this terminal open through the human disposition and
operator attestation steps. The finally block removes the temporary grant even
if the shell exits with an error, then verifies that no grant remains for this
human and target service account.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed; the current Windows PowerShell
  # terminal is also supported.
  \$ErrorActionPreference = 'Stop'
  Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'
  \$humanPrincipal = '$HUMAN_PRINCIPAL'
  \$targetPrincipal = '$DEMONSTRATOR_IDENTITY'
  \$member = 'user:' + \$humanPrincipal
  \$grantAdded = \$false
  function Wait-ForRelayToken {
    param([string]\$Audience, [string]\$TargetPrincipal)
    \$tokenExitCode = 1
    for (\$attempt = 1; \$attempt -le 24; \$attempt++) {
      Start-Sleep -Seconds 5
      \$previousPreference = \$ErrorActionPreference
      \$ErrorActionPreference = 'Continue'
      try {
        uv run python -c "from braille_errata_relay.cli import _identity_token; _identity_token(audience='\$Audience', impersonate_service_account='\$TargetPrincipal')" *> \$null
        \$tokenExitCode = \$LASTEXITCODE
      }
      finally {
        \$ErrorActionPreference = \$previousPreference
      }
      if (\$tokenExitCode -eq 0) { return }
    }
    throw 'Local user ADC did not gain the temporary Token Creator permission before the bounded wait expired.'
  }
  try {
    if ((gcloud config get-value account).Trim() -ne \$humanPrincipal) { throw 'Active gcloud account does not match the named human principal.' }
    \$before = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$beforeBindings = if (\$null -eq \$before.PSObject.Properties['bindings']) { @() } else { @(\$before.bindings) }
    if (@(\$beforeBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Refusing to reuse a pre-existing Token Creator grant.' }
    gcloud iam service-accounts add-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet
    if (\$LASTEXITCODE -ne 0) { throw 'Temporary demonstrator Token Creator grant failed.' }
    \$grantAdded = \$true
    Wait-ForRelayToken -Audience '$AUDIENCE' -TargetPrincipal \$targetPrincipal
    \$env:RELAY_PRESENTATION_API_URL = '$SERVICE_URL'
    \$env:RELAY_PRESENTATION_AUDIENCE = '$AUDIENCE'
    \$env:RELAY_PRESENTATION_IMPERSONATE_SERVICE_ACCOUNT = \$targetPrincipal
    \$env:RELAY_PRESENTATION_SESSION_SECRET = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]\$PSItem })
    uv run python -m braille_errata_relay.presentation.app
  }
  finally {
    \$removeExit = 0
    if (\$grantAdded) { gcloud iam service-accounts remove-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet; \$removeExit = \$LASTEXITCODE }
    \$after = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$afterBindings = if (\$null -eq \$after.PSObject.Properties['bindings']) { @() } else { @(\$after.bindings) }
    if (@(\$afterBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Temporary Token Creator grant remains.' }
    if (\$removeExit -ne 0) { throw 'Temporary Token Creator cleanup failed.' }
    Write-Output 'PASS: temporary Token Creator grant removed.'
  }

Open http://127.0.0.1:8765. Choose production_coordinator and record
HALT_REQUESTED. That creates only a human disposition record; it does not
operate CUPS.
EOF
confirm "HALT-RECORDED" "the coordinator recorded HALT_REQUESTED in the review shell"

cat <<EOF
Step 11 — prepare a second temporary telemetry publisher before the human
cancellation. This keeps identity propagation outside the later observation's
freshness window. Run it in a separate Windows terminal and leave it at its
explicit publish prompt.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed, but the already-open Windows
  # PowerShell terminal works too. Do not type pwsh.exe inside that terminal.
  \$ErrorActionPreference = 'Stop'
  Set-Location -LiteralPath '$WINDOWS_REPO_ROOT'
  \$humanPrincipal = '$HUMAN_PRINCIPAL'
  \$targetPrincipal = '$TELEMETRY_IDENTITY'
  \$member = 'user:' + \$humanPrincipal
  \$grantAdded = \$false
  function Wait-ForRelayToken {
    param([string]\$Audience, [string]\$TargetPrincipal)
    \$tokenExitCode = 1
    for (\$attempt = 1; \$attempt -le 24; \$attempt++) {
      Start-Sleep -Seconds 5
      \$previousPreference = \$ErrorActionPreference
      \$ErrorActionPreference = 'Continue'
      try {
        uv run python -c "from braille_errata_relay.cli import _identity_token; _identity_token(audience='\$Audience', impersonate_service_account='\$TargetPrincipal')" *> \$null
        \$tokenExitCode = \$LASTEXITCODE
      }
      finally {
        \$ErrorActionPreference = \$previousPreference
      }
      if (\$tokenExitCode -eq 0) { return }
    }
    throw 'Local user ADC did not gain the temporary Token Creator permission before the bounded wait expired.'
  }
  try {
    if ((gcloud config get-value account).Trim() -ne \$humanPrincipal) { throw 'Active gcloud account does not match the named human principal.' }
    \$before = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$beforeBindings = if (\$null -eq \$before.PSObject.Properties['bindings']) { @() } else { @(\$before.bindings) }
    if (@(\$beforeBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Refusing to reuse a pre-existing Token Creator grant.' }
    gcloud iam service-accounts add-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet
    if (\$LASTEXITCODE -ne 0) { throw 'Temporary telemetry Token Creator grant failed.' }
    \$grantAdded = \$true
    Wait-ForRelayToken -Audience '$AUDIENCE' -TargetPrincipal \$targetPrincipal
    Write-Output 'READY: temporary telemetry identity token path is verified. Leave this terminal open.'
    \$publish = Read-Host 'After the later WSL observation, type PUBLISH-LATER-OBSERVATION'
    if (\$publish -ne 'PUBLISH-LATER-OBSERVATION') { throw 'Later observation publication was not confirmed.' }
    \$observationPath = Join-Path 'work\\live-bridge' 'active-review-job-$NEW_JOB_ID-later-observation.json'
    if (-not (Test-Path -LiteralPath \$observationPath -PathType Leaf)) { throw 'Later local observation file is absent.' }
    uv run braille-relay publish-site-observation --service-url '$SERVICE_URL' --audience '$AUDIENCE' --impersonate-service-account \$targetPrincipal --observation \$observationPath
    if (\$LASTEXITCODE -ne 0) { throw 'Later telemetry observation publication did not complete.' }
  }
  finally {
    \$removeExit = 0
    if (\$grantAdded) { gcloud iam service-accounts remove-iam-policy-binding \$targetPrincipal --member=\$member --role='roles/iam.serviceAccountTokenCreator' --condition=None --quiet; \$removeExit = \$LASTEXITCODE }
    \$after = gcloud iam service-accounts get-iam-policy \$targetPrincipal --format=json | ConvertFrom-Json
    \$afterBindings = if (\$null -eq \$after.PSObject.Properties['bindings']) { @() } else { @(\$after.bindings) }
    if (@(\$afterBindings | Where-Object { \$PSItem.role -eq 'roles/iam.serviceAccountTokenCreator' -and @(\$PSItem.members) -contains \$member }).Count -ne 0) { throw 'Temporary Token Creator grant remains.' }
    if (\$removeExit -ne 0) { throw 'Temporary Token Creator cleanup failed.' }
    Write-Output 'PASS: temporary telemetry Token Creator grant removed.'
  }
EOF
confirm "LATER-TELEMETRY-READY" "the separate Windows terminal reported READY and is waiting to publish the later observation"

cat <<EOF
Step 12 — visibly switch to the independent CUPS operator terminal or UI. The
Relay shell cannot do this for you. Cancel only the exact job after independently
checking its ID and title.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  sudo -u $OPERATOR -- cancel $QUEUE-$NEW_JOB_ID
EOF
confirm "QUEUE-CANCELED" "the relay-operator canceled only the exact simulator job"

cat <<EOF
Step 13 — take one later read-only observation. This scheduler observation is
distinct from the human cancellation action and continues the canonical journal.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  test ! -e $CANONICAL_BRIDGE_WORK_ROOT/active-review-job-$NEW_JOB_ID-later-observation.json || { echo 'BLOCKED: later observation output path already exists'; exit 1; }
  sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3 -m relay_bridge.main observe-once --server localhost:631 --queue '$QUEUE' --site-id '$SITE_ID' --bridge-id '$BRIDGE_ID' --user '$OBSERVER' --journal $CANONICAL_BRIDGE_JOURNAL --require-job-id '$NEW_JOB_ID' --output $CANONICAL_BRIDGE_WORK_ROOT/active-review-job-$NEW_JOB_ID-later-observation.json

Return to the waiting Windows terminal and type PUBLISH-LATER-OBSERVATION.
Continue only if the CLI prints status ACCEPTED. If it prints BLOCKED or
REJECTED, do not acknowledge the outbox or represent cancellation as observed.
EOF
confirm "LATER-OBSERVATION-PUBLISHED" "the later read-only observation was accepted by Cloud Run"

cat <<EOF
Step 14 — acknowledge only the later observation Cloud Run accepted.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  sudo -u $OBSERVER -- env PYTHONPATH=local_bridge/src python3 -m relay_bridge.main acknowledge-published --journal $CANONICAL_BRIDGE_JOURNAL --observation-id LATER_ACCEPTED_OBSERVATION_SHA256
EOF
confirm "CANCELLATION-OBSERVED" "the exact accepted later observation recorded the scheduler state"

cat <<'EOF'
Step 15 — in the still-open loopback review shell, choose machine_operator and
record a simulated-demo operator attestation. Select only a fact the human can
personally attest. A queue cancellation alone must not be represented as device
stop or physical output isolation.
EOF
confirm "ATTESTATION-RECORDED" "the operator recorded a clearly labeled simulated-demo attestation"

cat <<EOF
Step 16 — restore the temporary simulator timing profile. Run this even if an
earlier walkthrough step failed.

WSL Ubuntu-24.04:
  cd $WSL_REPO_ROOT
  if sudo test -f $CAPTURE_BACKUP; then
    sudo install -o root -g lp -m 0640 $CAPTURE_BACKUP $CAPTURE_CONFIG
    sudo /usr/lib/cups/backend/relay-capture --validate-runtime-config $CAPTURE_CONFIG
    sudo systemctl restart cups
    sudo rm -f $CAPTURE_BACKUP
  elif sudo cmp -s config/cups/relay-capture.conf $CAPTURE_CONFIG; then
    echo 'PASS: committed default timing configuration is already installed; no active-review backup was needed.'
  else
    echo 'BLOCKED: active-review timing backup is missing and installed timing differs from the committed default.'
    echo 'After independently reviewing pending CUPS jobs, run: sudo bash infra/wsl/setup_cups_gate0.sh'
    exit 1
  fi

Close the loopback review shell with Ctrl+C. Its Windows PowerShell finally
block removes the temporary Token Creator grant and verifies its absence.
EOF
confirm "SIMULATOR-RESTORED" "the human restored the simulator timing profile"

cat <<'EOF'
Step 17 — after the review-shell terminal reports its cleanup result, verify
the scheduler remains paused and Cloud Run has no public invoker binding.

Windows PowerShell (5.1 or 7):
  # Prefer PowerShell 7 when it is installed; the current Windows PowerShell
  # terminal is also supported.
  $ErrorActionPreference = 'Stop'
  $RepoRoot = (git rev-parse --show-toplevel).Trim()
  Set-Location -LiteralPath $RepoRoot
  $project = (gcloud config get-value project).Trim()
  $scheduler = gcloud scheduler jobs describe relay-outbox-drain --location europe-west3 --project $project --format=json | ConvertFrom-Json
  if ($scheduler.state -ne 'PAUSED') { throw 'Outbox Scheduler is not paused.' }
  $policy = gcloud run services get-iam-policy braille-errata-relay --region europe-west3 --project $project --format=json | ConvertFrom-Json
  $bindings = if ($null -eq $policy.PSObject.Properties['bindings']) { @() } else { @($policy.bindings) }
  if (@($bindings | Where-Object { @($PSItem.members) -contains 'allUsers' -or @($PSItem.members) -contains 'allAuthenticatedUsers' }).Count -ne 0) { throw 'Cloud Run has a public invoker binding.' }
  Write-Output 'PASS: Scheduler is paused and Cloud Run remains private.'

Only after independently reviewing all facts, hashes, states, timestamps, and
principals should a human create sanitized live evidence. This harness creates
no evidence itself and never claims proof, replacement, notification, or closure.
EOF
confirm "CLOUD-BOUNDARIES-VERIFIED" "the human verified private Cloud Run and the paused Scheduler"

echo "PASS: human-run active professional-review sequence completed without Relay CUPS, Drive, IAM, or device control"
