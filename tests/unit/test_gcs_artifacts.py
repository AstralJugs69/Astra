from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.api_core.exceptions import PreconditionFailed

from braille_errata_relay.adapters.gcs_artifacts import (
    ArtifactIntegrityError,
    GcsArtifactStore,
    source_snapshot_ref,
)
from braille_errata_relay.domain.models import (
    ArtifactKind,
    ArtifactRef,
    SourceLocator,
    SourceMetadata,
    SourceProvider,
    SourceRevision,
    SourceSnapshot,
)


class FakeBlob:
    def __init__(self, existing: bytes | None = None, *, conflict: bool = False) -> None:
        self.existing = existing
        self.conflict = conflict
        self.metadata: dict[str, str] | None = None
        self.upload_calls: list[dict[str, object]] = []

    def upload_from_string(self, value: bytes, **kwargs: object) -> None:
        self.upload_calls.append({"value": value, **kwargs})
        if self.conflict:
            raise PreconditionFailed("already exists")
        self.existing = value

    def download_as_bytes(self) -> bytes:
        assert self.existing is not None
        return self.existing


class FakeBucket:
    def __init__(self, blobs: dict[str, FakeBlob]) -> None:
        self.blobs = blobs

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob())


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, _name: str) -> FakeBucket:
        return self._bucket


def _ref(data: bytes) -> ArtifactRef:
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    return ArtifactRef(
        sha256=digest,
        kind=ArtifactKind.SOURCE_SNAPSHOT,
        byte_length=len(data),
        uri=f"gs://relay-bucket/sources/file/1/{digest}.md",
    )


@pytest.mark.asyncio
async def test_put_once_uses_generation_zero_and_hash_metadata() -> None:
    data = b"synthetic source"
    ref = _ref(data)
    bucket = FakeBucket({})
    store = GcsArtifactStore(bucket_name="relay-bucket", client=FakeClient(bucket))  # type: ignore[arg-type]

    assert await store.put_once(data, ref=ref) == ref

    blob = bucket.blobs[ref.uri.removeprefix("gs://relay-bucket/")]
    assert blob.upload_calls[0]["if_generation_match"] == 0
    assert blob.upload_calls[0]["checksum"] == "crc32c"
    assert blob.metadata == {
        "relay-sha256": ref.sha256,
        "relay-artifact-kind": "SOURCE_SNAPSHOT",
    }


@pytest.mark.asyncio
async def test_create_conflict_is_idempotent_only_for_exact_existing_bytes() -> None:
    data = b"synthetic source"
    ref = _ref(data)
    name = ref.uri.removeprefix("gs://relay-bucket/")
    exact = GcsArtifactStore(
        bucket_name="relay-bucket",
        client=FakeClient(FakeBucket({name: FakeBlob(data, conflict=True)})),  # type: ignore[arg-type]
    )
    assert await exact.put_once(data, ref=ref) == ref

    mismatch = GcsArtifactStore(
        bucket_name="relay-bucket",
        client=FakeClient(FakeBucket({name: FakeBlob(b"different", conflict=True)})),  # type: ignore[arg-type]
    )
    with pytest.raises(ArtifactIntegrityError, match="different stored bytes"):
        await mismatch.put_once(data, ref=ref)


@pytest.mark.asyncio
async def test_read_rehashes_stored_bytes() -> None:
    data = b"synthetic source"
    ref = _ref(data)
    name = ref.uri.removeprefix("gs://relay-bucket/")
    store = GcsArtifactStore(
        bucket_name="relay-bucket",
        client=FakeClient(FakeBucket({name: FakeBlob(b"tampered")})),  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactIntegrityError, match="content-address"):
        await store.read(ref)


def test_source_snapshot_object_name_is_content_addressed() -> None:
    data = b"synthetic source"
    ref = _ref(data)
    snapshot = SourceSnapshot(
        revision=SourceRevision(
            revision_id=f"drive:file:62:{ref.sha256}",
            metadata=SourceMetadata(
                locator=SourceLocator(
                    provider=SourceProvider.GOOGLE_DRIVE,
                    file_id="file",
                    mime_type="text/markdown",
                ),
                provider_version="62",
                modified_at=None,
                byte_length=len(data),
            ),
            source_sha256=ref.sha256,
            fetched_at=datetime(2026, 8, 29, tzinfo=UTC),
        ),
        source_bytes=data,
    )

    derived = source_snapshot_ref(snapshot, bucket_name="relay-bucket")

    assert derived.uri == f"gs://relay-bucket/sources/file/62/{ref.sha256}.md"
    assert derived.sha256 == ref.sha256
