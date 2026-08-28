"""Bind real installed Liblouis/table hashes into a derived profile.

This script intentionally fails when Liblouis or the named tables are absent;
it never invents hashes. The source profile remains unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PROFILE = ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
OUTPUT_PROFILE = ROOT / "work" / "translation-profile.bound.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if importlib.util.find_spec("louis") is None:
        print("BLOCKED: upstream Liblouis Python binding is unavailable")
        return 2
    table_path = Path(os.environ.get("LIBLOUIS_TABLEPATH", "/opt/liblouis/share/liblouis/tables"))
    profile = json.loads(SOURCE_PROFILE.read_text(encoding="utf-8"))
    for table in profile["translation_tables"]:
        path = table_path / table["name"]
        if not path.is_file():
            print(f"BLOCKED: Liblouis table is missing: {path}")
            return 2
        table["sha256"] = file_sha256(path)
    import louis  # type: ignore[import-not-found]

    version = getattr(louis, "__version__", None)
    if not version and callable(getattr(louis, "version", None)):
        version = louis.version()
    profile["liblouis_version"] = str(version or "unreported")
    OUTPUT_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PROFILE.write_text(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(f"PASS: wrote bound profile to {OUTPUT_PROFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

