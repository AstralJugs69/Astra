"""Pure ModelProvider port interface.

Zero I/O, zero framework imports. Defines the protocol for model calling and cost accounting.
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple, Type, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class CostMetadata(BaseModel):
    """Token and latency accounting metadata for every model invocation or pass."""

    tier_invoked: str = "none"  # "none", "fast", "deep"
    model_name: Optional[str] = None
    model_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class ModelProvider(Protocol):
    """Protocol for calling Gemini models behind a clean domain abstraction."""

    async def generate_structured(
        self,
        prompt: str,
        response_schema: Type[T],
        model_name: Optional[str] = None,
        timeout_seconds: float = 5.0,
        system_instruction: Optional[str] = None,
        tier: str = "fast",
    ) -> Tuple[T, CostMetadata]:
        """Generates structured output parsed into the provided Pydantic model schema."""
        ...

    async def generate_text(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        timeout_seconds: float = 5.0,
        system_instruction: Optional[str] = None,
        tier: str = "fast",
    ) -> Tuple[str, CostMetadata]:
        """Generates plain text response with cost metadata."""
        ...
