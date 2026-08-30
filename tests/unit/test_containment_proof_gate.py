from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from braille_errata_relay.adapters.firestore_ledger import FirestoreGate0Ledger
from braille_errata_relay.application.containment_proof import (
    ContainmentProofConflict,
    ContainmentProofRejected,
    ContainmentProofWorkflow,
)
from braille_errata_relay.application.professional_review import ProfessionalReviewWorkflow
from braille_errata_relay.braille.profile import load_translation_profile, profile_sha256
from braille_errata_relay.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactOrigin,
    ArtifactRef,
    AttestationType,
    BaselineArtifacts,
    BaselineProductionLink,
    BlockingReason,
    IncidentCheckpoint,
    IncidentState,
    IncidentWorkflowStage,
    JobState,
    ProductionBaseline,
    ProfessionalDecision,
    ProofDecision,
    QueueObservation,
    RegisteredBaseline,
    SiteObservation,
    TranslationProfile,
    TruthBasis,
)


class _Snapshot:
    def __init__(self, value: dict[str, object] | None, reference: _Document | None = None) -> None:
        self.exists = value is not None
        self._value = value
        self.reference = reference

    def to_dict(self) -> dict[str, object] | None:
        return self._value.copy() if self._value is not None else None


class _Document:
    def __init__(self, store: dict[str, dict[str, object]], path: str) -> None:
        self.store = store
        self.path = path

    def get(self, transaction: object | None = None) -> _Snapshot:
        del transaction
        return _Snapshot(self.store.get(self.path), self)


class _Query:
    def __init__(self, store: dict[str, dict[str, object]], collection: str) -> None:
        self.store = store
        self.collection = collection
        self.maximum = 100

    def where(self, **_values: object) -> _Query:
        return self

    def order_by(self, *_values: object) -> _Query:
        return self

    def limit(self, value: int) -> _Query:
        self.maximum = value
        return self

    def stream(self) -> list[_Snapshot]:
        prefix = f"{self.collection}/"
        rows = sorted(
            (path, value) for path, value in self.store.items() if path.startswith(prefix)
        )
        return [
            _Snapshot(value, _Document(self.store, path)) for path, value in rows[: self.maximum]
        ]


class _Collection:
    def __init__(self, store: dict[str, dict[str, object]], name: str) -> None:
        self.store = store
        self.name = name

    def document(self, document_id: str) -> _Document:
        return _Document(self.store, f"{self.name}/{document_id}")

    def where(self, **values: object) -> _Query:
        return _Query(self.store, self.name).where(**values)


class _Client:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, object]] = {}

    def collection(self, name: str) -> _Collection:
        return _Collection(self.store, name)


class _Transaction:
    def __init__(self, store: dict[str, dict[str, object]]) -> None:
        self.store = store

    def create(self, ref: _Document, value: dict[str, object]) -> None:
        if ref.path in self.store:
            raise AssertionError(f"duplicate create: {ref.path}")
        self.store[ref.path] = value.copy()

    def set(self, ref: _Document, value: dict[str, object]) -> None:
        self.store[ref.path] = value.copy()

    def get(self, query: _Query) -> list[_Snapshot]:
        return query.stream()


class _ArtifactStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def read(self, ref: ArtifactRef) -> bytes:
        return self.values[ref.sha256]


@dataclass
class _Fixture:
    client: _Client
    ledger: FirestoreGate0Ledger
    now: list[datetime]
    checkpoint: IncidentCheckpoint
    profile: TranslationProfile
    artifacts: _ArtifactStore
    review: ProfessionalReviewWorkflow
    workflow: ContainmentProofWorkflow


ROOT = Path(__file__).resolve().parents[2]
INCIDENT_ID = "1" * 64
BASELINE_ID = "2" * 64
CANDIDATE_BRF_BYTES = b"fixture-candidate-brf-v1\r\n"
CANDIDATE_SHA256 = hashlib.sha256(CANDIDATE_BRF_BYTES).hexdigest()
APPROVED_BASELINE_SHA256 = "4" * 64


def _ref(kind: ArtifactKind, marker: str) -> ArtifactRef:
    return ArtifactRef(
        sha256=marker * 64,
        kind=kind,
        byte_length=12,
        uri=f"gs://relay-test/{kind.value.lower()}/{marker * 64}",
    )


def _manifest_for(
    *,
    candidate_sha256: str,
    checkpoint: IncidentCheckpoint,
    profile: TranslationProfile,
    created_at: datetime,
) -> tuple[ArtifactManifest, bytes, ArtifactRef]:
    manifest = ArtifactManifest(
        artifact_kind=ArtifactKind.FULL_CANDIDATE_BRF,
        artifact_sha256=candidate_sha256,
        byte_length=12,
        source_revision_id=checkpoint.new_source_revision_id,
        source_sha256=checkpoint.new_source_sha256,
        normalized_source_sha256="5" * 64,
        baseline_manifest_sha256="6" * 64,
        translation_profile_sha256=profile_sha256(profile),
        liblouis_version=profile.liblouis_version,
        formatter_version=profile.formatter_version,
        page_count=1,
        page_sha256=("7" * 64,),
        source_map_uri="gs://relay-test/private-source-map.json",
        created_at=created_at,
        generator_build={"build": "fixture"},
    )
    encoded = canonical_json_bytes(manifest.model_dump(mode="json"))
    digest = hashlib.sha256(encoded).hexdigest()
    return (
        manifest,
        encoded,
        ArtifactRef(
            sha256=digest,
            kind=ArtifactKind.ARTIFACT_MANIFEST,
            byte_length=len(encoded),
            uri=f"gs://relay-test/manifests/{digest}.json",
        ),
    )


def _fixture() -> _Fixture:
    now = [datetime(2026, 8, 30, 12, 0, tzinfo=UTC)]
    client = _Client()
    ledger = FirestoreGate0Ledger(
        project_id="test-project",
        client=client,  # type: ignore[arg-type]
        transaction_runner=lambda callback: callback(_Transaction(client.store)),
        clock=lambda: now[0],
    )
    unbound_profile = load_translation_profile(
        ROOT / "config" / "translation_profiles" / "demo-ueb-40x25-v1.json"
    )
    profile = unbound_profile.model_copy(
        update={
            "translation_tables": tuple(
                table.model_copy(update={"sha256": f"{index + 1:x}" * 64})
                for index, table in enumerate(unbound_profile.translation_tables)
            )
        }
    )
    provisional_checkpoint = IncidentCheckpoint(
        incident_id=INCIDENT_ID,
        baseline_id=BASELINE_ID,
        new_source_revision_id="drive:file:63:" + "8" * 64,
        new_source_sha256="8" * 64,
        production_job_lineage_id="9" * 64,
        stage=IncidentWorkflowStage.REPORT_READY,
        state_version=3,
        candidate_brf=ArtifactRef(
            sha256=CANDIDATE_SHA256,
            kind=ArtifactKind.FULL_CANDIDATE_BRF,
            byte_length=len(CANDIDATE_BRF_BYTES),
            uri=f"gs://relay-test/candidates/{CANDIDATE_SHA256}.brf",
        ),
        report=_ref(ArtifactKind.REPORT, "a"),
        disposition_packet=_ref(ArtifactKind.HUMAN_DISPOSITION_PACKET, "b"),
        report_created_at=now[0] - timedelta(seconds=1),
        report_ready_at=now[0] - timedelta(seconds=1),
        updated_at=now[0],
    )
    _, manifest_bytes, manifest_ref = _manifest_for(
        candidate_sha256=CANDIDATE_SHA256,
        checkpoint=provisional_checkpoint,
        profile=profile,
        created_at=now[0],
    )
    checkpoint = provisional_checkpoint.model_copy(update={"candidate_manifest": manifest_ref})
    baseline = RegisteredBaseline(
        baseline=ProductionBaseline(
            baseline_id=BASELINE_ID,
            production_id="WO-DEMO-001",
            source_revision_id="drive:file:62:" + "a" * 64,
            source_sha256="a" * 64,
            source_file_id="file",
            approved_brf_sha256=APPROVED_BASELINE_SHA256,
            baseline_manifest_sha256="6" * 64,
            translation_profile_sha256=profile_sha256(profile),
            artifact_origin=ArtifactOrigin.DEMO_GENERATED_FIXTURE,
            approval_label="DEMO_FIXTURE_APPROVED",
            site_id="demo-site",
            queue_name="Braille-Embosser-Sim",
            scheduler_job_id=42,
            scheduler_job_title=(f"BER|WO-DEMO-001|{APPROVED_BASELINE_SHA256[:12]}|BASELINE"),
        ),
        artifacts=BaselineArtifacts(
            source=_ref(ArtifactKind.SOURCE_SNAPSHOT, "a"),
            normalized_source=_ref(ArtifactKind.NORMALIZED_SOURCE, "b"),
            approved_brf=_ref(ArtifactKind.BASELINE_BRF, "4"),
            source_map=_ref(ArtifactKind.SOURCE_MAP, "c"),
            manifest=_ref(ArtifactKind.ARTIFACT_MANIFEST, "6"),
            translation_profile=_ref(ArtifactKind.TRANSLATION_PROFILE, "d"),
        ),
        created_at=now[0],
    )
    production_link = BaselineProductionLink(
        link_id="e" * 64,
        baseline_id=BASELINE_ID,
        scheduler_job_id=42,
        scheduler_job_title=f"BER|WO-DEMO-001|{APPROVED_BASELINE_SHA256[:12]}|BASELINE",
        site_observation_id="f" * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        baseline_brf_sha256=APPROVED_BASELINE_SHA256,
        baseline_state_version=1,
        idempotency_key_sha256="a" * 64,
        evidence_observed_at=now[0],
        linked_at=now[0],
    )
    client.store[f"incidents/{INCIDENT_ID}"] = {"record": checkpoint.model_dump(mode="json")}
    client.store[f"baselines/{BASELINE_ID}"] = {"record": baseline.model_dump(mode="json")}
    client.store[f"baseline_production_links/{BASELINE_ID}"] = {
        "record": production_link.model_dump(mode="json")
    }
    artifacts = _ArtifactStore()
    artifacts.values[CANDIDATE_SHA256] = CANDIDATE_BRF_BYTES
    artifacts.values[manifest_ref.sha256] = manifest_bytes
    review = ProfessionalReviewWorkflow(ledger=ledger, clock=lambda: now[0])
    workflow = ContainmentProofWorkflow(
        ledger=ledger,
        artifact_store=artifacts,
        profile=profile,
        bridge_id="single-pc-bridge",
        clock=lambda: now[0],
    )
    return _Fixture(client, ledger, now, checkpoint, profile, artifacts, review, workflow)


async def _halt_and_isolate(fixture: _Fixture) -> tuple[object, object]:
    halt = await fixture.review.record_disposition(
        incident_id=INCIDENT_ID,
        decision=ProfessionalDecision.HALT_REQUESTED,
        selected_role="production_coordinator",
        expected_state_version=0,
        note="Human coordinator requested a manual halt assessment.",
        idempotency_key="halt-1",
        actor_principal="coordinator@example.test",
    )
    fixture.now[0] += timedelta(seconds=1)
    isolation = await fixture.review.record_operator_attestation(
        incident_id=INCIDENT_ID,
        attestation_type=AttestationType.PHYSICAL_OUTPUT_ISOLATED,
        truth_basis=TruthBasis.SIMULATED_DEMO,
        selected_role="machine_operator",
        expected_state_version=halt.state.state_version,
        note="Operator isolated simulated physical output.",
        idempotency_key="isolation-1",
        actor_principal="operator@example.test",
    )
    return halt, isolation


def _admit_observation(
    fixture: _Fixture,
    *,
    observed_at: datetime,
    states: tuple[JobState, ...],
    marker: str = "d",
) -> SiteObservation:
    title = f"BER|WO-DEMO-001|{APPROVED_BASELINE_SHA256[:12]}|BASELINE"
    observations = tuple(
        QueueObservation(
            scheduler_job_id=42,
            owner="relay-operator",
            title=title,
            destination="Braille-Embosser-Sim",
            state=state,
            observed_at=observed_at,
            completed_at=observed_at
            if state in {JobState.CANCELED, JobState.ABORTED, JobState.COMPLETED}
            else None,
        )
        for state in states
    )
    observation = SiteObservation(
        observation_id=marker * 64,
        site_id="demo-site",
        bridge_id="single-pc-bridge",
        queue_name="Braille-Embosser-Sim",
        sequence=1,
        observed_at=observed_at,
        observations=observations,
        printer_state="idle",
        printer_accepting_jobs=True,
    )
    fixture.client.store[f"site_observations/{observation.observation_id}"] = {
        "record": observation.model_dump(mode="json")
    }
    head_id = canonical_sha256(
        {
            "site_id": observation.site_id,
            "bridge_id": observation.bridge_id,
            "queue_name": observation.queue_name,
        }
    )
    fixture.client.store[f"site_observation_heads/{head_id}"] = {
        "observation_id": observation.observation_id,
        "sequence": observation.sequence,
    }
    return observation


async def _ready_for_confirmation(fixture: _Fixture) -> tuple[object, object, SiteObservation]:
    halt, isolation = await _halt_and_isolate(fixture)
    fixture.now[0] += timedelta(seconds=1)
    observation = _admit_observation(
        fixture,
        observed_at=fixture.now[0],
        states=(JobState.CANCELED,),
    )
    return halt, isolation, observation


async def _confirm(
    fixture: _Fixture,
    *,
    halt: object,
    isolation: object,
    observation: SiteObservation,
    idempotency_key: str = "containment-1",
    expected_state_version: int = 2,
    note: str = "Coordinator confirms the complete attributed containment evidence set.",
) -> object:
    return await fixture.workflow.record_containment_confirmation(
        incident_id=INCIDENT_ID,
        halt_disposition_record_id=halt.disposition.record_id,
        site_observation_id=observation.observation_id,
        physical_output_isolation_attestation_id=isolation.attestation.record_id,
        selected_role="production_coordinator",
        expected_state_version=expected_state_version,
        note=note,
        idempotency_key=idempotency_key,
        actor_principal="coordinator@example.test",
    )


async def _approve_proof(
    fixture: _Fixture,
    *,
    expected_state_version: int = 4,
    idempotency_key: str = "proof-approve-1",
) -> object:
    provenance = await fixture.workflow.resolve_candidate_provenance(incident_id=INCIDENT_ID)
    return await fixture.workflow.record_proof(
        incident_id=INCIDENT_ID,
        candidate_sha256=provenance.candidate_sha256,
        manifest_sha256=provenance.manifest_sha256,
        decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
        review_basis="DEMO_FIXTURE_REVIEW",
        selected_role="proofreader",
        expected_state_version=expected_state_version,
        note="Fixture proof reviewed against the exact candidate lineage.",
        findings=("Fixture-only proof evidence is complete.",),
        visual_only_uncertainty=False,
        idempotency_key=idempotency_key,
        actor_principal="proofreader@example.test",
    )


@pytest.mark.asyncio
async def test_neither_an_attestation_nor_scheduler_state_alone_can_confirm_containment() -> None:
    attestation_only = _fixture()
    halt, isolation = await _halt_and_isolate(attestation_only)

    eligibility = await attestation_only.workflow.containment_eligibility(incident_id=INCIDENT_ID)

    assert eligibility.eligible is False
    assert eligibility.blocking_reason is BlockingReason.CONTAINMENT_EVIDENCE_MISSING
    with pytest.raises(ContainmentProofRejected) as missing_observation:
        await _confirm(
            attestation_only,
            halt=halt,
            isolation=isolation,
            observation=SiteObservation(
                observation_id="f" * 64,
                site_id="demo-site",
                bridge_id="single-pc-bridge",
                queue_name="Braille-Embosser-Sim",
                sequence=1,
                observed_at=attestation_only.now[0],
                observations=(),
                printer_state="idle",
            ),
        )
    assert missing_observation.value.reason is BlockingReason.CONTAINMENT_EVIDENCE_MISSING
    assert (
        await attestation_only.ledger.get_incident_review_state(INCIDENT_ID)
    ).state is IncidentState.CONTAINMENT_IN_PROGRESS

    scheduler_only = _fixture()
    halt_only = await scheduler_only.review.record_disposition(
        incident_id=INCIDENT_ID,
        decision=ProfessionalDecision.HALT_REQUESTED,
        selected_role="production_coordinator",
        expected_state_version=0,
        note="Manual containment assessment requested.",
        idempotency_key="halt-only",
        actor_principal="coordinator@example.test",
    )
    scheduler_only.now[0] += timedelta(seconds=1)
    observation = _admit_observation(
        scheduler_only,
        observed_at=scheduler_only.now[0],
        states=(JobState.CANCELED,),
    )

    scheduler_eligibility = await scheduler_only.workflow.containment_eligibility(
        incident_id=INCIDENT_ID
    )

    assert scheduler_eligibility.eligible is False
    assert scheduler_eligibility.blocking_reason is BlockingReason.CONTAINMENT_CONFIRMATION_REQUIRED
    assert halt_only.state.state is IncidentState.HALT_REQUESTED
    assert observation.observations[0].state is JobState.CANCELED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variant", "expected_reason"),
    (
        ("stale", BlockingReason.CONTAINMENT_EVIDENCE_STALE),
        ("missing", BlockingReason.CONTAINMENT_EVIDENCE_MISSING),
        ("ambiguous", BlockingReason.CONTAINMENT_EVIDENCE_AMBIGUOUS),
        ("blocking", BlockingReason.CONTAINMENT_EVIDENCE_MISMATCH),
    ),
)
async def test_containment_rejects_stale_missing_or_ambiguous_post_halt_observations(
    variant: str,
    expected_reason: BlockingReason,
) -> None:
    fixture = _fixture()
    halt, isolation = await _halt_and_isolate(fixture)
    fixture.now[0] += timedelta(seconds=19)
    if variant == "stale":
        observation = _admit_observation(
            fixture,
            observed_at=fixture.now[0] - timedelta(seconds=16),
            states=(JobState.CANCELED,),
        )
    elif variant == "missing":
        observation = _admit_observation(
            fixture,
            observed_at=fixture.now[0],
            states=(),
        )
    elif variant == "ambiguous":
        observation = _admit_observation(
            fixture,
            observed_at=fixture.now[0],
            states=(JobState.CANCELED, JobState.CANCELED),
        )
    else:
        observation = _admit_observation(
            fixture,
            observed_at=fixture.now[0],
            states=(JobState.UNKNOWN,),
        )

    eligibility = await fixture.workflow.containment_eligibility(incident_id=INCIDENT_ID)

    assert eligibility.eligible is False
    assert eligibility.blocking_reason is expected_reason
    with pytest.raises(ContainmentProofRejected) as rejected:
        await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)
    assert rejected.value.reason is expected_reason
    state = await fixture.ledger.get_incident_review_state(INCIDENT_ID)
    assert state is not None
    assert state.state is IncidentState.CONTAINMENT_IN_PROGRESS
    assert not any(path.startswith("containment_confirmations/") for path in fixture.client.store)


@pytest.mark.asyncio
async def test_unadmitted_or_superseded_observation_cannot_be_used_as_containment_evidence() -> (
    None
):
    fixture = _fixture()
    halt, isolation, observation = await _ready_for_confirmation(fixture)
    head_id = canonical_sha256(
        {
            "site_id": observation.site_id,
            "bridge_id": observation.bridge_id,
            "queue_name": observation.queue_name,
        }
    )
    # The record exists, but a different canonical cloud-head ID represents a
    # local pending/superseded outbox observation. It cannot support a human
    # containment conclusion.
    fixture.client.store[f"site_observation_heads/{head_id}"]["observation_id"] = "f" * 64

    eligibility = await fixture.workflow.containment_eligibility(incident_id=INCIDENT_ID)
    assert eligibility.eligible is False
    with pytest.raises(ContainmentProofRejected) as rejected:
        await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)

    assert rejected.value.reason is BlockingReason.OBSERVATION_OUTBOX_CONTRADICTION
    state = await fixture.ledger.get_incident_review_state(INCIDENT_ID)
    assert state is not None
    assert state.state is IncidentState.CONTAINMENT_IN_PROGRESS


@pytest.mark.asyncio
async def test_fresh_correlated_isolation_and_coordinator_confirmation_are_append_only_and_idempotent() -> (
    None
):
    fixture = _fixture()
    halt, isolation, observation = await _ready_for_confirmation(fixture)

    first = await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)
    replay = await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)

    assert first.duplicate is False
    assert replay.duplicate is True
    assert first.state.state is IncidentState.AWAITING_PROOF
    assert first.state.state_version == 4
    assert first.confirmation.observed_job_state is JobState.CANCELED
    assert first.confirmation.state_path == (
        IncidentState.CONTAINED_BY_HUMAN,
        IncidentState.AWAITING_PROOF,
    )
    assert sum(path.startswith("containment_confirmations/") for path in fixture.client.store) == 1
    with pytest.raises(ContainmentProofConflict):
        await _confirm(
            fixture,
            halt=halt,
            isolation=isolation,
            observation=observation,
            idempotency_key="containment-stale",
            expected_state_version=2,
        )


@pytest.mark.asyncio
async def test_proof_is_forbidden_precontainment_and_binds_exact_candidate_provenance() -> None:
    precontainment = _fixture()
    await _halt_and_isolate(precontainment)
    provenance = await precontainment.workflow.resolve_candidate_provenance(incident_id=INCIDENT_ID)
    with pytest.raises(ContainmentProofRejected) as before_containment:
        await precontainment.workflow.record_proof(
            incident_id=INCIDENT_ID,
            candidate_sha256=provenance.candidate_sha256,
            manifest_sha256=provenance.manifest_sha256,
            decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
            review_basis="DEMO_FIXTURE_REVIEW",
            selected_role="proofreader",
            expected_state_version=2,
            note="Proof cannot precede containment.",
            findings=(),
            visual_only_uncertainty=False,
            idempotency_key="premature-proof",
            actor_principal="proofreader@example.test",
        )
    assert before_containment.value.reason is BlockingReason.PROOF_NOT_ELIGIBLE

    fixture = _fixture()
    halt, isolation, observation = await _ready_for_confirmation(fixture)
    await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)
    provenance = await fixture.workflow.resolve_candidate_provenance(incident_id=INCIDENT_ID)
    with pytest.raises(ContainmentProofRejected) as wrong_candidate:
        await fixture.workflow.record_proof(
            incident_id=INCIDENT_ID,
            candidate_sha256="f" * 64,
            manifest_sha256=provenance.manifest_sha256,
            decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
            review_basis="DEMO_FIXTURE_REVIEW",
            selected_role="proofreader",
            expected_state_version=4,
            note="The form must bind the current candidate.",
            findings=(),
            visual_only_uncertainty=False,
            idempotency_key="wrong-candidate",
            actor_principal="proofreader@example.test",
        )
    assert wrong_candidate.value.reason is BlockingReason.CANDIDATE_PROVENANCE_MISMATCH
    with pytest.raises(ContainmentProofRejected) as visual_only:
        await fixture.workflow.record_proof(
            incident_id=INCIDENT_ID,
            candidate_sha256=provenance.candidate_sha256,
            manifest_sha256=provenance.manifest_sha256,
            decision=ProofDecision.APPROVED_FOR_HUMAN_SUBMISSION,
            review_basis="DEMO_FIXTURE_REVIEW",
            selected_role="proofreader",
            expected_state_version=4,
            note="Visual-only uncertainty must remain a block.",
            findings=(),
            visual_only_uncertainty=True,
            idempotency_key="visual-only",
            actor_principal="proofreader@example.test",
        )
    assert visual_only.value.reason is BlockingReason.PROOF_REVIEW_REQUIRED

    approved = await _approve_proof(fixture)
    replay = await _approve_proof(fixture)

    assert approved.duplicate is False
    assert replay.duplicate is True
    assert approved.state.state is IncidentState.AWAITING_REPLACEMENT
    assert approved.state.state_version == 6
    assert approved.proof.candidate_label == "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER"
    assert approved.proof.translation_profile_sha256 == profile_sha256(fixture.profile)
    assert all(table.sha256 for table in approved.proof.translation_tables)


@pytest.mark.asyncio
async def test_proof_provenance_rehashes_the_actual_candidate_brf() -> None:
    fixture = _fixture()
    fixture.artifacts.values[CANDIDATE_SHA256] = b"tampered-candidate-bytes"

    with pytest.raises(ContainmentProofRejected) as rejected:
        await fixture.workflow.resolve_candidate_provenance(incident_id=INCIDENT_ID)

    assert rejected.value.reason is BlockingReason.CANDIDATE_PROVENANCE_MISMATCH


@pytest.mark.asyncio
async def test_proof_rejection_preserves_a_visible_block_and_candidate_change_invalidates_prior_approval() -> (
    None
):
    rejected_fixture = _fixture()
    halt, isolation, observation = await _ready_for_confirmation(rejected_fixture)
    await _confirm(rejected_fixture, halt=halt, isolation=isolation, observation=observation)
    provenance = await rejected_fixture.workflow.resolve_candidate_provenance(
        incident_id=INCIDENT_ID
    )
    rejected = await rejected_fixture.workflow.record_proof(
        incident_id=INCIDENT_ID,
        candidate_sha256=provenance.candidate_sha256,
        manifest_sha256=provenance.manifest_sha256,
        decision=ProofDecision.REJECTED,
        review_basis="DEMO_FIXTURE_REVIEW",
        selected_role="proofreader",
        expected_state_version=4,
        note="Fixture proof found a mismatch.",
        findings=("Review again after correction.",),
        visual_only_uncertainty=False,
        idempotency_key="proof-rejected",
        actor_principal="proofreader@example.test",
    )
    assert rejected.state.state is IncidentState.PROOF_REJECTED
    assert rejected.state.blocking_reason is BlockingReason.PROOF_REJECTED

    fixture = _fixture()
    halt, isolation, observation = await _ready_for_confirmation(fixture)
    await _confirm(fixture, halt=halt, isolation=isolation, observation=observation)
    approved = await _approve_proof(fixture)
    current_checkpoint = await fixture.ledger.get_incident_checkpoint(INCIDENT_ID)
    assert current_checkpoint is not None
    fixture.now[0] += timedelta(seconds=1)
    new_candidate_bytes = b"fixture-candidate-brf-v2\r\n"
    new_candidate_sha256 = hashlib.sha256(new_candidate_bytes).hexdigest()
    _, new_manifest_bytes, new_manifest_ref = _manifest_for(
        candidate_sha256=new_candidate_sha256,
        checkpoint=current_checkpoint,
        profile=fixture.profile,
        created_at=fixture.now[0],
    )
    fixture.artifacts.values[new_candidate_sha256] = new_candidate_bytes
    fixture.artifacts.values[new_manifest_ref.sha256] = new_manifest_bytes
    updated_checkpoint = current_checkpoint.model_copy(
        update={
            "candidate_brf": ArtifactRef(
                sha256=new_candidate_sha256,
                kind=ArtifactKind.FULL_CANDIDATE_BRF,
                byte_length=len(new_candidate_bytes),
                uri=f"gs://relay-test/candidates/{new_candidate_sha256}.brf",
            ),
            "candidate_manifest": new_manifest_ref,
            "state_version": current_checkpoint.state_version + 1,
            "updated_at": fixture.now[0],
        }
    )
    fixture.client.store[f"incidents/{INCIDENT_ID}"] = {
        "record": updated_checkpoint.model_dump(mode="json")
    }

    with pytest.raises(ContainmentProofConflict, match="candidate changed"):
        await _approve_proof(fixture, expected_state_version=approved.state.state_version)

    invalidated_state = await fixture.ledger.get_incident_review_state(INCIDENT_ID)
    assert invalidated_state is not None
    assert invalidated_state.state is IncidentState.AWAITING_PROOF
    assert invalidated_state.state_version == 7
    assert invalidated_state.current_candidate_sha256 == new_candidate_sha256
    assert (
        sum(path.startswith("candidate_approval_invalidations/") for path in fixture.client.store)
        == 1
    )
    assert sum(path.startswith("proof_records/") for path in fixture.client.store) == 1
    renewed = await _approve_proof(
        fixture,
        expected_state_version=invalidated_state.state_version,
        idempotency_key="proof-approve-2",
    )
    assert renewed.state.state is IncidentState.AWAITING_REPLACEMENT


def test_containment_and_proof_workflow_has_no_cups_or_device_control_surface() -> None:
    source = ROOT / "src" / (ContainmentProofWorkflow.__module__.replace(".", "/") + ".py")
    rendered = source.read_text(encoding="utf-8").lower()

    for forbidden in (
        "import subprocess",
        "import cups",
        "cups.connection",
        "print-job",
        "create-job",
        "send-document",
        "hold-job",
        "release-job",
        "cancel-job",
        "restart-job",
    ):
        assert forbidden not in rendered
