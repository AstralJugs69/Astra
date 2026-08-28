from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_path() -> Path:
    path = Path(__file__).resolve().parents[2] / "work" / "pytest-data"
    path.mkdir(parents=True, exist_ok=True)
    return path

