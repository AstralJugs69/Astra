# Astra — authoritative Drive source

Google Drive is the demonstrated, read-only source-authority adapter for this
vertical slice. Astra follows one configured source file and does not create,
edit, move, delete, rename, or share Drive content. It is not a claim that
Drive is the universal intake system for braille facilities.

## Correctness path

1. A human registers a matching accepted baseline, then runs `INITIALIZE`
   once and retains its receipt ID before explicitly enabling the private
   automatic watch.
2. Cloud Scheduler invokes Astra's private automation route every minute. The
   route drains the Drive change feed from its durable cursor.
3. A matching change causes an authoritative metadata-and-byte refetch.
4. Metadata is checked before and after byte retrieval.
5. The fetched UTF-8 Markdown bytes and provider version form immutable source
   lineage; one durable outbox record then starts the existing deterministic
   and semantic investigation.

No matching change, replayed cursor, or unchanged source bytes creates a new
candidate or calls Gemini. A 360-second durable cycle lease and
content-addressed outbox records make duplicate scheduler deliveries and
restart recovery converge safely. The scheduler request deadline is 300
seconds; the Drive check is bounded to 60 seconds and one durable outbox record
to 210 seconds, preserving time for the final ledger transaction. If Drive
reconciliation fails, the cycle still processes one pre-existing durable outbox
record, records the failure, and retries the source path later.

The monitor distinguishes a newly queued source investigation from recovery of
an older outbox record and from a later Drive version with byte-identical
content. It therefore never labels an older recovery result as the outcome of
the source change just observed.

If Drive reports the configured source as removed or trashed, Relay persists a
cursor checkpoint and records a visible source-unavailable outcome without
creating a candidate or model task. A later ordinary restoration is discovered
through the same change feed and authoritative refetch.

The current release does not expose a Workspace Events or Pub/Sub source-event
adapter. Such a signal would be an optional future wake-up optimization, never
proof. The demonstrated correctness path is the Drive change feed plus
authoritative byte refetch and durable cursor.

## Supported source

The hero workflow has two deliberately small, read-only source modes:

| Configured Drive MIME type | Authoritative byte path | Intended use |
| --- | --- | --- |
| `text/markdown` | `files.get` byte download | Strict Markdown source file. |
| `application/vnd.google-apps.document` | `files.export` to Markdown | A simple native Google Doc edited naturally in the Drive UI. |

For a native Google Doc, Astra checks `capabilities.canDownload`, confirms the
expected MIME type and non-trashed metadata before and after the export, and
uses only the exported UTF-8 Markdown bytes as source truth. Drive’s change
feed remains a wake signal; it never becomes source truth. Export byte length
is intentionally not compared to native-Doc metadata size because Drive does
not define that metadata as the export length.

The live demo intentionally uses headings and paragraphs only. Rich document
ingestion, tables, drawings, comments, revision authoring, and general Google
Docs publishing are out of scope and fail closed rather than being silently
reformatted into Braille. Run the metadata-only doctor before relying on a
native Doc, then perform one human-observed baseline initialization/export
smoke before claiming that a particular document is live-ready. The configured
Drive ID is never rendered in the browser watch floor.

## Read-only access check

After optional local Drive ADC is configured with a project-owned OAuth client,
an evaluator may run:

~~~text
uv run --frozen braille-relay doctor --config .env --check-drive
~~~

This makes a metadata-only request and returns a sanitized pass/block result.
It does not download source bytes, mutate Drive, or reveal the file ID. The
deployed runtime identity performs live read-only reconciliation after the exact
source file has been shared with it; see
[Google Cloud, Drive, and local authentication](google-cloud-setup.md).

## Demo source edit — human action

The live demo asks a human to edit the same prepared authoritative source from
V1 to V2 using their normal Drive UI. Astra never makes that edit. With the
private automatic watch already enabled, no reconciliation command follows the
edit: Astra detects the change, re-fetches the source truth, and begins the
durable investigation. See [live-demo-runbook.md](live-demo-runbook.md).

`infra/gcp/reconcile_live_drive.ps1` remains an explicit diagnostic and
baseline-initialization tool. It is not part of the hero path. To configure or
enable the private watch, use
[fresh-project-deployment.md](fresh-project-deployment.md#11-automatic-drive-watch-configure-paused-then-enable-explicitly).
The enable command requires the 64-character receipt ID emitted by the one-time
`INITIALIZE` run; the configuration script checks its shape and does not
remotely verify the receipt.
If Drive is unavailable, use the sanitized fixture fallback and label it as a
UI/contracts demonstration only.
