"""Idempotent semantic assessment orchestration with a pre-invocation lease."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from braille_errata_relay.adapters.adk_assessor import (
    AdkSemanticAssessor,
    AssessmentTrace,
    semantic_execution_key,
)
from braille_errata_relay.adapters.firestore_ledger import (
    SemanticClaimStatus,
    SemanticExecutionClaim,
)
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import AssessmentInput, SemanticAssessment


class SemanticExecutionInProgress(RuntimeError):
    """Another worker owns the bounded semantic execution lease."""


class SemanticLedger(Protocol):
    async def claim_semantic_execution(
        self,
        *,
        execution_key: str,
        evidence_sha256: str,
        model_id: str,
        prompt_version: str,
        analysis_revision: int,
        lease_token: str,
        lease_seconds: int = 120,
    ) -> SemanticExecutionClaim: ...

    async def complete_semantic_execution(
        self,
        *,
        execution_key: str,
        lease_token: str,
        assessment: SemanticAssessment,
    ) -> bool: ...

    async def record_semantic_attempt(
        self,
        *,
        execution_key: str,
        lease_token: str,
        sanitized_record: Mapping[str, object],
    ) -> bool: ...


@dataclass(frozen=True)
class SemanticWorkflowResult:
    execution_key: str
    assessment: SemanticAssessment
    trace: AssessmentTrace | None
    reused: bool


class IdempotentSemanticWorkflow:
    def __init__(
        self,
        *,
        assessor: AdkSemanticAssessor,
        ledger: SemanticLedger,
        lease_seconds: int = 120,
    ) -> None:
        self.assessor = assessor
        self.ledger = ledger
        self.lease_seconds = lease_seconds

    async def assess(
        self,
        evidence: AssessmentInput,
        *,
        analysis_revision: int = 1,
    ) -> SemanticWorkflowResult:
        evidence_sha256 = canonical_sha256(evidence.model_dump(mode="json"))
        execution_key = semantic_execution_key(
            evidence,
            model_id=self.assessor.model_id,
            prompt_version=self.assessor.prompt_version,
            analysis_revision=analysis_revision,
        )
        lease_token = uuid.uuid4().hex
        claim = await self.ledger.claim_semantic_execution(
            execution_key=execution_key,
            evidence_sha256=evidence_sha256,
            model_id=self.assessor.model_id,
            prompt_version=self.assessor.prompt_version,
            analysis_revision=analysis_revision,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
        )
        if claim.status is SemanticClaimStatus.READY:
            if claim.assessment is None:
                raise RuntimeError("ready semantic claim has no assessment")
            return SemanticWorkflowResult(
                execution_key=execution_key,
                assessment=claim.assessment,
                trace=None,
                reused=True,
            )
        if claim.status is SemanticClaimStatus.IN_PROGRESS:
            raise SemanticExecutionInProgress("semantic execution is already leased")
        trace = await self.assessor.assess_with_trace(
            evidence,
            analysis_revision=analysis_revision,
        )
        await self.ledger.complete_semantic_execution(
            execution_key=execution_key,
            lease_token=lease_token,
            assessment=trace.assessment,
        )
        await self.ledger.record_semantic_attempt(
            execution_key=execution_key,
            lease_token=lease_token,
            sanitized_record=trace.sanitized_record(),
        )
        return SemanticWorkflowResult(
            execution_key=execution_key,
            assessment=trace.assessment,
            trace=trace,
            reused=False,
        )
