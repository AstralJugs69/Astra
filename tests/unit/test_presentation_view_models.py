from __future__ import annotations

import pytest

from braille_errata_relay.presentation.view_models import decision_cockpit


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_form"),
    (
        ("CONTINUE_ACCEPTED", "Review outcome recorded — continue accepted", "none"),
        ("HALT_REQUESTED", "Review outcome recorded — halt requested", "operator"),
        ("DEFERRED", "Review outcome recorded — decision deferred", "none"),
        ("REPORT_REJECTED", "Review outcome recorded — report rejected", "none"),
        (
            "RESOLVED_NO_REMEDIATION_BY_HUMAN",
            "Final human outcome — resolved without remediation",
            "none",
        ),
        ("RESOLVED_BY_HUMAN", "Final human outcome — resolved", "none"),
    ),
)
def test_decision_cockpit_names_recorded_human_outcomes(
    state: str, expected_status: str, expected_form: str
) -> None:
    cockpit = decision_cockpit({"state": state}, {})

    assert cockpit["status"] == expected_status
    assert cockpit["form"] == expected_form
    assert "Waiting for the next" not in cockpit["status"]
    assert cockpit["message"]


def test_incomplete_containment_names_the_evidence_block() -> None:
    cockpit = decision_cockpit(
        {"state": "CONTAINMENT_IN_PROGRESS"},
        {
            "containment_confirmation": {
                "eligible": False,
                "blocking_reason": "SITE_OBSERVATION_STALE",
            }
        },
    )

    assert cockpit["status"] == "Containment in progress — evidence incomplete"
    assert cockpit["form"] == "none"
    assert "SITE_OBSERVATION_STALE" in cockpit["message"]
