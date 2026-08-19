"""Local repository file slice retriever adapter."""

from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class RepoRetriever:
    """Reads slices of local workspace files safely."""

    def retrieve_file_slice(
        self,
        file_path: str,
        workspace_path: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_lines: int = 100,
    ) -> str:
        """Reads a bounded line slice of a file in the workspace."""
        path = Path(file_path)
        if not path.is_absolute() and workspace_path:
            path = Path(workspace_path) / file_path

        if not path.exists() or not path.is_file():
            logger.warning("file_not_found_for_evidence", path=str(path))
            return ""

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(lines)

            s_idx = max(0, (start_line - 1) if start_line else 0)
            e_idx = min(total_lines, end_line if end_line else (s_idx + max_lines))

            slice_lines = lines[s_idx:e_idx]
            numbered = [f"{i + s_idx + 1:4d}: {line}" for i, line in enumerate(slice_lines)]
            return "\n".join(numbered)

        except Exception as exc:
            logger.error("file_slice_read_error", path=str(path), error=str(exc))
            return ""
