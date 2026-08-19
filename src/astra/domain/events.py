"""Pure normalized domain event models.

Zero I/O, zero framework imports. Represents normalized events inside Astra.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    POST_TOOL_USE = "POST_TOOL_USE"
    STOP = "STOP"


class VerificationOutcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ToolCallSummary(BaseModel):
    name: str
    arguments_summary: str = ""
    had_error: bool = False
    exit_code: Optional[int] = None
    output_summary: str = ""


class AstraEvent(BaseModel):
    """Normalized internal event model for Astra."""

    event_id: str
    session_id: str
    event_type: EventType
    step_index: Optional[int] = None
    tool: Optional[ToolCallSummary] = None
    result_summary: Optional[str] = None
    error: Optional[str] = None
    workspace_path: Optional[str] = None
    transcript_ref: Optional[str] = None
    occurred_at: Optional[int] = None  # Epoch ms from client
    received_at: int  # Epoch ms on receipt
    correlation_id: str
    raw_schema_version: str = "antigravity-cli-v1"
    normalization_warnings: List[str] = Field(default_factory=list)

    @property
    def is_tool_failure(self) -> bool:
        """Returns True if the tool execution resulted in an error."""
        if self.error:
            return True
        if self.tool and self.tool.had_error:
            return True
        return False
