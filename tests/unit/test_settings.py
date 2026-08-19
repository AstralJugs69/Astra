"""Unit tests for Settings configuration."""

import os
from astra.settings import Settings


def test_settings_defaults():
    settings = Settings()
    assert settings.env == "dev"
    assert settings.fast_model == "gemini-3.7-flash"
    assert settings.deep_model == "gemini-3.7-flash"
    assert settings.fast_timeout_seconds == 4.0
    assert settings.deep_timeout_seconds == 12.0
    assert settings.max_forced_continuations_per_signature == 2
    assert settings.persistence_backend == "IN_MEMORY"


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("ASTRA_ENV", "prod")
    monkeypatch.setenv("ASTRA_FAST_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("ASTRA_MAX_FORCED_CONTINUATIONS_PER_SIGNATURE", "4")

    settings = Settings()
    assert settings.env == "prod"
    assert settings.fast_timeout_seconds == 3.5
    assert settings.max_forced_continuations_per_signature == 4
