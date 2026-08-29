[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ServiceUrl,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SchedulerServiceAccount,

    [ValidateNotNullOrEmpty()]
    [string]$JobName = "relay-outbox-drain",

    [ValidateNotNullOrEmpty()]
    [string]$Location = "europe-west3"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$messageBody = Join-Path $repositoryRoot "config\scheduler\outbox-drain-request.v1.json"

if (-not (Test-Path -LiteralPath $messageBody -PathType Leaf)) {
    throw "Canonical scheduler request body is missing."
}

$body = Get-Content -Raw -LiteralPath $messageBody | ConvertFrom-Json
if ($body.schema_version -ne "outbox-drain-request.v1" -or $body.limit -ne 10) {
    throw "Canonical scheduler request body is invalid."
}

$target = $ServiceUrl.TrimEnd("/") + "/internal/outbox-drain"

gcloud scheduler jobs update http $JobName `
    --location $Location `
    --uri $target `
    --http-method POST `
    --oidc-service-account-email $SchedulerServiceAccount `
    --oidc-token-audience $ServiceUrl.TrimEnd("/") `
    --update-headers "Content-Type=application/json" `
    --message-body-from-file $messageBody `
    --quiet

if ($LASTEXITCODE -ne 0) {
    throw "Cloud Scheduler update failed."
}

# Configuration never opts into continuous operation. A human may run one bounded
# acceptance execution later, after the deployed route has been verified.
gcloud scheduler jobs pause $JobName --location $Location --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Cloud Scheduler was updated but could not be returned to PAUSED."
}

Write-Output "PASS: scheduler uses the canonical JSON body and remains paused"
