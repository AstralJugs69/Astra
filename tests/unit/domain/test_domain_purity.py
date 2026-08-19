"""Architectural invariant test: domain/ purity.

Asserts that src/astra/domain has ZERO imports of I/O frameworks, SDKs, or outer layers.
"""

import ast
from pathlib import Path
import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[3] / "src" / "astra" / "domain"

FORBIDDEN_MODULES = {
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "urllib",
    "requests",
    "google.genai",
    "google.cloud",
    "google.auth",
    "firestore",
    "astra.api",
    "astra.integration",
    "astra.infrastructure",
    "astra.tiers",
    "astra.engines",
    "astra.application",
}


def test_domain_has_zero_forbidden_imports():
    domain_files = list(DOMAIN_DIR.glob("*.py"))
    assert len(domain_files) > 0, "No domain python files found"

    for file_path in domain_files:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in FORBIDDEN_MODULES:
                        assert not alias.name.startswith(forbidden), (
                            f"Domain purity violation in {file_path.name}: imports '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for forbidden in FORBIDDEN_MODULES:
                    assert not module.startswith(forbidden), (
                        f"Domain purity violation in {file_path.name}: from '{module}' import ..."
                    )
