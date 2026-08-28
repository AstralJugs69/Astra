#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POLICY="$ROOT/config/cups/relay-observer-policy.conf"
BACKEND="$ROOT/simulator/cups_backend/relay_capture_backend.py"
CUPS_CONF="/etc/cups/cupsd.conf"
POLICY_DEST="/etc/cups/relay-observer-policy.conf"
BACKEND_DEST="/usr/lib/cups/backend/relay-capture"
QUEUE="${CUPS_QUEUE:-Braille-Embosser-Sim}"
CAPTURE_ROOT="${SIM_OUTPUT_ROOT:-/var/lib/braille-relay/captures}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This setup changes only the WSL CUPS simulator and needs sudo." >&2
  echo "Run: sudo bash $0" >&2
  exit 2
fi
command -v cupsd >/dev/null || { echo "cupsd is not installed" >&2; exit 2; }
command -v lpadmin >/dev/null || { echo "lpadmin is not installed" >&2; exit 2; }
[[ -f "$POLICY" ]] || { echo "missing policy: $POLICY" >&2; exit 2; }
[[ -f "$BACKEND" ]] || { echo "missing capture backend: $BACKEND" >&2; exit 2; }

install -m 0644 "$POLICY" "$POLICY_DEST"
install -m 0755 "$BACKEND" "$BACKEND_DEST"
install -d -o lp -g lp -m 0750 "$CAPTURE_ROOT"

if ! getent passwd relay-operator >/dev/null; then
  useradd --create-home --shell /bin/bash relay-operator
fi
if ! getent passwd relay-observer >/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin relay-observer
fi
usermod -a -G lp relay-operator
usermod -a -G lp relay-observer

if grep -q '^DefaultPolicy ' "$CUPS_CONF"; then
  sed -i 's/^DefaultPolicy .*/DefaultPolicy relay-observer/' "$CUPS_CONF"
else
  printf '%s\n' 'DefaultPolicy relay-observer' >> "$CUPS_CONF"
fi
if grep -q '<Policy relay-observer>' "$CUPS_CONF"; then
  echo "relay-observer policy already exists; inspect it before rerunning setup" >&2
  exit 2
fi
cat "$POLICY" >> "$CUPS_CONF"

cupsd -t -c "$CUPS_CONF"
lpadmin -p "$QUEUE" -v relay-capture://demo-embosser -m raw -E
lpadmin -p "$QUEUE" -o document-format-default=application/vnd.cups-raw
systemctl reload cups

echo "PASS: CUPS policy validated and $QUEUE points at relay-capture://demo-embosser"
echo "MANUAL: set passwords in an interactive terminal only:"
echo "  sudo passwd relay-operator"
echo "  sudo passwd relay-observer"
echo "MANUAL: reconnect any shells running as these identities after group changes."