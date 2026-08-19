#!/usr/bin/env python3
"""PostToolUse hook dispatcher for Antigravity CLI.

Stdlib-only, fast fail-open execution. Relays tool execution output to Astra backend.
"""

import sys
from pathlib import Path

# Ensure hooks directory is in sys.path so common.py can be imported directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import execute_hook  # noqa: E402

POST_TOOL_USE_TIMEOUT_SECONDS = 3.0

if __name__ == "__main__":
    execute_hook(event_type="PostToolUse", timeout_seconds=POST_TOOL_USE_TIMEOUT_SECONDS)
