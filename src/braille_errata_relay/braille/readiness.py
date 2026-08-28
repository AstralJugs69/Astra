"""Gate 0 checks for the installed Liblouis profile and translation path."""

from __future__ import annotations

import hashlib
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from braille_errata_relay.domain.models import TranslationProfile

from .errors import LiblouisUnavailableError, ProfileNotReadyError, TranslationError
from .liblouis_adapter import LiblouisAdapter


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    reason: str | None
    checks: tuple[str, ...]
    liblouis_version: str | None = None


def _load_louis() -> Any:
    try:
        return importlib.import_module("louis")
    except ImportError as exc:
        raise LiblouisUnavailableError(
            "the pinned upstream Liblouis Python binding is not installed"
        ) from exc


def _table_path(table_root: Path, table_name: str) -> Path:
    candidate = (table_root / table_name).resolve()
    root = table_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ProfileNotReadyError("translation table path escaped LIBLOUIS_TABLEPATH")
    return candidate


def check_liblouis_readiness(
    profile: TranslationProfile,
    *,
    table_root: str | Path | None = None,
    louis_module: Any | None = None,
) -> ReadinessReport:
    checks: list[str] = []
    if not profile.is_bound:
        missing = [table.name for table in profile.translation_tables if table.sha256 is None]
        return ReadinessReport(
            ready=False,
            reason="PROFILE_TABLE_HASHES_UNRESOLVED:" + ",".join(missing),
            checks=("profile_bound=false",),
        )

    root = Path(
        table_root or os.environ.get("LIBLOUIS_TABLEPATH", "/opt/liblouis/share/liblouis/tables")
    )
    try:
        louis = louis_module or _load_louis()
        adapter = LiblouisAdapter(louis)
        installed_version = adapter.version()
        checks.append(f"liblouis_version={installed_version}")
        if installed_version != profile.liblouis_version:
            return ReadinessReport(
                ready=False,
                reason="LIBLOUIS_VERSION_MISMATCH",
                checks=tuple(checks),
                liblouis_version=installed_version,
            )
        for table in profile.translation_tables:
            path = _table_path(root, table.name)
            if not path.is_file():
                return ReadinessReport(
                    ready=False,
                    reason=f"TABLE_MISSING:{table.name}",
                    checks=tuple(checks),
                    liblouis_version=installed_version,
                )
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            checks.append(f"table_hash:{table.name}={actual_hash}")
            if actual_hash != table.sha256:
                return ReadinessReport(
                    ready=False,
                    reason=f"TABLE_HASH_MISMATCH:{table.name}",
                    checks=tuple(checks),
                    liblouis_version=installed_version,
                )
        adapter.translate("Gate 0 smoke", profile)
        checks.append("translation_smoke=passed")
        return ReadinessReport(
            ready=True,
            reason=None,
            checks=tuple(checks),
            liblouis_version=installed_version,
        )
    except (LiblouisUnavailableError, ProfileNotReadyError, TranslationError, OSError) as exc:
        checks.append(f"translation_smoke=failed:{type(exc).__name__}")
        return ReadinessReport(
            ready=False,
            reason=type(exc).__name__,
            checks=tuple(checks),
            liblouis_version=checks[0].split("=", 1)[1] if checks else None,
        )
