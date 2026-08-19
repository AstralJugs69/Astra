# 📦 Astra Evidence Schema & Budgeting Specification

## 1. Overview
Astra enforces a strict **Bounded Evidence Principle**: reasoning engines consume curated, prioritized evidence packets, **never the raw agent session or entire repository**.

The Evidence Layer consists of two strictly separated operations:
1. **Evidence Retrieval (I/O)**: `infrastructure/evidence/` adapters fetch requested slices (transcripts, test outputs, file diffs).
2. **Evidence Assembly & Budgeting (Pure)**: `domain/evidence.py` sorts, deduplicates, and clamps candidate items to a hard token budget (default: 4,000 tokens).

---

## 2. Evidence Sources

```python
class EvidenceSource(str, Enum):
    TRANSCRIPT_SLICE = "TRANSCRIPT_SLICE"      # Recent turn window from Antigravity transcript
    TOOL_OUTPUT = "TOOL_OUTPUT"                # Output summary of specific tool execution
    CHANGED_FILE_SLICE = "CHANGED_FILE_SLICE"  # Diffs / slices of modified workspace files
    TEST_OUTPUT = "TEST_OUTPUT"                # Stdout/stderr from pytest/npm test executions
    WEB_SEARCH_RESULT = "WEB_SEARCH_RESULT"    # Bounded documentation lookups
    PRIOR_INTERVENTION = "PRIOR_INTERVENTION"  # History of Astra's own previous critiques
```

---

## 3. Core Objects

### 3.1 `EvidenceItem`
An individual unit of evidence retrieved via an `EvidenceRetriever` adapter:

```python
class EvidenceItem(BaseModel):
    id: str
    source: EvidenceSource
    reference: str             # Opaque locator: file path, line range, or turn index
    content: str = ""          # Text payload populated on retrieval
    relevance_score: float = 1.0
    timestamp: int = 0
    provenance: str = ""
    token_estimate: int = 0
```

### 3.2 `EvidencePacket`
The complete, bounded bundle provided to Deep Tier reasoning engines:

```python
class EvidencePacket(BaseModel):
    task: Optional[str] = None
    trajectory_summary: str = ""
    checkpoint: Optional[ReasoningCheckpoint] = None  # Embedded structured snapshot
    items: List[EvidenceItem] = Field(default_factory=list)
    token_budget: int = 4000
    token_used: int = 0
```

---

## 4. Pure Budgeting Algorithm

When `assemble_evidence_packet()` executes:
1. **Base Cost Accounting**: Calculates tokens consumed by `task` and `trajectory_summary`.
2. **Deduplication**: Deduplicates candidate items by `(source, reference)`.
3. **Priority Packing**: Sorts candidate items by `relevance_score` descending.
4. **Budget Clamping**: Ingests items until `remaining_budget` is exhausted. If an item partially exceeds the boundary and remaining budget $> 50$ tokens, it is cleanly truncated with `...[truncated to budget]`.

---

## 5. Clean Seam for Future Context Engine (ADR-016)
The `EvidenceRetriever` port (`domain/evidence_ports.py`) defines the exact boundary where a future graph-based context engine (AST graph, semantic code index) will plug in without requiring any changes to `domain`, `engines`, or `tiers`.
