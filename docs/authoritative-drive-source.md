# Authoritative Drive source

Drive is an MVP read-only adapter. Relay follows one configured source file and
does not create, edit, move, delete, rename, or share Drive content.

## Correctness path

1. A Workspace event may wake the reconciliation loop.
2. The change feed drains from its durable cursor.
3. A matching change causes an authoritative metadata-and-byte refetch.
4. Metadata is checked before and after byte retrieval.
5. The fetched UTF-8 Markdown bytes and provider version form immutable source
   lineage.

Events are therefore an optimization, not proof. The authoritative byte refetch
and durable cursor are the correctness path.

## Supported source

The hero workflow supports one `text/markdown` file under the strict parser.
Unsupported content fails closed; it does not become silently reformatted Braille
content. The configured Drive ID is not rendered in the browser watch floor.

## Read-only access check

After ordinary ADC is configured, an evaluator may run:

```text
uv run --frozen braille-relay doctor --config .env --check-drive
```

This makes a metadata-only request and returns a sanitized pass/block result.
It does not download source bytes, mutate Drive, or reveal the file ID.

## Demo source edit — human action

The live demo asks a human to edit the same prepared authoritative source from
V1 to V2 using their normal Drive UI. Relay never makes that edit. If Drive is
unavailable, use the sanitized fixture fallback described in
[live-demo-runbook.md](live-demo-runbook.md).
