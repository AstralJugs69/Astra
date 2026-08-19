"""Composition root: wires infrastructure adapters into application pipeline and domain."""

from functools import lru_cache
from typing import Optional

from astra.application.pipeline import DecisionPipeline
from astra.domain.persistence_ports import TrajectoryStateStore
from astra.domain.routing import RoutingMode
from astra.engines.bugfix.verifier import BugfixVerifier
from astra.engines.reasoning.alternatives import AlternativeRanker
from astra.engines.reasoning.critic import ReasoningCritic
from astra.infrastructure.evidence.composite_retriever import CompositeEvidenceRetriever
from astra.infrastructure.model_providers.antigravity_sdk_provider import AntigravitySdkProvider
from astra.infrastructure.persistence.firestore_store import FirestoreTrajectoryStore
from astra.infrastructure.persistence.memory_store import InMemoryTrajectoryStore
from astra.settings import Settings, get_settings
from astra.tiers.deep.orchestrator import DeepTierOrchestrator
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
    import os
    cfg = get_settings()
    proj_id = (
        cfg.gcp_project_id
        or cfg.firestore_project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("FIRESTORE_PROJECT_ID")
        or os.environ.get("ASTRA_PROJECT_ID")
    )
    return AntigravitySdkProvider(
        api_key=cfg.gemini_api_key,
        project_id=proj_id,
        location=cfg.vertex_location,
        default_model=cfg.fast_model,
        use_vertex_ai=cfg.use_vertex_ai,
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
def get_bugfix_verifier() -> BugfixVerifier:
    """Provides the bugfix verifier engine."""
    cfg = get_settings()
    return BugfixVerifier(
        model_provider=get_model_provider(),
        model_name=cfg.deep_model,
        timeout_seconds=cfg.deep_timeout_seconds,
    )


@lru_cache()
def get_reasoning_critic() -> ReasoningCritic:
    """Provides the reasoning critic engine."""
    cfg = get_settings()
    return ReasoningCritic(
        model_provider=get_model_provider(),
        model_name=cfg.deep_model,
        timeout_seconds=cfg.deep_timeout_seconds,
    )


@lru_cache()
def get_alternative_ranker() -> AlternativeRanker:
    """Provides the alternative ranker engine."""
    cfg = get_settings()
    return AlternativeRanker(
        model_provider=get_model_provider(),
        model_name=cfg.deep_model,
        timeout_seconds=cfg.deep_timeout_seconds,
    )


@lru_cache()
def get_deep_orchestrator() -> DeepTierOrchestrator:
    """Provides the deep tier reasoning orchestrator."""
    cfg = get_settings()
    try:
        r_mode = RoutingMode(cfg.routing_mode)
    except ValueError:
        r_mode = RoutingMode.RETURN_TO_MAIN_AGENT

    return DeepTierOrchestrator(
        evidence_retriever=get_evidence_retriever(),
        bugfix_verifier=get_bugfix_verifier(),
        reasoning_critic=get_reasoning_critic(),
        alternative_ranker=get_alternative_ranker(),
        default_routing_mode=r_mode,
    )


@lru_cache()
def get_decision_pipeline() -> DecisionPipeline:
    """Provides the fully wired application decision pipeline."""
    cfg = get_settings()
    return DecisionPipeline(
        state_store=get_persistence_store(),
        fast_assessor=get_fast_assessor(),
        deep_orchestrator=get_deep_orchestrator(),
        max_session_interventions=cfg.intervention_budget_per_session,
        max_forced_continuations_per_signature=cfg.max_forced_continuations_per_signature,
        anti_loop_cooldown_seconds=cfg.anti_loop_cooldown_seconds,
    )
