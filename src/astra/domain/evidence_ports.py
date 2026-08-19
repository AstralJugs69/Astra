"""Pure EvidenceRetriever port interface.

Zero I/O, zero framework imports. Defines the protocol for retrieving evidence from local workspace/transcripts.
"""

from typing import List, Optional, Protocol
from astra.domain.evidence import EvidenceItem
from astra.domain.trajectory import EvidenceRef


class EvidenceRetriever(Protocol):
    """Protocol for evidence retrieval adapters."""

    async def retrieve(
        self,
        requests: List[EvidenceRef],
        workspace_path: Optional[str] = None,
    ) -> List[EvidenceItem]:
        """Retrieves content for the requested evidence references."""
        ...
