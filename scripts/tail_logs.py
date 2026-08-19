"""Interactive CLI log viewer for Astra hook events and server logs.

Usage:
    python scripts/tail_logs.py
    python scripts/tail_logs.py --tools-only
    python scripts/tail_logs.py --interventions-only
    python scripts/tail_logs.py --search "run_command"
"""

import argparse
import os
import sys
import time

LOG_FILE = "C:/dev/Astra/hook_events.log"


def format_colored_line(line: str) -> str:
    """Applies ANSI colors for easy terminal scanning."""
    line = line.strip()
    if "[HOOK:PostToolUse]" in line:
        return f"\033[94m{line}\033[0m"  # Blue
    elif "BLOCK_STOP" in line or "INTERVENE" in line:
        return f"\033[91;1m{line}\033[0m"  # Bold Red
    elif "Decision: ALLOW" in line or "200_OK" in line:
        return f"\033[92m{line}\033[0m"  # Green
    elif "ERR" in line or "Fallback" in line:
        return f"\033[93m{line}\033[0m"  # Yellow
    return line


def main():
    parser = argparse.ArgumentParser(description="Tail and filter Astra hook logs.")
    parser.add_argument("--lines", "-n", type=int, default=30, help="Number of initial lines to show")
    parser.add_argument("--tools-only", action="store_true", help="Only show PostToolUse tool calls")
    parser.add_argument("--interventions-only", action="store_true", help="Only show Stop interventions & blocks")
    parser.add_argument("--search", "-s", type=str, default="", help="Case-insensitive search filter")
    parser.add_argument("--follow", "-f", action="store_true", help="Follow live log updates")

    args = parser.parse_args()

    if not os.path.exists(LOG_FILE):
        print(f"Log file not found: {LOG_FILE}")
        return

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    filtered = []
    for line in lines:
        if args.tools_only and "[TOOL:" not in line:
            continue
        if args.interventions_only and "BLOCK_STOP" not in line and "Astra Stop" not in line:
            continue
        if args.search and args.search.lower() not in line.lower():
            continue
        filtered.append(line)

    print("=" * 80)
    print(f"  ASTRA HOOK LOGS ({len(filtered)} matching events)")
    print("=" * 80)

    for line in filtered[-args.lines:]:
        print(format_colored_line(line))

    if args.follow:
        print("\n--- Following live log updates (Ctrl+C to exit) ---")
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if line:
                        if args.tools_only and "[TOOL:" not in line:
                            continue
                        if args.interventions_only and "BLOCK_STOP" not in line and "Astra Stop" not in line:
                            continue
                        if args.search and args.search.lower() not in line.lower():
                            continue
                        print(format_colored_line(line))
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
