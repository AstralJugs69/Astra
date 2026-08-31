"""Materialize one original V1/V2 demo source revision for a human upload.

The output is deliberately an explicit local file chosen by the human.  It
does not call Drive, mutate a configured source, or invoke Liblouis.  The
checked-in golden test independently proves the rendered BRF/page-impact
property of the same deterministic source generator.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.fixtures.demo_volume_source import build_demo_volume


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=("v1", "v2"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.suffix.lower() != ".md":
        raise ValueError("output must be an explicit .md file")
    if output.exists():
        raise FileExistsError("refusing to overwrite an existing local source file")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_demo_volume(args.version))
    print(f"PASS: wrote original synthetic {args.version.upper()} source to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
