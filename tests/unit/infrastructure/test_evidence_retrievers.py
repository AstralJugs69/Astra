"""Unit tests for evidence retrievers."""

import json
from pathlib import Path
import pytest

from astra.domain.evidence import EvidenceSource
from astra.domain.trajectory import EvidenceRef
from astra.infrastructure.evidence.composite_retriever import CompositeEvidenceRetriever
from astra.infrastructure.evidence.repo_retriever import RepoRetriever
from astra.infrastructure.evidence.transcript_retriever import TranscriptRetriever


def test_transcript_retriever(tmp_path):
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "Fix bug"}),
        json.dumps({"step_index": 2, "type": "TOOL_CALL", "content": "running test"}),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    retriever = TranscriptRetriever()
    content = retriever.retrieve_slice(str(transcript_file), max_turns=2)
    assert "[Step 1 | USER_INPUT]" in content
    assert "[Step 2 | TOOL_CALL]" in content


def test_repo_retriever(tmp_path):
    source_file = tmp_path / "app.py"
    source_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5", encoding="utf-8")

    retriever = RepoRetriever()
    content = retriever.retrieve_file_slice(str(source_file), start_line=2, end_line=4)
    assert "   2: line 2" in content
    assert "   3: line 3" in content
    assert "line 1" not in content
    assert "line 5" not in content


@pytest.mark.asyncio
async def test_composite_evidence_retriever(tmp_path):
    source_file = tmp_path / "app.py"
    source_file.write_text("def test(): pass", encoding="utf-8")

    composite = CompositeEvidenceRetriever()
    refs = [
        EvidenceRef(
            source_type=EvidenceSource.CHANGED_FILE_SLICE.value,
            locator=str(source_file),
            summary="File slice",
        ),
        EvidenceRef(
            source_type=EvidenceSource.TEST_OUTPUT.value,
            locator="test_1",
            summary="1 failed in 0.2s",
        ),
    ]

    items = await composite.retrieve(refs)
    assert len(items) == 2
    assert items[0].source == EvidenceSource.CHANGED_FILE_SLICE
    assert "def test(): pass" in items[0].content
    assert items[1].source == EvidenceSource.TEST_OUTPUT
    assert "1 failed in 0.2s" in items[1].content
