"""Versioned translation-profile loading and readiness checks."""

from __future__ import annotations

import json
from pathlib import Path

from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import TranslationProfile

from .errors import IncompatibleBaselineError, ProfileNotReadyError


def load_translation_profile(path: str | Path) -> TranslationProfile:
    return TranslationProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))


def profile_sha256(profile: TranslationProfile) -> str:
    return canonical_sha256(profile.model_dump(mode="json"))


def require_bound_profile(profile: TranslationProfile) -> None:
    if not profile.is_bound:
        missing = [table.name for table in profile.translation_tables if table.sha256 is None]
        raise ProfileNotReadyError(
            "translation profile is not bound to installed table hashes: " + ", ".join(missing)
        )


def require_compatible_profile(
    *, baseline_profile_sha256: str, candidate_profile_sha256: str
) -> None:
    if baseline_profile_sha256 != candidate_profile_sha256:
        raise IncompatibleBaselineError(
            "baseline and candidate translation/layout profiles are incompatible"
        )


def profile_json(profile: TranslationProfile) -> str:
    """Return canonical profile JSON for evidence and profile-hash inspection."""

    return json.dumps(
        profile.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

