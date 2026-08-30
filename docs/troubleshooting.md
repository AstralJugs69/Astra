# Troubleshooting

## `doctor` reports ordinary ADC blocked

Run browser-based authentication from a user-controlled terminal:

```text
gcloud auth login
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly
```

Do not paste a password, access token, or key file into any command or issue.

## The local watch page shows unavailable/reconnecting

The presentation shell is still local and safe. Verify `.env` has non-secret
private Relay origin and demonstrator principal values, then confirm the
temporary human-authorized impersonation path separately. The page must not
fall back to a public API or a service-account key.

## `init-local-config` refuses `.env`

It refuses overwrite by design. Review the current file and repeat with
`--force` only when replacing the intended local configuration. It accepts only
`.env` or `.env.local` as output names.

## `doctor --check-drive` is blocked

Check that the configured source is a supported `text/markdown` Google Drive
file and that ordinary ADC has Drive readonly scope. The check is metadata-only;
do not work around a block by widening Drive write permissions.

## Liblouis readiness is blocked

Install the pinned Liblouis version and Python binding through the documented
WSL/container setup. Do not substitute a fake translator or ignore a table hash
mismatch. The Windows host may honestly skip the upstream binding while the
container test remains the reproducible golden path.

## WSL/CUPS is blocked

Run the CUPS setup/runbook in a human-controlled WSL terminal and preserve the
exact failing command and sanitized error. Do not use the Relay app or bridge to
operate the queue, and do not weaken the observer policy to make a denial test
pass.

## The scheduled closure script refuses to run near a boundary

That refusal protects the recurring scheduler’s ownership. Wait for a safe
window or use the documented operator procedure; do not unpause the scheduler
or force a run from the dashboard.
