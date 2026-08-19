"""Composite EvidenceRetriever adapter implementing domain EvidenceRetriever port."""

import uuid
from typing import List, Optional
import structlog

from astra.domain.evidence import EvidenceItem, EvidenceSource
from astra.domain.trajectory import EvidenceRef
from astra.infrastructure.evidence.repo_retriever import RepoRetriever
from astra.infrastructure.evidence.transcript_retriever import TranscriptRetriever
from astra.infrastructure.evidence.web_research import WebResearchRetriever

logger = structlog.get_logger(__name__)


class CompositeEvidenceRetriever:
    """Composite retriever dispatching evidence references to specialized handlers."""

    def __init__(
        self,
        transcript_retriever: Optional[TranscriptRetriever] = None,
        repo_retriever: Optional[RepoRetriever] = None,
        web_retriever: Optional[WebResearchRetriever] = None,
    ):
        self.transcript_retriever = transcript_retriever or TranscriptRetriever()
        self.repo_retriever = repo_retriever or RepoRetriever()
        self.web_retriever = web_retriever or WebResearchRetriever()

    async def retrieve(
        self,
        requests: List[EvidenceRef],
        workspace_path: Optional[str] = None,
    ) -> List[EvidenceItem]:
        """Retrieves and populates content for each evidence reference."""
        items: List[EvidenceItem] = []

        for req in requests:
            content = ""
            source_type = req.source_type

            if source_type == EvidenceSource.TRANSCRIPT_SLICE.value:
                content = self.transcript_retriever.retrieve_slice(transcript_path=req.locator)
                source_enum = EvidenceSource.TRANSCRIPT_SLICE
            elif source_type == EvidenceSource.CHANGED_FILE_SLICE.value:
                content = self.repo_retriever.retrieve_file_slice(
                    file_path=req.locator, workspace_path=workspace_path
                )
                source_enum = EvidenceSource.CHANGED_FILE_SLICE
            elif source_type == EvidenceSource.TEST_OUTPUT.value:
                content = req.summary or "Test output not captured"
                source_enum = EvidenceSource.TEST_OUTPUT
            elif source_type == EvidenceSource.WEB_SEARCH_RESULT.value:
                content = await self.web_retriever.search(query=req.locator)
                source_enum = EvidenceSource.WEB_SEARCH_RESULT
            else:
                content = req.summary
                source_enum = EvidenceSource.PRIOR_INTERVENTION

            item = EvidenceItem(
                id=str(uuid.uuid4()),
                source=source_enum,
                reference=req.locator,
                content=content,
                relevance_score=0.9,
                timestamp=req.timestamp,
                provenance=source_type,
            )
            items.append(item)

        return items
