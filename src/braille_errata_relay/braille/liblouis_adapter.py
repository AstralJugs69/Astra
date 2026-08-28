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
        root_table = profile.translation_tables[0].name
        mode = 0
        if hasattr(self._louis, "dotsIO") and hasattr(self._louis, "ucBrl"):
            mode = int(self._louis.dotsIO) | int(self._louis.ucBrl)
        try:
            translated = self._louis.translateString([root_table], text, mode=mode)
        except TypeError:
            translated = self._louis.translateString([root_table], text, 0, mode)
        except Exception as exc:  # pragma: no cover - exact exception is binding-specific
            raise TranslationError("Liblouis translation failed") from exc
        if not isinstance(translated, str) or not translated:
            raise TranslationError("Liblouis returned no translation")
        if any(ord(char) > 0x28FF for char in translated):
            raise TranslationError("Liblouis returned non-Braille output")
        return translated

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

