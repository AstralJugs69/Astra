# Astra — live demo runbook

> **This braille master was approved yesterday. Production has already started.
> Now the authoritative source changes.**

This runbook leads with the operational problem, then shows Astra doing the
autonomous investigation and recovery preparation. The human boundary comes
after the value is visible: Astra cannot silently acquire authority over
professional proofing or physical production equipment.

This is a narration guide. The detailed human-owned CUPS and professional
review procedure remains
[active-professional-review-demo.md](active-professional-review-demo.md).

## Choose the truthful demo mode

| Mode | What it demonstrates | What it must not be claimed to demonstrate |
| --- | --- | --- |
| **Live private path** | Configured private Cloud Run, Drive reconciliation, durable state, actual adapters, and any fresh local-floor evidence you actually run. | A physical-device action or human decision that was not actually observed and recorded. |
| **Offline fixture** | Visual dashboard, quiet/alert states, responsive layout, SSE-style UI behavior, and safety contracts. | Live Drive, Gemini, Cloud Run, CUPS, human-review, replacement-submission, or endpoint execution. |

The final readiness record preserves an honest status for each release
activity. Review [testing-and-evidence.md](testing-and-evidence.md) before
making a live claim.

## Before recording the live path

1. **Prepare the baseline and source.** Use one supported authoritative
   text/Markdown Drive file and a known V1 to V2 correction. See
   [authoritative-drive-source.md](authoritative-drive-source.md). Register the
   matching accepted baseline, then initialize that source once before recording
   and retain the returned receipt ID for the automatic-watch enablement.
2. **Prepare production observation honestly.** If you plan to show a CUPS
   card as fresh evidence, use the independent human-owned local-floor
   procedure and preserve the resulting read-only observation. If you did not
   run it, label any displayed CUPS information as historical, stale, or
   fixture evidence rather than calling it current.
3. **Start the local view.** The loopback watch floor is a read-only view of
   durable incident state from Astra’s private service. It does not watch Drive
   directly; Drive reconciliation runs behind the private boundary, and the
   browser receives neither Drive credentials nor the source identifier.
4. **Verify credentials and authority.** The service remains private. A
   temporary demonstrator Token Creator binding is absent before use, narrowly
   present only during the human-authorized local presentation operation, and
   verified absent afterward. See
   [google-cloud-setup.md](google-cloud-setup.md).
5. **Enable the automatic watch.** Configure
   `astra-automation-cycle` through
   [fresh-project-deployment.md](fresh-project-deployment.md#11-automatic-drive-watch-configure-paused-then-enable-explicitly),
   then explicitly enable it with the one-time `INITIALIZE` receipt ID. The
   cycle invokes Astra privately every minute;
   the older standalone outbox job can remain paused. Do not use the dashboard
   to reconcile Drive, submit a job, or mutate CUPS.

## Live flow: prepare for 2–5 minutes after the Drive edit

The exact reveal time is intentionally variable: the next one-minute scheduler
tick, authoritative byte refetch, durable outbox work, and bounded semantic
assessment all have to finish. Do not promise a sub-minute transition. For a
recorded video, retain the waiting state or explicitly label an edit rather
than presenting a pre-existing incident as a live result.

### 0:00–0:25 — establish the real problem

Show the approved baseline/current production context and say:

> “This braille master was approved yesterday. Production has already started.
> A correction has just arrived in the authoritative source. The question is
> not merely ‘can we regenerate a file?’ It is ‘what can no longer be trusted,
> what output may be affected, and what must the responsible professional do?’”

### 0:25 — change the authoritative source

In the normal Google Drive UI, make the prepared V1-to-V2 edit. Astra never
makes this edit. Then leave Astra alone: its already-enabled private automatic
cycle notices the change, drains the change feed, and re-fetches authoritative
metadata and bytes. No reconciliation command is run after the edit.

Before making the edit, open the loopback **`/watch`** page and wait for its
initial snapshot to show **Connected**. That initial connection deliberately
suppresses historical alerts. If you want sound, click **Enable audible
alerts** now; browsers require that explicit local gesture. Keep this page
visible while the next automatic cycle completes. The scheduler is a private
OIDC caller, not a browser button or a synthetic event. The manual
`reconcile_live_drive.ps1` command remains available for baseline setup and
diagnostics, but is deliberately absent from this path.

The watch page labels the durable automatic cycle explicitly. It can say that
the source is being checked, that no source revision was found, that a revision
advanced the durable workflow, or that the status is unavailable. It never
labels the browser's own poll time as a successful Drive check.

### After the durable transition arrives — reveal autonomous investigation

The already-open loopback **`/watch`** page refreshes its compact incident
summary before it announces a new durable transition. Explain:

> “This screen receives sanitized, durable incident state from the private
> service. The initial snapshot intentionally creates no alert. A new durable
> incident or stage transition appears live.”

Show the progression:

1. accepted source revision and immutable lineage;
2. source difference;
3. deterministic Liblouis candidate BRF;
4. exact page impact and profile/hash identity;
5. Gemini/ADK’s bounded, schema-validated semantic assessment;
6. fresh/ambiguous/stale production-evidence status;
7. recovery recommendation and professional disposition packet.

Emphasize that Astra completes this investigation and recovery preparation
without asking a human to reconstruct the evidence manually.

### 2:00–2:45 — make the evidence legible

Open incident detail. Contrast the evidence types rather than collapsing them:

- **source evidence**: old/new authoritative source blocks and revision
  lineage;
- **deterministic production evidence**: candidate BRF hash, pinned profile,
  Liblouis table identity, and impacted pages;
- **semantic evidence**: bounded Gemini assessment with cited evidence spans;
- **production observation**: a fresh real observation only if you just ran
  it; otherwise truthfully label historical, stale, or fixture basis;
- **human evidence**: professional disposition and operator attestation are
  separate attributable records.

The dashboard's local alert acknowledgement and sound control are local UI
preferences. They do not record a professional decision or operate equipment.

### 2:45–3:25 — show the authority gate

Now explain the deliberate boundary:

> “Astra knows this approved master is no longer trustworthy and has assembled
> the recovery case. It does not impersonate a proofreader, cancel a queue, or
> claim that paper has been isolated. The responsible professional and machine
> operator use their existing controls, and Astra records only attributable
> evidence.”

If the incident is fail-closed, show the block. Do not advance it for the
video. Scheduler cancellation, device stop, physical-output isolation, proof
approval, and final verification are separate facts.

### 3:25–3:45 — optional bounded Story 5 ending

Only if the preconditions are real:

1. a qualified professional proof-approves the exact candidate;
2. an operator independently submits it through the existing CUPS/vendor
   surface;
3. a fresh, unambiguous, read-only observation is available.

Then Astra may link the observation and reach **REPLACEMENT_OBSERVED**. This
does not claim endpoint completion, final physical verification, notification,
or closure. Astra does not submit the replacement.

### 3:45–4:10 — show deployment and close

Show private Cloud Run, the architecture, the deterministic/semantic split, and
the read-only CUPS boundary. Close with:

> “Astra autonomously performs the investigation and recovery preparation.
> Humans retain professional and irreversible physical-production authority.”

## Recording storyboard

| Time | Visual | Narration focus |
| --- | --- | --- |
| Start | Approved baseline, current production context, and a connected `/watch` page | Late corrections create a source-to-production trust problem; historical data is quiet. |
| Human edit | V1-to-V2 Drive edit, then hands off | Drive is authoritative; bytes are refetched rather than inferred. |
| 2–5 min after edit | Watch status, alert, and live incident summary | Astra autonomously builds the recovery case from durable evidence. |
| After report readiness | Incident detail | Diff, BRF/page impact, Gemini assessment, and observation are different evidence types. |
| Then | Human authority gate | No device-control capability; fail-closed evidence protects the decision. |
| Optional | Replacement observation | Human submits independently; Astra may observe, not submit. |
| Close | Private Cloud Run / architecture | Production-minded, deeply implemented vertical slice with deliberate authority boundaries. |

## Offline fixture fallback

If Drive, private Cloud Run, ADC, temporary impersonation, or the WSL local
floor is unavailable, use the visibly labeled fixture:

~~~powershell
uv run --frozen python -m braille_errata_relay.presentation.screenshot_fixture --port 8877
~~~

Open **http://127.0.0.1:8877/watch/quiet** first, then
**http://127.0.0.1:8877/watch** for the mismatch alert. State plainly:

> “This is the sanitized offline demo fixture. It shows the UI and safety
> contracts, not live source detection, model execution, cloud state, CUPS,
> professional action, replacement submission, or physical endpoint proof.”

The fixture is a legitimate visual backup, not a substitute for live evidence.

## Related runbooks

- [Authoritative Drive source](authoritative-drive-source.md)
- [Google Cloud and temporary authentication](google-cloud-setup.md)
- [Local CUPS simulator and bridge](local-floor-and-cups-simulator.md)
- [Active professional review](active-professional-review-demo.md)
- [Testing and evidence chronology](testing-and-evidence.md)
- [Current final readiness evidence](../demo/evidence/final-demo-readiness.json)
