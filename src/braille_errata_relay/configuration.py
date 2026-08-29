"""Fail-closed resolution for versioned runtime configuration files."""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_ROOT_ENV = "RELAY_CONFIG_ROOT"


def resolve_config_path(*, direct_env: str, relative_path: str) -> Path:
    """Resolve a config file without deriving repository paths from package code.

    Deployed runtimes set either the direct path or RELAY_CONFIG_ROOT.
    The relative fallback supports repository commands whose working directory
    is the project root and fails naturally when that contract is not met.
    """

    direct = os.environ.get(direct_env)
    if direct:
        return Path(direct)
    root = os.environ.get(CONFIG_ROOT_ENV)
    if root:
        return Path(root) / relative_path
    return Path("config") / relative_path
