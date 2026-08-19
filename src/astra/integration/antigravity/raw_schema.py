"""Raw Antigravity hook payload schemas.

Loosely typed with extra='allow' to absorb upstream Antigravity CLI payload evolution.
This is the ONLY place in Astra permitted to know Antigravity's raw JSON field names.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RawToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    args: Optional[Dict[str, Any]] = None


class RawToolResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    output: Optional[str] = None
    exitCode: Optional[int] = None
    error: Optional[str] = None


class RawHookEnvelope(BaseModel):
    """Outer envelope sent by local hook relay."""
    model_config = ConfigDict(extra="allow")

    event_type: str
    correlation_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    client_timestamp_ms: Optional[int] = None


class RawAntigravityPayload(BaseModel):
    """Raw payload sent on stdin by Antigravity CLI."""
    model_config = ConfigDict(extra="allow")

    conversationId: Optional[str] = None
    workspacePaths: Optional[List[str]] = None
    transcriptPath: Optional[str] = None
    artifactDirectoryPath: Optional[str] = None
    modelName: Optional[str] = None
    stepIdx: Optional[int] = None
    executionNum: Optional[int] = None
    terminationReason: Optional[str] = None
    error: Optional[str] = None
    fullyIdle: Optional[bool] = None
    toolCall: Optional[RawToolCall] = None
    toolResult: Optional[RawToolResult] = None
