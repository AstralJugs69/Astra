#!/usr/bin/env bash
# scripts/install_hooks.sh — Installs Astra hooks into an Antigravity workspace

set -euo pipefail

WORKSPACE="${1:-.}"
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hooks"

echo "Installing Astra hooks into workspace: ${WORKSPACE}"

TARGET_DIR="${WORKSPACE}/.agents"
mkdir -p "${TARGET_DIR}"

PYTHON_BIN=$(command -v python3 || command -v python)

cat <<EOF > "${TARGET_DIR}/hooks.json"
{
  "hooks": {
    "PostToolUse": {
      "command": "${PYTHON_BIN}",
      "args": ["${HOOKS_DIR}/post_tool_use.py"]
    },
    "Stop": {
      "command": "${PYTHON_BIN}",
      "args": ["${HOOKS_DIR}/stop.py"]
    }
  }
}
EOF

echo "✅ Astra hooks successfully installed to ${TARGET_DIR}/hooks.json"
echo "   PostToolUse: ${HOOKS_DIR}/post_tool_use.py"
echo "   Stop:        ${HOOKS_DIR}/stop.py"
