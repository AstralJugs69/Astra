# Astra — local configuration

`braille-relay init-local-config` creates an ignored local `.env` containing
only non-secret identifiers. It never reads a Gmail password, creates a key, or
stores an OAuth access token. This is not a Cloud Run deployment template:
local `SITE_ID`, `QUEUE_NAME`, and `LOCAL_BRIDGE_ID` identify the local
presentation/observer topology, while deployed Cloud Run uses separate
`RELAY_*` configuration. See [fresh-project deployment](fresh-project-deployment.md).

## Initialize

~~~text
uv run --frozen braille-relay init-local-config \
  --project-id <project-id> \
  --region europe-west3 \
  --drive-source <drive-url-or-file-id> \
  --source-mime-type application/vnd.google-apps.document \
  --site-id <site-id> \
  --queue-name <queue-name> \
  --bridge-id <bridge-id> \
  --demonstrator-principal <service-account-email> \
  --telemetry-principal <service-account-email> \
  --relay-api-url <private-https-origin> \
  --relay-audience <private-https-origin>
~~~

This command **writes local state only**. To replace an existing file, add
`--force` deliberately. It accepts these standard Drive forms:

- direct file ID;
- `https://drive.google.com/file/d/<id>/...`;
- `https://drive.google.com/open?id=<id>`;
- `https://docs.google.com/document/d/<id>/...`.

It rejects non-HTTPS, credential-bearing, or non-Google URLs.

## Fields

| `.env` field | Required | Purpose |
| --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Yes | Deployment project reference. |
| `CLOUD_RUN_REGION` | Yes | Defaults to `europe-west3`. |
| `DRIVE_FILE_ID` | Yes | One authoritative source identity. |
| `DRIVE_SOURCE_MIME_TYPE` | Yes | One strict source type: `text/markdown` for a Drive-hosted UTF-8 Markdown file, or `application/vnd.google-apps.document` for a native Google Doc exported read-only as Markdown. |
| `SITE_ID`, `QUEUE_NAME`, `LOCAL_BRIDGE_ID` | Yes | Read-only local site-observation identity. |
| `DEMONSTRATOR_PRINCIPAL_EMAIL` | Live presentation | Human-authorized temporary impersonation target. |
| `PUBLIC_READER_PRINCIPAL_EMAIL` | Cloud Run public dashboard only | Dedicated service identity admitted only to monitor-safe private API GET routes. It is not written by `init-local-config`. |
| `INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL` | Telemetry runbook | Read-only bridge telemetry principal. |
| `RELAY_API_BASE_URL`, `RELAY_API_AUDIENCE` | Live presentation | Credential-free private HTTPS origins. |

## Doctor

~~~text
uv run --frozen braille-relay doctor --config .env
uv run --frozen braille-relay doctor --config .env --check-drive
uv run --frozen braille-relay doctor --config .env --check-wsl-cups
~~~

The base command does not mutate local, cloud, Drive, CUPS, or device state.
`--check-drive` performs one metadata-only Google Drive read; it does not edit,
move, share, or create a file. It is optional and needs a project-owned OAuth
client configured for `drive.readonly`, as documented in
[Google Cloud, Drive, and local authentication](google-cloud-setup.md).
`--check-wsl-cups` checks WSL availability only and does not invoke CUPS
commands.

Diagnostics deliberately say “configured” rather than printing Drive IDs,
private service origins, or principal addresses.
