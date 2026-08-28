from __future__ import annotations

import pytest

from braille_errata_relay.braille.errors import TranslationError
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.domain.models import TranslationProfile, TranslationTable


def profile() -> TranslationProfile:
    return TranslationProfile(
        profile_id="test-profile",
        liblouis_version="3.38.0",
        translation_tables=(
            TranslationTable(name="en-ueb-g2.ctb", sha256="a" * 64),
            TranslationTable(name="en-us-brf.dis", sha256="b" * 64),
        ),
        cells_per_line=40,
        lines_per_page=25,
    )


class FakeLouis:
    dotsIO = 1
    ucBrl = 2
    __version__ = "3.38.0"

    def __init__(self, output: str) -> None:
        self.output = output

    def translateString(self, _tables: list[str], _text: str, *, mode: int) -> str:
        assert mode == self.dotsIO | self.ucBrl
        return self.output


@pytest.mark.parametrize("output", ["abc", "\u2840", "\u200b", "\u2801\n"])
def test_liblouis_rejects_non_six_dot_unicode_output(output: str) -> None:
    with pytest.raises(TranslationError, match="six-dot"):
        LiblouisAdapter(FakeLouis(output)).translate("text", profile())


def test_liblouis_accepts_only_unicode_six_dot_cells() -> None:
    assert (
        LiblouisAdapter(FakeLouis("\u2801\u2800\u283f")).translate("text", profile())
        == "\u2801\u2800\u283f"
    )


def test_liblouis_binding_without_unicode_flags_fails_closed() -> None:
    class NoUnicodeFlags:
        __version__ = "3.38.0"

        @staticmethod
        def translateString(_tables: list[str], _text: str, *, mode: int) -> str:
            return "\u2801"

    with pytest.raises(TranslationError, match="Unicode six-dot output flags"):
        LiblouisAdapter(NoUnicodeFlags()).translate("text", profile())
