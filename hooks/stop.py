#!/usr/bin/env python3
"""Stop hook dispatcher for Antigravity CLI.

Stdlib-only, fast fail-open execution. Audits agent termination attempts and enforces
verification checks or forced continuations.
"""

import sys
from pathlib import Path

# Ensure hooks directory is in sys.path so common.py can be imported directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import execute_hook  # noqa: E402

STOP_TIMEOUT_SECONDS = 15.0

if __name__ == "__main__":
    execute_hook(event_type="Stop", timeout_seconds=STOP_TIMEOUT_SECONDS)
