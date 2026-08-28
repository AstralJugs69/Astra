from __future__ import annotations

import hashlib
from pathlib import Path

from braille_errata_relay.braille.profile import load_translation_profile
from braille_errata_relay.braille.readiness import check_liblouis_readiness
from braille_errata_relay.domain.models import TranslationProfile, TranslationTable


class SmokeLouis:
    __version__ = "3.38.0"
    dotsIO = 1
    ucBrl = 2

    @staticmethod
    def translateString(_tables: list[str], _text: str, *, mode: int) -> str:
        assert mode == 3
        return "\u2801"


def bound_profile(hashes: tuple[str, str]) -> TranslationProfile:
    return TranslationProfile(
        profile_id="bound",
        liblouis_version="3.38.0",
        translation_tables=(
            TranslationTable(name="en-ueb-g2.ctb", sha256=hashes[0]),
            TranslationTable(name="en-us-brf.dis", sha256=hashes[1]),
        ),
        cells_per_line=40,
        lines_per_page=25,
    )


def test_unbound_profile_is_not_ready_even_if_binding_exists() -> None:
    report = check_liblouis_readiness(
        load_translation_profile("config/translation_profiles/demo-ueb-40x25-v1.json"),
        louis_module=SmokeLouis(),
    )
    assert report.ready is False
    assert report.reason == "PROFILE_TABLE_HASHES_UNRESOLVED:en-ueb-g2.ctb,en-us-brf.dis"


def test_readiness_requires_exact_tables_version_and_translation_smoke(tmp_path: Path) -> None:
    first = b"real table bytes"
    second = b"real display table bytes"
    (tmp_path / "en-ueb-g2.ctb").write_bytes(first)
    (tmp_path / "en-us-brf.dis").write_bytes(second)
    hashes = (
        hashlib.sha256(first).hexdigest(),
        hashlib.sha256(second).hexdigest(),
    )
    report = check_liblouis_readiness(
        bound_profile(hashes),
        table_root=tmp_path,
        louis_module=SmokeLouis(),
    )
    assert report.ready is True
    assert "translation_smoke=passed" in report.checks


def test_readiness_blocks_a_table_hash_mismatch(tmp_path: Path) -> None:
    (tmp_path / "en-ueb-g2.ctb").write_bytes(b"actual")
    (tmp_path / "en-us-brf.dis").write_bytes(b"display")
    report = check_liblouis_readiness(
        bound_profile(("a" * 64, "b" * 64)),
        table_root=tmp_path,
        louis_module=SmokeLouis(),
    )
    assert report.ready is False
    assert report.reason == "TABLE_HASH_MISMATCH:en-ueb-g2.ctb"
