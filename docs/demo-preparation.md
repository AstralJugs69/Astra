# Astra — live-demo preparation

This is the shortest safe preparation path for a live recording. It creates no
fake incident, rewrites no historical evidence, and never lets Astra operate a
CUPS queue or endpoint.

## 1. Prepare a fresh source lineage

Use a new, human-owned Drive file for each serious rehearsal or recording
attempt. Do **not** switch an old source back from V2 to V1 and do not reset
Firestore, GCS, or the canonical observation journal.

The checked-in source is original and synthetic. Materialize the planned V1
revision without overwriting a local file:

```powershell
uv run --frozen python .\demo\scripts\export_demo_volume.py `
  --version v1 --output .\work\demo-source\cellular-systems-v1.md
```

It renders, with the pinned Liblouis profile, to 46 Braille pages. The one
factual correction is deliberately near the middle: V1 says the **nucleus**
stores water and dissolved minerals; V2 corrects that sentence to the
**vacuole**. The deterministic regression fixture proves the impact is page 24
of 46 and that the suffix resynchronizes after that page.

Create a simple native Google Doc or Markdown file from V1. Use headings and
paragraphs only. Then run the existing human-authorized baseline registration
and one-time `INITIALIZE` procedure from
[fresh-project deployment](fresh-project-deployment.md#14-initialize-once-then-let-the-automatic-watch-reconcile).
Enable the automatic watch only after that returned receipt is present. This is
baseline preparation, not the live reveal.

For another take, create a new Drive source and new baseline lineage. Preserve
the old source, Cloud evidence, and local journal as historical evidence.

## 2. Verify the safe prerequisites

Run the non-mutating readiness helper before opening the recording:

```powershell
.\infra\demo\test_demo_readiness.ps1
```

It runs the existing sanitized local doctor (including optional Drive metadata
and WSL availability checks) and reads Cloud Run/Scheduler configuration. It
does not run or pause Scheduler, edit Drive, publish telemetry, modify IAM, or
contact CUPS. A blocked result is a reason to fix setup before making the Drive
edit—not a reason to retry with a manual reconciliation command.

The automatic scheduler must be enabled and the service must remain private.
The local dashboard uses a separate, temporary, narrowly scoped demonstrator
identity only while the human runs it; see
[Google Cloud setup](google-cloud-setup.md).

## 3. Keep production observation fresh (only if the live story needs it)

This is optional for a source-to-report demo. If you show a fresh production
context, first have the human independently submit the exact prepared job
through the existing CUPS/operator surface. Copy only its numeric scheduler job
ID.

After the required temporary telemetry authority has been explicitly granted,
start the bounded monitor in a human-owned terminal:

```powershell
.\infra\demo\arm_fresh_observation.ps1 -Arm -SchedulerJobId <numeric-job-id>
```

The foreground prompt is the fixed `relay-observer` CUPS identity. Enter its
password only at that prompt. The monitor:

- reads only that exact job through CUPS Get operations every five seconds;
- appends each observation to the one canonical hash-chained journal;
- runs a separate hidden telemetry-only publisher;
- acknowledges an outbox item **only** after private Cloud Run accepts its
  exact observation ID;
- stops after 15 minutes, fails closed on a missing job, and records sanitized
  status under `work/live-bridge/demo-monitor-<session>/`.

It never submits, holds, releases, cancels, restarts, pauses, or changes a
queue/device. Stop the foreground observer with Ctrl+C after the recording;
the publisher drains already-created canonical records and exits. If it reports
a block, do not edit Drive—preserve the status and resolve the visible issue.

While it is running, verify the hard 15-second freshness condition with the
session status path printed by the arm command:

```powershell
.\infra\demo\test_demo_readiness.ps1 `
  -MonitorStatusPath .\work\live-bridge\demo-monitor-<session>\observer-status.json
```

## 4. Record the simple live story

1. Launch the loopback dashboard with `infra/demo/start_demo.ps1` and leave
   `/watch` open until it says **Live connection**.
2. Make the one V1-to-V2 correction in the prepared Drive file.
3. Do nothing locally afterward. The private automatic cycle uses the Drive
   change feed as a wake signal, then re-fetches authoritative metadata and
   bytes before creating durable workflow evidence.
4. Let `/watch` show durable source, diff, candidate, impact, Gemini-assessment,
   and report milestones. Its initial snapshot is intentionally quiet; a new
   durable incident is the alert.
5. Open **Review incident**. The decision cockpit distinguishes source,
   Gemini, deterministic Braille, read-only production, and human evidence.
   The production coordinator’s eligible disposition is the one action shown
   prominently; later gates stay behind progressive disclosure.
6. Use **Printable incident report** and browser **Print / Save as PDF** for
   the deterministic report view. It makes no additional Gemini request.

The full narration and safety boundaries are in
[live-demo-runbook.md](live-demo-runbook.md).

## 5. Show cloud proof without manufacturing activity

After the durable report appears, note the real Drive-edit timestamp. In Google
Cloud Console, show the `astra-automation-cycle` execution and the private
Cloud Run request/logs in that same time window. If useful, open the incident’s
existing immutable artifact/state record. These are evidence of the automatic
path—not a substitute for the visible live sequence.

Never run a scheduler job manually after the edit to make the proof look
faster, and never claim an older incident is the result of the edit on screen.
