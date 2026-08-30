# Live demo runbook

This is a narration and human-authority guide. It does not replace the detailed
[active professional review runbook](active-professional-review-demo.md), which
remains the source of truth for human-only CUPS/telemetry steps.

## Before recording

1. **Human-owned local/CUPS action:** pre-stage the approved baseline and use
   the independent operator surface to submit the simulator job. Preserve fresh
   read-only observation evidence; do not claim endpoint completion.
2. **Local non-mutating action:** start the loopback watch floor with
   `infra/demo/start_demo.ps1` or prepare the offline fixture.
3. **Cloud state check:** confirm the service is private, temporary Token
   Creator grants are absent before and after use, and the recurring scheduler
   is paused outside the intended reconciliation path.

## Live flow (about 3–4 minutes)

1. Open `/watch` in its quiet connected state. Explain that it is a local,
   read-only monitor watching the authoritative source.
2. Show the pipeline: Drive revision → deterministic diff → Liblouis candidate
   → page impact → Gemini semantic assessment → professional report.
3. **Human Drive action:** edit the same prepared authoritative source from the
   V1 correction to V2 using Drive’s normal UI.
4. Trigger or wait for the existing authorized Drive reconciliation path. Do not
   use the dashboard to run it.
5. Show persisted stages arrive live. The alert appears only for a new qualifying
   transition, never for historical records loaded on page open.
6. Acknowledge the alert locally if desired and say plainly that this creates no
   professional record, containment, cancellation, proof, or production action.
7. Open incident detail. Show old/new authoritative source evidence,
   deterministic source/BRF hashes, page impact, persisted Gemini assessment,
   and real read-only CUPS observation as distinct cards.
8. Act as the professional only through the eligible, fresh-evidence forms in
   the separate detailed runbook. If the incident is fail-closed, show the block
   honestly rather than advancing it.
9. Point out the simulated physical endpoint boundary and the fact that CUPS
   scheduling remains real while endpoint simulation is limited.
10. End on the private Cloud Run architecture and the exact human-authority
    boundary.

## Recording storyboard

| Time | Visual | Narration |
| --- | --- | --- |
| 0:00–0:30 | Quiet `/watch` | “Relay watches and explains; it never drives the production floor.” |
| 0:30–1:10 | Pipeline and source change | “Drive wakes a deterministic lineage workflow; the byte refetch remains authoritative.” |
| 1:10–1:45 | New alert | “This is a local alert for a new durable mismatch, not a device command.” |
| 1:45–2:45 | Incident detail | “Source diff, Liblouis BRF/page impact, Gemini assessment, and CUPS evidence are separate facts.” |
| 2:45–3:30 | Boundary/evidence | “A human retains disposition, proof, submission, and closure authority.” |

## Honest fallback

If Drive, private Cloud Run, ADC, or the WSL local floor is unavailable during
recording, use `presentation.screenshot_fixture` and state that it is an
offline sanitized fixture. It demonstrates the UI and contracts only; it is
not live execution and cannot prove CUPS, Drive, cloud, human-review, or
endpoint behavior.
