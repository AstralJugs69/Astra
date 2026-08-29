#!/usr/bin/env bash
# Human-operated local Gate 0 exercise for the fixed WSL CUPS simulator.
#
# This is neither a Relay endpoint nor a bridge command surface. A human runs
# it locally and explicitly confirms each CUPS mutation through the separate
# relay-operator identity. It never accepts, stores, or prints passwords.
set -euo pipefail

umask 077

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
QUEUE="Braille-Embosser-Sim"
DEVICE_URI="relay-capture://demo-embosser"
CAPTURE_ROOT="/var/lib/braille-relay/captures"
INSTALLED_BACKEND="/usr/lib/cups/backend/relay-capture"
OPERATOR="relay-operator"
OBSERVER="relay-observer"
ENDPOINT_AUDITOR="relay-endpoint-auditor"
CANDIDATE="$ROOT/demo/expected/v1.brf"
LIFECYCLE_CANDIDATE=""
EVIDENCE="$ROOT/demo/evidence/gate0-local-floor.json"
TEMP_ROOT=""
RESUME_TERMINAL_JOB_ID=""
RESUME_TERMINATED_JOB_ID=""
RESUME_HELD_JOB_ID=""
RESUME_OPEN_JOB_ID=""

usage() {
  cat <<'EOF'
Usage:
  bash infra/wsl/run_gate0_local_floor.sh
  bash infra/wsl/run_gate0_local_floor.sh --resume-captures TERMINAL_JOB_ID TERMINATED_JOB_ID
  bash infra/wsl/run_gate0_local_floor.sh --resume-captures TERMINAL_JOB_ID TERMINATED_JOB_ID --resume-auth-probes HELD_JOB_ID OPEN_JOB_ID

Run this only from a normal local human WSL account after
setup_cups_gate0.sh has configured the fixed relay-capture simulator queue.
The script prompts locally for sudo and CUPS credentials as needed. It does
not accept credentials in arguments, environment variables, or files.

The human operator explicitly confirms local test-job submission, release,
and cancellation. The script validates exact bytes, capture evidence, and the
observer authorization floor before writing sanitized local evidence.

The resume form accepts only explicit CUPS job IDs. It revalidates both
captures from authoritative bytes and manifests, then continues with fresh
authorization probes. It never infers lineage from queue contents.

The extended resume form also reuses explicit, independently created held and
empty authorization-probe jobs after a verifier interruption. It does not
discover or select jobs from queue contents.
EOF
}

fail() {
  echo "BLOCKED: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_ROOT" && "$TEMP_ROOT" == /tmp/braille-relay-gate0.* && -d "$TEMP_ROOT" ]]; then
    rm -rf -- "$TEMP_ROOT"
  fi
}
trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null || fail "required command is missing: $1"
}

confirm() {
  local phrase="$1"
  local purpose="$2"
  local answer=""
  echo "HUMAN OPERATOR ACTION: $purpose"
  read -r -p "Type $phrase to continue: " answer
  [[ "$answer" == "$phrase" ]] || fail "human authorization was not provided"
}

operator() {
  sudo -iu "$OPERATOR" -- "$@"
}

endpoint_auditor() {
  sudo -iu "$ENDPOINT_AUDITOR" -- "$@"
}

cleanup_probe_job() {
  local job_id="$1"
  local output=""
  if output="$(operator cancel "$QUEUE-$job_id" 2>&1)"; then
    echo "PASS: human operator canceled local authorization probe job $job_id"
    return
  fi
  if grep -Eqi "already (aborted|canceled|cancelled|completed)|completed and cannot be changed" <<< "$output"; then
    echo "PASS: local authorization probe job $job_id was already terminal"
    return
  fi
  printf '%s\n' "$output" >&2
  fail "human operator could not clean up local authorization probe job $job_id"
}

parse_lp_job_id() {
  local output="$1"
  if [[ "$output" =~ request[[:space:]]id[[:space:]]is[[:space:]]${QUEUE}-([1-9][0-9]*) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return
  fi
  fail "CUPS did not return a numeric job ID for the fixed simulator queue"
}

parse_open_job_id() {
  local output="$1"
  if [[ "$output" =~ job[[:space:]]ID[[:space:]]([1-9][0-9]*) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return
  fi
  fail "CUPS did not return a numeric open Send-Document probe job ID"
}

schedule_or_accept_terminal() {
  local job_id="$1"
  local release_output=""
  local active_jobs=""
  if release_output="$(operator lp -i "$QUEUE-$job_id" -H immediate 2>&1)"; then
    return
  fi
  if grep -Fqi "job is completed and cannot be changed" <<< "$release_output"; then
    echo "INFO: local simulator job $job_id completed before immediate release"
    return
  fi
  active_jobs="$(operator lpstat -W not-completed -o "$QUEUE" 2>&1)" || {
    printf '%s\n' "$active_jobs" >&2
    fail "could not observe the fixed simulator queue as relay-operator"
  }
  if grep -Eq "^${QUEUE}-${job_id}[[:space:]]" <<< "$active_jobs"; then
    printf '%s\n' "$release_output" >&2
    fail "human operator could not release local simulator job $job_id"
  fi
  echo "INFO: local simulator job $job_id reached a terminal scheduler state before immediate release"
}

wait_until_terminal() {
  local job_id="$1"
  local active_jobs=""
  for _attempt in $(seq 1 90); do
    active_jobs="$(operator lpstat -W not-completed -o "$QUEUE" 2>&1)" || {
      printf '%s\n' "$active_jobs" >&2
      fail "could not observe the fixed simulator queue as relay-operator"
    }
    if ! grep -Eq "^${QUEUE}-${job_id}[[:space:]]" <<< "$active_jobs"; then
      return
    fi
    sleep 1
  done
  fail "local simulator job $job_id did not reach a terminal scheduler state within 90 seconds"
}

wait_for_capture_input() {
  local job_id="$1"
  for _attempt in $(seq 1 30); do
    if sudo test -f "$CAPTURE_ROOT/$job_id/input.brf"; then
      return
    fi
    sleep 1
  done
  fail "local simulator did not receive job $job_id bytes within 30 seconds"
}
wait_for_capture_manifest() {
  local job_id="$1"
  for _attempt in $(seq 1 30); do
    if sudo test -f "$CAPTURE_ROOT/$job_id/manifest.json"; then
      return
    fi
    sleep 1
  done
  fail "local simulator did not publish a terminal manifest for job $job_id within 30 seconds"
}

verify_capture() {
  local job_id="$1"
  local expected_state="$2"
  local candidate="$3"
  local destination="$4"
  if ! endpoint_auditor python3 "$ROOT/infra/wsl/verify_capture_evidence.py" \
    --job-id "$job_id" \
    --candidate "$candidate" \
    --expected-state "$expected_state" > "$destination"; then
    [[ ! -s "$destination" ]] || cat "$destination" >&2
    fail "capture evidence verification failed for local simulator job $job_id"
  fi
  cat "$destination"
}

write_sanitized_evidence() {
  local completed_json="$1"
  local terminated_json="$2"
  python3 - "$completed_json" "$terminated_json" "$EVIDENCE" <<'PY'
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def capture_subset(value: object, expected_state: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("state") != expected_state:
        raise ValueError("capture verifier result is incomplete")
    required = (
        "candidate_sha256",
        "backend_received_sha256",
        "captured_output_sha256",
        "terminal_event_sha256",
        "pages_total",
        "pages_completed",
        "manifest_schema_valid",
        "event_chain_valid",
    )
    if any(name not in value for name in required):
        raise ValueError("capture verifier result is missing a safe evidence field")
    return {"state": expected_state, **{name: value[name] for name in required}}


completed = capture_subset(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), "COMPLETED")
terminated = capture_subset(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")), "TERMINATED")
if not isinstance(completed["captured_output_sha256"], str):
    raise ValueError("completed simulator evidence lacks an output hash")
if terminated["captured_output_sha256"] is not None:
    raise ValueError("terminated simulator evidence unexpectedly contains an output hash")

payload = {
    "schema_version": "gate0-local-floor-evidence.v1",
    "recorded_at": datetime.now(UTC).isoformat(),
    "queue": "Braille-Embosser-Sim",
    "simulated_endpoint": True,
    "fixture": "demo/expected/v1.brf",
    "full_capture": completed,
    "terminated_capture": terminated,
    "checks": {
        "operator_terminal_submission": "PASS",
        "full_capture_exact_byte_passthrough": "PASS",
        "operator_hold_release_cancel": "PASS",
        "terminated_capture_journal": "PASS",
        "observer_authorization_denials": "PASS",
        "observer_filesystem_isolation": "PASS",
    },
}
destination = Path(sys.argv[3])
destination.parent.mkdir(parents=True, exist_ok=True)
part = destination.with_name(destination.name + ".part")
with part.open("w", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(part, destination)
PY
}

if [[ $# -eq 6 && "$1" == "--resume-captures" && "$4" == "--resume-auth-probes" ]]; then
  RESUME_TERMINAL_JOB_ID="$2"
  RESUME_TERMINATED_JOB_ID="$3"
  RESUME_HELD_JOB_ID="$5"
  RESUME_OPEN_JOB_ID="$6"
elif [[ $# -eq 3 && "$1" == "--resume-captures" ]]; then
  RESUME_TERMINAL_JOB_ID="$2"
  RESUME_TERMINATED_JOB_ID="$3"
elif [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi
for resume_id in "$RESUME_TERMINAL_JOB_ID" "$RESUME_TERMINATED_JOB_ID" "$RESUME_HELD_JOB_ID" "$RESUME_OPEN_JOB_ID"; do
  [[ -z "$resume_id" || "$resume_id" =~ ^[1-9][0-9]*$ ]] || fail "resume job IDs must be positive"
done
[[ "$(id -u)" -ne 0 ]] || fail "run this from a normal local human account, not root"
[[ -f "$CANDIDATE" ]] || fail "fixed V1 candidate BRF is missing"

require_command lp
require_command lpstat
require_command cancel
require_command cmp
require_command python3
require_command sudo
require_command tee
id "$OPERATOR" >/dev/null || fail "$OPERATOR account is missing"
id "$OBSERVER" >/dev/null || fail "$OBSERVER account is missing"
[[ -r "$INSTALLED_BACKEND" ]] || fail "installed CUPS backend is missing"
cmp -s "$ROOT/simulator/cups_backend/relay_capture_backend.py" "$INSTALLED_BACKEND" || {
  fail "installed backend differs from this verified source; a human must run setup_cups_gate0.sh first"
}

TEMP_ROOT="$(mktemp -d /tmp/braille-relay-gate0.XXXXXX)"
chmod 0711 "$TEMP_ROOT"
LIFECYCLE_CANDIDATE="$TEMP_ROOT/lifecycle-candidate.brf"
python3 - "$CANDIDATE" "$LIFECYCLE_CANDIDATE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
Path(sys.argv[2]).write_bytes(b"\x0c".join((source,) * 12))
PY
chmod 0644 "$LIFECYCLE_CANDIDATE"

echo "INFO: this is a fixed local simulator exercise, never a Relay control surface"
sudo -v
device_line="$(sudo lpstat -v "$QUEUE")" || fail "fixed simulator queue is unavailable"
[[ "$device_line" == "device for $QUEUE: $DEVICE_URI" ]] || {
  printf '%s\n' "$device_line" >&2
  fail "queue does not point to the fixed simulated endpoint"
}
sudo bash "$ROOT/infra/wsl/setup_cups_gate0.sh" --inspect

completed_json="$TEMP_ROOT/completed-capture.json"
terminated_json="$TEMP_ROOT/terminated-capture.json"
if [[ -n "$RESUME_TERMINAL_JOB_ID" ]]; then
  terminal_job_id="$RESUME_TERMINAL_JOB_ID"
  lifecycle_job_id="$RESUME_TERMINATED_JOB_ID"
  echo "INFO: revalidating explicit completed job $terminal_job_id and terminated job $lifecycle_job_id"
else
  confirm "SUBMIT-LOCAL-TERMINAL" "submit the fixed V1 BRF to the simulated endpoint"
  terminal_output="$(operator lp -d "$QUEUE" -o raw -t "BER|GATE0|terminal-passthrough" "$CANDIDATE")"
  printf '%s\n' "$terminal_output"
  terminal_job_id="$(parse_lp_job_id "$terminal_output")"
  schedule_or_accept_terminal "$terminal_job_id"
  wait_until_terminal "$terminal_job_id"

  confirm "EXERCISE-LOCAL-LIFECYCLE" "submit, hold, release, and cancel a slow local test job"
  lifecycle_output="$(operator lp -d "$QUEUE" -o raw -H hold -t "BER|GATE0|lifecycle" "$LIFECYCLE_CANDIDATE")"
  printf '%s\n' "$lifecycle_output"
  lifecycle_job_id="$(parse_lp_job_id "$lifecycle_output")"
  operator lp -i "$QUEUE-$lifecycle_job_id" -H immediate >/dev/null
  wait_for_capture_input "$lifecycle_job_id"
  operator cancel "$QUEUE-$lifecycle_job_id" >/dev/null
  wait_until_terminal "$lifecycle_job_id"
fi

wait_for_capture_manifest "$terminal_job_id"
verify_capture "$terminal_job_id" "COMPLETED" "$CANDIDATE" "$completed_json"
sudo bash "$ROOT/infra/wsl/verify_observer_filesystem_access.sh" --job-id "$terminal_job_id"
wait_for_capture_manifest "$lifecycle_job_id"
verify_capture "$lifecycle_job_id" "TERMINATED" "$LIFECYCLE_CANDIDATE" "$terminated_json"

if [[ -n "$RESUME_HELD_JOB_ID" ]]; then
  held_job_id="$RESUME_HELD_JOB_ID"
  open_job_id="$RESUME_OPEN_JOB_ID"
  echo "INFO: reusing explicit held job $held_job_id and empty job $open_job_id for authorization probes"
else
  confirm "CREATE-LOCAL-AUTH-PROBES" "create held and empty local jobs for observer-denial probes"
  held_output="$(operator lp -d "$QUEUE" -o raw -H hold -t "BER|GATE0|held-auth-probe" "$CANDIDATE")"
  printf '%s\n' "$held_output"
  held_job_id="$(parse_lp_job_id "$held_output")"
  open_output="$TEMP_ROOT/open-job.out"
  set +e
  operator python3 "$ROOT/infra/wsl/create_open_cups_job.py" --queue "$QUEUE" | tee "$open_output"
  open_status="${PIPESTATUS[0]}"
  set -e
  [[ "$open_status" -eq 0 ]] || fail "human operator could not create the empty Send-Document probe job"
  open_job_id="$(parse_open_job_id "$(<"$open_output")")"
fi

echo "HUMAN OBSERVER ACTION: enter the observer CUPS password only at the local prompt"
python3 "$ROOT/infra/wsl/verify_cups_gate0.py" \
  --queue "$QUEUE" \
  --job-id "$held_job_id" \
  --send-document-job-id "$open_job_id" \
  --restart-job-id "$terminal_job_id" \
  --brf "$CANDIDATE" \
  --probe-admin-mutation

confirm "CLEANUP-LOCAL-AUTH-PROBES" "cancel the two local-only authorization probe jobs"
cleanup_probe_job "$held_job_id"
cleanup_probe_job "$open_job_id"

write_sanitized_evidence "$completed_json" "$terminated_json"
echo "PASS: local Gate 0 CUPS floor exercised; sanitized evidence written to demo/evidence/gate0-local-floor.json"
echo "NOTE: CUPS cancellation and simulated endpoint termination are not proof of physical-output isolation, proof approval, or replacement submission."
