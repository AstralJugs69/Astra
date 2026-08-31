# Judge testing: hosted evidence or full reproduction

Astra supports two deliberately different evaluation paths. They use the same
durable records and preserve the same human-authority boundary, but they do not
grant the same capabilities.

## Path A — deployed read-only dashboard

Use the public Astra judge dashboard when you want to understand the project
without provisioning Google Cloud or installing the local production-floor
simulator.

The hosted dashboard lets you:

1. inspect registered source-to-Braille baselines;
2. watch the durable reconciliation status;
3. open past incident reports and recorded human outcomes;
4. compare the old and new source evidence;
5. inspect deterministic page impact, the persisted Gemini assessment, and the
   attributable timeline; and
6. follow the prepared source-document link.

The public service is intentionally **GET-only**. Its attached service account
can call only the private API's monitor-safe GET routes. It cannot register a
source, fetch an approved candidate BRF, record a decision, mutate Drive, invoke
the scheduler, call a CUPS bridge, or control any production device. Direct
unauthenticated access to the underlying API remains denied by Cloud Run.

The prepared authoritative source is:

<https://docs.google.com/document/d/1w5YaYKGFAkJSsRIMRKRX1jX6QUgpGqKYU7sHKd_1qi4/edit?tab=t.0>

If Google asks the evaluator to sign in, the document owner has not enabled
"Anyone with the link — Viewer". Do not grant public Editor access to the
authoritative source. A judge who wants to create a correction should use a
copy they own and follow Path B.

## Path B — reproduce Astra in your own account

Use the repository path when you want to test actual source editing, private
automation, Gemini/ADK, deterministic Liblouis BRF generation, durable
Firestore/GCS lineage, or the real CUPS queue/capture simulator.

1. Clone <https://github.com/AstralJugs69/Astra>.
2. Install Python 3.11 or 3.12 and `uv`, then run `uv sync --frozen`.
3. Follow [fresh-project-deployment.md](fresh-project-deployment.md) to create
   the private Cloud Run API, identities, Firestore, GCS, and paused scheduler.
4. Create a native Google Doc or Drive-hosted Markdown file and share it as
   Viewer with the generated runtime service account.
5. Follow [guided-baseline-onboarding.md](guided-baseline-onboarding.md) to
   verify the source and register deterministic baseline lineage.
6. Follow [local-floor-and-cups-simulator.md](local-floor-and-cups-simulator.md)
   only if you want the real CUPS queue/capture portion of the demonstration.
7. Use [live-demo-runbook.md](live-demo-runbook.md) for the edit-to-report
   walkthrough.

This path keeps billing, IAM, source ownership, and all human actions inside the
evaluator's own account. The deployment scripts leave the scheduler paused by
default and never make the private API public.

## What both paths prove

Only the physical embosser endpoint is simulated. Source refetch, lineage,
Liblouis translation, BRF serialization, exact hashing, page-impact analysis,
Gemini's bounded structured assessment, durable workflow state, retry
convergence, and the CUPS queue/capture harness are implemented components.

Neither path turns Astra into a Braille publishing platform or production
system of record. Professional approval, physical-output isolation, replacement
submission, and final physical verification remain attributable human work in
the facility's existing systems.
