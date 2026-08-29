[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$JobName = "relay-outbox-drain",

    [ValidateNotNullOrEmpty()]
    [string]$Location = "europe-west3",

    [AllowEmptyString()]
    [string]$RepoRoot = "",

    [ValidateRange(1, 12)]
    [int]$MaximumLogAttempts = 12,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 5,

    [ValidateRange(15, 90)]
    [int]$MinimumQuietWindowSeconds = 45,

    [switch]$ExecuteSingleRun,

    [ValidateSet("", "RECOVER-FAILED-HTTP-500")]
    [string]$RecoveryAuthorization = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ExecuteSingleRun) {
    throw "Refusing to trigger a scheduler execution without -ExecuteSingleRun."
}
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
    $temporary = Join-Path $directory ("." + [IO.Path]::GetFileName($destination) + "." +
        [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $json = $Value | ConvertTo-Json -Depth 8 -Compress
        [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.Encoding]::UTF8)
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-SecondsUntilNextDemoSchedule {
    param([Parameter(Mandatory)][datetime]$Now)

    $minute = Get-Date -Date $Now -Second 0 -Millisecond 0
    $minutesToAdd = if (($minute.Minute % 2) -eq 0) { 2 } else { 1 }
    $next = $minute.AddMinutes($minutesToAdd)
    return [math]::Floor(($next - $Now).TotalSeconds)
}

$repoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
$requestPath = Join-Path $repoRootPath "config\scheduler\outbox-drain-request.v1.json"
if (-not (Test-Path -LiteralPath $requestPath -PathType Leaf)) {
    throw "Canonical scheduler request body is missing."
}
$request = Get-Content -Raw -LiteralPath $requestPath | ConvertFrom-Json
if ($request.schema_version -ne "outbox-drain-request.v1" -or $request.limit -ne 10) {
    throw "Canonical scheduler request body is invalid."
}

$job = & gcloud.cmd scheduler jobs describe $JobName --location=$Location --format=json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the scheduler state."
}
$state = [string]$job.state
if ($state -ne "PAUSED") {
    throw "Refusing one-shot execution because the scheduler is not PAUSED."
}
if ($job.schedule -ne "*/2 * * * *" -or $job.timeZone -ne "Etc/UTC") {
    throw "Refusing one-shot execution because the fixed demo schedule changed."
}

$requestSha256 = Get-Sha256File -Path $requestPath
$now = (Get-Date).ToUniversalTime()
$secondsUntilNextSchedule = Get-SecondsUntilNextDemoSchedule -Now $now
if ($secondsUntilNextSchedule -lt $MinimumQuietWindowSeconds) {
    throw "Refusing one-shot execution too close to the next recurring schedule boundary."
}
$closureWorkRoot = Join-Path $repoRootPath "work\live-closure"
$attemptPath = Join-Path $closureWorkRoot "single-scheduler-run-attempt.json"
$recoveryAttemptPath = Join-Path $closureWorkRoot "single-scheduler-recovery-attempt.json"
$resultPath = Join-Path $closureWorkRoot "single-scheduler-run.json"
$priorAttemptPresent = Test-Path -LiteralPath $attemptPath -PathType Leaf
$recoveryRun = $false
if ($priorAttemptPresent) {
    if ($RecoveryAuthorization -ne "RECOVER-FAILED-HTTP-500") {
        throw "A prior one-shot scheduler attempt is recorded; refusing an unreviewed second execution."
    }
    if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
        throw "A successful one-shot scheduler result already exists; refusing recovery."
    }
    if (Test-Path -LiteralPath $recoveryAttemptPath -PathType Leaf) {
        throw "A scheduler recovery attempt is already recorded; refusing another execution."
    }
    $priorAttempt = Get-Content -Raw -LiteralPath $attemptPath | ConvertFrom-Json
    if (
        $priorAttempt.schema_version -ne "single-scheduler-run-attempt.v1" -or
        $priorAttempt.request_body_sha256 -ne $requestSha256
    ) {
        throw "The prior scheduler attempt marker does not match the canonical request."
    }
    $attemptPath = $recoveryAttemptPath
    $recoveryRun = $true
}
elseif ($RecoveryAuthorization) {
    throw "Recovery authorization was supplied without a prior failed attempt marker."
}
$startedAt = $now.AddSeconds(-5).ToString("yyyy-MM-ddTHH:mm:ssZ")
$runError = $null
$pauseError = $null
$schedulerHttpStatus = $null
$uvicornHttpStatus = $null

try {
    & gcloud.cmd scheduler jobs resume $JobName --location=$Location --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Scheduler could not be temporarily resumed for its one authorized run."
    }
    Write-JsonAtomic -Path $attemptPath -Value ([ordered]@{
        schema_version = if ($recoveryRun) {
            "single-scheduler-recovery-attempt.v1"
        }
        else {
            "single-scheduler-run-attempt.v1"
        }
        request_body_sha256 = $requestSha256
    })
    & gcloud.cmd scheduler jobs run $JobName --location=$Location --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Scheduler did not accept the one-shot execution."
    }

    # Return to PAUSED before polling logs. This leaves only the explicit
    # on-demand invocation in the authorized window, never continuous delivery.
    & gcloud.cmd scheduler jobs pause $JobName --location=$Location --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Could not immediately return the scheduler to PAUSED."
    }

    $schedulerFilter = (
        'resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"' + $JobName + '\" AND ' +
        'timestamp>=\"' + $startedAt + '\"'
    )
    $uvicornFilter = (
        'resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"braille-errata-relay\" AND ' +
        'textPayload:\"/internal/outbox-drain\" AND timestamp>=\"' + $startedAt + '\"'
    )
    for ($attempt = 1; $attempt -le $MaximumLogAttempts; $attempt++) {
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
        if ($schedulerSuccess.Count -gt 0 -and $uvicornSuccess.Count -gt 0) {
            if ($schedulerSuccess.Count -ne 1 -or $uvicornSuccess.Count -ne 1) {
                throw "One-shot scheduler evidence contains more than one successful execution."
            }
            $schedulerHttpStatus = 200
            $uvicornHttpStatus = 200
            break
        }
        Start-Sleep -Seconds $RetryDelaySeconds
    }
    if ($schedulerHttpStatus -ne 200 -or $uvicornHttpStatus -ne 200) {
        throw "The one-shot scheduler execution did not produce fresh HTTP 200 evidence in both logs."
    }
}
catch {
    $runError = $_
}
finally {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud.cmd scheduler jobs pause $JobName --location=$Location --quiet *> $null
        if ($LASTEXITCODE -ne 0) {
            $pauseError = "Could not return the scheduler to PAUSED."
        }
        else {
            $pausedState = ([string](& gcloud.cmd scheduler jobs describe $JobName --location=$Location `
                --format="value(state)")).Trim()
            if ($LASTEXITCODE -ne 0 -or $pausedState -ne "PAUSED") {
                $pauseError = "Scheduler state is not PAUSED after one-shot evidence collection."
            }
        }
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if ($null -ne $runError) {
    throw $runError
}
if ($null -ne $pauseError) {
    throw $pauseError
}

$result = [ordered]@{
    schema_version = "single-scheduler-run.v1"
    request_body_sha256 = $requestSha256
    scheduler_http_status = $schedulerHttpStatus
    uvicorn_http_status = $uvicornHttpStatus
    paused_after_evidence = $true
    recovery_run = $recoveryRun
}
Write-JsonAtomic -Path $resultPath -Value $result
Write-Output ($result | ConvertTo-Json -Compress)
