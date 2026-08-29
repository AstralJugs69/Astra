#!/usr/bin/env bash
# Reproducible local-only setup for the WSL2 CUPS simulator.
#
# This script configures infrastructure only. It never submits, holds,
# releases, cancels, restarts, or otherwise mutates a CUPS job. Those actions
# remain separate, explicit actions by relay-operator through CUPS.
set -euo pipefail

umask 077

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POLICY="$ROOT/config/cups/relay-observer-policy.conf"
BACKEND="$ROOT/simulator/cups_backend/relay_capture_backend.py"
CAPTURE_CONFIG="$ROOT/config/cups/relay-capture.conf"
CUPS_CONF="/etc/cups/cupsd.conf"
POLICY_DEST="/etc/cups/relay-observer-policy.conf"
BACKEND_DEST="/usr/lib/cups/backend/relay-capture"
CAPTURE_CONFIG_DEST="/etc/cups/relay-capture.conf"
PRINTERS_CONF="/etc/cups/printers.conf"
PPD_DEST="/etc/cups/ppd/Braille-Embosser-Sim.ppd"
QUEUE="Braille-Embosser-Sim"
DEVICE_URI="relay-capture://demo-embosser"
CAPTURE_ROOT="/var/lib/braille-relay/captures"
OBSERVER_STATE_ROOT="/var/lib/braille-relay/observer"
BACKUP_ROOT="/var/lib/braille-relay/setup-backups"
OPERATOR="relay-operator"
OBSERVER="relay-observer"
AUDIT_GROUP="relay-audit"

MODE="apply"
TEMP_ROOT=""
CANDIDATE_CONF=""
BACKUP_DIR=""
ROLLBACK_ARMED=false

usage() {
  cat <<'EOF'
Usage: sudo bash infra/wsl/setup_cups_gate0.sh [--inspect|--dry-run]

  --inspect  Read and report the installed local CUPS simulator state only.
  --dry-run  Build and validate a replacement cupsd.conf candidate only.

Without an option, the script atomically installs the policy/backend/timing configuration and
configures the fixed Braille-Embosser-Sim raw simulator queue. It preserves a
root-only backup and restores the prior configuration if installation, queue
configuration, validation, activation (reload or restart), or post-install
assertions fail.
EOF
}

fail() {
  echo "BLOCKED: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null || fail "required command is missing: $1"
}

cleanup_temp() {
  if [[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" ]]; then
    rm -rf -- "$TEMP_ROOT"
  fi
}

backup_file() {
  local source="$1"
  local name="$2"
  if [[ -e "$source" ]]; then
    cp -a -- "$source" "$BACKUP_DIR/$name"
  else
    : > "$BACKUP_DIR/$name.absent"
  fi
}

restore_file() {
  local destination="$1"
  local name="$2"
  if [[ -e "$BACKUP_DIR/$name.absent" ]]; then
    rm -f -- "$destination"
  else
    install -d -m 0755 -- "$(dirname "$destination")"
    cp -a -- "$BACKUP_DIR/$name" "$destination"
  fi
}

rollback() {
  echo "ROLLBACK: restoring the prior local CUPS simulator configuration" >&2
  set +e
  restore_file "$CUPS_CONF" "cupsd.conf"
  restore_file "$POLICY_DEST" "relay-observer-policy.conf"
  restore_file "$BACKEND_DEST" "relay-capture"
  restore_file "$CAPTURE_CONFIG_DEST" "relay-capture.conf"
  restore_file "$PRINTERS_CONF" "printers.conf"
  restore_file "$PPD_DEST" "Braille-Embosser-Sim.ppd"
  systemctl restart cups
}

activate_cups_configuration() {
  if systemctl reload cups; then
    echo "PASS: reloaded CUPS configuration"
    return
  fi

  # Some WSL package units intentionally do not implement the reload job.
  # The candidate configuration has already passed cupsd -t, so a controlled
  # restart is the safe, explicit fallback before any post-install assertion.
  echo "INFO: cups.service does not support reload; restarting after validation"
  systemctl restart cups || fail "could not restart CUPS after configuration validation"
  echo "PASS: restarted CUPS configuration"
}

on_exit() {
  local status=$?
  if [[ "$status" -ne 0 && "$ROLLBACK_ARMED" == true ]]; then
    rollback
  fi
  cleanup_temp
  return "$status"
}
trap on_exit EXIT

prepare_candidate() {
  TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/braille-relay-cups.XXXXXX")"
  chmod 0700 "$TEMP_ROOT"
  CANDIDATE_CONF="$TEMP_ROOT/cupsd.conf"
  if ! awk '
    BEGIN { inside_policy = 0 }
    /^[[:space:]]*<Policy[[:space:]]+relay-observer>[[:space:]]*$/ {
      inside_policy = 1
      next
    }
    inside_policy && /^[[:space:]]*<\/Policy>[[:space:]]*$/ {
      inside_policy = 0
      next
    }
    !inside_policy && !/^[[:space:]]*DefaultPolicy[[:space:]]+/ { print }
    END { if (inside_policy) { exit 4 } }
  ' "$CUPS_CONF" > "$CANDIDATE_CONF"; then
    fail "could not remove an existing relay-observer policy from cupsd.conf"
  fi
  printf '\nDefaultPolicy relay-observer\n\n' >> "$CANDIDATE_CONF"
  cat "$POLICY" >> "$CANDIDATE_CONF"
  local policy_count
  policy_count="$(grep -Ec '^[[:space:]]*<Policy[[:space:]]+relay-observer>[[:space:]]*$' "$CANDIDATE_CONF" || true)"
  [[ "$policy_count" == "1" ]] || fail "candidate cupsd.conf must contain exactly one relay-observer policy"
}

validate_candidate() {
  local output
  if ! output="$(cupsd -t -c "$CANDIDATE_CONF" 2>&1)"; then
    printf '%s\n' "$output" >&2
    fail "candidate cupsd.conf did not validate"
  fi
  if grep -Eq 'Bad IPP operation name|Unknown directive|not found\.' <<< "$output"; then
    printf '%s\n' "$output" >&2
    fail "candidate cupsd.conf emitted configuration diagnostics"
  fi
}

validate_capture_timing_config() {
  if ! python3 "$BACKEND" --validate-runtime-config "$CAPTURE_CONFIG"; then
    fail "capture timing configuration did not validate"
  fi
}

ensure_account() {
  local account="$1"
  if getent passwd "$account" >/dev/null; then
    return
  fi
  if [[ "$account" == "$OPERATOR" ]]; then
    useradd --create-home --shell /bin/bash "$account"
  else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$account"
  fi
}

ensure_group() {
  local group="$1"
  getent group "$group" >/dev/null || groupadd --system "$group"
}

remove_from_group() {
  local account="$1"
  local group="$2"
  if id -nG "$account" | tr ' ' '\n' | grep -Fxq "$group"; then
    gpasswd -d "$account" "$group" >/dev/null
  fi
}

assert_observer_isolated() {
  local groups
  groups="$(id -nG "$OBSERVER")"
  if tr ' ' '\n' <<< "$groups" | grep -Fxq "$CUPS_GROUP"; then
    fail "$OBSERVER must not be a member of $CUPS_GROUP"
  fi
  if tr ' ' '\n' <<< "$groups" | grep -Fxq "$AUDIT_GROUP"; then
    fail "$OBSERVER must not be a member of $AUDIT_GROUP"
  fi
  if runuser -u "$OBSERVER" -- test -x "$CAPTURE_ROOT"; then
    fail "$OBSERVER can traverse the capture root"
  fi
  if [[ -d /var/spool/cups ]] && runuser -u "$OBSERVER" -- test -x /var/spool/cups; then
    fail "$OBSERVER can traverse the CUPS spool"
  fi
}

assert_installed_state() {
  systemctl is-active --quiet cups || fail "CUPS is not active"
  local device_line
  device_line="$(lpstat -v "$QUEUE")" || fail "configured queue is missing: $QUEUE"
  [[ "$device_line" == "device for $QUEUE: $DEVICE_URI" ]] || {
    fail "configured queue device URI is not exactly $DEVICE_URI"
  }
  local printer_block
  printer_block="$(awk -v queue="$QUEUE" '
    $0 == "<Printer " queue ">" { printing = 1 }
    printing { print }
    printing && $0 == "</Printer>" { exit }
  ' "$PRINTERS_CONF")"
  grep -Fxq "OpPolicy relay-observer" <<< "$printer_block" || {
    fail "configured queue does not use printer-op-policy=relay-observer"
  }
  [[ -x "$BACKEND_DEST" ]] || fail "capture backend is not executable"
  [[ "$(stat -c '%U:%G:%a' "$BACKEND_DEST")" == "root:root:755" ]] || {
    fail "capture backend must be root:root mode 755"
  }
  [[ "$(stat -c '%U:%G:%a' "$CAPTURE_CONFIG_DEST")" == "root:$CUPS_GROUP:640" ]] || {
    fail "capture timing configuration must be root:$CUPS_GROUP mode 640"
  }
  [[ "$(stat -c '%U:%G:%a' "$CAPTURE_ROOT")" == "lp:$AUDIT_GROUP:2750" ]] || {
    fail "capture root must preserve lp:$AUDIT_GROUP mode 2750 group inheritance"
  }
  cupsd -t -c "$CUPS_CONF" >/dev/null
  assert_observer_isolated
}

inspect() {
  require_command cupsd
  require_command lpstat
  echo "INSPECT: queue=$QUEUE expected_device_uri=$DEVICE_URI"
  if cupsd -t -c "$CUPS_CONF" >/dev/null 2>&1; then
    echo "PASS: installed cupsd.conf validates"
  else
    echo "BLOCKED: installed cupsd.conf did not validate"
  fi
  if systemctl is-active --quiet cups; then
    echo "PASS: CUPS service is active"
  else
    echo "BLOCKED: CUPS service is inactive"
  fi
  local device_line
  if device_line="$(lpstat -v "$QUEUE" 2>&1)"; then
    echo "INSPECT: $device_line"
  else
    echo "INSPECT: queue status is unavailable to this identity: $device_line"
  fi
  if getent passwd "$OBSERVER" >/dev/null; then
    echo "INSPECT: $OBSERVER groups=$(id -nG "$OBSERVER")"
  fi
}

CUPS_GROUP="lp"

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  case "$1" in
    --inspect) MODE="inspect" ;;
    --dry-run) MODE="dry-run" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
fi

[[ -f "$POLICY" ]] || fail "missing policy: $POLICY"
[[ -f "$BACKEND" ]] || fail "missing capture backend: $BACKEND"
[[ -f "$CAPTURE_CONFIG" ]] || fail "missing capture timing configuration: $CAPTURE_CONFIG"

if [[ "$MODE" == "inspect" ]]; then
  inspect
  exit 0
fi

require_command cupsd
require_command python3
if [[ "$MODE" == "dry-run" ]]; then
  prepare_candidate
  validate_candidate
  validate_capture_timing_config
  echo "PASS: replacement cupsd.conf candidate and capture timing configuration validate; no local state was changed"
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This local WSL simulator setup needs sudo." >&2
  echo "Run: sudo bash $0" >&2
  exit 2
fi

require_command gpasswd
require_command install
require_command lpadmin
require_command lpstat
require_command runuser
require_command systemctl

ensure_account "$OPERATOR"
ensure_account "$OBSERVER"
ensure_group "$AUDIT_GROUP"

# CUPS authorization is identity/policy based. Neither Relay identity receives
# direct spool access through lp; the human operator receives audit access only.
remove_from_group "$OPERATOR" "$CUPS_GROUP"
remove_from_group "$OBSERVER" "$CUPS_GROUP"
remove_from_group "$OBSERVER" "$AUDIT_GROUP"
usermod -a -G "$AUDIT_GROUP" "$OPERATOR"

install -d -o lp -g "$AUDIT_GROUP" -m 2750 "$CAPTURE_ROOT"
chown lp:"$AUDIT_GROUP" "$CAPTURE_ROOT"
chmod 2750 "$CAPTURE_ROOT"
install -d -o "$OBSERVER" -g "$OBSERVER" -m 0700 "$OBSERVER_STATE_ROOT"

prepare_candidate
validate_candidate
validate_capture_timing_config

install -d -m 0700 "$BACKUP_ROOT"
BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$BACKUP_DIR"
backup_file "$CUPS_CONF" "cupsd.conf"
backup_file "$POLICY_DEST" "relay-observer-policy.conf"
backup_file "$BACKEND_DEST" "relay-capture"
backup_file "$CAPTURE_CONFIG_DEST" "relay-capture.conf"
backup_file "$PRINTERS_CONF" "printers.conf"
backup_file "$PPD_DEST" "Braille-Embosser-Sim.ppd"
ROLLBACK_ARMED=true

install -m 0644 "$POLICY" "$POLICY_DEST"
install -m 0755 "$BACKEND" "$BACKEND_DEST"
install -o root -g "$CUPS_GROUP" -m 0640 "$CAPTURE_CONFIG" "$CAPTURE_CONFIG_DEST"
install -m 0644 "$CANDIDATE_CONF" "$CUPS_CONF"
cupsd -t -c "$CUPS_CONF" >/dev/null

lpadmin -p "$QUEUE" -v "$DEVICE_URI" -m raw -E
lpadmin -p "$QUEUE" -o document-format-default=application/vnd.cups-raw
lpadmin -p "$QUEUE" -o printer-op-policy=relay-observer
activate_cups_configuration
assert_installed_state
ROLLBACK_ARMED=false

echo "PASS: local CUPS simulator configured with the fixed $QUEUE raw queue"
echo "PASS: root-controlled slow capture timing configuration installed"
echo "PASS: backup retained under $BACKUP_DIR (root-readable only)"
echo "MANUAL: set CUPS passwords interactively only, if Basic authentication is enabled:"
echo "  sudo passwd relay-operator"
echo "  sudo passwd relay-observer"
echo "MANUAL: production job actions must use an independent relay-operator shell."
