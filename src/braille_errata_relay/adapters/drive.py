"""Read-only Google Drive blob provider and complete change-feed reconciler."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from braille_errata_relay.domain.models import (
    DriveChangeBatch,
    DriveChangeSignal,
    SourceLocator,
    SourceMetadata,
    SourceProvider,
    SourceRevision,
    SourceSnapshot,
)

MARKDOWN_MIME_TYPE = "text/markdown"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
SUPPORTED_SOURCE_MIME_TYPES = frozenset({MARKDOWN_MIME_TYPE, GOOGLE_DOCS_MIME_TYPE})
DRIVE_METADATA_FIELDS = (
    "id,mimeType,modifiedTime,name,size,trashed,version,capabilities(canDownload)"
)
DRIVE_CHANGE_FIELDS = (
    "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,mimeType,trashed,version))"
)


class DriveSourceError(RuntimeError):
    pass


class DriveSourceInaccessible(DriveSourceError):
    pass


class DriveSourceRemoved(DriveSourceError):
    pass


class DriveSourceInvalid(DriveSourceError):
    pass


def _parse_modified_at(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DriveSourceInvalid("Drive modifiedTime is not a string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise DriveSourceInvalid("Drive modifiedTime is malformed") from exc


async def _execute_request(request: Any, *, attempts: int = 3) -> object:
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(request.execute)
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0))
            if status not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
            await asyncio.sleep(0.05 * (2**attempt))
    raise AssertionError("bounded Drive retry loop exhausted unexpectedly")


class DriveBlobProvider:
    """Fetch one configured Markdown blob or native Google Doc read-only.

    Native Docs are exported as Markdown after metadata has established the
    configured provider identity. The exported bytes (not a native-document
    metadata size) are the authoritative source input for the existing strict
    Markdown normalizer.
    """

    def __init__(
        self,
        *,
        service: Any,
        expected_file_id: str,
        supported_mime_type: str = MARKDOWN_MIME_TYPE,
        max_bytes: int = 1_048_576,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not expected_file_id:
            raise ValueError("DRIVE_FILE_ID must be explicit")
        if max_bytes <= 0:
            raise ValueError("Drive source maximum must be positive")
        if supported_mime_type not in SUPPORTED_SOURCE_MIME_TYPES:
            raise ValueError("configured Drive source MIME type is unsupported")
        self.service = service
        self.expected_file_id = expected_file_id
        self.supported_mime_type = supported_mime_type
        self.max_bytes = max_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _metadata(self) -> dict[str, object]:
        request = self.service.files().get(
            fileId=self.expected_file_id,
            fields=DRIVE_METADATA_FIELDS,
            supportsAllDrives=True,
        )
        try:
            value = await _execute_request(request)
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0))
            if status in {403, 404}:
                raise DriveSourceInaccessible("configured Drive source is inaccessible") from exc
            raise DriveSourceError("Drive metadata request failed") from exc
        if not isinstance(value, dict):
            raise DriveSourceInvalid("Drive metadata response is not an object")
        return value

    def _validate_metadata(self, metadata: dict[str, object]) -> str:
        if metadata.get("id") != self.expected_file_id:
            raise DriveSourceInvalid("Drive returned a different file identity")
        if metadata.get("trashed") is True:
            raise DriveSourceRemoved("configured Drive source was removed")
        if metadata.get("mimeType") != self.supported_mime_type:
            raise DriveSourceInvalid("configured Drive source has an unsupported MIME type")
        version = metadata.get("version")
        if isinstance(version, int):
            provider_version = str(version)
        elif isinstance(version, str) and version:
            provider_version = version
        else:
            raise DriveSourceInvalid("Drive provider version is missing")
        if self.supported_mime_type == GOOGLE_DOCS_MIME_TYPE:
            capabilities = metadata.get("capabilities")
            if not isinstance(capabilities, dict) or capabilities.get("canDownload") is not True:
                raise DriveSourceInaccessible("configured Google Doc cannot be exported")
        declared_size = metadata.get("size")
        if self.supported_mime_type == MARKDOWN_MIME_TYPE and declared_size is not None:
            try:
                parsed_size = int(str(declared_size))
            except ValueError as exc:
                raise DriveSourceInvalid("Drive source size is malformed") from exc
            if parsed_size < 0 or parsed_size > self.max_bytes:
                raise DriveSourceInvalid("Drive source exceeds the configured byte limit")
        return provider_version

    async def fetch_revision(self, locator: SourceLocator) -> SourceSnapshot:
        if locator.provider is not SourceProvider.GOOGLE_DRIVE:
            raise DriveSourceInvalid("source provider is not Google Drive")
        if locator.file_id != self.expected_file_id:
            raise DriveSourceInvalid("source locator is outside the configured Drive file")
        if locator.mime_type != self.supported_mime_type:
            raise DriveSourceInvalid("source locator MIME type is unsupported")

        before = await self._metadata()
        provider_version = self._validate_metadata(before)
        # google-api-python-client exposes a Drive blob request as get_media().
        # A native Google Doc is not a blob: export_media() is the documented
        # read-only acquisition path. The configured provider MIME remains in
        # the SourceLocator; only the exported bytes are Markdown.
        if self.supported_mime_type == GOOGLE_DOCS_MIME_TYPE:
            request = self.service.files().export_media(
                fileId=self.expected_file_id,
                mimeType=MARKDOWN_MIME_TYPE,
            )
        else:
            request = self.service.files().get_media(
                fileId=self.expected_file_id,
                supportsAllDrives=True,
            )
        try:
            value = await _execute_request(request)
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0))
            if status in {403, 404}:
                raise DriveSourceInaccessible("configured Drive bytes are inaccessible") from exc
            raise DriveSourceError("Drive content request failed") from exc
        if not isinstance(value, bytes):
            raise DriveSourceInvalid("Drive content response is not bytes")
        if len(value) > self.max_bytes:
            raise DriveSourceInvalid("Drive source exceeds the configured byte limit")
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DriveSourceInvalid("Drive source is not valid UTF-8") from exc

        after = await self._metadata()
        after_version = self._validate_metadata(after)
        if after_version != provider_version:
            raise DriveSourceInvalid("Drive source changed during authoritative fetch")
        if before.get("mimeType") != after.get("mimeType"):
            raise DriveSourceInvalid("Drive source MIME type changed during fetch")
        if self.supported_mime_type == MARKDOWN_MIME_TYPE:
            declared_size = after.get("size")
            if declared_size is not None and int(str(declared_size)) != len(value):
                raise DriveSourceInvalid("Drive metadata and fetched byte length disagree")

        source_sha256 = hashlib.sha256(value).hexdigest()
        revision_id = f"drive:{self.expected_file_id}:{provider_version}:{source_sha256}"
        metadata = SourceMetadata(
            locator=locator,
            provider_version=provider_version,
            modified_at=_parse_modified_at(after.get("modifiedTime")),
            byte_length=len(value),
        )
        return SourceSnapshot(
            revision=SourceRevision(
                revision_id=revision_id,
                metadata=metadata,
                source_sha256=source_sha256,
                fetched_at=self._clock(),
            ),
            source_bytes=value,
        )


class DriveChangeReconciler:
    """Drain all pages and refetch the configured file after matching signals."""

    def __init__(self, *, provider: DriveBlobProvider) -> None:
        self.provider = provider
        self.service = provider.service

    async def get_start_cursor(self) -> str:
        request = self.service.changes().getStartPageToken(
            fields="startPageToken",
            supportsAllDrives=True,
        )
        try:
            value = await _execute_request(request)
        except HttpError as exc:
            raise DriveSourceError("Drive start-page-token request failed") from exc
        if not isinstance(value, dict):
            raise DriveSourceInvalid("Drive start page token is missing")
        token = value.get("startPageToken")
        if not isinstance(token, str):
            raise DriveSourceInvalid("Drive start page token is missing")
        if not token:
            raise DriveSourceInvalid("Drive start page token is empty")
        return token

    async def drain(self, cursor: str) -> DriveChangeBatch:
        if not cursor:
            raise ValueError("Drive reconciliation cursor is required")
        page_token = cursor
        signals: list[DriveChangeSignal] = []
        snapshots_by_revision: dict[str, SourceSnapshot] = {}
        final_cursor: str | None = None
        last_matching_change_removed: bool | None = None
        while True:
            request = self.service.changes().list(
                pageToken=page_token,
                spaces="drive",
                includeRemoved=True,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields=DRIVE_CHANGE_FIELDS,
            )
            try:
                value = await _execute_request(request)
            except HttpError as exc:
                raise DriveSourceError("Drive changes.list request failed") from exc
            if not isinstance(value, dict):
                raise DriveSourceInvalid("Drive changes response is not an object")
            changes = value.get("changes", [])
            if not isinstance(changes, list):
                raise DriveSourceInvalid("Drive changes field is not an array")
            for change in changes:
                if not isinstance(change, dict):
                    raise DriveSourceInvalid("Drive change entry is not an object")
                if change.get("fileId") != self.provider.expected_file_id:
                    continue
                removed = change.get("removed") is True
                file_value = change.get("file")
                trashed = isinstance(file_value, dict) and file_value.get("trashed") is True
                signals.append(
                    DriveChangeSignal(
                        file_id=self.provider.expected_file_id,
                        removed=removed or trashed,
                    )
                )
                last_matching_change_removed = removed or trashed
            next_page = value.get("nextPageToken")
            if next_page is not None:
                if not isinstance(next_page, str) or not next_page:
                    raise DriveSourceInvalid("Drive next page token is malformed")
                page_token = next_page
                continue
            new_start = value.get("newStartPageToken")
            if not isinstance(new_start, str) or not new_start:
                raise DriveSourceInvalid("Drive final start page token is missing")
            final_cursor = new_start
            break

        # Drive change entries are wake-up signals, not historical source
        # bytes. Refetch exactly once after the complete relevant feed segment
        # has been drained. This keeps a large backlog bounded by one source
        # read and makes a remove-then-restore sequence converge to its final
        # observed state. A final removal still becomes a durable cursor
        # checkpoint with no candidate or model invocation.
        if last_matching_change_removed is False:
            locator = SourceLocator(
                provider=SourceProvider.GOOGLE_DRIVE,
                file_id=self.provider.expected_file_id,
                mime_type=self.provider.supported_mime_type,
            )
            try:
                snapshot = await self.provider.fetch_revision(locator)
            except DriveSourceRemoved:
                signals.append(
                    DriveChangeSignal(file_id=self.provider.expected_file_id, removed=True)
                )
            else:
                snapshots_by_revision[snapshot.revision.revision_id] = snapshot
        return DriveChangeBatch(
            start_cursor=cursor,
            final_cursor=final_cursor,
            signals=tuple(signals),
            snapshots=tuple(snapshots_by_revision.values()),
        )
