"""Bind real installed Liblouis/table hashes into a derived profile.

This script intentionally fails when Liblouis, the named tables, or the real
translation smoke test are absent; it never invents hashes. The source profile
remains unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.readiness import check_liblouis_readiness
from braille_errata_relay.domain.models import TranslationProfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PROFILE = ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
OUTPUT_PROFILE = ROOT / "work" / "translation-profile.bound.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    part.replace(path)


def main() -> int:
    if importlib.util.find_spec("louis") is None:
        print("BLOCKED: upstream Liblouis Python binding is unavailable")
        return 2
    import louis  # type: ignore[import-not-found]

    table_root = Path(
        os.environ.get("LIBLOUIS_TABLEPATH", "/opt/liblouis/share/liblouis/tables")
    ).resolve()
    profile = json.loads(SOURCE_PROFILE.read_text(encoding="utf-8"))
    for table in profile["translation_tables"]:
        path = (table_root / table["name"]).resolve()
        if table_root not in path.parents or not path.is_file():
            print(f"BLOCKED: Liblouis table is missing or outside the table root: {path}")
            return 2
        table["sha256"] = file_sha256(path)
    profile["liblouis_version"] = LiblouisAdapter(louis).version()
    bound_profile = TranslationProfile.model_validate(profile)
    report = check_liblouis_readiness(
        bound_profile,
        table_root=table_root,
        louis_module=louis,
    )
    if not report.ready:
        print(f"BLOCKED: Liblouis readiness failed: {report.reason}")
        return 2
    _atomic_write_json(OUTPUT_PROFILE, bound_profile.model_dump(mode="json"))
    print(f"PASS: wrote bound profile to {OUTPUT_PROFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
