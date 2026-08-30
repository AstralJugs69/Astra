# Astra — authoritative Drive source

Google Drive is the demonstrated, read-only source-authority adapter for this
vertical slice. Astra follows one configured source file and does not create,
edit, move, delete, rename, or share Drive content. It is not a claim that
Drive is the universal intake system for braille facilities.

## Correctness path

1. A human-authorized scheduler/reconciliation invocation starts the current
   demonstrated adapter.
2. The Drive change feed drains from its durable cursor.
3. A matching change causes an authoritative metadata-and-byte refetch.
4. Metadata is checked before and after byte retrieval.
5. The fetched UTF-8 Markdown bytes and provider version form immutable source
   lineage.

The current release does not expose a Workspace Events or Pub/Sub source-event
adapter. Such a signal would be an optional future wake-up optimization, never
proof. The demonstrated correctness path is the Drive change feed plus
authoritative byte refetch and durable cursor.

## Supported source

The hero workflow supports one `text/markdown` file under the strict parser.
Unsupported content fails closed; it does not become silently reformatted
Braille content. The configured Drive ID is not rendered in the browser watch
floor.

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
V1 to V2 using their normal Drive UI. Astra never makes that edit. The human
then runs the explicitly authorized reconciliation described in
[live-demo-runbook.md](live-demo-runbook.md). If Drive is unavailable, use the
sanitized fixture fallback and label it as a UI/contracts demonstration only.
