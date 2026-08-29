"""Register an immutable synthetic baseline without owning production work."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from braille_errata_relay.adapters.firestore_ledger import StoredSourceRevision
from braille_errata_relay.adapters.gcs_artifacts import content_addressed_ref
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.braille.normalize import normalize_source_bytes
from braille_errata_relay.braille.profile import profile_json, profile_sha256
from braille_errata_relay.braille.render import render
from braille_errata_relay.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactOrigin,
    ArtifactRef,
    BaselineArtifacts,
    ProductionBaseline,
    RegisteredBaseline,
    TranslationProfile,
)


class BaselineRegistrationError(RuntimeError):
    pass


class BaselineLedger(Protocol):
    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None: ...

    async def register_baseline(self, baseline: RegisteredBaseline) -> bool: ...


class ArtifactStore(Protocol):
    bucket_name: str

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef: ...

    async def read(self, ref: ArtifactRef) -> bytes: ...


@dataclass(frozen=True)
class BaselineRegistrationResult:
    record: RegisteredBaseline
    duplicate: bool


def baseline_id(
    *,
    production_id: str,
    source_revision_id: str,
    approved_brf_sha256: str,
    translation_profile_sha256: str,
) -> str:
    identity = production_id + source_revision_id + approved_brf_sha256 + translation_profile_sha256
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class BaselineRegistrationWorkflow:
    def __init__(
        self,
        *,
        ledger: BaselineLedger,
        artifact_store: ArtifactStore,
        profile: TranslationProfile,
        translator: LiblouisAdapter,
    ) -> None:
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.profile = profile
        self.translator = translator

    async def register_demo_fixture(
        self,
        *,
        production_id: str,
        source_revision_id: str,
        expected_file_id: str,
        approval_label: str,
        site_id: str,
        queue_name: str,
    ) -> BaselineRegistrationResult:
        if approval_label != "DEMO_FIXTURE_APPROVED":
            raise BaselineRegistrationError(
                "synthetic baseline approval label must be DEMO_FIXTURE_APPROVED"
            )
        source = await self.ledger.get_source_revision(source_revision_id)
        if source is None:
            raise BaselineRegistrationError("source revision is not durably claimed")
        if source.file_id != expected_file_id:
            raise BaselineRegistrationError("source revision does not belong to the requested file")
        raw = await self.artifact_store.read(source.artifact)
        if hashlib.sha256(raw).hexdigest() != source.source_sha256:
            raise BaselineRegistrationError("source artifact failed lineage verification")
        normalized = normalize_source_bytes(raw, document_id=source.file_id)
        rendered = render(
            normalized,
            self.profile,
            self.translator,
            source_revision_id=source.revision_id,
            source_sha256=source.source_sha256,
            artifact_kind=ArtifactKind.BASELINE_BRF,
            created_at=source.fetched_at,
            generator_build={"profile_sha256": profile_sha256(self.profile)},
        )

        normalized_bytes = canonical_json_bytes(normalized.model_dump(mode="json"))
        source_map_bytes = canonical_json_bytes(rendered.source_map)
        profile_bytes = profile_json(self.profile).encode("utf-8")
        brf_ref = content_addressed_ref(
            rendered.brf,
            bucket_name=self.artifact_store.bucket_name,
            object_name=f"braille/baselines/{rendered.manifest.artifact_sha256}.brf",
            kind=ArtifactKind.BASELINE_BRF,
        )
        normalized_ref = content_addressed_ref(
            normalized_bytes,
            bucket_name=self.artifact_store.bucket_name,
            object_name=(
                f"normalized/{source.source_sha256}/{normalized.normalized_source_sha256}.json"
            ),
            kind=ArtifactKind.NORMALIZED_SOURCE,
        )
        source_map_ref = content_addressed_ref(
            source_map_bytes,
            bucket_name=self.artifact_store.bucket_name,
            object_name=f"maps/{brf_ref.sha256}.json",
            kind=ArtifactKind.SOURCE_MAP,
        )
        profile_ref = content_addressed_ref(
            profile_bytes,
            bucket_name=self.artifact_store.bucket_name,
            object_name=f"profiles/{profile_sha256(self.profile)}.json",
            kind=ArtifactKind.TRANSLATION_PROFILE,
        )
        manifest = rendered.manifest.model_copy(update={"source_map_uri": source_map_ref.uri})
        manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
        manifest_sha256 = canonical_sha256(manifest.model_dump(mode="json"))
        manifest_ref = content_addressed_ref(
            manifest_bytes,
            bucket_name=self.artifact_store.bucket_name,
            object_name=f"manifests/{manifest_sha256}.json",
            kind=ArtifactKind.ARTIFACT_MANIFEST,
        )
        if manifest_ref.sha256 != manifest_sha256:
            raise BaselineRegistrationError("manifest canonical identity is inconsistent")

        for artifact, ref in (
            (normalized_bytes, normalized_ref),
            (rendered.brf, brf_ref),
            (source_map_bytes, source_map_ref),
            (manifest_bytes, manifest_ref),
            (profile_bytes, profile_ref),
        ):
            await self.artifact_store.put_once(artifact, ref=ref)

        identity = baseline_id(
            production_id=production_id,
            source_revision_id=source.revision_id,
            approved_brf_sha256=brf_ref.sha256,
            translation_profile_sha256=profile_ref.sha256,
        )
        record = RegisteredBaseline(
            baseline=ProductionBaseline(
                baseline_id=identity,
                production_id=production_id,
                source_revision_id=source.revision_id,
                source_sha256=source.source_sha256,
                source_file_id=source.file_id,
                approved_brf_sha256=brf_ref.sha256,
                baseline_manifest_sha256=manifest_ref.sha256,
                translation_profile_sha256=profile_ref.sha256,
                artifact_origin=ArtifactOrigin.DEMO_GENERATED_FIXTURE,
                approval_label=approval_label,
                site_id=site_id,
                queue_name=queue_name,
            ),
            artifacts=BaselineArtifacts(
                source=source.artifact,
                normalized_source=normalized_ref,
                approved_brf=brf_ref,
                source_map=source_map_ref,
                manifest=manifest_ref,
                translation_profile=profile_ref,
            ),
            created_at=source.fetched_at,
        )
        duplicate = await self.ledger.register_baseline(record)
        return BaselineRegistrationResult(record=record, duplicate=duplicate)
