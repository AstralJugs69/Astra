from __future__ import annotations

from datetime import datetime, timezone

from braille_errata_relay.contracts.canonical_json import canonical_json_bytes, canonical_sha256


def test_canonical_json_sorts_keys_and_is_compact() -> None:
    value = {"b": 2, "a": 1, "when": datetime(2026, 8, 28, tzinfo=timezone.utc)}
    assert canonical_json_bytes(value) == b'{"a":1,"b":2,"when":"2026-08-28T00:00:00+00:00"}'
    assert canonical_sha256(value) == canonical_sha256({"when": value["when"], "a": 1, "b": 2})

