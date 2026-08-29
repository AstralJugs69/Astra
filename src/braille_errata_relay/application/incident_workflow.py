"""Resumable report-first source revision investigation workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from braille_errata_relay.adapters.adk_assessor import (
    SemanticAssessmentBlocked,
)
from braille_errata_relay.adapters.firestore_ledger import (
    IncidentCheckpointCommit,
    StoredSourceRevision,
)
from braille_errata_relay.adapters.gcs_artifacts import content_addressed_ref
from braille_errata_relay.application.semantic_workflow import (
    IdempotentSemanticWorkflow,
    SemanticWorkflowResult,
)
from braille_errata_relay.braille.diff import diff_sources
from braille_errata_relay.braille.errors import (
    IncompatibleBaselineError,
    LiblouisUnavailableError,
    ProfileNotReadyError,
    TranslationError,
    UnsupportedContentError,
)
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.page_impact import compare_brf
from braille_errata_relay.braille.profile import profile_sha256, require_compatible_profile
from braille_errata_relay.braille.render import render
from braille_errata_relay.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    AssessmentInput,
    BaselineStatus,
    BlockingReason,
    BrailleImpact,
    EvidenceSide,
    HumanDispositionPacket,
    IncidentCheckpoint,
    IncidentWorkflowStage,
    NormalizedSource,
    ProductionContext,
    ProductionIncidentReport,
    RegisteredBaseline,
    SemanticAssessment,
    SemanticEvidenceSpan,
    SemanticImpactSummary,
    SiteObservation,
    TranslationProfile,
)
from braille_errata_relay.domain.recommendation import containment_recommendation


class IncidentWorkflowError(RuntimeError):
    pass


class IncidentLedger(Protocol):
    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None: ...

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None: ...

    async def claim_incident(
        self,
        checkpoint: IncidentCheckpoint,
    ) -> IncidentCheckpointCommit: ...

    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None: ...

    async def advance_incident(
        self,
        checkpoint: IncidentCheckpoint,
        *,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit: ...

    async def allocate_report_created_at(
        self,
        *,
        incident_id: str,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit: ...

    async def get_latest_site_observation(
        self,
        *,
        site_id: str,
        bridge_id: str,
        queue_name: str,
    ) -> SiteObservation | None: ...


class IncidentArtifactStore(Protocol):
    bucket_name: str

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef: ...

    async def read(self, ref: ArtifactRef) -> bytes: ...


class SemanticWorkflow(Protocol):
    async def assess(
        self,
        evidence: AssessmentInput,
        *,
        analysis_revision: int = 1,
    ) -> SemanticWorkflowResult: ...


@dataclass(frozen=True)
class IncidentWorkflowResult:
    checkpoint: IncidentCheckpoint
    report: ProductionIncidentReport | None
    disposition_packet: HumanDispositionPacket | None
    duplicate_source: bool = False


def production_job_lineage_id(baseline: RegisteredBaseline) -> str:
    value = baseline.baseline
    return canonical_sha256(
        {
            "site_id": value.site_id,
            "queue_name": value.queue_name,
            "scheduler_job_id": value.scheduler_job_id,
            "scheduler_job_title": value.scheduler_job_title,
            "approved_brf_sha256": value.approved_brf_sha256,
        }
    )


def incident_id(
    *,
    baseline_manifest_sha256: str,
    new_source_sha256: str,
    translation_profile_sha256: str,
    job_lineage_id: str,
) -> str:
    identity = (
        baseline_manifest_sha256 + new_source_sha256 + translation_profile_sha256 + job_lineage_id
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _semantic_input(source_diff: Mapping[str, object], impact: BrailleImpact) -> AssessmentInput:
    from braille_errata_relay.domain.models import SourceDiff

    parsed = SourceDiff.model_validate(source_diff)
    spans: list[SemanticEvidenceSpan] = []
    for side, blocks in (
        (EvidenceSide.OLD, parsed.old_blocks),
        (EvidenceSide.NEW, parsed.new_blocks),
    ):
        spans.extend(
            SemanticEvidenceSpan(
                span_id=f"{side.value}:{block.block_id}",
                side=side,
                block_kind=block.kind,
                text=block.text,
            )
            for block in blocks
        )
    context_seen: set[str] = set()
    for block in parsed.context_blocks:
        if block.block_id in context_seen:
            continue
        context_seen.add(block.block_id)
        spans.append(
            SemanticEvidenceSpan(
                span_id=f"context:{block.block_id}",
                side=EvidenceSide.CONTEXT,
                block_kind=block.kind,
                text=block.text,
            )
        )
    return AssessmentInput(
        evidence_spans=tuple(spans),
        impact_summary=SemanticImpactSummary(
            pages_changed=impact.pages_changed,
            baseline_page_count=impact.baseline_page_count,
            candidate_page_count=impact.candidate_page_count,
        ),
    )


class IncidentWorkflow:
    def __init__(
        self,
        *,
        ledger: IncidentLedger,
        artifact_store: IncidentArtifactStore,
        profile: TranslationProfile,
        translator: LiblouisAdapter,
        semantic_workflow: SemanticWorkflow | IdempotentSemanticWorkflow,
        bridge_id: str,
        clock: Callable[[], datetime] | None = None,
        observation_max_age_seconds: float = 15.0,
    ) -> None:
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.profile = profile
        self.translator = translator
        self.semantic_workflow = semantic_workflow
        self.bridge_id = bridge_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.observation_max_age_seconds = observation_max_age_seconds

    async def _store_json(
        self,
        value: object,
        *,
        object_name: str,
        kind: ArtifactKind,
    ) -> ArtifactRef:
        payload = canonical_json_bytes(value)
        ref = content_addressed_ref(
            payload,
            bucket_name=self.artifact_store.bucket_name,
            object_name=object_name,
            kind=kind,
        )
        await self.artifact_store.put_once(payload, ref=ref)
        return ref

    async def _advance(
        self,
        checkpoint: IncidentCheckpoint,
        *,
        stage: IncidentWorkflowStage,
        updates: Mapping[str, object],
    ) -> IncidentCheckpoint:
        target = checkpoint.model_copy(
            update={
                **dict(updates),
                "stage": stage,
                "state_version": checkpoint.state_version + 1,
                "updated_at": self.clock(),
            }
        )
        committed = await self.ledger.advance_incident(
            target,
            expected_state_version=checkpoint.state_version,
        )
        return committed.checkpoint

    async def process_source_revision(
        self,
        *,
        baseline_id: str,
        new_source_revision_id: str,
    ) -> IncidentWorkflowResult:
        baseline = await self.ledger.get_baseline(baseline_id)
        if baseline is None:
            raise IncidentWorkflowError("approved baseline lineage is missing")
        source = await self.ledger.get_source_revision(new_source_revision_id)
        if source is None:
            raise IncidentWorkflowError("new source revision is not durably claimed")
        if source.source_sha256 == baseline.baseline.source_sha256:
            checkpoint = IncidentCheckpoint(
                incident_id=incident_id(
                    baseline_manifest_sha256=baseline.baseline.baseline_manifest_sha256,
                    new_source_sha256=source.source_sha256,
                    translation_profile_sha256=baseline.baseline.translation_profile_sha256,
                    job_lineage_id=production_job_lineage_id(baseline),
                ),
                baseline_id=baseline_id,
                new_source_revision_id=source.revision_id,
                new_source_sha256=source.source_sha256,
                production_job_lineage_id=production_job_lineage_id(baseline),
                stage=IncidentWorkflowStage.REPORT_READY,
                updated_at=source.fetched_at,
            )
            return IncidentWorkflowResult(checkpoint, None, None, duplicate_source=True)
        if baseline.baseline.status is not BaselineStatus.PRODUCTION_LINK_VERIFIED:
            raise IncidentWorkflowError("baseline production link is not verified")
        job_lineage = production_job_lineage_id(baseline)
        identity = incident_id(
            baseline_manifest_sha256=baseline.baseline.baseline_manifest_sha256,
            new_source_sha256=source.source_sha256,
            translation_profile_sha256=baseline.baseline.translation_profile_sha256,
            job_lineage_id=job_lineage,
        )
        checkpoint = (
            await self.ledger.claim_incident(
                IncidentCheckpoint(
                    incident_id=identity,
                    baseline_id=baseline_id,
                    new_source_revision_id=source.revision_id,
                    new_source_sha256=source.source_sha256,
                    production_job_lineage_id=job_lineage,
                    updated_at=source.fetched_at,
                )
            )
        ).checkpoint
        if checkpoint.stage in {
            IncidentWorkflowStage.REPORT_READY,
            IncidentWorkflowStage.NEEDS_REVIEW,
        }:
            return await self._load_result(checkpoint)

        try:
            if checkpoint.stage is IncidentWorkflowStage.DETECTED:
                baseline_normalized = NormalizedSource.model_validate_json(
                    await self.artifact_store.read(baseline.artifacts.normalized_source)
                )
                raw = await self.artifact_store.read(source.artifact)
                normalized = normalize_source_bytes(
                    raw,
                    document_id=source.file_id,
                    previous=baseline_normalized,
                )
                source_diff = diff_sources(baseline_normalized, normalized)
                normalized_ref = await self._store_json(
                    normalized.model_dump(mode="json"),
                    object_name=(
                        f"normalized/{source.source_sha256}/"
                        f"{normalized.normalized_source_sha256}.json"
                    ),
                    kind=ArtifactKind.NORMALIZED_SOURCE,
                )
                diff_body = source_diff.model_dump(mode="json")
                diff_ref = await self._store_json(
                    diff_body,
                    object_name=f"diffs/{identity}/{canonical_sha256(diff_body)}.json",
                    kind=ArtifactKind.SOURCE_DIFF,
                )
                checkpoint = await self._advance(
                    checkpoint,
                    stage=IncidentWorkflowStage.DIFF_READY,
                    updates={"normalized_source": normalized_ref, "source_diff": diff_ref},
                )

            if checkpoint.stage is IncidentWorkflowStage.DIFF_READY:
                require_compatible_profile(
                    baseline_profile_sha256=baseline.baseline.translation_profile_sha256,
                    candidate_profile_sha256=profile_sha256(self.profile),
                )
                if checkpoint.normalized_source is None:
                    raise IncidentWorkflowError("normalized source checkpoint is missing")
                normalized = NormalizedSource.model_validate_json(
                    await self.artifact_store.read(checkpoint.normalized_source)
                )
                candidate = render(
                    normalized,
                    self.profile,
                    self.translator,
                    source_revision_id=source.revision_id,
                    source_sha256=source.source_sha256,
                    artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
                    baseline_manifest_sha256=baseline.baseline.baseline_manifest_sha256,
                    created_at=source.fetched_at,
                    generator_build={"profile_sha256": profile_sha256(self.profile)},
                )
                brf_ref = content_addressed_ref(
                    candidate.brf,
                    bucket_name=self.artifact_store.bucket_name,
                    object_name=f"braille/candidates/{candidate.manifest.artifact_sha256}.brf",
                    kind=ArtifactKind.FULL_CANDIDATE_BRF,
                )
                source_map_body = candidate.source_map
                source_map_ref = await self._store_json(
                    source_map_body,
                    object_name=f"maps/{brf_ref.sha256}.json",
                    kind=ArtifactKind.SOURCE_MAP,
                )
                manifest = candidate.manifest.model_copy(
                    update={"source_map_uri": source_map_ref.uri}
                )
                manifest_body = manifest.model_dump(mode="json")
                manifest_ref = await self._store_json(
                    manifest_body,
                    object_name=f"manifests/{canonical_sha256(manifest_body)}.json",
                    kind=ArtifactKind.ARTIFACT_MANIFEST,
                )
                await self.artifact_store.put_once(candidate.brf, ref=brf_ref)
                checkpoint = await self._advance(
                    checkpoint,
                    stage=IncidentWorkflowStage.CANDIDATE_READY,
                    updates={
                        "candidate_brf": brf_ref,
                        "candidate_source_map": source_map_ref,
                        "candidate_manifest": manifest_ref,
                    },
                )

            if checkpoint.stage is IncidentWorkflowStage.CANDIDATE_READY:
                if checkpoint.candidate_brf is None:
                    raise IncidentWorkflowError("candidate BRF checkpoint is missing")
                baseline_brf = await self.artifact_store.read(baseline.artifacts.approved_brf)
                candidate_brf = await self.artifact_store.read(checkpoint.candidate_brf)
                impact = compare_brf(
                    baseline_brf,
                    candidate_brf,
                    self.profile,
                    baseline_artifact_sha256=baseline.baseline.approved_brf_sha256,
                    candidate_artifact_sha256=checkpoint.candidate_brf.sha256,
                ).impact
                impact_body = impact.model_dump(mode="json")
                impact_ref = await self._store_json(
                    impact_body,
                    object_name=f"impacts/{identity}/{canonical_sha256(impact_body)}.json",
                    kind=ArtifactKind.BRAILLE_IMPACT,
                )
                checkpoint = await self._advance(
                    checkpoint,
                    stage=IncidentWorkflowStage.IMPACT_READY,
                    updates={"braille_impact": impact_ref},
                )

            if checkpoint.stage is IncidentWorkflowStage.IMPACT_READY:
                if checkpoint.source_diff is None or checkpoint.braille_impact is None:
                    raise IncidentWorkflowError("semantic evidence checkpoint is incomplete")
                source_diff_body = json.loads(
                    (await self.artifact_store.read(checkpoint.source_diff)).decode("utf-8")
                )
                impact = BrailleImpact.model_validate_json(
                    await self.artifact_store.read(checkpoint.braille_impact)
                )
                semantic = await self.semantic_workflow.assess(
                    _semantic_input(source_diff_body, impact)
                )
                semantic_body = semantic.assessment.model_dump(mode="json")
                semantic_ref = await self._store_json(
                    semantic_body,
                    object_name=(f"semantic/{identity}/{canonical_sha256(semantic_body)}.json"),
                    kind=ArtifactKind.SEMANTIC_ASSESSMENT,
                )
                checkpoint = await self._advance(
                    checkpoint,
                    stage=IncidentWorkflowStage.SEMANTIC_READY,
                    updates={"semantic_assessment": semantic_ref},
                )

            if checkpoint.stage is IncidentWorkflowStage.SEMANTIC_READY:
                checkpoint = await self._build_report(
                    checkpoint=checkpoint,
                    baseline=baseline,
                    source=source,
                )
        except IncompatibleBaselineError:
            checkpoint = await self._advance(
                checkpoint,
                stage=IncidentWorkflowStage.NEEDS_REVIEW,
                updates={"blocking_reason": BlockingReason.INCOMPATIBLE_BASELINE_PROFILE},
            )
        except UnsupportedContentError:
            checkpoint = await self._advance(
                checkpoint,
                stage=IncidentWorkflowStage.NEEDS_REVIEW,
                updates={"blocking_reason": BlockingReason.UNSUPPORTED_CONTENT},
            )
        except (LiblouisUnavailableError, ProfileNotReadyError, TranslationError):
            checkpoint = await self._advance(
                checkpoint,
                stage=IncidentWorkflowStage.NEEDS_REVIEW,
                updates={"blocking_reason": BlockingReason.BRAILLE_ENGINE_NOT_READY},
            )
        except SemanticAssessmentBlocked:
            checkpoint = await self._advance(
                checkpoint,
                stage=IncidentWorkflowStage.NEEDS_REVIEW,
                updates={"blocking_reason": BlockingReason.SEMANTIC_ASSESSMENT_INVALID},
            )
        return await self._load_result(checkpoint)

    async def _build_report(
        self,
        *,
        checkpoint: IncidentCheckpoint,
        baseline: RegisteredBaseline,
        source: StoredSourceRevision,
    ) -> IncidentCheckpoint:
        allocation = await self.ledger.allocate_report_created_at(
            incident_id=checkpoint.incident_id,
            expected_state_version=checkpoint.state_version,
        )
        checkpoint = allocation.checkpoint
        required = (
            checkpoint.source_diff,
            checkpoint.candidate_brf,
            checkpoint.candidate_manifest,
            checkpoint.braille_impact,
            checkpoint.semantic_assessment,
        )
        if any(ref is None for ref in required):
            raise IncidentWorkflowError("report checkpoint lineage is incomplete")
        impact = BrailleImpact.model_validate_json(
            await self.artifact_store.read(checkpoint.braille_impact)  # type: ignore[arg-type]
        )
        semantic = SemanticAssessment.model_validate_json(
            await self.artifact_store.read(checkpoint.semantic_assessment)  # type: ignore[arg-type]
        )
        observation = await self.ledger.get_latest_site_observation(
            site_id=baseline.baseline.site_id,
            bridge_id=self.bridge_id,
            queue_name=baseline.baseline.queue_name,
        )
        now = self.clock()
        recommendation = containment_recommendation(
            assessment=semantic,
            impact=impact,
            job_state=None,
            site_observation=observation,
            expected_job_id=baseline.baseline.scheduler_job_id,
            expected_queue_name=baseline.baseline.queue_name,
            expected_job_title=baseline.baseline.scheduler_job_title,
            expected_artifact_sha256=baseline.baseline.approved_brf_sha256,
            now=now,
            max_age_seconds=self.observation_max_age_seconds,
        )
        selected_job = None
        if observation is not None and baseline.baseline.scheduler_job_id is not None:
            selected_job = next(
                (
                    job
                    for job in observation.observations
                    if job.scheduler_job_id == baseline.baseline.scheduler_job_id
                ),
                None,
            )
        age = None
        if observation is not None:
            age = max(0.0, (now - observation.observed_at).total_seconds())
        context = ProductionContext(
            scheduler_job_id=baseline.baseline.scheduler_job_id,
            last_observed_state=selected_job.state if selected_job is not None else None,
            pages_observed_complete=(
                selected_job.impressions_completed if selected_job is not None else None
            ),
            observation_id=observation.observation_id if observation is not None else None,
            observation_age_seconds=age,
        )
        if checkpoint.report_created_at is None:
            raise IncidentWorkflowError("report creation time was not durably allocated")
        report = ProductionIncidentReport(
            incident_id=checkpoint.incident_id,
            baseline_id=checkpoint.baseline_id,
            old_source_revision_id=baseline.baseline.source_revision_id,
            new_source_revision_id=source.revision_id,
            source_diff_artifact_sha256=checkpoint.source_diff.sha256,  # type: ignore[union-attr]
            semantic_assessment=semantic,
            braille_impact=impact,
            production_context=context,
            recommended_human_steps=tuple(step.value for step in recommendation.steps),
            blocking_reason=recommendation.blocking_reason,
            created_at=checkpoint.report_created_at,
        )
        report_body = report.model_dump(mode="json")
        report_ref = await self._store_json(
            report_body,
            object_name=f"reports/{checkpoint.incident_id}/{canonical_sha256(report_body)}.json",
            kind=ArtifactKind.REPORT,
        )
        packet = HumanDispositionPacket(
            incident_id=checkpoint.incident_id,
            baseline_id=checkpoint.baseline_id,
            external_production_id=baseline.baseline.production_id,
            report_sha256=report_ref.sha256,
            candidate_brf=checkpoint.candidate_brf,  # type: ignore[arg-type]
            candidate_manifest=checkpoint.candidate_manifest,  # type: ignore[arg-type]
            baseline_brf_sha256=baseline.baseline.approved_brf_sha256,
            translation_profile_sha256=baseline.baseline.translation_profile_sha256,
            braille_impact=impact,
            semantic_assessment=semantic,
            site_observation_id=observation.observation_id if observation is not None else None,
            observation_age_seconds=age,
            recommended_human_steps=tuple(step.value for step in recommendation.steps),
            blocking_reason=recommendation.blocking_reason,
        )
        packet_body = packet.model_dump(mode="json")
        packet_ref = await self._store_json(
            packet_body,
            object_name=(
                f"disposition-packets/{checkpoint.incident_id}/{canonical_sha256(packet_body)}.json"
            ),
            kind=ArtifactKind.HUMAN_DISPOSITION_PACKET,
        )
        stage = (
            IncidentWorkflowStage.NEEDS_REVIEW
            if recommendation.blocking_reason is not None
            else IncidentWorkflowStage.REPORT_READY
        )
        return await self._advance(
            checkpoint,
            stage=stage,
            updates={
                "report": report_ref,
                "disposition_packet": packet_ref,
                "blocking_reason": recommendation.blocking_reason,
            },
        )

    async def _load_result(self, checkpoint: IncidentCheckpoint) -> IncidentWorkflowResult:
        report = (
            ProductionIncidentReport.model_validate_json(
                await self.artifact_store.read(checkpoint.report)
            )
            if checkpoint.report is not None
            else None
        )
        packet = (
            HumanDispositionPacket.model_validate_json(
                await self.artifact_store.read(checkpoint.disposition_packet)
            )
            if checkpoint.disposition_packet is not None
            else None
        )
        return IncidentWorkflowResult(checkpoint, report, packet)
