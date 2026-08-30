from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEXES = ROOT / "firestore.indexes.json"
SCRIPT = ROOT / "infra" / "gcp" / "ensure_outbox_lease_index.ps1"
TIMELINE_SCRIPT = ROOT / "infra" / "gcp" / "ensure_incident_timeline_index.ps1"


def test_outbox_lease_query_has_a_checked_in_composite_index() -> None:
    payload = json.loads(INDEXES.read_text(encoding="utf-8"))

    assert payload["fieldOverrides"] == []
    assert payload["indexes"] == [
        {
            "collectionGroup": "outbox",
            "queryScope": "COLLECTION",
            "fields": [
                {"fieldPath": "status", "order": "ASCENDING"},
                {"fieldPath": "created_at", "order": "ASCENDING"},
            ],
        },
        {
            "collectionGroup": "incident_timeline_events",
            "queryScope": "COLLECTION",
            "fields": [
                {"fieldPath": "record.incident_id", "order": "ASCENDING"},
                {"fieldPath": "record.recorded_at", "order": "ASCENDING"},
            ],
        },
    ]


def test_index_provisioner_is_bounded_idempotent_and_does_not_touch_cups() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "gcloud.cmd firestore indexes composite create" in script
    assert "--collection-group=outbox" in script
    assert "--field-config=field-path=status,order=ascending" in script
    assert "--field-config=field-path=created_at,order=ascending" in script
    assert "Get-MatchingOutboxIndex" in script
    assert "/collectionGroups/outbox/indexes/" in script
    assert 'matching.state -eq "READY"' in script
    assert "MaximumAttempts" in script
    for forbidden in ("cups", "lp ", "cancel", "hold", "release", "restart", "print-job"):
        assert forbidden not in script.casefold()


def test_incident_timeline_query_has_a_bounded_idempotent_index_provisioner() -> None:
    script = TIMELINE_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud.cmd firestore indexes composite create" in script
    assert "--collection-group=incident_timeline_events" in script
    assert "--field-config=field-path=record.incident_id,order=ascending" in script
    assert "--field-config=field-path=record.recorded_at,order=ascending" in script
    assert "Get-MatchingIncidentTimelineIndex" in script
    assert "/collectionGroups/incident_timeline_events/indexes/" in script
    assert 'matching.state -eq "READY"' in script
    assert "MaximumAttempts" in script
    for forbidden in ("cups", "lp ", "cancel", "hold", "release", "restart", "print-job"):
        assert forbidden not in script.casefold()
