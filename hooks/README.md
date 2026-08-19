# Astra Local Hooks (`hooks/`)

This directory contains the local, stdlib-only lifecycle hook scripts that run inside your Google Antigravity CLI (`agy`) workspace.

## Philosophy & Design Principles
1. **Stdlib-Only**: Zero `pip install` requirements. These scripts run instantly under any system Python interpreter without virtualenv dependencies.
2. **Fail-Open by Construction**: If Astra backend is unreachable, times out, or encounters an internal error, the hook immediately emits the fail-open default (`{}` for `PostToolUse`, `{"decision": "continue"}` for `Stop`), guaranteeing the main agent is never blocked by a companion outage.
3. **Thin Relay**: Hook scripts do not perform parsing or reasoning locally; they relay stdin JSON over HTTPS to the Astra backend service and echo the decision JSON to stdout.

## Installation & Setup

1. Copy `hooks.json.example` to your workspace root's `.agents/hooks.json` or register it in your Antigravity configuration:
   ```bash
   mkdir -p .agents
   cp hooks/hooks.json.example .agents/hooks.json
   ```

2. Configure environment variables (optional, defaults point to local Astra backend):
   ```bash
   export ASTRA_ENDPOINT_URL="http://127.0.0.1:8080/event"
   export ASTRA_AUTH_TOKEN="astra-dev-secret-token-change-in-prod"
   ```

3. Verify hook execution locally using the test suite:
   ```bash
   pytest tests/integration/hooks/test_hooks_fail_open.py -v
   ```
