from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from braille_errata_relay.adapters.drive import (
    DRIVE_CHANGE_FIELDS,
    DRIVE_METADATA_FIELDS,
    DriveBlobProvider,
    DriveChangeReconciler,
    DriveSourceInvalid,
    DriveSourceRemoved,
)
from braille_errata_relay.domain.models import DriveChangeSignal, SourceLocator, SourceProvider


class Response(dict[str, object]):
    status: int
    reason = "test"

    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status


class FakeRequest:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def execute(self) -> object:
        index = min(self.calls, len(self.outcomes) - 1)
        self.calls += 1
        value = self.outcomes[index]
        if isinstance(value, Exception):
            raise value
        return value


class FakeFiles:
    def __init__(self, metadata: list[object], media: list[object]) -> None:
        self.metadata = metadata
        self.media = media
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> FakeRequest:
        self.calls.append(kwargs)
        return FakeRequest([self.metadata.pop(0)])

    def get_media(self, **kwargs: object) -> FakeRequest:
        self.calls.append({"method": "get_media", **kwargs})
        return FakeRequest([self.media.pop(0)])


class FakeChanges:
    def __init__(self, start: object, pages: list[object]) -> None:
        self.start = start
        self.pages = pages
        self.list_calls: list[dict[str, object]] = []

    def getStartPageToken(self, **kwargs: object) -> FakeRequest:
        assert kwargs == {"fields": "startPageToken", "supportsAllDrives": True}
        return FakeRequest([self.start])

    def list(self, **kwargs: object) -> FakeRequest:
        self.list_calls.append(kwargs)
        return FakeRequest([self.pages.pop(0)])


class FakeDrive:
    def __init__(self, files: FakeFiles, changes: FakeChanges | None = None) -> None:
        self._files = files
        self._changes = changes

    def files(self) -> FakeFiles:
        return self._files

    def changes(self) -> FakeChanges:
        assert self._changes is not None
        return self._changes


def _metadata(*, version: str = "62", size: int = 12) -> dict[str, object]:
    return {
        "id": "drive-file",
        "mimeType": "text/markdown",
        "modifiedTime": "2026-08-29T00:00:00Z",
        "name": "synthetic.md",
        "size": str(size),
        "trashed": False,
        "version": version,
    }


def _provider(service: Any, *, max_bytes: int = 1024) -> DriveBlobProvider:
    return DriveBlobProvider(
        service=service,
        expected_file_id="drive-file",
        max_bytes=max_bytes,
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )


def _locator() -> SourceLocator:
    return SourceLocator(
        provider=SourceProvider.GOOGLE_DRIVE,
        file_id="drive-file",
        mime_type="text/markdown",
    )


@pytest.mark.asyncio
async def test_provider_refetches_metadata_and_hashes_authoritative_bytes() -> None:
    content = b"# Synthetic\n"
    files = FakeFiles([_metadata(size=len(content)), _metadata(size=len(content))], [content])
    provider = _provider(FakeDrive(files))

    snapshot = await provider.fetch_revision(_locator())

    expected_hash = hashlib.sha256(content).hexdigest()
    assert snapshot.source_bytes == content
    assert snapshot.revision.source_sha256 == expected_hash
    assert snapshot.revision.revision_id == f"drive:drive-file:62:{expected_hash}"
    assert files.calls[0]["fields"] == DRIVE_METADATA_FIELDS
    assert files.calls[1]["method"] == "get_media"
    assert files.calls[2]["fields"] == DRIVE_METADATA_FIELDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "content", "message"),
    [
        ({**_metadata(), "mimeType": "application/pdf"}, b"# Synthetic", "MIME"),
        (_metadata(size=4), b"\xff\xfe\x00\x00", "UTF-8"),
        ({**_metadata(), "trashed": True}, b"# Synthetic", "removed"),
        (_metadata(size=2048), b"# Synthetic", "byte limit"),
    ],
)
async def test_provider_fails_closed_on_unsupported_source(
    metadata: dict[str, object], content: bytes, message: str
) -> None:
    files = FakeFiles([metadata, metadata.copy()], [content])
    provider = _provider(FakeDrive(files), max_bytes=1024)

    with pytest.raises((DriveSourceInvalid, DriveSourceRemoved), match=message):
        await provider.fetch_revision(_locator())


@pytest.mark.asyncio
async def test_provider_fails_if_version_changes_during_fetch() -> None:
    content = b"# Synthetic\n"
    files = FakeFiles(
        [_metadata(version="62", size=len(content)), _metadata(version="63", size=len(content))],
        [content],
    )

    with pytest.raises(DriveSourceInvalid, match="changed during"):
        await _provider(FakeDrive(files)).fetch_revision(_locator())


@pytest.mark.asyncio
async def test_reconciler_drains_every_page_filters_and_refetches() -> None:
    content = b"# Synthetic\n"
    files = FakeFiles([_metadata(size=len(content)), _metadata(size=len(content))], [content])
    changes = FakeChanges(
        {"startPageToken": "cursor-0"},
        [
            {
                "changes": [{"fileId": "other-file", "removed": False}],
                "nextPageToken": "cursor-page-2",
            },
            {
                "changes": [
                    {
                        "fileId": "drive-file",
                        "removed": False,
                        "file": {"id": "drive-file", "trashed": False},
                    }
                ],
                "newStartPageToken": "cursor-final",
            },
        ],
    )
    reconciler = DriveChangeReconciler(provider=_provider(FakeDrive(files, changes)))

    assert await reconciler.get_start_cursor() == "cursor-0"
    batch = await reconciler.drain("cursor-0")

    assert batch.start_cursor == "cursor-0"
    assert batch.final_cursor == "cursor-final"
    assert len(batch.signals) == 1
    assert len(batch.snapshots) == 1
    assert [call["pageToken"] for call in changes.list_calls] == [
        "cursor-0",
        "cursor-page-2",
    ]
    assert all(call["fields"] == DRIVE_CHANGE_FIELDS for call in changes.list_calls)


@pytest.mark.asyncio
async def test_reconciler_checkpoints_a_removed_file_without_creating_a_snapshot() -> None:
    changes = FakeChanges(
        {"startPageToken": "cursor-0"},
        [
            {
                "changes": [{"fileId": "drive-file", "removed": True}],
                "newStartPageToken": "cursor-final",
            }
        ],
    )
    service = FakeDrive(FakeFiles([], []), changes)

    batch = await DriveChangeReconciler(provider=_provider(service)).drain("cursor-0")

    assert batch.start_cursor == "cursor-0"
    assert batch.final_cursor == "cursor-final"
    assert batch.signals == (DriveChangeSignal(file_id="drive-file", removed=True),)
    assert batch.snapshots == ()


@pytest.mark.asyncio
async def test_reconciler_recovers_a_remove_then_restore_sequence_with_one_final_refetch() -> None:
    content = b"# Restored source\n"
    files = FakeFiles([_metadata(size=len(content)), _metadata(size=len(content))], [content])
    changes = FakeChanges(
        {"startPageToken": "cursor-0"},
        [
            {
                "changes": [{"fileId": "drive-file", "removed": True}],
                "nextPageToken": "cursor-page-2",
            },
            {
                "changes": [
                    {
                        "fileId": "drive-file",
                        "removed": False,
                        "file": {"id": "drive-file", "trashed": False},
                    }
                ],
                "newStartPageToken": "cursor-final",
            },
        ],
    )
    reconciler = DriveChangeReconciler(provider=_provider(FakeDrive(files, changes)))

    batch = await reconciler.drain("cursor-0")

    assert batch.final_cursor == "cursor-final"
    assert [signal.removed for signal in batch.signals] == [True, False]
    assert len(batch.snapshots) == 1
    assert batch.snapshots[0].source_bytes == content
    assert len(files.calls) == 3


@pytest.mark.asyncio
async def test_transient_drive_error_is_bounded_and_retried() -> None:
    content = b"# Synthetic\n"
    transient = HttpError(Response(503), b"temporary")
    first_metadata = FakeRequest([transient, _metadata(size=len(content))])

    class TransientFiles(FakeFiles):
        def get(self, **kwargs: object) -> FakeRequest:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return first_metadata
            return FakeRequest([_metadata(size=len(content))])

        def get_media(self, **kwargs: object) -> FakeRequest:
            self.calls.append({"method": "get_media", **kwargs})
            return FakeRequest([content])

    files = TransientFiles([], [])

    snapshot = await _provider(FakeDrive(files)).fetch_revision(_locator())

    assert snapshot.source_bytes == content
    assert first_metadata.calls == 2
