# Astra — fresh-project Google Cloud deployment

This is a human-reviewed deployment path for the demonstrated Astra vertical
slice. It creates real Google Cloud resources. Read every command before
running it in your own project.

It deliberately does **not** create service-account JSON keys, make Cloud Run
public, provision a production device, submit a CUPS job, alter Drive content,
or enable a continuous scheduler by default.

The outcome is a private Cloud Run service with:

- a pinned Liblouis runtime built from this repository;
- a Firestore Native default database for the durable ledger;
- a private GCS artifact bucket with uniform access and public-access
  prevention;
- a bounded Gemini/ADK semantic-assessment adapter;
- a single read-only Google Drive source adapter;
- separate caller identities for source, telemetry, scheduler, demonstrator,
  and endpoint-evidence routes;
- a private automatic Drive/outbox scheduler job, configured paused until a
  human explicitly enables it.

This guide supports the current release, not a generic production platform.
The current source adapter accepts one shared native Google Doc or one
**text/markdown** Drive file; the current Firestore helper scripts support only
the **(default)** database.

## 1. Decide whether you need this path

Use the [offline evaluator path](quickstart.md)
if you want to inspect the UI and contracts. It requires no Google account,
cloud resources, Drive access, CUPS, or credentials.

Continue here only if you want to reproduce the configured private Cloud Run,
Drive, Firestore/GCS, Gemini/ADK, or CUPS-adapter seams. This is a
production-style setup exercise, not a requirement for viewing the project.

## 2. Prerequisites

You need:

- a Google Cloud project with billing enabled;
- a project administrator who can enable APIs, create service accounts, create
  Firestore/GCS resources, deploy Cloud Run, and set resource-scoped IAM;
- Google Cloud CLI, Docker or Cloud Build access, Git, Python 3.11 or 3.12,
  and uv on the deploy machine;
- a Google Drive file you may share read-only with a service account;
- a Vertex AI region and explicit Gemini model ID approved for the project;
- Windows plus WSL2/Ubuntu and CUPS only if you also want the optional local
  production-floor harness.

The examples use PowerShell and **europe-west3** for Cloud Run. Firestore,
Cloud Storage, and the selected Vertex AI model must each support the location
you choose. Do not assume a model is available in every region.

## 3. Select a project and enable only the needed APIs

Use an existing billed project, or create one through your organization’s
approved process. If you create a project with the CLI, linking billing is a
separate human-authorized step.

~~~powershell
$ProjectId = '<your-project-id>'
$RunRegion = 'europe-west3'
$FirestoreLocation = 'europe-west3'
$VertexLocation = 'global'

gcloud config set project $ProjectId
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com storage.googleapis.com aiplatform.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com iamcredentials.googleapis.com serviceusage.googleapis.com drive.googleapis.com logging.googleapis.com
~~~

The current release does not require Pub/Sub, Workspace Events, Secret Manager,
or a Gemini API key fallback. Do not provision those services for this release.

## 4. Create the durable data resources

Choose these names before running the commands. Bucket names are globally
unique.

~~~powershell
$ArtifactBucket = '<globally-unique-astra-artifacts-bucket>'
$FirestoreDatabase = '(default)'

gcloud firestore databases create --database=$FirestoreDatabase --location=$FirestoreLocation --type=firestore-native --delete-protection
gcloud storage buckets create ('gs://' + $ArtifactBucket) --location=$RunRegion --uniform-bucket-level-access --public-access-prevention
~~~

Firestore location and database choice are difficult to change later. The
checked-in index helpers hard-code **(default)**, so use that database for this
release. Delete protection is appropriate for a persistent demonstration
project; a temporary experiment may omit it only after an explicit lifecycle
decision.

The artifact bucket contains content-addressed source and derived evidence. The
application performs create-only upload and verified readback. It does not need
public objects, object ACLs, object deletion, or listing authority.

## 5. Create the six service identities

Keep the runtime identity separate from identities that invoke private API
routes. Replace the account names only if your organizational naming policy
requires it.

~~~powershell
$ServiceName = 'braille-errata-relay'
$RuntimeName = 'astra-runtime'
$SourceName = 'astra-source-invoker'
$TelemetryName = 'astra-telemetry-invoker'
$SchedulerName = 'astra-scheduler-invoker'
$DemonstratorName = 'astra-demonstrator'
$EndpointEvidenceName = 'astra-endpoint-evidence'

gcloud iam service-accounts create $RuntimeName --display-name='Astra runtime'
gcloud iam service-accounts create $SourceName --display-name='Astra source caller'
gcloud iam service-accounts create $TelemetryName --display-name='Astra telemetry caller'
gcloud iam service-accounts create $SchedulerName --display-name='Astra scheduler caller'
gcloud iam service-accounts create $DemonstratorName --display-name='Astra local demonstrator'
gcloud iam service-accounts create $EndpointEvidenceName --display-name='Astra endpoint evidence caller'

$RuntimeServiceAccount = $RuntimeName + '@' + $ProjectId + '.iam.gserviceaccount.com'
$SourceServiceAccount = $SourceName + '@' + $ProjectId + '.iam.gserviceaccount.com'
$TelemetryServiceAccount = $TelemetryName + '@' + $ProjectId + '.iam.gserviceaccount.com'
$SchedulerServiceAccount = $SchedulerName + '@' + $ProjectId + '.iam.gserviceaccount.com'
$DemonstratorServiceAccount = $DemonstratorName + '@' + $ProjectId + '.iam.gserviceaccount.com'
$EndpointEvidenceServiceAccount = $EndpointEvidenceName + '@' + $ProjectId + '.iam.gserviceaccount.com'
~~~

### Runtime permissions

Grant the Cloud Run runtime only the permissions evidenced by the current
adapter code:

~~~powershell
gcloud projects add-iam-policy-binding $ProjectId --member=('serviceAccount:' + $RuntimeServiceAccount) --role='roles/datastore.user'
gcloud projects add-iam-policy-binding $ProjectId --member=('serviceAccount:' + $RuntimeServiceAccount) --role='roles/aiplatform.user'
gcloud storage buckets add-iam-policy-binding ('gs://' + $ArtifactBucket) --member=('serviceAccount:' + $RuntimeServiceAccount) --role='roles/storage.objectCreator'
gcloud storage buckets add-iam-policy-binding ('gs://' + $ArtifactBucket) --member=('serviceAccount:' + $RuntimeServiceAccount) --role='roles/storage.objectViewer'
~~~

The runtime receives no Cloud Run Invoker role. It does not need CUPS
credentials, production-device credentials, or an administrator role.

### Route identities

The five caller identities later receive **roles/run.invoker** on this one
Cloud Run service. Astra then applies a second, application-level check: exact
OIDC audience plus the expected email address for each route.

| Identity | Route family allowed by Astra |
| --- | --- |
| source | Internal source-job route |
| telemetry | Internal site-observation route |
| scheduler | Internal automatic Drive/outbox cycle and diagnostic routes |
| demonstrator | Human-review API routes under /api |
| endpoint evidence | Endpoint-receipt and historical-link-correction routes |

Do not grant **allUsers**, **allAuthenticatedUsers**, or a project-wide Token
Creator role.

## 6. Prepare the authoritative Drive source

The current source adapter is narrow by design:

1. Create either a simple native Google Doc or upload one UTF-8 Markdown file
   whose Google Drive MIME type is **text/markdown**. Native Docs are exported
   read-only as Markdown; those exported bytes are authoritative input.
2. Share that exact file, or the appropriate Shared Drive content, with
   **$RuntimeServiceAccount** as a viewer. The runtime uses Drive read-only
   scopes and re-fetches metadata plus bytes before accepting a revision.
3. Copy the file ID. Do not put a personal password, OAuth token, or source
   content into the repository.

Some Google Workspace organizations restrict sharing with service accounts or
third-party OAuth clients. Ask the Workspace administrator to permit the
least-privileged approved path; do not try to bypass the policy.

See [authoritative-drive-source.md](authoritative-drive-source.md) for the
source-authority rule and reconciliation behavior.

## 7. Prepare a non-secret deployment environment file

Create an ignored file such as **work/deploy.env** with the following shape.
It contains identifiers and configuration, not a secret. Do not use the local
presentation **.env** as this file: local configuration uses SITE_ID,
QUEUE_NAME, and LOCAL_BRIDGE_ID, while the Cloud Run service reads the
RELAY-prefixed values below.

~~~dotenv
GOOGLE_CLOUD_PROJECT=<project-id>
CLOUD_RUN_REGION=europe-west3
GOOGLE_CLOUD_LOCATION=<Vertex-location-for-your-model>
GEMINI_MODEL=<explicit-approved-Vertex-Gemini-model-id>
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_GENAI_USE_ENTERPRISE=TRUE

FIRESTORE_DATABASE=(default)
GCS_ARTIFACT_BUCKET=<globally-unique-artifact-bucket>
RUNTIME_SERVICE_ACCOUNT_EMAIL=<astra-runtime-service-account-email>

DRIVE_FILE_ID=<one-shared-drive-file-id>
# Choose: text/markdown or application/vnd.google-apps.document
DRIVE_SOURCE_MIME_TYPE=application/vnd.google-apps.document
SOURCE_MAX_BYTES=1048576
SEMANTIC_CONTEXT_CHARS=12000
SEMANTIC_MODEL_TIMEOUT_SECONDS=90

INTERNAL_SOURCE_PUSH_PRINCIPAL_EMAIL=<source-service-account-email>
INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL=<telemetry-service-account-email>
INTERNAL_SCHEDULER_PRINCIPAL_EMAIL=<scheduler-service-account-email>
DEMONSTRATOR_PRINCIPAL_EMAIL=<demonstrator-service-account-email>
ENDPOINT_EVIDENCE_PRINCIPAL_EMAIL=<endpoint-evidence-service-account-email>

RELAY_SITE_ID=<site-id>
RELAY_BRIDGE_ID=<bridge-id>
RELAY_CUPS_QUEUE_NAME=<queue-name>
~~~

The two GOOGLE_GENAI settings are consumed by the pinned Google Gen AI/ADK
runtime rather than validated directly by Astra’s settings model. Use an exact
Gemini model ID available in the selected Vertex location; do not use an API
key fallback.

Do not set **INTERNAL_OIDC_AUDIENCE** yet. It must equal the generated Cloud
Run URL, which does not exist until the first private deployment finishes.

## 8. Deploy privately in two passes

The checked-in Dockerfile builds the pinned Liblouis environment. The
repository’s .gcloudignore excludes local configuration, generated work, and
credential material from a source upload.

From the repository root:

~~~powershell
$DeployEnvPath = (Resolve-Path -LiteralPath '.\work\deploy.env').Path
gcloud run deploy $ServiceName --source . --region=$RunRegion --service-account=$RuntimeServiceAccount --no-allow-unauthenticated --ingress=all --env-vars-file=$DeployEnvPath

$ServiceUrl = (gcloud run services describe $ServiceName --region=$RunRegion --format='value(status.url)').Trim()
gcloud run services update $ServiceName --region=$RunRegion --update-env-vars=('INTERNAL_OIDC_AUDIENCE=' + $ServiceUrl)
~~~

The first pass deliberately leaves protected routes unavailable because the
correct OIDC audience is not known yet. The second pass binds the audience to
the actual private service URL. Do not substitute a guessed URL or make the
service public to work around this step.

Cloud Run source deployment uses Cloud Build and Artifact Registry under the
project’s approved build configuration. The deployer needs the standard
source-deploy permissions and permission to act as the runtime service
account. Refer to Google’s current
[Cloud Run source deployment guide](https://cloud.google.com/run/docs/deploying-source-code)
and [Cloud Run IAM role reference](https://cloud.google.com/run/docs/reference/iam/roles)
if organization policy requires a custom deployer role.

## 9. Bind private service callers

After the service exists, grant each caller identity service-level invocation
permission. This allows it through Cloud Run’s outer IAM gate; it does not
override Astra’s route-level exact-principal check.

~~~powershell
$CallerServiceAccounts = @($SourceServiceAccount, $TelemetryServiceAccount, $SchedulerServiceAccount, $DemonstratorServiceAccount, $EndpointEvidenceServiceAccount)
foreach ($Caller in $CallerServiceAccounts) {
    gcloud run services add-iam-policy-binding $ServiceName --region=$RunRegion --member=('serviceAccount:' + $Caller) --role='roles/run.invoker'
}

gcloud run services get-iam-policy $ServiceName --region=$RunRegion --format=json
~~~

Review the final policy. There must be no binding granting **roles/run.invoker**
to allUsers or allAuthenticatedUsers.

## 10. Create the two Firestore indexes

The application needs these exact default-database indexes:

- outbox: status ascending, created_at ascending;
- incident timeline: record.incident_id ascending, record.recorded_at ascending.

Run the checked-in idempotent helpers from PowerShell:

~~~powershell
.\infra\gcp\ensure_outbox_lease_index.ps1
.\infra\gcp\ensure_incident_timeline_index.ps1
~~~

They create and poll the indexes until ready. They do not support a named
Firestore database in this release.

## 11. Automatic Drive watch: configure paused, then enable explicitly

The automatic watch is the normal live path after the one-time source
initialization in step 14. It calls the private `/internal/automation-cycle`
route every minute, drains `changes.list` from the durable cursor, refetches
source truth when the configured file changed, and processes one resulting
durable outbox record. It does not make Drive write calls or gain CUPS/device
authority.

The checked-in script creates or normalizes the job with the canonical
[automation-cycle request](../config/scheduler/automation-cycle-request.v1.json)
and leaves it **PAUSED** unless `-EnableAutomaticWatch` is supplied. It reads
the deployed `INTERNAL_OIDC_AUDIENCE` rather than assuming the service status
URL is the token audience.

For an optional non-mutating preflight, append `-WhatIf` to the first command.
It shows the intended scheduler configuration without creating, updating,
pausing, or resuming a job.

Run only the first command at this stage. Complete the initialization in step
14 before returning here to run the explicit enablement command.

~~~powershell
$AutomationSchedulerJobName = 'astra-automation-cycle'

# Create or normalize the private job; its safe default is PAUSED.
.\infra\gcp\configure_automation_scheduler.ps1 `
  -ServiceUrl $ServiceUrl `
  -ServiceName $ServiceName `
  -SchedulerServiceAccount $SchedulerServiceAccount `
  -JobName $AutomationSchedulerJobName `
  -Location $RunRegion

# Run this only after the INITIALIZE command in step 14 returns a receipt ID.
# The script validates its SHA-256 shape; it does not remotely verify the receipt.
$InitializationReceiptId = '<64-character-initialize-receipt-id>'
.\infra\gcp\configure_automation_scheduler.ps1 `
  -ServiceUrl $ServiceUrl `
  -ServiceName $ServiceName `
  -SchedulerServiceAccount $SchedulerServiceAccount `
  -JobName $AutomationSchedulerJobName `
  -Location $RunRegion `
  -InitializationReceiptId $InitializationReceiptId `
  -EnableAutomaticWatch
~~~

The scheduler uses the existing dedicated scheduler identity and private Cloud
Run IAM boundary. It does not add IAM bindings, expose a public route, or mint
service-account keys. The older standalone `astra-outbox-drain` job is not
needed for the automatic watch; if it exists, leave it paused.

## 12. Authentication: normal local presentation versus optional Drive check

### Local presentation and private Cloud Run access

The local loopback dashboard needs ordinary user Application Default
Credentials with Cloud Platform scope. It never receives credentials in the
browser:

~~~text
gcloud auth login
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform
~~~

This is separate from the attached Cloud Run runtime identity. It is sufficient
for the local presentation server’s temporary, audience-bound demonstrator
token path.

### Optional local Drive metadata diagnostic

The base doctor command does not call Drive. Only this optional command needs a
local user Drive scope:

~~~text
uv run --frozen braille-relay doctor --config .env --check-drive
~~~

Current gcloud guidance requires a project-owned OAuth client for a non-Cloud
scope such as Drive. If you truly need this optional local diagnostic, create
an approved Desktop OAuth client in your project, keep the downloaded client
file outside the repository, and use:

~~~powershell
gcloud auth application-default login --client-id-file='C:\safe\astra-drive-oauth-client.json' --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly
~~~

The OAuth client file is not a service-account key, but it is local
credential configuration and must not be committed. If Workspace policy blocks
it, skip this optional diagnostic or have the administrator approve the
client/scope. Do not weaken the policy or use a key file.

## 13. Verify safely

The service is private, so an unauthenticated request must be rejected at the
Cloud Run IAM boundary. For the documented authenticated, read-only smoke, use
the checked-in helper:

~~~powershell
.\infra\gcp\test_private_routes.ps1 -ServiceName $ServiceName -Region $RunRegion
~~~

The application requests are GET-only, but the helper itself temporarily adds
a service-account-scoped Token Creator binding to mint an audience-bound
identity token and removes it in a finally block. Review the output and confirm
the cleanup check passes. Its successful authenticated **readyz** response
proves the pinned profile and Liblouis readiness; it is necessary but does
**not** prove Firestore, GCS, Drive, Gemini, OIDC principal configuration, or
a source-to-production lifecycle. Do not add **VerifyEmptyOutboxReplay** during
an initial smoke because it invokes the outbox route.

Then run:

~~~powershell
uv run --frozen braille-relay doctor --config .env
~~~

The local doctor is non-mutating. For the live watch floor’s short-lived
demonstrator permission and cleanup sequence, follow
[google-cloud-setup.md](google-cloud-setup.md) rather than leaving a permanent
Token Creator grant.

## 14. Initialize once, then let the automatic watch reconcile

The present release uses a private Cloud Scheduler cycle, not a Workspace
Events/Pub/Sub subscription. The cycle runs **Drive changes.list plus
authoritative byte refetch**. A notification, timestamp, or scheduler tick is
never source truth on its own.

Before enabling the watch, initialize the Drive cursor and register the
matching accepted baseline. The recommended path is the loopback dashboard at
`/setup/source`, which verifies the configured source and performs
initialization plus deterministic registration in the required order. See
[guided-baseline-onboarding.md](guided-baseline-onboarding.md).

The lower-level diagnostic path remains available. First initialize the
accepted source revision:

~~~powershell
.\infra\gcp\reconcile_live_drive.ps1 -Operation INITIALIZE -ExecuteDriveRead
~~~

Record the returned `receipt_id` and pass it to the enable command in step 11.
That script validates the receipt's SHA-256 shape only; it does not remotely
verify the record. This diagnostic operation uses the scheduler identity and
the project's temporary scoped impersonation procedure. Then register that
exact revision through the authenticated baseline API/CLI before enabling the
watch. Never register a revision that has not been durably initialized.

After it succeeds and the automatic watch is enabled in step 11, a human Drive
V1-to-V2 edit requires no reconciliation command: Astra detects it on the next
cycle and begins the durable investigation. `reconcile_live_drive.ps1 -Operation RECONCILE`
remains an explicit diagnostic/recovery tool, not the hero path.
Read [authoritative-drive-source.md](authoritative-drive-source.md) and
[live-demo-runbook.md](live-demo-runbook.md) before exercising it.

## 15. Cleanup and non-negotiable boundaries

At the end of an experiment:

- pause the automatic scheduler when the live watch is no longer needed;
- remove only the specific temporary Token Creator bindings created for the
  human operation, and verify their absence;
- keep Cloud Run private and re-check there is no public invoker binding;
- retain or deliberately delete the Firestore database and artifact bucket
  according to the project’s data-retention decision;
- revoke local ADC only if appropriate for the user and machine;
- never delete or rewrite immutable evidence merely to make a demo look
  cleaner.

This guide does not change the core boundary: Astra investigates and prepares
recovery; humans retain professional approval and physical-production
authority.

## Official references

- [Cloud Run source deployment](https://cloud.google.com/run/docs/deploying-source-code)
- [Cloud Run private access and invoker IAM](https://cloud.google.com/run/docs/securing/managing-access)
- [Cloud Scheduler OIDC HTTP targets](https://cloud.google.com/scheduler/docs/http-target-auth)
- [Firestore Native database creation](https://cloud.google.com/firestore/docs/manage-databases)
- [Cloud Storage bucket creation flags](https://cloud.google.com/sdk/gcloud/reference/storage/buckets/create)
- [Cloud Storage IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles)
- [Vertex AI Gemini quickstart](https://cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart)
- [gcloud Application Default Credentials login](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/login)
