"""Composition root: wires infrastructure adapters into application pipeline and domain."""

from functools import lru_cache
from typing import Optional

from astra.application.pipeline import DecisionPipeline
from astra.domain.persistence_ports import TrajectoryStateStore
from astra.infrastructure.evidence.composite_retriever import CompositeEvidenceRetriever
from astra.infrastructure.model_providers.antigravity_sdk_provider import AntigravitySdkProvider
from astra.infrastructure.persistence.firestore_store import FirestoreTrajectoryStore
from astra.infrastructure.persistence.memory_store import InMemoryTrajectoryStore
from astra.settings import Settings, get_settings
from astra.tiers.fast.assessor import FastTierAssessor

# Singleton in-memory store for dev / testing across requests
_in_memory_store = InMemoryTrajectoryStore()


@lru_cache()
def get_persistence_store() -> TrajectoryStateStore:
    """Provides the configured persistence store (Firestore or In-Memory)."""
    cfg = get_settings()
    if cfg.persistence_backend.upper() == "FIRESTORE":
        return FirestoreTrajectoryStore(
            project_id=cfg.firestore_project_id,
            collection_prefix=cfg.firestore_collection_prefix,
            timeout_seconds=cfg.firestore_timeout_seconds,
        )
    return _in_memory_store


@lru_cache()
def get_model_provider() -> AntigravitySdkProvider:
    """Provides the Antigravity SDK model provider."""
    cfg = get_settings()
    return AntigravitySdkProvider(
        api_key=cfg.gemini_api_key,
        default_model=cfg.fast_model,
    )


@lru_cache()
def get_evidence_retriever() -> CompositeEvidenceRetriever:
    """Provides the composite evidence retriever."""
    return CompositeEvidenceRetriever()


@lru_cache()
def get_fast_assessor() -> FastTierAssessor:
    """Provides the fast tier assessor."""
    cfg = get_settings()
    return FastTierAssessor(
        model_provider=get_model_provider(),
        fast_model=cfg.fast_model,
        timeout_seconds=cfg.fast_timeout_seconds,
    )


@lru_cache()
def get_decision_pipeline() -> DecisionPipeline:
    """Provides the fully wired application decision pipeline."""
    cfg = get_settings()
    return DecisionPipeline(
        state_store=get_persistence_store(),
        fast_assessor=get_fast_assessor(),
        max_session_interventions=cfg.intervention_budget_per_session,
        max_forced_continuations_per_signature=cfg.max_forced_continuations_per_signature,
        anti_loop_cooldown_seconds=cfg.anti_loop_cooldown_seconds,
    )
