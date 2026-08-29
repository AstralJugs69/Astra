"""Typed domain contracts for the report-first production overlay.

The models deliberately describe evidence and human-owned transitions.  They do not
contain a production-control command or a generic device operation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HexSha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=512)]
BoundedNote = Annotated[str, Field(max_length=2000)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class SourceProvider(StrEnum):
    GOOGLE_DRIVE = "google_drive"


class SourceBlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"


class ArtifactKind(StrEnum):
    BASELINE_BRF = "BASELINE_BRF"
    FULL_CANDIDATE_BRF = "FULL_CANDIDATE_BRF"
    RANGE_CANDIDATE_BRF = "RANGE_CANDIDATE_BRF"
    SOURCE_SNAPSHOT = "SOURCE_SNAPSHOT"
    NORMALIZED_SOURCE = "NORMALIZED_SOURCE"
    SOURCE_DIFF = "SOURCE_DIFF"
    SOURCE_MAP = "SOURCE_MAP"
    ARTIFACT_MANIFEST = "ARTIFACT_MANIFEST"
    TRANSLATION_PROFILE = "TRANSLATION_PROFILE"
    BRAILLE_IMPACT = "BRAILLE_IMPACT"
    SEMANTIC_ASSESSMENT = "SEMANTIC_ASSESSMENT"
    REPORT = "REPORT"
    HUMAN_DISPOSITION_PACKET = "HUMAN_DISPOSITION_PACKET"
    ENDPOINT_RECEIPT = "ENDPOINT_RECEIPT"


class ArtifactOrigin(StrEnum):
    EXTERNALLY_APPROVED_IMPORT = "EXTERNALLY_APPROVED_IMPORT"
    DEMO_GENERATED_FIXTURE = "DEMO_GENERATED_FIXTURE"


class BaselineStatus(StrEnum):
    AWAITING_PRODUCTION_LINK = "AWAITING_PRODUCTION_LINK"
    PROVISIONAL_PRODUCTION_LINK = "PROVISIONAL_PRODUCTION_LINK"
    PRODUCTION_LINK_VERIFIED = "PRODUCTION_LINK_VERIFIED"


class ProductionLinkBlockingReason(StrEnum):
    BASELINE_NOT_FOUND = "BASELINE_NOT_FOUND"
    BASELINE_NOT_AWAITING_LINK = "BASELINE_NOT_AWAITING_LINK"
    IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
    MISSING_SITE_OBSERVATION = "MISSING_SITE_OBSERVATION"
    STALE_SITE_OBSERVATION = "STALE_SITE_OBSERVATION"
    AMBIGUOUS_SITE_OBSERVATION = "AMBIGUOUS_SITE_OBSERVATION"
    WRONG_JOB = "WRONG_JOB"
    WRONG_TITLE = "WRONG_TITLE"
    WRONG_ARTIFACT = "WRONG_ARTIFACT"
    WRONG_QUEUE = "WRONG_QUEUE"
    STALE_STATE_VERSION = "STALE_STATE_VERSION"
    MISSING_ENDPOINT_EVIDENCE = "MISSING_ENDPOINT_EVIDENCE"
    ENDPOINT_EVIDENCE_MISMATCH = "ENDPOINT_EVIDENCE_MISMATCH"
    ENDPOINT_EVIDENCE_CONFLICT = "ENDPOINT_EVIDENCE_CONFLICT"
    HISTORICAL_CORRECTION_REQUIRED = "HISTORICAL_CORRECTION_REQUIRED"


class IncidentState(StrEnum):
    DETECTED = "DETECTED"
    ASSESSING = "ASSESSING"
    REPORT_READY = "REPORT_READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CONTINUE_ACCEPTED = "CONTINUE_ACCEPTED"
    HALT_REQUESTED = "HALT_REQUESTED"
    DEFERRED = "DEFERRED"
    REPORT_REJECTED = "REPORT_REJECTED"
    CONTAINMENT_IN_PROGRESS = "CONTAINMENT_IN_PROGRESS"
    CONTAINED_BY_HUMAN = "CONTAINED_BY_HUMAN"
    CONTAINMENT_UNCERTAIN = "CONTAINMENT_UNCERTAIN"
    AWAITING_PROOF = "AWAITING_PROOF"
    PROOF_REJECTED = "PROOF_REJECTED"
    PROOF_APPROVED = "PROOF_APPROVED"
    AWAITING_REPLACEMENT = "AWAITING_REPLACEMENT"
    REPLACEMENT_OBSERVED = "REPLACEMENT_OBSERVED"
    VERIFYING = "VERIFYING"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESOLVED_BY_HUMAN = "RESOLVED_BY_HUMAN"
    RESOLVED_NO_REMEDIATION_BY_HUMAN = "RESOLVED_NO_REMEDIATION_BY_HUMAN"


class IncidentWorkflowStage(StrEnum):
    DETECTED = "DETECTED"
    DIFF_READY = "DIFF_READY"
    CANDIDATE_READY = "CANDIDATE_READY"
    IMPACT_READY = "IMPACT_READY"
    SEMANTIC_READY = "SEMANTIC_READY"
    REPORT_READY = "REPORT_READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class BlockingReason(StrEnum):
    SOURCE_INACCESSIBLE = "SOURCE_INACCESSIBLE"
    SEMANTIC_ASSESSMENT_INVALID = "SEMANTIC_ASSESSMENT_INVALID"
    SEMANTIC_REVIEW_REQUIRED = "SEMANTIC_REVIEW_REQUIRED"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    INCOMPATIBLE_BASELINE_PROFILE = "INCOMPATIBLE_BASELINE_PROFILE"
    BRAILLE_ENGINE_NOT_READY = "BRAILLE_ENGINE_NOT_READY"
    SITE_OBSERVATION_STALE = "SITE_OBSERVATION_STALE"
    MISSING_LINEAGE = "MISSING_LINEAGE"
    AMBIGUOUS_SITE_EVIDENCE = "AMBIGUOUS_SITE_EVIDENCE"
    SITE_OBSERVATION_BLOCKING = "SITE_OBSERVATION_BLOCKING"
    WRONG_QUEUE = "WRONG_QUEUE"
    JOB_LINEAGE_MISMATCH = "JOB_LINEAGE_MISMATCH"
    TELEMETRY_REPLAY = "TELEMETRY_REPLAY"
    OUTPUT_INTEGRITY_FAILED = "OUTPUT_INTEGRITY_FAILED"
    STALE_STATE_VERSION = "STALE_STATE_VERSION"


class Materiality(StrEnum):
    MATERIAL = "MATERIAL"
    NOT_MATERIAL = "NOT_MATERIAL"
    UNCERTAIN = "UNCERTAIN"


class ChangeKind(StrEnum):
    FACTUAL_CORRECTION = "FACTUAL_CORRECTION"
    INSTRUCTION_CHANGE = "INSTRUCTION_CHANGE"
    NAVIGATION_CHANGE = "NAVIGATION_CHANGE"
    EDITORIAL_CHANGE = "EDITORIAL_CHANGE"
    FORMATTING_ONLY = "FORMATTING_ONLY"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class JobState(StrEnum):
    PENDING = "PENDING"
    PENDING_HELD = "PENDING_HELD"
    PROCESSING = "PROCESSING"
    PROCESSING_STOPPED = "PROCESSING_STOPPED"
    CANCELED = "CANCELED"
    ABORTED = "ABORTED"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


class ProfessionalDecision(StrEnum):
    CONTINUE_ACCEPTED = "CONTINUE_ACCEPTED"
    HALT_REQUESTED = "HALT_REQUESTED"
    DEFERRED = "DEFERRED"
    REPORT_REJECTED = "REPORT_REJECTED"


class AttestationType(StrEnum):
    DEVICE_STOP_CONFIRMED = "DEVICE_STOP_CONFIRMED"
    PHYSICAL_OUTPUT_ISOLATED = "PHYSICAL_OUTPUT_ISOLATED"
    BUFFER_CLEARED = "BUFFER_CLEARED"
    ACTION_NOT_POSSIBLE = "ACTION_NOT_POSSIBLE"


class TruthBasis(StrEnum):
    HUMAN_ATTESTATION = "HUMAN_ATTESTATION"
    SIMULATED_DEMO = "SIMULATED_DEMO"


class CaptureState(StrEnum):
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


class ProofDecision(StrEnum):
    APPROVED_FOR_HUMAN_SUBMISSION = "APPROVED_FOR_HUMAN_SUBMISSION"
    REJECTED = "REJECTED"


class ReviewBasis(StrEnum):
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DEMO_FIXTURE_REVIEW = "DEMO_FIXTURE_REVIEW"


class VerificationResult(StrEnum):
    VERIFIED_FOR_HUMAN_CLOSURE = "VERIFIED_FOR_HUMAN_CLOSURE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class SourceLocator(DomainModel):
    provider: SourceProvider
    file_id: NonEmpty
    mime_type: str = "text/markdown"


class SourceMetadata(DomainModel):
    locator: SourceLocator
    provider_version: NonEmpty
    modified_at: datetime | None = None
    byte_length: int = Field(ge=0)


class SourceRevision(DomainModel):
    revision_id: NonEmpty
    metadata: SourceMetadata
    source_sha256: HexSha256
    fetched_at: datetime


class SourceSnapshot(DomainModel):
    """Authoritatively fetched bytes paired with immutable provider lineage.

    The bytes are deliberately excluded from normal model serialization so a
    repository adapter cannot accidentally place source content in Firestore.
    Artifact storage receives them through the explicit ``source_bytes`` field.
    """

    revision: SourceRevision
    source_bytes: bytes = Field(repr=False, exclude=True)


class DriveChangeSignal(DomainModel):
    file_id: NonEmpty
    removed: bool = False


class DriveChangeBatch(DomainModel):
    start_cursor: NonEmpty
    final_cursor: NonEmpty
    signals: tuple[DriveChangeSignal, ...]
    snapshots: tuple[SourceSnapshot, ...]


class SourceBlock(DomainModel):
    block_id: NonEmpty
    kind: SourceBlockKind
    text: NonEmpty
    ordinal: int = Field(ge=0)


class NormalizedSource(DomainModel):
    schema_version: int = 1
    document_id: NonEmpty
    blocks: tuple[SourceBlock, ...]
    normalized_text: str
    normalized_source_sha256: HexSha256


class SourceDiff(DomainModel):
    schema_version: int = 1
    old_source_sha256: HexSha256
    new_source_sha256: HexSha256
    changed_block_ids: tuple[NonEmpty, ...]
    old_blocks: tuple[SourceBlock, ...]
    new_blocks: tuple[SourceBlock, ...]
    context_blocks: tuple[SourceBlock, ...] = ()


class TranslationTable(DomainModel):
    name: NonEmpty
    sha256: HexSha256 | None = None


class TranslationProfile(DomainModel):
    schema_version: str = "translation-profile.v1"
    profile_id: NonEmpty
    language: str = "en-US"
    braille_code: str = "UEB_GRADE_2"
    liblouis_version: NonEmpty
    translation_tables: tuple[TranslationTable, ...]
    formatter_version: NonEmpty = "relay-formatter.v1"
    cells_per_line: int = Field(gt=0, le=200)
    lines_per_page: int = Field(gt=0, le=200)
    newline_bytes_hex: str = "0d0a"
    page_separator_hex: str = "0c"
    final_page_separator: bool = False
    page_number_policy: str = "NONE_FOR_FIXTURE"
    normalization: str = "NFC_LF_TRIM_TRAILING_SPACE"

    @property
    def is_bound(self) -> bool:
        return self.liblouis_version != "unresolved" and all(
            table.sha256 is not None for table in self.translation_tables
        )


class PageRange(DomainModel):
    start: int = Field(gt=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> PageRange:
        if self.end < self.start:
            raise ValueError("page range end must not precede start")
        return self

    def as_tuple(self) -> tuple[int, int]:
        return self.start, self.end


class PageRecord(DomainModel):
    number: int = Field(gt=0)
    sha256: HexSha256
    source_block_ids: tuple[NonEmpty, ...] = ()


class ArtifactManifest(DomainModel):
    schema_version: str = "artifact-manifest.v1"
    artifact_kind: ArtifactKind
    artifact_sha256: HexSha256
    byte_length: int = Field(ge=0)
    source_revision_id: NonEmpty
    source_sha256: HexSha256
    normalized_source_sha256: HexSha256
    baseline_manifest_sha256: HexSha256 | None = None
    translation_profile_sha256: HexSha256
    liblouis_version: NonEmpty
    formatter_version: NonEmpty
    page_count: int = Field(ge=0)
    page_sha256: tuple[HexSha256, ...]
    source_map_uri: str
    created_at: datetime
    generator_build: dict[str, str] = Field(default_factory=dict)
    parent_artifact_sha256: HexSha256 | None = None
    page_range: PageRange | None = None

    @field_validator("page_sha256")
    @classmethod
    def page_count_matches(cls, value: tuple[HexSha256, ...]) -> tuple[HexSha256, ...]:
        return value

    @model_validator(mode="after")
    def validate_page_count(self) -> ArtifactManifest:
        if self.page_count != len(self.page_sha256):
            raise ValueError("page_count must equal the number of page hashes")
        return self


class ArtifactRef(DomainModel):
    sha256: HexSha256
    kind: ArtifactKind
    byte_length: int = Field(ge=0)
    uri: str


class ProductionBaseline(DomainModel):
    baseline_id: HexSha256
    production_id: NonEmpty
    source_revision_id: NonEmpty
    source_sha256: HexSha256
    source_file_id: NonEmpty | None = None
    approved_brf_sha256: HexSha256
    baseline_manifest_sha256: HexSha256
    translation_profile_sha256: HexSha256
    artifact_origin: ArtifactOrigin
    approval_label: NonEmpty
    site_id: NonEmpty
    queue_name: NonEmpty
    scheduler_job_id: int | None = Field(default=None, gt=0)
    scheduler_job_title: NonEmpty | None = None
    compatible_profile: bool = True
    production_id_origin: Literal["EXTERNAL_REFERENCE"] = "EXTERNAL_REFERENCE"
    status: BaselineStatus = BaselineStatus.AWAITING_PRODUCTION_LINK
    state_version: int = Field(default=0, ge=0)


class BaselineArtifacts(DomainModel):
    source: ArtifactRef
    normalized_source: ArtifactRef
    approved_brf: ArtifactRef
    source_map: ArtifactRef
    manifest: ArtifactRef
    translation_profile: ArtifactRef


class RegisteredBaseline(DomainModel):
    schema_version: Literal["registered-baseline.v1"] = "registered-baseline.v1"
    baseline: ProductionBaseline
    artifacts: BaselineArtifacts
    created_at: datetime


class BaselineProductionLink(DomainModel):
    schema_version: Literal["baseline-production-link.v1", "baseline-production-link.v2"] = (
        "baseline-production-link.v2"
    )
    link_id: HexSha256
    baseline_id: HexSha256
    scheduler_job_id: int = Field(gt=0)
    scheduler_job_title: NonEmpty
    site_observation_id: HexSha256
    site_id: NonEmpty
    bridge_id: NonEmpty
    queue_name: NonEmpty
    baseline_brf_sha256: HexSha256
    baseline_state_version: int = Field(ge=1)
    idempotency_key_sha256: HexSha256
    evidence_observed_at: datetime
    linked_at: datetime | None = None
    verified_at: datetime | None = None
    verification_basis: Literal[
        "READ_ONLY_EXACT_JOB_QUEUE_TITLE_AND_HASH_PREFIX",
        "READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY",
    ] = "READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY"

    @model_validator(mode="after")
    def validate_link_semantics(self) -> BaselineProductionLink:
        if self.schema_version == "baseline-production-link.v1":
            if self.verified_at is None or self.linked_at is not None:
                raise ValueError("legacy production link timestamps are inconsistent")
            if self.verification_basis != "READ_ONLY_EXACT_JOB_QUEUE_TITLE_AND_HASH_PREFIX":
                raise ValueError("legacy production link basis is inconsistent")
        else:
            if self.linked_at is None or self.verified_at is not None:
                raise ValueError("advisory production link timestamps are inconsistent")
            if self.verification_basis != "READ_ONLY_EXACT_JOB_QUEUE_AND_TITLE_ADVISORY_ONLY":
                raise ValueError("advisory production link basis is inconsistent")
        return self


class EndpointReceipt(DomainModel):
    schema_version: Literal["endpoint-receipt.v1"] = "endpoint-receipt.v1"
    receipt_id: HexSha256
    baseline_id: HexSha256
    production_link_id: HexSha256
    scheduler_job_id: int = Field(gt=0)
    scheduler_job_title: NonEmpty
    site_id: NonEmpty
    queue_name: NonEmpty
    simulated_endpoint_id: Literal["relay-capture://demo-embosser"] = (
        "relay-capture://demo-embosser"
    )
    approved_baseline_brf_sha256: HexSha256
    endpoint_received_sha256: HexSha256
    capture_manifest_sha256: HexSha256
    terminal_event_sha256: HexSha256
    capture_state: CaptureState
    evidence_timestamp: datetime
    verified_at: datetime
    truth_basis: Literal["SIMULATED_DEMO"] = "SIMULATED_DEMO"
    submitting_principal: NonEmpty
    idempotency_key_sha256: HexSha256
    expected_baseline_state_version: int = Field(ge=1)
    baseline_state_version: int = Field(ge=2)
    artifact_uri: str

    @model_validator(mode="after")
    def validate_exact_bytes_and_version(self) -> EndpointReceipt:
        if self.endpoint_received_sha256 != self.approved_baseline_brf_sha256:
            raise ValueError("endpoint-received bytes do not match the approved baseline")
        if self.baseline_state_version != self.expected_baseline_state_version + 1:
            raise ValueError("endpoint receipt target version is invalid")
        if not self.artifact_uri.startswith("gs://"):
            raise ValueError("endpoint receipt artifact must be an immutable GCS object")
        return self


class EndpointEvidenceSubmission(DomainModel):
    schema_version: Literal["endpoint-evidence-submission.v1"] = "endpoint-evidence-submission.v1"
    baseline_id: HexSha256
    production_link_id: HexSha256
    scheduler_job_id: int = Field(gt=0)
    scheduler_job_title: NonEmpty
    site_id: NonEmpty
    queue_name: NonEmpty
    simulated_endpoint_id: Literal["relay-capture://demo-embosser"] = (
        "relay-capture://demo-embosser"
    )
    approved_baseline_brf_sha256: HexSha256
    endpoint_received_sha256: HexSha256
    capture_manifest_sha256: HexSha256
    terminal_event_sha256: HexSha256
    capture_state: CaptureState
    evidence_timestamp: datetime
    truth_basis: Literal["SIMULATED_DEMO"] = "SIMULATED_DEMO"
    expected_baseline_state_version: int = Field(ge=1)
    idempotency_key: NonEmpty

    @model_validator(mode="after")
    def validate_exact_received_bytes(self) -> EndpointEvidenceSubmission:
        if self.endpoint_received_sha256 != self.approved_baseline_brf_sha256:
            raise ValueError("endpoint-received bytes do not match the approved baseline")
        return self


class BaselineLinkCorrection(DomainModel):
    schema_version: Literal["baseline-link-correction.v1"] = "baseline-link-correction.v1"
    correction_id: HexSha256
    baseline_id: HexSha256
    production_link_id: HexSha256
    expected_baseline_state_version: int = Field(ge=1)
    baseline_state_version: int = Field(ge=2)
    reason: Literal["PRIOR_LINK_LACKED_ENDPOINT_BYTE_CONFIRMATION"] = (
        "PRIOR_LINK_LACKED_ENDPOINT_BYTE_CONFIRMATION"
    )
    prior_report_id: HexSha256 | None = None
    prior_report_created_before_endpoint_confirmation: Literal[True] = True
    corrected_at: datetime
    submitting_principal: NonEmpty
    idempotency_key_sha256: HexSha256

    @model_validator(mode="after")
    def validate_target_version(self) -> BaselineLinkCorrection:
        if self.baseline_state_version != self.expected_baseline_state_version + 1:
            raise ValueError("correction target version is invalid")
        return self


class EvidenceSide(StrEnum):
    OLD = "old"
    NEW = "new"
    CONTEXT = "context"


class SemanticEvidenceSpan(DomainModel):
    span_id: NonEmpty
    side: EvidenceSide
    block_kind: SourceBlockKind
    text: Annotated[str, Field(min_length=1, max_length=4000)]


class SemanticImpactSummary(DomainModel):
    pages_changed: bool
    baseline_page_count: int = Field(ge=0)
    candidate_page_count: int = Field(ge=0)


class AssessmentInput(DomainModel):
    schema_version: str = "semantic-assessment-input.v1"
    evidence_spans: tuple[SemanticEvidenceSpan, ...] = Field(min_length=1, max_length=16)
    impact_summary: SemanticImpactSummary

    @model_validator(mode="after")
    def enforce_bounded_context(self) -> AssessmentInput:
        if sum(len(span.text) for span in self.evidence_spans) > 12000:
            raise ValueError("semantic evidence exceeds the 12000 character boundary")
        return self


class SemanticAssessment(DomainModel):
    schema_version: Literal["semantic-assessment.v1"] = "semantic-assessment.v1"
    assessment_id: HexSha256
    analysis_revision: int = Field(ge=1)
    model_id: NonEmpty
    prompt_version: NonEmpty
    materiality: Materiality
    change_kind: ChangeKind
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    rationale: tuple[Annotated[str, Field(min_length=1, max_length=1000)], ...]
    evidence_span_ids: tuple[NonEmpty, ...]
    uncertainties: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = ()
    confidence: Confidence
    requires_professional_review: bool = True


class BrailleImpact(DomainModel):
    baseline_artifact_sha256: HexSha256
    candidate_artifact_sha256: HexSha256
    old_page_range: PageRange | None
    new_page_range: PageRange | None
    resynchronized_after_page: int | None = Field(default=None, ge=0)
    candidate_page_count: int = Field(ge=0)
    baseline_page_count: int = Field(ge=0)
    pages_changed: bool
    algorithm: str = "page-prefix-suffix.v1"


class ProductionContext(DomainModel):
    scheduler_job_id: int | None = Field(default=None, gt=0)
    last_observed_state: JobState | None = None
    pages_observed_complete: int | None = Field(default=None, ge=0)
    observation_id: HexSha256 | None = None
    observation_age_seconds: float | None = Field(default=None, ge=0)


class ProductionIncidentReport(DomainModel):
    schema_version: str = "production-incident-report.v1"
    incident_id: HexSha256
    baseline_id: HexSha256
    old_source_revision_id: NonEmpty
    new_source_revision_id: NonEmpty
    source_diff_artifact_sha256: HexSha256
    semantic_assessment: SemanticAssessment
    braille_impact: BrailleImpact
    production_context: ProductionContext
    recommended_human_steps: tuple[NonEmpty, ...]
    recommendation_policy_version: NonEmpty = "relay-policy.v1"
    blocking_reason: BlockingReason | None = None
    created_at: datetime


class HumanDispositionPacket(DomainModel):
    schema_version: Literal["human-disposition-packet.v1"] = "human-disposition-packet.v1"
    incident_id: HexSha256
    baseline_id: HexSha256
    external_production_id: NonEmpty
    report_sha256: HexSha256
    candidate_brf: ArtifactRef
    candidate_manifest: ArtifactRef
    candidate_label: Literal["CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER"] = (
        "CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER"
    )
    baseline_brf_sha256: HexSha256
    translation_profile_sha256: HexSha256
    braille_impact: BrailleImpact
    semantic_assessment: SemanticAssessment
    site_observation_id: HexSha256 | None = None
    observation_age_seconds: float | None = Field(default=None, ge=0)
    recommended_human_steps: tuple[NonEmpty, ...]
    blocking_reason: BlockingReason | None = None
    authority_notice: tuple[NonEmpty, ...] = (
        "Human professionals retain disposition and containment authority.",
        "Human proof approval is required before any replacement submission.",
        "Relay does not control CUPS, an embosser, or another production device.",
    )


class IncidentCheckpoint(DomainModel):
    schema_version: Literal["incident-checkpoint.v1"] = "incident-checkpoint.v1"
    incident_id: HexSha256
    baseline_id: HexSha256
    new_source_revision_id: NonEmpty
    new_source_sha256: HexSha256
    production_job_lineage_id: HexSha256
    stage: IncidentWorkflowStage = IncidentWorkflowStage.DETECTED
    state_version: int = Field(default=0, ge=0)
    normalized_source: ArtifactRef | None = None
    source_diff: ArtifactRef | None = None
    candidate_brf: ArtifactRef | None = None
    candidate_source_map: ArtifactRef | None = None
    candidate_manifest: ArtifactRef | None = None
    braille_impact: ArtifactRef | None = None
    semantic_assessment: ArtifactRef | None = None
    report: ArtifactRef | None = None
    disposition_packet: ArtifactRef | None = None
    report_created_at: datetime | None = None
    report_ready_at: datetime | None = None
    blocking_reason: BlockingReason | None = None
    updated_at: datetime


class Incident(DomainModel):
    incident_id: HexSha256
    baseline_id: HexSha256
    state: IncidentState
    state_version: int = Field(ge=0)
    report_ready_at: datetime | None = None
    current_candidate_sha256: HexSha256 | None = None
    blocking_reason: BlockingReason | None = None
    last_attributable_evidence_id: NonEmpty | None = None


class QueueObservation(DomainModel):
    scheduler_job_id: int = Field(gt=0)
    owner: NonEmpty
    title: NonEmpty
    destination: NonEmpty
    state: JobState
    state_reasons: tuple[NonEmpty, ...] = ()
    observed_at: datetime
    job_created_at: datetime | None = None
    processing_at: datetime | None = None
    completed_at: datetime | None = None
    impressions_completed: int | None = Field(default=None, ge=0)


class SiteObservation(DomainModel):
    schema_version: str = "site-observation.v1"
    observation_id: HexSha256
    site_id: NonEmpty
    bridge_id: NonEmpty
    queue_name: NonEmpty
    sequence: int = Field(gt=0)
    observed_at: datetime
    observations: tuple[QueueObservation, ...]
    printer_state: str = "unknown"
    printer_state_reasons: tuple[NonEmpty, ...] = ()
    printer_accepting_jobs: bool | None = None
    previous_observation_sha256: HexSha256 | None = None
    source: str = "cups_read_only_observer"


class ProfessionalDisposition(DomainModel):
    record_id: HexSha256
    incident_id: HexSha256
    decision: ProfessionalDecision
    selected_role: str = "production_coordinator"
    expected_state_version: int = Field(ge=0)
    idempotency_key: NonEmpty
    note: BoundedNote = ""
    actor_principal: NonEmpty
    recorded_at: datetime


class OperatorAttestation(DomainModel):
    record_id: HexSha256
    incident_id: HexSha256
    attestation_type: AttestationType
    truth_basis: TruthBasis
    selected_role: str = "machine_operator"
    expected_state_version: int = Field(ge=0)
    idempotency_key: NonEmpty
    note: BoundedNote = ""
    actor_principal: NonEmpty
    recorded_at: datetime


class ProofRecord(DomainModel):
    record_id: HexSha256
    incident_id: HexSha256
    candidate_sha256: HexSha256
    manifest_sha256: HexSha256
    decision: ProofDecision
    review_basis: ReviewBasis
    selected_role: str = "proofreader"
    expected_state_version: int = Field(ge=0)
    idempotency_key: NonEmpty
    findings: tuple[BoundedNote, ...] = ()
    actor_principal: NonEmpty
    recorded_at: datetime


class ReplacementLink(DomainModel):
    record_id: HexSha256
    incident_id: HexSha256
    approved_artifact_sha256: HexSha256
    queue_name: NonEmpty
    scheduler_job_id: int = Field(gt=0)
    observed_job_title: NonEmpty
    selected_role: str = "machine_operator"
    expected_state_version: int = Field(ge=0)
    idempotency_key: NonEmpty
    actor_principal: NonEmpty
    recorded_at: datetime


class VerificationInvariant(DomainModel):
    name: NonEmpty
    passed: bool
    details: str = ""


class VerificationReport(DomainModel):
    schema_version: str = "verification-report.v1"
    verification_id: HexSha256
    incident_id: HexSha256
    old_scheduler_job_id: int | None = Field(default=None, gt=0)
    replacement_scheduler_job_id: int | None = Field(default=None, gt=0)
    approved_artifact_sha256: HexSha256
    operator_linked_artifact_sha256: HexSha256 | None = None
    endpoint_received_sha256: HexSha256 | None = None
    endpoint_completed_capture_sha256: HexSha256 | None = None
    raw_passthrough_preflight_id: HexSha256 | None = None
    replacement_state: JobState
    old_job_terminal_evidence: str | None = None
    containment_attestation_ids: tuple[HexSha256, ...] = ()
    invariants: tuple[VerificationInvariant, ...]
    result: VerificationResult
    created_at: datetime


def enum_values(enum_type: type[StrEnum]) -> list[str]:
    """Expose closed enum values for schema and policy tooling."""

    return [member.value for member in enum_type]


def assert_no_production_control_fields(value: Any) -> None:
    """Fail closed if an untrusted payload tries to smuggle a control command."""

    forbidden = {
        "print",
        "submit",
        "cancel",
        "hold",
        "release",
        "restart",
        "pause",
        "resume",
        "execute",
        "run_shell",
        "device_command",
    }
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        found = keys.intersection(forbidden)
        if found:
            raise ValueError(f"production-control fields are forbidden: {sorted(found)}")
        for child in value.values():
            assert_no_production_control_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_no_production_control_fields(child)
