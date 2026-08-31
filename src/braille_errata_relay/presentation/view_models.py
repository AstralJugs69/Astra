"""Pure, provenance-preserving display models for the local review surface.

The module makes the demo easier to read without becoming a second workflow
engine. Every label is derived from an already persisted artifact or an
authoritative eligibility record. It deliberately has no network, CUPS, Drive,
or model-client dependency.
"""

from __future__ import annotations

from collections.abc import Mapping

_WORKFLOW_LABELS = {
    "DETECTED": "Authoritative revision verified",
    "DIFF_READY": "Source correction isolated",
    "CANDIDATE_READY": "Candidate Braille regenerated",
    "IMPACT_READY": "Braille page impact calculated",
    "SEMANTIC_READY": "Gemini assessment recorded",
    "REPORT_READY": "Professional report ready",
    "NEEDS_REVIEW": "Stopped safely — human review required",
}

_MILESTONES = (
    ("normalized_source", "Authoritative revision verified"),
    ("source_diff", "Source correction isolated"),
    ("candidate_brf", "Candidate Braille regenerated"),
    ("braille_impact", "Braille page impact calculated"),
    ("semantic_assessment", "Gemini assessment recorded"),
    ("report", "Professional report ready"),
)


def mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def workflow_label(stage: object) -> str:
    """Map a closed durable stage to a human label without guessing progress."""

    if isinstance(stage, str):
        return _WORKFLOW_LABELS.get(stage, "Waiting for durable workflow evidence")
    return "Waiting for durable workflow evidence"


def workflow_progress(
    checkpoint: Mapping[str, object], *, stage: object
) -> tuple[dict[str, str], ...]:
    """Render milestones from persisted artifact presence, not enum position.

    A `NEEDS_REVIEW` stage is intentionally *not* treated as evidence that all
    previous work completed. This matters for fail-closed incident paths.
    """

    steps: list[dict[str, str]] = []
    blocked = stage == "NEEDS_REVIEW"
    for artifact_name, label in _MILESTONES:
        if checkpoint.get(artifact_name) is not None:
            status = "complete"
        elif blocked:
            status = "blocked"
        else:
            status = "waiting"
        steps.append({"label": label, "status": status})
    return tuple(steps)


def _page_range(value: object) -> tuple[int, int] | None:
    item = mapping(value)
    start = item.get("start")
    end = item.get("end")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 1 <= start <= end
    ):
        return start, end
    return None


def _range_label(page_range: tuple[int, int] | None) -> str:
    if page_range is None:
        return "not recorded"
    start, end = page_range
    return str(start) if start == end else f"{start}–{end}"


def braille_ripple(impact: Mapping[str, object]) -> dict[str, object]:
    """Return a safe, textual visualisation of deterministic page impact."""

    baseline_total = impact.get("baseline_page_count")
    candidate_total = impact.get("candidate_page_count")
    pages_changed = impact.get("pages_changed") is True
    old_range = _page_range(impact.get("old_page_range"))
    new_range = _page_range(impact.get("new_page_range"))
    resync = impact.get("resynchronized_after_page")
    valid_totals = (
        isinstance(baseline_total, int)
        and not isinstance(baseline_total, bool)
        and baseline_total > 0
        and isinstance(candidate_total, int)
        and not isinstance(candidate_total, bool)
        and candidate_total > 0
    )
    if not valid_totals or not pages_changed or (old_range is None and new_range is None):
        return {
            "available": False,
            "headline": "Deterministic page impact is unavailable.",
            "baseline_total": 0,
            "candidate_total": 0,
            "old_range": "not recorded",
            "new_range": "not recorded",
            "resynchronization": "No verified resynchronization point is recorded.",
            "segments": (),
        }
    assert isinstance(baseline_total, int) and not isinstance(baseline_total, bool)
    assert isinstance(candidate_total, int) and not isinstance(candidate_total, bool)
    resync_page = (
        resync
        if (
            isinstance(resync, int)
            and not isinstance(resync, bool)
            and 1 <= resync <= max(baseline_total, candidate_total)
        )
        else None
    )
    changed_range = new_range or old_range
    assert changed_range is not None
    start, end = changed_range
    total = max(baseline_total, candidate_total)
    if start > total or end > total:
        return {
            "available": False,
            "headline": "Deterministic page impact is unavailable.",
            "baseline_total": 0,
            "candidate_total": 0,
            "old_range": "not recorded",
            "new_range": "not recorded",
            "resynchronization": "No verified resynchronization point is recorded.",
            "segments": (),
        }
    suffix_start = (resync_page + 1) if resync_page is not None else (end + 1)
    segments: list[dict[str, object]] = []
    if start > 1:
        segments.append({"kind": "match", "label": f"1–{start - 1} match", "pages": start - 1})
    segments.append(
        {"kind": "changed", "label": f"{start}–{end} changed", "pages": end - start + 1}
    )
    if suffix_start <= total:
        segments.append(
            {
                "kind": "suffix",
                "label": f"{suffix_start}–{total} unchanged suffix",
                "pages": total - suffix_start + 1,
            }
        )
    resynchronization = (
        f"Suffix resynchronized after page {resync_page}."
        if resync_page is not None
        else "No verified suffix resynchronization point is recorded."
    )
    return {
        "available": True,
        "headline": (
            f"Pages {_range_label(old_range)} of {baseline_total} became "
            f"pages {_range_label(new_range)} of {candidate_total}."
        ),
        "baseline_total": baseline_total,
        "candidate_total": candidate_total,
        "old_range": _range_label(old_range),
        "new_range": _range_label(new_range),
        "resynchronization": resynchronization,
        "segments": tuple(segments),
    }


def decision_cockpit(
    review_state: Mapping[str, object], review_actions: Mapping[str, object]
) -> dict[str, str]:
    """Identify the one human gate to elevate; the API remains authoritative."""

    state = review_state.get("state")
    blocking_reason = review_state.get("blocking_reason")
    if state in {"REPORT_READY", "NEEDS_REVIEW"}:
        return {
            "role": "Production coordinator",
            "action": "Record professional disposition",
            "form": "disposition",
            "status": "Professional report ready"
            if not blocking_reason
            else "Human review required",
            "message": "Choose one attributable professional disposition after reviewing the evidence.",
        }
    if state == "HALT_REQUESTED":
        return {
            "role": "Machine operator",
            "action": (
                "Perform any authorized containment outside Astra, then record "
                "the attributable result"
            ),
            "form": "operator",
            "status": "Review outcome recorded — halt requested",
            "message": (
                "The coordinator requested a halt. Astra recorded that decision but did not "
                "stop CUPS, an embosser, or any physical device."
            ),
        }
    if state == "CONTINUE_ACCEPTED":
        return {
            "role": "Production coordinator",
            "action": "Preserve the recorded continuation decision and its audit evidence",
            "form": "none",
            "status": "Review outcome recorded — continue accepted",
            "message": (
                "The coordinator accepted continuation after reviewing the report. "
                "Astra performed no production action."
            ),
        }
    if state == "DEFERRED":
        return {
            "role": "Production coordinator",
            "action": "Resolve the stated deferral before beginning a new assessment",
            "form": "none",
            "status": "Review outcome recorded — decision deferred",
            "message": (
                "The coordinator deferred the decision. No containment, proof, replacement, "
                "or closure is claimed."
            ),
        }
    if state == "REPORT_REJECTED":
        return {
            "role": "Production coordinator",
            "action": "Correct the report evidence before beginning a new assessment",
            "form": "none",
            "status": "Review outcome recorded — report rejected",
            "message": (
                "The coordinator rejected the report. The candidate remains unapproved and "
                "no production action was performed."
            ),
        }
    if state == "CONTAINMENT_IN_PROGRESS":
        action = mapping(review_actions.get("containment_confirmation"))
        if action.get("eligible") is True:
            return {
                "role": "Production coordinator",
                "action": "Record containment confirmation",
                "form": "containment",
                "status": "Attributable containment evidence is ready for confirmation",
                "message": "The coordinator can now evaluate the complete containment evidence set.",
            }
        reason = action.get("blocking_reason")
        reason_label = reason if isinstance(reason, str) else "REQUIRED_EVIDENCE_UNAVAILABLE"
        return {
            "role": "Production coordinator and machine operator",
            "action": "Collect the missing attributable evidence before containment confirmation",
            "form": "none",
            "status": "Containment in progress — evidence incomplete",
            "message": f"Containment is not yet confirmed. Current block: {reason_label}.",
        }
    if state == "AWAITING_PROOF":
        action = mapping(review_actions.get("proof"))
        if action.get("eligible") is True:
            return {
                "role": "Proofreader",
                "action": "Record exact-candidate proof decision",
                "form": "proof",
                "status": "Exact candidate proof is awaiting human review",
                "message": "Only the exact candidate and its bound manifest are eligible for review.",
            }
    if state == "AWAITING_REPLACEMENT":
        action = mapping(review_actions.get("replacement_observation"))
        if action.get("eligible") is True:
            return {
                "role": "Machine operator",
                "action": "Link independent replacement observation",
                "form": "replacement",
                "status": "Awaiting human-controlled replacement submission",
                "message": (
                    "Astra can record a fresh read-only observation only after an independent "
                    "human submission."
                ),
            }
    if state == "RESOLVED_NO_REMEDIATION_BY_HUMAN":
        return {
            "role": "Production coordinator",
            "action": "No further Astra action is required",
            "form": "none",
            "status": "Final human outcome — resolved without remediation",
            "message": "The attributable human workflow is complete without a replacement job.",
        }
    if state == "RESOLVED_BY_HUMAN":
        return {
            "role": "Production coordinator",
            "action": "Preserve the final verification and audit evidence",
            "form": "none",
            "status": "Final human outcome — resolved",
            "message": "The attributable human workflow and final verification are complete.",
        }
    return {
        "role": "Qualified human reviewer",
        "action": "Review visible evidence",
        "form": "none",
        "status": "Waiting for the next evidence-backed human gate",
        "message": (
            "No human record is currently eligible. Review the durable state and visible "
            "blocking reason."
        ),
    }


def report_view(
    *,
    stage: object,
    checkpoint: Mapping[str, object],
    review_state: Mapping[str, object],
    review_actions: Mapping[str, object],
    impact: Mapping[str, object],
) -> dict[str, object]:
    """Build the common derived data used by incident and print-report views."""

    return {
        "workflow_label": workflow_label(stage),
        "workflow_progress": workflow_progress(checkpoint, stage=stage),
        "ripple": braille_ripple(impact),
        "cockpit": decision_cockpit(review_state, review_actions),
    }
