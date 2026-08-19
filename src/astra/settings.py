"""Centralized typed configuration using Pydantic Settings.

Single entry point for all environment variables in Astra.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration settings for Astra."""

    model_config = SettingsConfigDict(
        env_prefix="ASTRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    env: str = Field(default="dev", description="Environment: dev, test, prod")
    host: str = Field(default="0.0.0.0", description="Server bind host")
    port: int = Field(default=8080, description="Server port")
    auth_token: str = Field(
        default="astra-dev-secret-token-change-in-prod",
        description="Shared secret Bearer token for API auth",
    )

    # Model & SDK Settings (Unified Flash model across Fast and Deep tiers)
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    fast_model: str = Field(default="gemini-2.5-flash", description="Unified model for fast tier classification")
    deep_model: str = Field(default="gemini-2.5-flash", description="Unified model for deep reasoning engines")
    fast_timeout_seconds: float = Field(default=4.0, description="Fast tier timeout in seconds")
    deep_timeout_seconds: float = Field(default=12.0, description="Deep tier engine timeout in seconds")

    # Persistence Settings
    persistence_backend: str = Field(
        default="IN_MEMORY",
        description="Persistence backend: IN_MEMORY or FIRESTORE",
    )
    firestore_project_id: Optional[str] = Field(default=None, alias="FIRESTORE_PROJECT_ID")
    firestore_collection_prefix: str = Field(default="astra", description="Firestore collection name prefix")
    firestore_timeout_seconds: float = Field(default=1.5, description="Firestore per-op timeout")

    # Escalation & Anti-Loop Parameters (Experimental)
    max_forced_continuations_per_signature: int = Field(
        default=2,
        description="Max forced Stop continuations before surfacing to user",
    )
    anti_loop_cooldown_seconds: float = Field(
        default=30.0,
        description="Minimum seconds between forced interventions for same signature",
    )
    intervention_budget_per_session: int = Field(
        default=5,
        description="Maximum total interventions allowed per session",
    )

    # Routing Settings
    routing_mode: str = Field(
        default="return_to_main_agent",
        description="Deep tier routing mode: return_to_main_agent, astra_reasons_further, combined",
    )

    # Observability
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    verbose_logs: bool = Field(default=True, description="Whether to emit verbose stage latency logs")


def get_settings() -> Settings:
    """Factory for loading settings singleton."""
    return Settings()
