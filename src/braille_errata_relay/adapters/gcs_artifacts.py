"""Create-only content-addressed Cloud Storage artifact adapter."""

from __future__ import annotations

import asyncio
import hashlib
from typing import cast
from urllib.parse import quote

from google.api_core.exceptions import PreconditionFailed
from google.cloud.storage.client import Client  # type: ignore[import-untyped]

from braille_errata_relay.domain.models import ArtifactKind, ArtifactRef, SourceSnapshot


class ArtifactIntegrityError(RuntimeError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _segment(value: str) -> str:
    if not value:
        raise ValueError("artifact path segment cannot be empty")
    return quote(value, safe="-._~")


def source_snapshot_ref(snapshot: SourceSnapshot, *, bucket_name: str) -> ArtifactRef:
    revision = snapshot.revision
    locator = revision.metadata.locator
    object_name = (
        f"sources/{_segment(locator.file_id)}/"
        f"{_segment(revision.metadata.provider_version)}/{revision.source_sha256}.md"
    )
    return ArtifactRef(
        sha256=revision.source_sha256,
        kind=ArtifactKind.SOURCE_SNAPSHOT,
        byte_length=len(snapshot.source_bytes),
        uri=f"gs://{bucket_name}/{object_name}",
    )


class GcsArtifactStore:
    def __init__(
        self,
        *,
        bucket_name: str,
        project_id: str | None = None,
        client: Client | None = None,
    ) -> None:
        if not bucket_name:
            raise ValueError("GCS_ARTIFACT_BUCKET must be explicit")
        self.bucket_name = bucket_name
        self.client = client or Client(project=project_id)
        self.bucket = self.client.bucket(bucket_name)

    def _object_name(self, ref: ArtifactRef) -> str:
        prefix = f"gs://{self.bucket_name}/"
        if not ref.uri.startswith(prefix):
            raise ArtifactIntegrityError("artifact reference is outside the configured bucket")
        object_name = ref.uri.removeprefix(prefix)
        if not object_name or object_name.startswith("/") or ".." in object_name.split("/"):
            raise ArtifactIntegrityError("artifact object path is invalid")
        return object_name

    async def put_once(self, artifact: bytes, *, ref: ArtifactRef) -> ArtifactRef:
        if len(artifact) != ref.byte_length or _sha256(artifact) != ref.sha256:
            raise ArtifactIntegrityError("artifact bytes do not match the immutable reference")
        blob = self.bucket.blob(self._object_name(ref))
        blob.metadata = {
            "relay-sha256": ref.sha256,
            "relay-artifact-kind": ref.kind.value,
        }
        try:
            await asyncio.to_thread(
                blob.upload_from_string,
                artifact,
                content_type="application/octet-stream",
                if_generation_match=0,
                checksum="crc32c",
            )
        except PreconditionFailed:
            existing = await asyncio.to_thread(blob.download_as_bytes)
            if len(existing) != ref.byte_length or _sha256(existing) != ref.sha256:
                raise ArtifactIntegrityError(
                    "create-only conflict refers to different stored bytes"
                ) from None
        return ref

    async def read(self, ref: ArtifactRef) -> bytes:
        blob = self.bucket.blob(self._object_name(ref))
        value = await asyncio.to_thread(blob.download_as_bytes)
        value = cast(bytes, value)
        if len(value) != ref.byte_length or _sha256(value) != ref.sha256:
            raise ArtifactIntegrityError("stored artifact failed content-address verification")
        return value
