from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from braille_errata_relay.adapters.firestore_ledger import StoredSourceRevision
from braille_errata_relay.application.baseline_registration import (
    BaselineRegistrationError,
    BaselineRegistrationWorkflow,
)
from braille_errata_relay.braille.liblouis_adapter import LiblouisAdapter
from braille_errata_relay.contracts.canonical_json import canonical_sha256
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    RegisteredBaseline,
    TranslationProfile,
    TranslationTable,
)


class CellLouis:
    dotsIO = 1
    ucBrl = 2
    __version__ = "3.38.0"

    @staticmethod
    def translateString(_tables: list[str], text: str, *, mode: int) -> str:
        assert mode == 3
        return "\u2801" * len(text)


class MemoryArtifacts:
    bucket_name = "relay-test"

    def __init__(self, initial: tuple[ArtifactRef, bytes]) -> None:
        self.values = {initial[0].uri: initial[1]}

    async def read(self, ref: ArtifactRef) -> bytes:
        return self.values[ref.uri]

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef:
        assert hashlib.sha256(artifact).hexdigest() == ref.sha256
        existing = self.values.setdefault(ref.uri, artifact)
        assert existing == artifact
        return ref


class MemoryBaselineLedger:
    def __init__(self, source: StoredSourceRevision) -> None:
        self.source = source
        self.baselines: dict[str, RegisteredBaseline] = {}

    async def get_source_revision(self, revision_id: str) -> StoredSourceRevision | None:
        return self.source if revision_id == self.source.revision_id else None

    async def register_baseline(self, baseline: RegisteredBaseline) -> bool:
        existing = self.baselines.setdefault(baseline.baseline.baseline_id, baseline)
        assert existing == baseline
        return existing is not baseline


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


def _components() -> tuple[BaselineRegistrationWorkflow, MemoryBaselineLedger]:
    raw = b"# Biology\n\nThe nucleus stores genetic instructions.\n"
    digest = hashlib.sha256(raw).hexdigest()
    source_ref = ArtifactRef(
        sha256=digest,
        kind=ArtifactKind.SOURCE_SNAPSHOT,
        byte_length=len(raw),
        uri=f"gs://relay-test/sources/file/62/{digest}.md",
    )
    source = StoredSourceRevision(
        revision_id=f"drive:file:62:{digest}",
        source_sha256=digest,
        file_id="file",
        mime_type="text/markdown",
        provider_version="62",
        fetched_at=datetime(2026, 8, 29, tzinfo=UTC),
        artifact=source_ref,
    )
    ledger = MemoryBaselineLedger(source)
    workflow = BaselineRegistrationWorkflow(
        ledger=ledger,
        artifact_store=MemoryArtifacts((source_ref, raw)),
        profile=_profile(),
        translator=LiblouisAdapter(CellLouis()),
    )
    return workflow, ledger


@pytest.mark.asyncio
async def test_demo_baseline_registration_is_immutable_and_idempotent() -> None:
    workflow, ledger = _components()
    revision_id = ledger.source.revision_id
    values = {
        "production_id": "WO-DEMO-001",
        "source_revision_id": revision_id,
        "expected_file_id": "file",
        "approval_label": "DEMO_FIXTURE_APPROVED",
        "site_id": "demo-site",
        "queue_name": "Braille-Embosser-Sim",
    }

    first = await workflow.register_demo_fixture(**values)
    second = await workflow.register_demo_fixture(**values)

    assert first.duplicate is False
    assert second.duplicate is True
    assert first.record == second.record
    assert len(ledger.baselines) == 1
    assert first.record.baseline.production_id_origin == "EXTERNAL_REFERENCE"
    assert first.record.baseline.status == "AWAITING_PRODUCTION_LINK"
    assert first.record.baseline.scheduler_job_id is None
    assert first.record.baseline.approval_label == "DEMO_FIXTURE_APPROVED"
    assert first.record.baseline.artifact_origin == "DEMO_GENERATED_FIXTURE"
    assert first.record.artifacts.approved_brf.sha256 == (first.record.baseline.approved_brf_sha256)
    assert first.record.artifacts.manifest.sha256 == (
        first.record.baseline.baseline_manifest_sha256
    )
    assert canonical_sha256(first.record.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_demo_baseline_rejects_unapproved_fixture_label() -> None:
    workflow, ledger = _components()

    with pytest.raises(BaselineRegistrationError, match="DEMO_FIXTURE_APPROVED"):
        await workflow.register_demo_fixture(
            production_id="WO-DEMO-001",
            source_revision_id=ledger.source.revision_id,
            expected_file_id="file",
            approval_label="APPROVED",
            site_id="demo-site",
            queue_name="Braille-Embosser-Sim",
        )
