from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from braille_errata_relay.adapters.adk_assessor import (
    SemanticAssessmentBlocked,
    SemanticAssessmentUnavailable,
)
from braille_errata_relay.adapters.firestore_ledger import (
    IncidentCheckpointCommit,
    StoredSourceRevision,
)
from braille_errata_relay.adapters.gcs_artifacts import content_addressed_ref
from braille_errata_relay.application.baseline_registration import (
    BaselineRegistrationWorkflow,
    baseline_registration_idempotency_key,
)
from braille_errata_relay.application.incident_workflow import (
    IncidentWorkflow,
    IncidentWorkflowError,
)
from braille_errata_relay.application.semantic_workflow import (
    SemanticExecutionInProgress,
    SemanticWorkflowResult,
)
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    AssessmentInput,
    BaselineStatus,
    IncidentCheckpoint,
    IncidentWorkflowStage,
    JobState,
    QueueObservation,
    RegisteredBaseline,
    SemanticAssessment,
    SiteObservation,
    TranslationProfile,
    TranslationTable,
)

ROOT = Path(__file__).resolve().parents[2]


class DeterministicLouis:
    dotsIO = 1
    ucBrl = 2
    __version__ = "3.38.0"

    @staticmethod
    def translateString(_tables: list[str], text: str, *, mode: int) -> str:
        assert mode == 3
        return "".join(chr(0x2800 + (ord(char) % 64)) for char in text)


class MemoryArtifacts:
    bucket_name = "relay-test"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.fail_after: int | None = None
        self.puts = 0

    async def read(self, ref: ArtifactRef) -> bytes:
        value = self.values[ref.uri]
        assert hashlib.sha256(value).hexdigest() == ref.sha256
        return value

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef:
        assert hashlib.sha256(artifact).hexdigest() == ref.sha256
        existing = self.values.setdefault(ref.uri, artifact)
        assert existing == artifact
        self.puts += 1
        if self.fail_after == self.puts:
            raise RuntimeError("simulated crash after immutable artifact write")
        return ref


class MemoryLedger:
    def __init__(self) -> None:
        self.sources: dict[str, StoredSourceRevision] = {}
        self.baselines: dict[str, RegisteredBaseline] = {}
        self.incidents: dict[str, IncidentCheckpoint] = {}
        self.observation: SiteObservation | None = None
        self.now = datetime(2026, 8, 29, tzinfo=UTC)

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None:
        return self.sources.get(revision_id)

    async def register_baseline(self, baseline: RegisteredBaseline) -> bool:
        existing = self.baselines.setdefault(baseline.baseline.baseline_id, baseline)
        assert existing == baseline
        return existing is not baseline

    async def get_baseline(self, baseline_id: str) -> RegisteredBaseline | None:
        return self.baselines.get(baseline_id)

    async def claim_incident(
        self,
        checkpoint: IncidentCheckpoint,
    ) -> IncidentCheckpointCommit:
        existing = self.incidents.get(checkpoint.incident_id)
        if existing is None:
            self.incidents[checkpoint.incident_id] = checkpoint
            return IncidentCheckpointCommit(checkpoint, False)
        assert existing.baseline_id == checkpoint.baseline_id
        assert existing.new_source_sha256 == checkpoint.new_source_sha256
        assert existing.production_job_lineage_id == checkpoint.production_job_lineage_id
        return IncidentCheckpointCommit(existing, True)

    async def get_incident_checkpoint(self, incident_id: str) -> IncidentCheckpoint | None:
        return self.incidents.get(incident_id)

    async def advance_incident(
        self,
        checkpoint: IncidentCheckpoint,
        *,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        current = self.incidents[checkpoint.incident_id]
        if current.state_version == checkpoint.state_version:
            replay = checkpoint
            if current.report_ready_at is not None and checkpoint.report_ready_at is None:
                replay = checkpoint.model_copy(update={"report_ready_at": current.report_ready_at})
            assert current == replay
            return IncidentCheckpointCommit(current, True)
        assert current.state_version == expected_state_version
        if (
            checkpoint.stage
            in {
                IncidentWorkflowStage.REPORT_READY,
                IncidentWorkflowStage.NEEDS_REVIEW,
            }
            and checkpoint.report is not None
        ):
            checkpoint = checkpoint.model_copy(update={"report_ready_at": self.now})
        self.incidents[checkpoint.incident_id] = checkpoint
        return IncidentCheckpointCommit(checkpoint, False)

    async def allocate_report_created_at(
        self,
        *,
        incident_id: str,
        expected_state_version: int,
    ) -> IncidentCheckpointCommit:
        current = self.incidents[incident_id]
        if current.report_created_at is not None:
            return IncidentCheckpointCommit(current, True)
        assert current.state_version == expected_state_version
        assert current.stage is IncidentWorkflowStage.SEMANTIC_READY
        allocated = current.model_copy(
            update={
                "report_created_at": self.now,
                "state_version": current.state_version + 1,
                "updated_at": self.now,
            }
        )
        self.incidents[incident_id] = allocated
        return IncidentCheckpointCommit(allocated, False)

    async def get_latest_site_observation(self, **_values: str) -> SiteObservation | None:
        return self.observation


class IdempotentSemantic:
    def __init__(self) -> None:
        self.calls = 0
        self.values: dict[str, SemanticAssessment] = {}

    async def assess(
        self,
        evidence: AssessmentInput,
        *,
        analysis_revision: int = 1,
    ) -> SemanticWorkflowResult:
        key = canonical_sha256(evidence.model_dump(mode="json"))
        assessment = self.values.get(key)
        reused = assessment is not None
        if assessment is None:
            self.calls += 1
            cited = tuple(
                span.span_id for span in evidence.evidence_spans if span.side.value != "context"
            )
            assessment = SemanticAssessment(
                assessment_id=canonical_sha256({"evidence": key, "revision": analysis_revision}),
                analysis_revision=analysis_revision,
                model_id="gemini-test",
                prompt_version="semantic-assessment.v1",
                materiality="MATERIAL",
                change_kind="FACTUAL_CORRECTION",
                summary="The scientific referent changed.",
                rationale=("The old and new terms identify different organelles.",),
                evidence_span_ids=cited,
                uncertainties=(),
                confidence="HIGH",
                requires_professional_review=False,
            )
            self.values[key] = assessment
        return SemanticWorkflowResult(
            execution_key=key,
            assessment=assessment,
            trace=None,
            reused=reused,
        )


class FailOnceSemantic:
    def __init__(self, error: RuntimeError, delegate: IdempotentSemantic) -> None:
        self.error = error
        self.delegate = delegate
        self.failed = False

    async def assess(
        self,
        evidence: AssessmentInput,
        *,
        analysis_revision: int = 1,
    ) -> SemanticWorkflowResult:
        if not self.failed:
            self.failed = True
            raise self.error
        return await self.delegate.assess(evidence, analysis_revision=analysis_revision)


class BlockedSemantic:
    async def assess(
        self,
        _evidence: AssessmentInput,
        *,
        analysis_revision: int = 1,
    ) -> SemanticWorkflowResult:
        del analysis_revision
        raise SemanticAssessmentBlocked("invalid grounded output")


def _profile() -> TranslationProfile:
    return TranslationProfile(
        profile_id="demo-ueb-40x25-v1",
        liblouis_version="3.38.0",
        translation_tables=(
            TranslationTable(name="en-ueb-g2.ctb", sha256="a" * 64),
            TranslationTable(name="en-us-brf.dis", sha256="b" * 64),
        ),
        cells_per_line=40,
        lines_per_page=25,
    )


def _add_source(
    ledger: MemoryLedger,
    artifacts: MemoryArtifacts,
    *,
    version: str,
    raw: bytes,
    fetched_at: datetime,
) -> StoredSourceRevision:
    digest = hashlib.sha256(raw).hexdigest()
    revision_id = f"drive:file:{version}:{digest}"
    ref = content_addressed_ref(
        raw,
        bucket_name=artifacts.bucket_name,
        object_name=f"sources/file/{version}/{digest}.md",
        kind=ArtifactKind.SOURCE_SNAPSHOT,
    )
    artifacts.values[ref.uri] = raw
    source = StoredSourceRevision(
        revision_id=revision_id,
        source_sha256=digest,
        file_id="file",
        mime_type="text/markdown",
        provider_version=version,
        fetched_at=fetched_at,
        artifact=ref,
    )
    ledger.sources[revision_id] = source
    return source


async def _system() -> tuple[
    IncidentWorkflow,
    MemoryLedger,
    MemoryArtifacts,
    IdempotentSemantic,
    RegisteredBaseline,
    StoredSourceRevision,
]:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    ledger = MemoryLedger()
    ledger.now = now + timedelta(seconds=2)
    artifacts = MemoryArtifacts()
    translator = LiblouisAdapter(DeterministicLouis())
    profile = _profile()
    v1 = _add_source(
        ledger,
        artifacts,
        version="62",
        raw=b"# Biology\n\nThe mitochondria stores genetic instructions.\n",
        fetched_at=now,
    )
    v2 = _add_source(
        ledger,
        artifacts,
        version="63",
        raw=b"# Biology\n\nThe nucleus stores genetic instructions.\n",
        fetched_at=now + timedelta(seconds=1),
    )
    registration = await BaselineRegistrationWorkflow(
        ledger=ledger,
        artifact_store=artifacts,
        profile=profile,
        translator=translator,
    ).register_demo_fixture(
        production_id="WO-DEMO-001",
        source_revision_id=v1.revision_id,
        expected_file_id="file",
        approval_label="DEMO_FIXTURE_APPROVED",
        site_id="demo-site",
        queue_name="Braille-Embosser-Sim",
        idempotency_key=baseline_registration_idempotency_key(
            production_id="WO-DEMO-001",
            source_file_id="file",
            source_revision_id=v1.revision_id,
            translation_profile_id="demo-ueb-40x25-v1",
            approval_label="DEMO_FIXTURE_APPROVED",
            site_id="demo-site",
            queue_name="Braille-Embosser-Sim",
        ),
    )
    title = f"BER|WO-DEMO-001|{registration.record.baseline.approved_brf_sha256[:12]}|BASELINE"
    baseline = registration.record.model_copy(
        update={
            "baseline": registration.record.baseline.model_copy(
                update={
                    "scheduler_job_id": 42,
                    "scheduler_job_title": title,
                    "status": BaselineStatus.PRODUCTION_LINK_VERIFIED,
                    "state_version": 1,
                }
            )
        }
    )
    ledger.baselines[baseline.baseline.baseline_id] = baseline
    job = QueueObservation(
        scheduler_job_id=42,
        owner="relay-operator",
        title=title,
        destination="Braille-Embosser-Sim",
        state=JobState.PROCESSING,
        observed_at=now + timedelta(seconds=1),
        impressions_completed=0,
    )
    observation_body = {
        "site_id": "demo-site",
        "bridge_id": "demo-bridge",
        "queue_name": "Braille-Embosser-Sim",
        "sequence": 1,
        "observed_at": (now + timedelta(seconds=1)).isoformat(),
        "observations": [job.model_dump(mode="json")],
        "printer_state": "processing",
        "printer_state_reasons": [],
        "printer_accepting_jobs": True,
        "previous_observation_sha256": None,
        "source": "cups_read_only_observer",
        "schema_version": "site-observation.v1",
    }
    ledger.observation = SiteObservation.model_validate(
        {**observation_body, "observation_id": canonical_sha256(observation_body)}
    )
    semantic = IdempotentSemantic()
    workflow = IncidentWorkflow(
        ledger=ledger,
        artifact_store=artifacts,
        profile=profile,
        translator=translator,
        semantic_workflow=semantic,
        bridge_id="demo-bridge",
        clock=lambda: now + timedelta(seconds=2),
    )
    return workflow, ledger, artifacts, semantic, baseline, v2


@pytest.mark.asyncio
async def test_v1_to_v2_builds_one_report_first_incident_and_reuses_it() -> None:
    workflow, ledger, artifacts, semantic, baseline, v2 = await _system()

    first = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )
    allocated_created_at = first.checkpoint.report_created_at
    allocated_ready_at = first.checkpoint.report_ready_at
    ledger.now += timedelta(hours=1)
    second = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )

    assert first.checkpoint.stage == "REPORT_READY"
    assert first.checkpoint == second.checkpoint
    assert first.report == second.report
    assert first.disposition_packet == second.disposition_packet
    assert first.report is not None and first.disposition_packet is not None
    assert allocated_created_at is not None
    assert allocated_ready_at is not None
    assert first.report.created_at == allocated_created_at
    assert first.report.created_at > v2.fetched_at
    assert second.checkpoint.report_created_at == allocated_created_at
    assert second.checkpoint.report_ready_at == allocated_ready_at
    assert semantic.calls == 1
    assert len(ledger.incidents) == 1
    assert first.report.braille_impact.pages_changed is True
    assert "FULL_VOLUME_REPLACEMENT_REVIEW" in first.report.recommended_human_steps
    assert "CONSIDER_OPERATOR_STOP_AND_ISOLATION" in first.report.recommended_human_steps
    assert first.disposition_packet.candidate_label == ("CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER")
    assert first.disposition_packet.authority_notice
    assert first.checkpoint.report is not None
    assert first.checkpoint.disposition_packet is not None
    assert artifacts.values[first.checkpoint.report.uri]
    report_payload = json.loads(artifacts.values[first.checkpoint.report.uri])
    packet_payload = json.loads(artifacts.values[first.checkpoint.disposition_packet.uri])
    for schema_name, payload in (
        ("production-incident-report.v1.json", report_payload),
        ("human-disposition-packet.v1.json", packet_payload),
    ):
        schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        assert list(validator.iter_errors(payload)) == []


@pytest.mark.asyncio
async def test_content_equivalent_drive_revision_reuses_first_incident_without_model_call() -> None:
    workflow, ledger, artifacts, semantic, baseline, v2 = await _system()
    first = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )
    repeated_v2 = _add_source(
        ledger,
        artifacts,
        version="64",
        raw=artifacts.values[v2.artifact.uri],
        fetched_at=v2.fetched_at + timedelta(minutes=1),
    )

    replay = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=repeated_v2.revision_id,
    )

    assert replay.checkpoint == first.checkpoint
    assert replay.checkpoint.new_source_revision_id == v2.revision_id
    assert replay.report == first.report
    assert replay.report is not None
    assert replay.report.new_source_revision_id == v2.revision_id
    assert semantic.calls == 1


@pytest.mark.asyncio
async def test_content_equivalent_replay_resumes_partial_incident_on_first_revision_lineage() -> (
    None
):
    workflow, ledger, artifacts, _semantic, baseline, v2 = await _system()
    delegate = IdempotentSemantic()
    workflow.semantic_workflow = FailOnceSemantic(
        SemanticAssessmentUnavailable("model transport unavailable"), delegate
    )
    with pytest.raises(SemanticAssessmentUnavailable):
        await workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        )
    repeated_v2 = _add_source(
        ledger,
        artifacts,
        version="64",
        raw=artifacts.values[v2.artifact.uri],
        fetched_at=v2.fetched_at + timedelta(minutes=1),
    )

    recovered = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=repeated_v2.revision_id,
    )

    assert recovered.checkpoint.new_source_revision_id == v2.revision_id
    assert recovered.report is not None
    assert recovered.report.new_source_revision_id == v2.revision_id
    assert delegate.calls == 1


@pytest.mark.parametrize(
    "transient_error",
    [
        SemanticAssessmentUnavailable("model transport unavailable"),
        SemanticExecutionInProgress("semantic lease is active"),
    ],
)
@pytest.mark.asyncio
async def test_transient_semantic_failure_leaves_incident_resumable_at_impact_ready(
    transient_error: RuntimeError,
) -> None:
    workflow, ledger, _artifacts, _semantic, baseline, v2 = await _system()
    delegate = IdempotentSemantic()
    workflow.semantic_workflow = FailOnceSemantic(transient_error, delegate)

    with pytest.raises(type(transient_error)):
        await workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        )

    checkpoint = next(iter(ledger.incidents.values()))
    assert checkpoint.stage is IncidentWorkflowStage.IMPACT_READY
    assert checkpoint.semantic_assessment is None
    assert checkpoint.report is None

    recovered = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )
    replay = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )

    assert recovered.checkpoint.stage is IncidentWorkflowStage.REPORT_READY
    assert replay.checkpoint == recovered.checkpoint
    assert replay.report == recovered.report
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_invalid_semantic_output_terminalizes_as_needs_review() -> None:
    workflow, ledger, _artifacts, _semantic, baseline, v2 = await _system()
    workflow.semantic_workflow = BlockedSemantic()

    result = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )

    assert result.checkpoint.stage is IncidentWorkflowStage.NEEDS_REVIEW
    assert result.checkpoint.blocking_reason == "SEMANTIC_ASSESSMENT_INVALID"
    assert result.checkpoint.report is None
    assert next(iter(ledger.incidents.values())) == result.checkpoint


@pytest.mark.asyncio
async def test_baseline_revision_replay_does_not_create_an_incident() -> None:
    workflow, ledger, _artifacts, semantic, baseline, _v2 = await _system()

    result = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=baseline.baseline.source_revision_id,
    )

    assert result.duplicate_source is True
    assert result.report is None
    assert ledger.incidents == {}
    assert semantic.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        BaselineStatus.AWAITING_PRODUCTION_LINK,
        BaselineStatus.PROVISIONAL_PRODUCTION_LINK,
    ],
)
async def test_changed_revision_cannot_treat_unverified_baseline_as_production_linked(
    status: BaselineStatus,
) -> None:
    workflow, ledger, _artifacts, semantic, baseline, v2 = await _system()
    unlinked = baseline.model_copy(
        update={
            "baseline": baseline.baseline.model_copy(
                update={
                    "scheduler_job_id": (
                        None
                        if status is BaselineStatus.AWAITING_PRODUCTION_LINK
                        else baseline.baseline.scheduler_job_id
                    ),
                    "scheduler_job_title": (
                        None
                        if status is BaselineStatus.AWAITING_PRODUCTION_LINK
                        else baseline.baseline.scheduler_job_title
                    ),
                    "status": status,
                    "state_version": (
                        0 if status is BaselineStatus.AWAITING_PRODUCTION_LINK else 1
                    ),
                }
            )
        }
    )
    ledger.baselines[baseline.baseline.baseline_id] = unlinked

    with pytest.raises(IncidentWorkflowError, match="not verified"):
        await workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        )

    assert semantic.calls == 0
    assert ledger.incidents == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_write", range(1, 10))
async def test_restart_after_each_artifact_checkpoint_converges(failure_write: int) -> None:
    workflow, _ledger, artifacts, semantic, baseline, v2 = await _system()
    artifacts.puts = 0
    artifacts.fail_after = failure_write

    with pytest.raises(RuntimeError, match="simulated crash"):
        await workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        )

    artifacts.fail_after = None
    recovered = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )

    assert recovered.checkpoint.stage == "REPORT_READY"
    assert recovered.report is not None
    assert recovered.disposition_packet is not None
    assert semantic.calls == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_revision_converges_to_one_candidate_and_report() -> None:
    workflow, ledger, artifacts, semantic, baseline, v2 = await _system()

    first, second = await asyncio.gather(
        workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        ),
        workflow.process_source_revision(
            baseline_id=baseline.baseline.baseline_id,
            new_source_revision_id=v2.revision_id,
        ),
    )

    assert first.checkpoint == second.checkpoint
    assert semantic.calls == 1
    assert len(ledger.incidents) == 1
    assert sum("/braille/candidates/" in uri for uri in artifacts.values) == 1
    assert sum("/reports/" in uri for uri in artifacts.values) == 1
    assert sum("/disposition-packets/" in uri for uri in artifacts.values) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence_kind", "reason"),
    [
        ("missing", "MISSING_LINEAGE"),
        ("stale", "SITE_OBSERVATION_STALE"),
        ("wrong-title", "JOB_LINEAGE_MISMATCH"),
        ("unknown", "SITE_OBSERVATION_BLOCKING"),
    ],
)
async def test_blocking_site_evidence_produces_review_packet_not_precise_recommendation(
    evidence_kind: str,
    reason: str,
) -> None:
    workflow, ledger, _artifacts, _semantic, baseline, v2 = await _system()
    if evidence_kind == "missing":
        ledger.observation = None
    else:
        assert ledger.observation is not None
        if evidence_kind == "stale":
            ledger.observation = ledger.observation.model_copy(
                update={"observed_at": datetime(2026, 8, 29, 11, 0, tzinfo=UTC)}
            )
        elif evidence_kind == "wrong-title":
            job = ledger.observation.observations[0].model_copy(update={"title": "wrong"})
            ledger.observation = ledger.observation.model_copy(update={"observations": (job,)})
        else:
            job = ledger.observation.observations[0].model_copy(update={"state": JobState.UNKNOWN})
            ledger.observation = ledger.observation.model_copy(update={"observations": (job,)})

    result = await workflow.process_source_revision(
        baseline_id=baseline.baseline.baseline_id,
        new_source_revision_id=v2.revision_id,
    )

    assert result.checkpoint.stage == "NEEDS_REVIEW"
    assert result.checkpoint.blocking_reason == reason
    assert result.report is not None and result.disposition_packet is not None
    assert result.report.blocking_reason == reason
    assert "CONSIDER_OPERATOR_STOP_AND_ISOLATION" not in result.report.recommended_human_steps
