[CmdletBinding()]
param(
    [string]$BaselineId = '',

    [string]$ApprovedBrfSha256 = '',

    [string]$ProductionId = 'BIOLOGY-VOLUME-2-DEMO',

    [string]$Confirmation = '',

    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = 'braille-errata-relay',

    [ValidateNotNullOrEmpty()]
    [string]$Region = 'europe-west3',

    [switch]$ValidateOnly
)

<#
.SYNOPSIS
Arm one registered demo baseline through the real local CUPS simulator.

.DESCRIPTION
This human-invoked helper saves rehearsal time without forging a production
link. It downloads the exact immutable registered BRF, refuses every queue
except the fixed relay-capture demo endpoint, submits through relay-operator,
and reuses the existing read-only observation and exact-byte receipt helpers.

The resulting endpoint receipt is explicitly SIMULATED_DEMO. This script must
never be used with a physical embosser or a production queue.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $repoRoot

if ($ValidateOnly) {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'pyproject.toml'))) {
        throw 'Resolved repository root is not an Astra checkout.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'infra\gcp\link_local_baseline_job.ps1'))) {
        throw 'The production-link evidence helper is missing.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'infra\gcp\confirm_local_endpoint_receipt.ps1'))) {
        throw 'The endpoint-receipt evidence helper is missing.'
    }
    Write-Output 'PASS: simulated baseline arming helper is structurally ready'
    exit 0
}

if ($BaselineId -notmatch '^[0-9a-f]{64}$') {
    throw 'BaselineId must be the complete lowercase baseline SHA-256 shown on the monitor page.'
}
if ($ApprovedBrfSha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'ApprovedBrfSha256 must be the complete lowercase BRF SHA-256 shown on the monitor page.'
}
if ($ProductionId -notmatch '^[A-Za-z0-9._-]{1,128}$') {
    throw 'ProductionId contains unsupported characters.'
}
if ($Confirmation -ne 'ARM-SIMULATED-BASELINE') {
    throw 'Pass -Confirmation ARM-SIMULATED-BASELINE to authorize this local simulator submission.'
}

$project = ([string](gcloud config get-value project 2>$null)).Trim()
$account = ([string](gcloud config get-value account 2>$null)).Trim()
if (-not $project -or -not $account) {
    throw 'An active human gcloud project and account are required.'
}

$service = gcloud run services describe $ServiceName `
    --project=$project `
    --region=$Region `
    --format=json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw 'The private Astra service could not be inspected.'
}
$environment = @{}
foreach ($entry in $service.spec.template.spec.containers[0].env) {
    $environment[[string]$entry.name] = [string]$entry.value
}
$queueName = [string]$environment['RELAY_CUPS_QUEUE_NAME']
$bucketName = [string]$environment['GCS_ARTIFACT_BUCKET']
if ($queueName -ne 'Braille-Embosser-Sim') {
    throw 'Refusing to arm a baseline because the configured queue is not Braille-Embosser-Sim.'
}
if ($bucketName -notmatch '^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$') {
    throw 'The configured immutable artifact bucket is unavailable or malformed.'
}

$deviceLine = [string](
    wsl.exe -d Ubuntu-24.04 --user relay-operator -- lpstat -v $queueName
)
if ($LASTEXITCODE -ne 0 -or $deviceLine.Trim() -ne "device for ${queueName}: relay-capture://demo-embosser") {
    throw 'Refusing submission because the queue is not bound to the fixed relay-capture demo endpoint.'
}

$workRoot = Join-Path $repoRoot 'work\demo-arm'
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
$brfPath = Join-Path $workRoot "$BaselineId.brf"
$artifactUri = "gs://$bucketName/braille/baselines/$ApprovedBrfSha256.brf"
gcloud storage cp $artifactUri $brfPath --quiet | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $brfPath -PathType Leaf)) {
    throw 'The immutable registered BRF could not be downloaded.'
}
$downloadedSha256 = (Get-FileHash -LiteralPath $brfPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($downloadedSha256 -ne $ApprovedBrfSha256) {
    throw 'The downloaded BRF does not match the registered immutable hash.'
}

$wslBrfPath = ([string](wsl.exe -d Ubuntu-24.04 --exec wslpath -a $brfPath)).Trim()
if (-not $wslBrfPath.StartsWith('/')) {
    throw 'The immutable BRF path could not be resolved inside WSL.'
}
$jobTitle = "BER|$ProductionId|$($ApprovedBrfSha256.Substring(0, 12))|BASELINE"

Write-Output 'SIMULATED_DEMO: submitting the exact registered BRF to Braille-Embosser-Sim.'
$submission = @(
    wsl.exe -d Ubuntu-24.04 --user relay-operator -- `
        lp -d $queueName -o raw -t $jobTitle $wslBrfPath
)
if ($LASTEXITCODE -ne 0) {
    throw 'The human-authorized local simulator submission failed.'
}
$submissionText = ($submission -join "`n").Trim()
$escapedQueue = [Regex]::Escape($queueName)
if ($submissionText -notmatch "${escapedQueue}-(?<job>[0-9]+)") {
    throw 'CUPS accepted a response whose scheduler job ID could not be parsed safely.'
}
$schedulerJobId = [int]$Matches['job']

$linkOutput = @(
    & (Join-Path $repoRoot 'infra\gcp\link_local_baseline_job.ps1') `
        -BaselineId $BaselineId `
        -SchedulerJobId $schedulerJobId `
        -ExpectedJobTitle $jobTitle `
        -ServiceName $ServiceName `
        -Region $Region
)
$linkJsonLine = @(
    $linkOutput | Where-Object {
        $_ -is [string] -and $_.TrimStart().StartsWith('{')
    } | Select-Object -Last 1
)
if ($linkJsonLine.Count -ne 1) {
    throw 'The advisory link completed without one machine-readable result.'
}
$linkResult = $linkJsonLine[0] | ConvertFrom-Json
if ($linkResult.status -ne 'PROVISIONAL_PRODUCTION_LINK') {
    throw 'The simulated CUPS observation did not create the expected provisional link.'
}
$productionLinkId = [string]$linkResult.production_link.link_id
$currentStateVersion = [int]$linkResult.baseline.baseline.state_version
if ($productionLinkId -notmatch '^[0-9a-f]{64}$' -or $currentStateVersion -lt 1) {
    throw 'The provisional production-link result is malformed.'
}

& (Join-Path $repoRoot 'infra\gcp\confirm_local_endpoint_receipt.ps1') `
    -BaselineId $BaselineId `
    -ProductionLinkId $productionLinkId `
    -SchedulerJobId $schedulerJobId `
    -ExpectedJobTitle $jobTitle `
    -ApprovedBrfSha256 $ApprovedBrfSha256 `
    -CurrentStateVersion $currentStateVersion `
    -ServiceName $ServiceName `
    -Region $Region
if ($LASTEXITCODE -ne 0) {
    throw 'The exact-byte SIMULATED_DEMO endpoint receipt did not verify.'
}

[ordered]@{
    schema_version = 'simulated-baseline-arming.v1'
    status = 'PRODUCTION_LINK_VERIFIED'
    truth_basis = 'SIMULATED_DEMO'
    baseline_id = $BaselineId
    production_link_id = $productionLinkId
    scheduler_job_id = $schedulerJobId
    approved_brf_sha256 = $ApprovedBrfSha256
    physical_embosser = 'NOT_USED'
} | ConvertTo-Json -Compress

