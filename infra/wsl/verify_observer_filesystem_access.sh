#!/usr/bin/env bash
# Verify that the read-only Relay observer has no direct capture or spool access.
set -euo pipefail

OBSERVER="relay-observer"
CAPTURE_ROOT="/var/lib/braille-relay/captures"
JOB_ID=""

usage() {
  cat <<'EOF'
Usage: sudo bash infra/wsl/verify_observer_filesystem_access.sh --job-id JOB_ID

This is a read-only verification. It requires an already-completed simulator
capture so it can prove relay-observer cannot traverse the capture tree or
read the captured BRF, journal, or manifest. It never reads the files as the
observer and it does not submit or mutate CUPS jobs.
EOF
}

fail() {
  echo "BLOCKED: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job-id)
      [[ $# -ge 2 ]] || fail "--job-id requires a value"
      JOB_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || fail "run this read-only verifier through sudo"
[[ "$JOB_ID" =~ ^[1-9][0-9]*$ ]] || fail "--job-id must be a positive integer"
command -v runuser >/dev/null || fail "runuser is required"
getent passwd "$OBSERVER" >/dev/null || fail "$OBSERVER account is missing"

observer_groups="$(id -nG "$OBSERVER")"
if tr ' ' '\n' <<< "$observer_groups" | grep -Eq '^(lp|relay-audit)$'; then
  fail "$OBSERVER has a privileged supplemental group"
fi

job_dir="$CAPTURE_ROOT/$JOB_ID"
input_path="$job_dir/input.brf"
output_path="$job_dir/output.brf"
events_path="$job_dir/events.jsonl"
manifest_path="$job_dir/manifest.json"

for evidence_path in "$CAPTURE_ROOT" "$job_dir" "$input_path" "$output_path" "$events_path" "$manifest_path"; do
  [[ -e "$evidence_path" ]] || fail "expected completed capture evidence is missing"
done

assert_denied_traverse() {
  local label="$1"
  local path="$2"
  if runuser -u "$OBSERVER" -- test -x "$path"; then
    fail "$OBSERVER can traverse $label"
  fi
  echo "PASS: relay-observer cannot traverse $label"
}

assert_denied_read() {
  local label="$1"
  local path="$2"
  if runuser -u "$OBSERVER" -- test -r "$path"; then
    fail "$OBSERVER can read $label"
  fi
  echo "PASS: relay-observer cannot read $label"
}

assert_denied_traverse "capture root" "$CAPTURE_ROOT"
assert_denied_traverse "capture job directory" "$job_dir"
assert_denied_read "captured input BRF" "$input_path"
assert_denied_read "captured output BRF" "$output_path"
assert_denied_read "capture journal" "$events_path"
assert_denied_read "capture manifest" "$manifest_path"

if [[ -d /var/spool/cups ]]; then
  assert_denied_traverse "CUPS spool" /var/spool/cups
fi

echo "PASS: observer filesystem-isolation floor for completed job $JOB_ID"
