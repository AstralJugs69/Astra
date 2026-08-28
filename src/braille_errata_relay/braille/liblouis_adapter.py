"""Pinned Liblouis adapter; no fallback translator is provided."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from braille_errata_relay.domain.models import SourceBlock, TranslationProfile

from .errors import LiblouisUnavailableError, TranslationError
from .profile import require_bound_profile


@dataclass(frozen=True)
class TranslatedBlock:
    block_id: str
    source_text: str
    unicode_cells: str


def _load_louis() -> Any:
    try:
        import louis  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LiblouisUnavailableError(
            "the pinned upstream Liblouis Python binding is not installed"
        ) from exc
    return louis


def _require_unicode_six_dot_cells(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TranslationError("Liblouis returned no Unicode Braille cells")
    if any(not 0x2800 <= ord(char) <= 0x283F for char in value):
        raise TranslationError("Liblouis output crossed the boundary without Unicode six-dot cells")
    return value


class LiblouisAdapter:
    """Translate complete logical blocks through the configured Liblouis table."""

    def __init__(self, louis_module: Any | None = None) -> None:
        self._louis = louis_module or _load_louis()

    def version(self) -> str:
        value = getattr(self._louis, "version", None)
        if callable(value):
            return str(value())
        value = getattr(self._louis, "__version__", None)
        if value:
            return str(value)
        return "unreported"

    def translate(self, text: str, profile: TranslationProfile) -> str:
        require_bound_profile(profile)
        if not isinstance(text, str) or not text:
            raise TranslationError("Liblouis input must be non-empty text")
        if not hasattr(self._louis, "dotsIO") or not hasattr(self._louis, "ucBrl"):
            raise TranslationError("Liblouis binding lacks Unicode six-dot output flags")
        root_table = profile.translation_tables[0].name
        mode = int(self._louis.dotsIO) | int(self._louis.ucBrl)
        try:
            translated = self._louis.translateString([root_table], text, mode=mode)
        except Exception as exc:  # pragma: no cover - exact exception is binding-specific
            raise TranslationError("Liblouis translation failed") from exc
        return _require_unicode_six_dot_cells(translated)

    def translate_blocks(
        self, blocks: tuple[SourceBlock, ...], profile: TranslationProfile
    ) -> tuple[TranslatedBlock, ...]:
        return tuple(
            TranslatedBlock(
                block_id=block.block_id,
                source_text=block.text,
                unicode_cells=self.translate(block.text, profile),
            )
            for block in blocks
        )
