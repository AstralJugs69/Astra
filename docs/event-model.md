# 📡 Astra Event Model Specification

## 1. Overview
The Astra Event Model establishes the formal boundary between the raw, surface-dependent Antigravity CLI lifecycle hooks and Astra's pure hexagonal domain core.

Astra normalizes all raw hook payloads into a strictly-typed `AstraEvent` entity, enforcing:
1. **Schema Insulation**: Changes to Antigravity CLI's JSON payload shape only affect `integration/antigravity/normalize.py`.
2. **Deterministic Redaction**: Pattern-redacting secrets, tokens, and credentials at ingestion before persistence or model delivery.
3. **Payload Truncation**: Safeguarding against massive terminal outputs (e.g. 50k line logs) by bounding summaries.

---

## 2. Event Types

| Hook Name | `EventType` | Ingestion Semantic | Output Channel |
| :--- | :--- | :--- | :--- |
| `PostToolUse` | `POST_TOOL_USE` | Fired immediately after an agent tool execution finishes. Ingests tool name, arguments summary, exit code / error, and result summary. | Passive observation (`{}` default) or non-authoritative Assist payload. |
| `Stop` | `STOP` | Fired when the agent attempts to conclude its turn or session. Ingests termination reason and final workspace state. | Authoritative approval (`{"decision": "allow"}`) or forced continuation (`{"decision": "continue", "reason": "..."}`). |

---

## 3. Schemas

### 3.1 Raw Ingestion Model (`integration/antigravity/raw_schema.py`)
Loosely typed Pydantic model (`extra="allow"`) accommodating variations across Antigravity CLI versions:

```python
class RawHookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    step_idx: Optional[int] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[Union[Dict[str, Any], str]] = None
    tool_output: Optional[Union[Dict[str, Any], str]] = None
    error: Optional[Union[str, bool, Dict[str, Any]]] = None
    workspace_paths: Optional[List[str]] = None
    transcript_path: Optional[str] = None
    termination_reason: Optional[str] = None
```

### 3.2 Normalized Domain Event (`domain/events.py`)
Pure domain entity with zero framework dependencies:

```python
class AstraEvent(BaseModel):
    event_id: str
    session_id: str
    event_type: EventType
    step_index: Optional[int] = None
    tool: Optional[ToolCallSummary] = None
    result_summary: str = ""
    error: Optional[str] = None
    workspace_path: Optional[str] = None
    transcript_ref: Optional[str] = None  # Pointer to transcript slice, not content
    occurred_at: Optional[int] = None
    received_at: int
    correlation_id: str
    raw_schema_version: str = "antigravity-cli-v1"
    normalization_warnings: List[str] = Field(default_factory=list)
```

---

## 4. Privacy & Redaction Policy
During normalization, all string fields in `tool_arguments` and `result_summary` pass through `sanitize_and_redact()`:
- Common API key patterns (`AIza...`, `sk-...`, `bearer ...`) are replaced with `[REDACTED]`.
- Strings exceeding 2,000 characters are safely clamped with `[truncated to budget]`.
