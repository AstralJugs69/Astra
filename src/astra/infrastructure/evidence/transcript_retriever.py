"""Transcript slice retriever adapter."""

import json
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class TranscriptRetriever:
    """Extracts slices from Antigravity transcript .jsonl files."""

    def retrieve_slice(self, transcript_path: str, max_turns: int = 5) -> str:
        """Reads the trailing N turns from the transcript file."""
        path = Path(transcript_path)
        if not path.exists() or not path.is_file():
            logger.warning("transcript_file_not_found", path=transcript_path)
            return ""

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            recent_lines = lines[-max_turns:]
            turns_data = []

            for line in recent_lines:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    # Extract step_index, type, content / tool info
                    step_idx = obj.get("step_index", obj.get("stepIdx", "?"))
                    step_type = obj.get("type", "UNKNOWN")
                    content = str(obj.get("content", ""))[:300]
                    turns_data.append(f"[Step {step_idx} | {step_type}] {content}")
                except Exception:
                    turns_data.append(line[:200])

            return "\n".join(turns_data)

        except Exception as exc:
            logger.error("transcript_read_error", path=transcript_path, error=str(exc))
            return ""
