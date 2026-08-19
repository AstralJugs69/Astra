"""Base engine abstract class."""

from abc import ABC, abstractmethod
from typing import Optional

from astra.domain.evidence import EvidencePacket
from astra.domain.model_ports import ModelProvider
from astra.domain.reasoning_ports import EngineResult
from astra.domain.trajectory import TrajectoryState


class BaseEngine(ABC):
    """Abstract base class for Astra reasoning engines."""

    def __init__(
        self,
        model_provider: ModelProvider,
        model_name: str = "gemini-2.5-pro",
        timeout_seconds: float = 8.0,
    ):
        self.model_provider = model_provider
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    async def run(
        self,
        evidence: EvidencePacket,
        state: TrajectoryState,
    ) -> EngineResult:
        """Executes the engine reasoning assessment."""
        ...
