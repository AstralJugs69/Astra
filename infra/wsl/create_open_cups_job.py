#!/usr/bin/env python3
"""Create one empty CUPS job for a human-operated Send-Document denial probe.

This is not a Relay endpoint or agent tool. A human running it as
relay-operator creates an empty scheduler job only; it transmits no BRF bytes
and cannot produce physical output. Cancel the returned job after the observer
authorization probe.
"""

from __future__ import annotations

import argparse
import getpass

import cups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=631)
    parser.add_argument("--queue", default="Braille-Embosser-Sim")
    parser.add_argument("--user", default="relay-operator")
    parser.add_argument("--title", default="BER|GATE0|send-document-probe")
    args = parser.parse_args()
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    password = getpass.getpass(f"Password for {args.user}: ")
    cups.setUser(args.user)
    cups.setPasswordCB(lambda _prompt: password)
    try:
        connection = cups.Connection(host=args.host, port=args.port)
        if args.queue not in connection.getPrinters():
            raise RuntimeError(f"configured queue is missing: {args.queue}")
        job_id = connection.createJob(args.queue, args.title, {})
        if not isinstance(job_id, int) or job_id < 1:
            raise RuntimeError("CUPS did not return an open job ID")
        print(f"PASS: human operator created empty Send-Document probe job ID {job_id}")
        return 0
    finally:
        cups.setPasswordCB(lambda _prompt: "")


if __name__ == "__main__":
    raise SystemExit(main())
