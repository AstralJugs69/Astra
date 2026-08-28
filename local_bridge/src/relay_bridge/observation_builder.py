"""Canonical, hash-addressed observation envelopes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_observation(
    *, site_id: str, bridge_id: str, queue_name: str, sequence: int, queue_snapshot: dict[str, Any], previous_sha256: str | None
) -> dict[str, object]:
    body = {
        "schema_version": "site-observation.v1",
        "site_id": site_id,
        "bridge_id": bridge_id,
        "queue_name": queue_name,
        "sequence": sequence,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "queue_snapshot": queue_snapshot,
        "previous_observation_sha256": previous_sha256,
        "source": "cups_read_only_observer",
    }
    digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return {**body, "observation_id": digest}

