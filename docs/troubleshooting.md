# Astra — troubleshooting

Start by choosing the truthful path. The offline fixture is the fastest way to
verify the visual experience and requires no Google account, Drive access,
CUPS, or production hardware:

~~~text
uv run --frozen python -m braille_errata_relay.presentation.screenshot_fixture --port 8877
~~~

Open `http://127.0.0.1:8877/watch/quiet`. It is visibly marked `SANITIZED DEMO
FIXTURE` and proves UI/contracts only. It is not a workaround for a blocked
live integration claim.

## `doctor` reports ordinary ADC blocked

For local presentation and private Cloud Run access, use ordinary
Google-Cloud-only ADC from a user-controlled terminal:

~~~text
gcloud auth login
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform
~~~

If the browser cannot be launched automatically, use `--no-launch-browser` and
complete the displayed user flow. If a local quota-project warning is the only
block, follow the Cloud SDK guidance or use `--disable-quota-project`; do not
paste a password, access token, or key file into a command or issue.

## `doctor --check-drive` is blocked

The base `doctor` command does not call Drive. `--check-drive` is an optional
metadata-only local diagnostic. Current Google Cloud CLI guidance requires a
project-owned OAuth desktop client for a non-Google-Cloud scope such as
`drive.readonly`; the generic Cloud SDK OAuth client may be blocked for that
scope.

Keep that client JSON outside the repository and use it only from the local
machine:

~~~text
gcloud auth application-default login --client-id-file='C:\safe\astra-drive-oauth-client.json' --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly
uv run --frozen braille-relay doctor --config .env --check-drive
~~~

For the deployed live path, the attached runtime service account—not local
user ADC—reads the one Drive file shared with it. See
[Google Cloud, Drive, and local authentication](google-cloud-setup.md).

## The local watch page shows unavailable or reconnecting

The presentation shell is still loopback-only and safe. Check the non-secret
private Relay origin, audience, and demonstrator principal in `.env`, then run
the base `doctor` command. The live watch needs a human-authorized,
service-account-scoped temporary Token Creator grant for its short-lived ID
token path; it must be absent before and after the operation. The page must
not fall back to a public API or a service-account key.

## `init-local-config` refuses `.env`

It refuses overwrite by design. Review the current file and repeat with
`--force` only when replacing the intended local configuration. It accepts only
`.env` or `.env.local` as output names.

## Liblouis readiness is blocked

Install the pinned Liblouis version and Python binding through the documented
WSL/container setup. Do not substitute a fake translator or ignore a table-hash
mismatch. The Windows host may honestly skip the upstream binding while the
container remains the frozen installed-runtime verification path.

## WSL/CUPS is blocked

Run the CUPS setup/runbook in a human-controlled WSL terminal and preserve the
exact failing command and sanitized error. Do not use Astra or the bridge to
operate the queue, and do not weaken the observer policy to make a denial test
pass.

## The automatic Drive watch is paused or will not enable

The normal live path is one-time `INITIALIZE`, followed by an explicitly
enabled private `astra-automation-cycle` job. The enable command requires the
64-character receipt ID returned by `INITIALIZE`; the configuration script
checks the receipt's shape but does not remotely verify it. Start with the
paused configuration and follow the exact sequence in
[fresh-project deployment](fresh-project-deployment.md#11-automatic-drive-watch-configure-paused-then-enable-explicitly).

To inspect the job without changing it:

~~~text
gcloud scheduler jobs describe astra-automation-cycle --location=europe-west3
~~~

If the job is enabled but a Drive edit does not appear after two cycles, verify
the configured file is shared read-only with the runtime identity and that the
initialization completed. Preserve the scheduler and Cloud Run error evidence.
`reconcile_live_drive.ps1 -Operation RECONCILE` is an explicit diagnostic tool,
not a button to use in the demo hero path. Do not expose the service publicly
or use the dashboard to force a cycle.

For first-time private deployment, use the explicit
[fresh-project deployment guide](fresh-project-deployment.md), not this
troubleshooting page.
