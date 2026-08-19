"""Hook payload recording utility for Antigravity CLI lifecycle hooks."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Record Antigravity hook payloads to fixtures.")
    parser.add_argument("--event", required=True, choices=["PostToolUse", "Stop", "PreToolUse"], help="Hook event type")
    parser.add_argument("--output-dir", default="tests/fixtures/hook_payloads", help="Output directory for captured payloads")
    args = parser.parse_args()

    # Read raw stdin
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            payload = {"_raw_empty": True}
        else:
            payload = json.loads(raw_input)
    except Exception as e:
        payload = {"_error": str(e), "_raw": raw_input if 'raw_input' in locals() else ""}

    # Ensure output directory exists
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine filename based on payload characteristics
    timestamp = int(time.time() * 1000)
    event_type = args.event.lower()
    
    if args.event == "PostToolUse":
        had_error = bool(payload.get("error"))
        status_tag = "error" if had_error else "success"
        filename = f"post_tool_use_{status_tag}_{timestamp}.json"
    elif args.event == "Stop":
        term_reason = payload.get("terminationReason", "unknown")
        had_error = bool(payload.get("error"))
        status_tag = "failed" if had_error else "success"
        filename = f"stop_{term_reason}_{status_tag}_{timestamp}.json"
    else:
        filename = f"{event_type}_{timestamp}.json"

    out_file = out_dir / filename
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Output standard expected stdout so agent execution continues normally
    if args.event == "PostToolUse":
        print("{}")
    elif args.event == "Stop":
        print(json.dumps({"decision": "continue", "reason": "Astra payload capture"}))
    else:
        print("{}")


if __name__ == "__main__":
    main()
