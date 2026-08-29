#!/usr/bin/env bash
# Print-only, human-operated Story 3 demonstration harness.
#
# This harness does not submit, hold, release, cancel, restart, or otherwise
# control CUPS. It also does not change Drive, invoke Cloud Run, or write
# evidence. It prints exact, bounded commands and pauses for the person who is
# authorized to perform each separate action.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
QUEUE="Braille-Embosser-Sim"
SITE_ID="demo-site"
BRIDGE_ID="single-pc-bridge"
CAPTURE_CONFIG="/etc/cups/relay-capture.conf"
CAPTURE_BACKUP="/etc/cups/relay-capture.conf.active-professional-review.bak"

BASELINE_ID=""
SUPERSEDES_LINK_ID=""
BASELINE_STATE_VERSION=""
SERVICE_URL=""
AUDIENCE=""
TELEMETRY_IDENTITY=""
DEMONSTRATOR_IDENTITY=""

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
    --demonstrator-identity DEMONSTRATOR_SERVICE_ACCOUNT

The script is a print-only runbook. It never executes a human CUPS action,
Drive action, Cloud Run mutation, or timing-profile change. It only computes
the committed fixture hash, prints safe commands, and waits for explicit human
confirmations.
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-id) BASELINE_ID="${2:-}"; shift 2 ;;
    --supersedes-production-link-id) SUPERSEDES_LINK_ID="${2:-}"; shift 2 ;;
    --baseline-state-version) BASELINE_STATE_VERSION="${2:-}"; shift 2 ;;
    --service-url) SERVICE_URL="${2:-}"; shift 2 ;;
    --audience) AUDIENCE="${2:-}"; shift 2 ;;
    --telemetry-identity) TELEMETRY_IDENTITY="${2:-}"; shift 2 ;;
    --demonstrator-identity) DEMONSTRATOR_IDENTITY="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

require_sha256 "$BASELINE_ID" "baseline ID"
require_sha256 "$SUPERSEDES_LINK_ID" "superseded production link ID"
require_positive_integer "$BASELINE_STATE_VERSION" "baseline state version"
[[ "$SERVICE_URL" =~ ^https://[^[:space:]]+$ ]] || fail "service URL must be an HTTPS URL"
[[ "$AUDIENCE" =~ ^https://[^[:space:]]+$ ]] || fail "audience must be an HTTPS URL"
[[ "$TELEMETRY_IDENTITY" =~ ^[^[:space:]]+@[^[:space:]]+$ ]] || fail "telemetry identity is invalid"
[[ "$DEMONSTRATOR_IDENTITY" =~ ^[^[:space:]]+@[^[:space:]]+$ ]] || fail "demonstrator identity is invalid"

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

Step 1 — in the Drive browser, restore the watched same-file fixture to V1.
The relay must observe a real revision later; do not use a copied file.
EOF
confirm "DRIVE-V1-RESTORED" "the Drive fixture was visibly restored to V1"

cat <<EOF
Step 2 — in a separate WSL terminal, temporarily slow only the simulated endpoint.
Run these commands yourself; retain the backup until the final restore step:

  sudo cp -p $CAPTURE_CONFIG $CAPTURE_BACKUP
  printf 'page_delay_seconds=60\\n' | sudo tee $CAPTURE_CONFIG >/dev/null
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
Step 3 — in an independent relay-operator CUPS shell, submit the exact V1 BRF.
This is a human CUPS operation. Run it yourself:

  cd $ROOT
  lp -d $QUEUE -o raw -t '$TITLE' demo/expected/v1.brf

Copy only the numeric scheduler job ID from CUPS' response into the next prompt.
EOF
read -r -p "Human-entered new CUPS scheduler job ID: " NEW_JOB_ID
require_positive_integer "$NEW_JOB_ID" "new scheduler job ID"
confirm "BASELINE-SUBMITTED" "the independent operator submitted the exact V1 BRF"

cat <<EOF
Step 4 — in WSL, capture a fresh *read-only* observation while the simulator is still processing:

  cd $ROOT
  PYTHONPATH=local_bridge/src python3 -m relay_bridge.main observe-once \\
    --server localhost:631 --queue '$QUEUE' --site-id '$SITE_ID' --bridge-id '$BRIDGE_ID' \\
    --journal work/active-review/journal.sqlite3 --require-job-id '$NEW_JOB_ID' \\
    --output work/active-review/active-observation.json

Step 5 — in the Windows terminal, publish that observation using only the telemetry identity:

  cd $ROOT
  braille-relay publish-site-observation \\
    --service-url '$SERVICE_URL' --audience '$AUDIENCE' \\
    --impersonate-service-account '$TELEMETRY_IDENTITY' \\
    --observation work/active-review/active-observation.json

After Cloud acceptance, in WSL acknowledge the durable local outbox by copying
the observation ID printed by the publish command:

  PYTHONPATH=local_bridge/src python3 -m relay_bridge.main acknowledge-published \\
    --journal work/active-review/journal.sqlite3 --observation-id OBSERVATION_SHA256
EOF
confirm "ACTIVE-OBSERVED" "the human published and acknowledged one exact read-only observation"

cat <<EOF
Step 6 — in the Windows terminal, append an advisory superseding link. This
changes only the cloud evidence ledger; it never changes CUPS:

  cd $ROOT
  braille-relay supersede-baseline-production \\
    --service-url '$SERVICE_URL' --audience '$AUDIENCE' \\
    --impersonate-service-account '$DEMONSTRATOR_IDENTITY' \\
    --baseline-id '$BASELINE_ID' \\
    --supersedes-production-link-id '$SUPERSEDES_LINK_ID' \\
    --scheduler-job-id '$NEW_JOB_ID' \\
    --expected-state-version '$BASELINE_STATE_VERSION'

Copy the returned new link ID and baseline_state_version. The old link remains
immutable history; the new link is still only advisory until exact received-byte
evidence is accepted.
EOF
read -r -p "New production link ID from the append-only response: " NEW_LINK_ID
require_sha256 "$NEW_LINK_ID" "new production link ID"
read -r -p "New baseline state version from the append-only response: " NEW_STATE_VERSION
require_positive_integer "$NEW_STATE_VERSION" "new baseline state version"
confirm "SUPERSESSION-APPENDED" "the human saw the new advisory link and preserved the old link"

cat <<EOF
Step 7 — in the Windows terminal, confirm the separate active acceptance record
before the simulator reaches a terminal manifest. This uses only the dedicated
endpoint-evidence identity and the fixed-root read-only auditor:

  cd $ROOT
  powershell -NoProfile -ExecutionPolicy Bypass -File .\\infra\\gcp\\confirm_local_endpoint_receipt.ps1 \\
    -BaselineId '$BASELINE_ID' -ProductionLinkId '$NEW_LINK_ID' \\
    -SchedulerJobId '$NEW_JOB_ID' -ExpectedJobTitle '$TITLE' \\
    -ApprovedBrfSha256 '$V1_SHA256' -CurrentStateVersion '$NEW_STATE_VERSION'

Do not infer completion from this accepted RECEIVED fact. The terminal manifest
will remain separate evidence when the simulated endpoint later completes.
EOF
confirm "ACTIVE-RECEIPT-CONFIRMED" "the exact active received-byte receipt was accepted"

cat <<'EOF'
Step 8 — in the Drive browser, edit that same watched file from V1 to V2.
Then, in the Windows terminal, deliberately invoke the existing guarded source
read and one bounded scheduler run yourself:

  powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gcp\reconcile_live_drive.ps1 \
    -Operation RECONCILE -ExecuteDriveRead
  powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\gcp\run_single_scheduler_closure.ps1 \
    -ExecuteSingleRun

Pause the Scheduler again after the bounded run. Verify the dashboard shows one
report-bearing incident and a visible blocking reason, if applicable.
EOF
confirm "REPORT-READY" "one report-bearing incident was visible in the review surface"

cat <<'EOF'
Step 9 — run the local presentation shell from Windows. It binds only 127.0.0.1
and keeps private Cloud Run credentials server-side. Set the values in your
current terminal only; never save the session secret in the repository:

  $env:RELAY_PRESENTATION_API_URL = 'https://PRIVATE_CLOUD_RUN_URL'
  $env:RELAY_PRESENTATION_AUDIENCE = 'https://PRIVATE_CLOUD_RUN_AUDIENCE'
  $env:RELAY_PRESENTATION_SESSION_SECRET = '<new-random-32-plus-character-value>'
  python -m braille_errata_relay.presentation.app

Open http://127.0.0.1:8765, choose production_coordinator, and record
HALT_REQUESTED. That creates only a human disposition record.
EOF
confirm "HALT-RECORDED" "the coordinator recorded HALT_REQUESTED in the review shell"

cat <<EOF
Step 10 — visibly switch to the independent CUPS operator terminal or UI. The
Relay shell cannot do this for you. The human operator may cancel only the exact
job after independently checking the job ID and title:

  cancel $QUEUE-$NEW_JOB_ID

Then take and publish a second read-only bridge observation using a new output
file, confirm it shows the later scheduler state, and acknowledge its local
outbox entry as in Steps 4–5.
EOF
confirm "QUEUE-ACTION-OBSERVED" "the human action and later read-only scheduler observation were separate facts"

cat <<'EOF'
Step 11 — in the review shell choose machine_operator and record a simulated-demo
operator attestation. Select only a fact the human can personally attest. A
queue cancellation alone must not be represented as device stop or physical
output isolation.
EOF
confirm "ATTESTATION-RECORDED" "the operator recorded a clearly labeled human attestation"

cat <<EOF
Step 12 — restore the temporary simulator timing profile in a separate WSL
terminal. Run these commands yourself even if an earlier demo step failed:

  sudo test -f $CAPTURE_BACKUP
  sudo install -o root -g lp -m 0640 $CAPTURE_BACKUP $CAPTURE_CONFIG
  sudo /usr/lib/cups/backend/relay-capture --validate-runtime-config $CAPTURE_CONFIG
  sudo systemctl restart cups
  sudo rm -f $CAPTURE_BACKUP

Only after verifying all timeline facts and hashes should a human create the
sanitized active-professional-review evidence artifact. This harness creates no
evidence itself and never claims proof, replacement, notification, or closure.
EOF
confirm "SIMULATOR-RESTORED" "the human restored the simulator timing profile"

echo "PASS: human-run active professional-review sequence completed without Relay CUPS or Drive control"
