"""Normalizer translating raw Antigravity hook envelopes to pure AstraEvents.

Performs secret redaction, truncation, and missing-field fallback.
"""

import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from astra.domain.events import AstraEvent, EventType, ToolCallSummary
from astra.integration.antigravity.raw_schema import RawAntigravityPayload, RawHookEnvelope

# Common secret patterns for redaction
SECRET_PATTERNS = [
    re.compile(r"(AIza[0-9A-Za-z-_]{35})"),  # Google API key
    re.compile(r"(sk-[a-zA-Z0-9]{32,})"),  # Generic bearer / API key
    re.compile(r"(Bearer\s+[a-zA-Z0-9_\-\.]{20,})", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Redacts common secret tokens and keys from strings."""
    if not text:
        return ""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def truncate_text(text: str, max_chars: int = 2000) -> str:
    """Truncates text safely with indicator."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated ({len(text)} chars total)]"


def normalize_antigravity_event(
    envelope: RawHookEnvelope,
    received_at_ms: Optional[int] = None,
) -> Tuple[Optional[AstraEvent], List[str]]:
    """Translates a raw hook envelope into a clean domain AstraEvent."""
    warnings: List[str] = []
    now_ms = received_at_ms or envelope.client_timestamp_ms or int(time.time() * 1000)

    # 1. Parse inner payload
    raw_payload_dict = envelope.payload or {}
    try:
        raw_payload = RawAntigravityPayload.model_validate(raw_payload_dict)
    except Exception as exc:
        warnings.append(f"raw_payload_validation_warning: {exc}")
        raw_payload = RawAntigravityPayload()

    # 2. Extract Session ID (Blocking requirement)
    session_id = (
        raw_payload.conversationId
        or raw_payload_dict.get("conversationId")
        or raw_payload_dict.get("sessionId")
        or raw_payload_dict.get("session_id")
    )
    if not session_id:
        warnings.append("missing_conversationId_in_payload")
        return None, warnings

    # 3. Determine Event Type
    event_type_str = envelope.event_type.upper().replace("-", "_")
    if "POST" in event_type_str or "TOOL" in event_type_str:
        event_type = EventType.POST_TOOL_USE
    elif "STOP" in event_type_str:
        event_type = EventType.STOP
    else:
        warnings.append(f"unrecognized_event_type: {envelope.event_type}")
        return None, warnings

    # 4. Extract Tool information if present
    tool_summary: Optional[ToolCallSummary] = None
    if raw_payload.toolCall and raw_payload.toolCall.name:
        args_str = ""
        if raw_payload.toolCall.args:
            # Flatten or summarize arguments
            cmd = raw_payload.toolCall.args.get("CommandLine") or raw_payload.toolCall.args.get("AbsolutePath")
            if cmd:
                args_str = str(cmd)
            else:
                args_str = str(raw_payload.toolCall.args)

        args_str = redact_secrets(truncate_text(args_str, max_chars=500))

        # Check tool result
        output_str = ""
        exit_code = None
        had_error = bool(raw_payload.error)

        if raw_payload.toolResult:
            if raw_payload.toolResult.output:
                output_str = raw_payload.toolResult.output
            if raw_payload.toolResult.exitCode is not None:
                exit_code = raw_payload.toolResult.exitCode
                if exit_code != 0:
                    had_error = True
            if raw_payload.toolResult.error:
                had_error = True

        output_str = redact_secrets(truncate_text(output_str, max_chars=2000))

        tool_summary = ToolCallSummary(
            name=raw_payload.toolCall.name,
            arguments_summary=args_str,
            had_error=had_error,
            exit_code=exit_code,
            output_summary=output_str,
        )

    # 5. Extract workspace and transcript references
    workspace_path = None
    if raw_payload.workspacePaths and len(raw_payload.workspacePaths) > 0:
        workspace_path = raw_payload.workspacePaths[0]

    transcript_ref = raw_payload.transcriptPath

    # 6. Extract error summary
    error_summary = None
    if raw_payload.error:
        error_summary = redact_secrets(truncate_text(raw_payload.error, max_chars=1000))

    event = AstraEvent(
        event_id=str(uuid.uuid4()),
        session_id=session_id,
        event_type=event_type,
        step_index=raw_payload.stepIdx,
        tool=tool_summary,
        result_summary=tool_summary.output_summary if tool_summary else None,
        error=error_summary,
        workspace_path=workspace_path,
        transcript_ref=transcript_ref,
        occurred_at=envelope.client_timestamp_ms or now_ms,
        received_at=now_ms,
        correlation_id=envelope.correlation_id,
        raw_schema_version="antigravity-cli-v1",
        normalization_warnings=warnings,
    )

    return event, warnings
