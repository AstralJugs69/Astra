from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cloud_run_recipe_uses_frozen_lock_and_installs_runtime_environment() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock README.md /app/" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "uv pip install --python /app/.venv/bin/python --no-deps /app" in dockerfile
    assert dockerfile.count("--mount=type=cache,target=/root/.cache/uv") == 2
    assert "UV_HTTP_TIMEOUT=600" in dockerfile
    assert "UV_HTTP_RETRIES=20" in dockerfile
    assert "UV_CONCURRENT_DOWNLOADS=1" in dockerfile
    assert "COPY --from=application /app/.venv /app/.venv" in dockerfile
    assert (
        "COPY infra/scripts/bind_liblouis_profile.py /app/infra/scripts/bind_liblouis_profile.py"
        in dockerfile
    )
    assert "/app/.venv/bin/python infra/scripts/bind_liblouis_profile.py" in dockerfile
    assert "uv run" not in dockerfile
    assert (
        "translation-profile.bound.json /app/config/translation_profiles/demo-ueb-40x25-v1.json"
        in dockerfile
    )
    assert "PYTHONPATH=/opt/liblouis-python" in dockerfile
    assert "LD_LIBRARY_PATH=/opt/liblouis/lib" in dockerfile
    assert "LIBLOUIS_TABLEPATH=/opt/liblouis/share/liblouis/tables" in dockerfile
    assert "RELAY_CONFIG_ROOT=/app/config" in dockerfile
    assert (
        "TRANSLATION_PROFILE_PATH=/app/config/translation_profiles/demo-ueb-40x25-v1.json"
        in dockerfile
    )
    assert "RELAY_RECOMMENDATION_POLICY=/app/config/policies/recommendation.v1.json" in dockerfile
    assert dockerfile.count("COPY src /app/src") == 1
    assert dockerfile.index("uv sync --frozen --no-dev --no-install-project") < dockerfile.index(
        "COPY src /app/src"
    )


def test_dependency_lock_is_committed_input_for_the_recipe() -> None:
    lockfile = ROOT / "uv.lock"
    assert lockfile.is_file()
    lock_text = lockfile.read_text(encoding="utf-8")
    assert 'name = "braille-errata-relay"' in lock_text
    assert 'name = "google-adk"' in lock_text
    assert 'name = "liblouis"' not in lock_text


def test_container_recipe_pins_external_build_inputs() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim@sha256:" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.6.8@sha256:" in dockerfile
    assert "ARG LIBLOUIS_COMMIT=07c61e58cfb8814f6842c7212063f829288638c1" in dockerfile
    assert 'test "$(git rev-parse HEAD)" = "${LIBLOUIS_COMMIT}"' in dockerfile
    assert "--enable-python-bindings" not in dockerfile
