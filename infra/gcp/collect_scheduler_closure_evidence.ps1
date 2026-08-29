[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [datetime]$EvidenceSinceUtc,

    [ValidateNotNullOrEmpty()]
    [string]$JobName = "relay-outbox-drain",

    [ValidateNotNullOrEmpty()]
    [string]$Location = "europe-west3",

    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "braille-errata-relay",

    [AllowEmptyString()]
    [string]$RepoRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Get-Sha256File {
    param([Parameter(Mandatory)][string]$Path)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead($Path)
        try {
            $hash = $algorithm.ComputeHash($stream)
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $algorithm.Dispose()
    }
    return (-join ($hash | ForEach-Object { $_.ToString("x2") }))
}

function Read-LogEntries {
    param([Parameter(Mandatory)][string]$Filter)

    $raw = & gcloud.cmd logging read $Filter --limit=20 --format=json
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Logging query failed."
    }
    if ([string]::IsNullOrWhiteSpace(($raw -join ""))) {
        return @()
    }
    return @($raw | ConvertFrom-Json | Where-Object { $null -ne $_ })
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][object]$Value
    )

    $destination = [IO.Path]::GetFullPath($Path)
    $directory = Split-Path -Parent $destination
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    $temporary = Join-Path $directory (
        "." + [IO.Path]::GetFileName($destination) + "." +
        [Guid]::NewGuid().ToString("N") + ".tmp"
    )
    try {
        $json = $Value | ConvertTo-Json -Depth 8 -Compress
        [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.Encoding]::UTF8)
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

$now = (Get-Date).ToUniversalTime()
$since = $EvidenceSinceUtc.ToUniversalTime()
if ($since -gt $now -or ($now - $since).TotalHours -gt 2) {
    throw "EvidenceSinceUtc must identify a recent completed invocation."
}

$repoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
$requestPath = Join-Path $repoRootPath "config\scheduler\outbox-drain-request.v1.json"
$workRoot = Join-Path $repoRootPath "work\live-closure"
$attemptPath = Join-Path $workRoot "single-scheduler-run-attempt.json"
$recoveryAttemptPath = Join-Path $workRoot "single-scheduler-recovery-attempt.json"
$resultPath = Join-Path $workRoot "single-scheduler-run.json"
foreach ($requiredPath in @($requestPath, $attemptPath, $recoveryAttemptPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required scheduler evidence input is missing."
    }
}

$requestSha256 = Get-Sha256File -Path $requestPath
$attempt = Get-Content -Raw -LiteralPath $attemptPath | ConvertFrom-Json
$recoveryAttempt = Get-Content -Raw -LiteralPath $recoveryAttemptPath | ConvertFrom-Json
if (
    $attempt.schema_version -ne "single-scheduler-run-attempt.v1" -or
    $attempt.request_body_sha256 -ne $requestSha256 -or
    $recoveryAttempt.schema_version -ne "single-scheduler-recovery-attempt.v1" -or
    $recoveryAttempt.request_body_sha256 -ne $requestSha256
) {
    throw "Scheduler attempt lineage does not match the canonical request."
}

$state = ([string](& gcloud.cmd scheduler jobs describe $JobName --location=$Location `
    --format="value(state)")).Trim()
if ($LASTEXITCODE -ne 0 -or $state -ne "PAUSED") {
    throw "Scheduler must be PAUSED before existing evidence can be collected."
}

$startedAt = $since.ToString("yyyy-MM-ddTHH:mm:ssZ")
$schedulerFilter = (
    'resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"' + $JobName + '\" AND ' +
    'timestamp>=\"' + $startedAt + '\"'
)
$uvicornFilter = (
    'resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"' +
    $ServiceName + '\" AND textPayload:\"/internal/outbox-drain\" AND timestamp>=\"' +
    $startedAt + '\"'
)
$schedulerEntries = Read-LogEntries -Filter $schedulerFilter
$uvicornEntries = Read-LogEntries -Filter $uvicornFilter
$schedulerSuccess = @(
    $schedulerEntries | Where-Object {
        $httpRequestProperty = $_.PSObject.Properties["httpRequest"]
        $null -ne $httpRequestProperty -and
            $null -ne $httpRequestProperty.Value -and
            [int]$httpRequestProperty.Value.status -eq 200
    }
)
$uvicornSuccess = @(
    $uvicornEntries | Where-Object {
        $textPayloadProperty = $_.PSObject.Properties["textPayload"]
        $null -ne $textPayloadProperty -and
            ([string]$textPayloadProperty.Value) -match
                '/internal/outbox-drain HTTP/1\.1"\s+200\b'
    }
)
if ($schedulerSuccess.Count -ne 1 -or $uvicornSuccess.Count -ne 1) {
    throw "Existing scheduler evidence must contain exactly one HTTP 200 in both log streams."
}

$result = [ordered]@{
    schema_version = "single-scheduler-run.v1"
    request_body_sha256 = $requestSha256
    scheduler_http_status = 200
    uvicorn_http_status = 200
    paused_after_evidence = $true
    recovery_run = $true
    collected_from_existing_run = $true
}
if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
    $existing = Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json
    if (
        $existing.schema_version -ne $result.schema_version -or
        $existing.request_body_sha256 -ne $result.request_body_sha256 -or
        [int]$existing.scheduler_http_status -ne 200 -or
        [int]$existing.uvicorn_http_status -ne 200 -or
        -not [bool]$existing.paused_after_evidence
    ) {
        throw "Existing scheduler result conflicts with the verified log evidence."
    }
}
else {
    Write-JsonAtomic -Path $resultPath -Value $result
}
Write-Output ($result | ConvertTo-Json -Compress)
