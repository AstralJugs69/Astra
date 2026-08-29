"""Fail-closed guard for simulator capture evidence.

Capture input/output BRF, capture journals, and manifests are human audit
evidence. The Relay observer is limited to CUPS Get operations and its own
observation journal, so it must not acquire a filesystem reader for this tree.
"""

from __future__ import annotations

from pathlib import Path


class CaptureReader:
    """Refuse capture access from the read-only observer process."""

    def __init__(self, root: str | Path) -> None:
        del root
        raise PermissionError(
            "relay-observer must not read simulator captures; use the human audit verifier"
        )
