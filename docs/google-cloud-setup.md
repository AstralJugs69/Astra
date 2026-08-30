# Astra — Google Cloud, Drive, and local authentication

This chapter explains the credential boundaries around Astra. For a new Google
Cloud project, use the explicit
[fresh-project deployment guide](fresh-project-deployment.md) first. This
chapter is intentionally narrower: it distinguishes the private Cloud Run
runtime, the local loopback dashboard, and the optional local Drive diagnostic.

Never put a Gmail password, OAuth access token, service-account JSON key, or
private Drive ID in source control, a demo recording, or a browser page.

## Three identities, three different jobs

| Identity | What it does | What it must not do |
| --- | --- | --- |
| **Cloud Run runtime service account** | Reads the one shared Drive source, calls Gemini/ADK, and reads/writes Firestore/GCS evidence. | Access CUPS, receive a service-account key, or operate physical production. |
| **Ordinary local user ADC** | Lets the local loopback presentation server obtain a short-lived token only during a human-authorized private-service session. | Reach Drive from the browser, become a permanent impersonation principal, or use a key file. |
| **Dedicated route service accounts** | Call only their allowlisted private Cloud Run route family: source, telemetry, scheduler, demonstrator, or endpoint evidence. | Substitute for one another or receive broad project-level authority. |

Google Cloud IAM protects entry to the private Cloud Run service. Astra then
verifies the OIDC audience and exact caller email per route. This is deliberate
defense in depth.

## Normal local sign-in: Cloud Platform scope only

The local watch floor does **not** need Drive permission on the laptop. Its
browser receives no Google credential, Drive ID, or private Cloud Run URL. The
Cloud Run runtime identity owns live Drive reads.

From a user-controlled terminal:

~~~text
gcloud auth login
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform
~~~

The two credential stores are intentionally separate: **gcloud auth login**
authenticates CLI operations, while **application-default login** creates the
ordinary local user credentials used by Google client libraries.

If browser launching is the only problem, use:

~~~text
gcloud auth application-default login --no-launch-browser --scopes=https://www.googleapis.com/auth/cloud-platform
~~~

If the account lacks permission to select a quota project, add
**--disable-quota-project**. Neither flag bypasses a Workspace, OAuth, or
organization policy.

## Why the previous Drive-scoped ADC command can be blocked

Google’s current CLI guidance treats Drive as a non-Google-Cloud scope. It
requires a project-owned OAuth client passed with **--client-id-file** rather
than the shared Cloud SDK sign-in client. That is why a generic command that
adds **drive.readonly** can be blocked even when normal Cloud Platform ADC is
valid.

This does not block the normal live watch floor. Skip the optional local Drive
check unless you specifically need it.

## Optional local Drive metadata check

The base diagnostic is non-mutating and does not call Drive:

~~~text
uv run --frozen braille-relay doctor --config .env
~~~

Only this optional command performs a metadata-only Drive read:

~~~text
uv run --frozen braille-relay doctor --config .env --check-drive
~~~

For that optional check:

1. Create an approved **Desktop OAuth client** in the project that owns the
   local evaluator flow.
2. If the OAuth audience is in testing, add the evaluator’s Google account as
   a test user. If Workspace policy applies, ask the administrator to allowlist
   the client and read-only scope.
3. Keep the downloaded client configuration outside the repository.
4. Request only Cloud Platform plus Drive read-only scopes:

~~~powershell
gcloud auth application-default login --client-id-file='C:\safe\astra-drive-oauth-client.json' --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly
~~~

The OAuth client configuration is not a service-account key, but it is still
local credential configuration and must never be committed. If an organization
blocks it, use the offline fixture or obtain approved administrator help; do
not weaken the policy.

## Runtime Drive access is separate

The deployed service uses the attached runtime service account and asks Google
authentication libraries for Drive read-only scope. Before live reconciliation:

1. enable the Drive API in the Cloud project;
2. use exactly one supported **text/markdown** Drive file;
3. share that file or approved Shared Drive content with the runtime service
   account as a viewer;
4. configure the same file ID as **DRIVE_FILE_ID** in the Cloud Run runtime
   environment.

The source adapter drains the Drive change feed and then re-fetches
authoritative metadata and bytes. A notification or changed timestamp is not
accepted as source truth on its own. See
[authoritative-drive-source.md](authoritative-drive-source.md).

## Private Cloud Run and the temporary demonstrator grant

Cloud Run must remain private. Do not add allUsers or allAuthenticatedUsers as
an invoker.

The loopback dashboard is a local server bound to 127.0.0.1. It uses ordinary
local user ADC to mint a short-lived, audience-bound ID token by impersonating
the dedicated demonstrator service account. That requires a narrowly scoped
**roles/iam.serviceAccountTokenCreator** binding on that one service account
only while the human is running the local presentation flow.

The required lifecycle is:

1. verify the binding is absent before the operation;
2. add it at the named service-account resource scope, never project scope;
3. start or perform the bounded operation;
4. remove the exact binding in a finally/cleanup path;
5. verify it is absent afterward.

The project’s human-owned runbook preserves that cleanup pattern:
[active-professional-review-demo.md](active-professional-review-demo.md).
The launcher at [infra/demo/start_demo.ps1](../infra/demo/start_demo.ps1)
never grants IAM itself.

## Safe verification

Use these checks in order:

~~~text
uv run --frozen braille-relay doctor --config .env
~~~

Then, only after the private service and temporary impersonation path have been
reviewed, follow the read-only smoke described in
[fresh-project-deployment.md](fresh-project-deployment.md#13-verify-safely).
It uses the checked-in helper, performs authenticated GET requests, and
verifies temporary Token Creator cleanup. Do not treat a public health endpoint
as an acceptable alternative; a private Cloud Run service should reject
unauthenticated traffic at its IAM boundary.

## What this setup does not authorize

Authentication for Astra does not authorize:

- Drive edits, sharing changes, or deletion;
- CUPS submit, hold, release, cancel, restart, pause, or device control;
- automatic professional disposition, proof approval, or replacement
  submission;
- endpoint completion, final physical verification, or closure;
- a service-account JSON key or permanent Token Creator grant.

For all local settings, see [configuration.md](configuration.md). For the
offline, no-auth evaluator path, see [quickstart.md](quickstart.md).

## Official references

- [Application Default Credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc)
- [gcloud ADC login flags and non-Cloud scopes](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/login)
- [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service)
- [Google Drive changes](https://developers.google.com/workspace/drive/api/guides/manage-changes)
