"""Read-only access to simulator manifests below one fixed evidence root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast


class CaptureReader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def read_manifest(self, scheduler_job_id: int) -> dict[str, object] | None:
        if scheduler_job_id <= 0:
            raise ValueError("scheduler job ID must be positive")
        path = (self.root / str(scheduler_job_id) / "manifest.json").resolve()
        if self.root not in path.parents:
            raise ValueError("capture path escaped configured root")
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("capture manifest must be a JSON object")
        return cast(dict[str, object], value)
