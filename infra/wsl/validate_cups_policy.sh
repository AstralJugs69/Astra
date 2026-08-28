#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POLICY="$ROOT/config/cups/relay-observer-policy.conf"
if [[ ! -f "$POLICY" ]]; then
  echo "missing policy: $POLICY" >&2
  exit 2
fi
command -v cupsd >/dev/null || {
  echo "BLOCKED: cupsd is not installed in this WSL distribution" >&2
  exit 2
}

TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
cp "$POLICY" "$TEMP_ROOT/relay-observer-policy.conf"
cat > "$TEMP_ROOT/cupsd.conf" <<EOF
ServerRoot /etc/cups
LogLevel warn
AccessLog stderr
ErrorLog stderr
PageLog /dev/null
Listen localhost:1631
DefaultAuthType Basic
DefaultPolicy relay-observer
EOF
cat "$TEMP_ROOT/relay-observer-policy.conf" >> "$TEMP_ROOT/cupsd.conf"

VALIDATION_OUTPUT="$(cupsd -t -c "$TEMP_ROOT/cupsd.conf" 2>&1)" || {
  printf '%s\n' "$VALIDATION_OUTPUT" >&2
  exit 1
}
printf '%s\n' "$VALIDATION_OUTPUT"
if grep -Eq 'Bad IPP operation name|Unknown directive|not found\.' <<< "$VALIDATION_OUTPUT"; then
  echo "BLOCKED: cupsd emitted policy/configuration diagnostics" >&2
  exit 1
fi
echo "PASS: cupsd accepted the loaded relay-observer policy syntax"