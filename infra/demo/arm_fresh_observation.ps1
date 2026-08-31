[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$SchedulerJobId,

    [ValidatePattern('^[A-Za-z0-9._-]{1,80}$')]
    [string]$Distro = 'Ubuntu-24.04',

    [ValidateRange(1, 15)]
    [int]$IntervalSeconds = 5,

    [ValidateRange(30, 1800)]
    [int]$MaxRuntimeSeconds = 900,

    [string]$ConfigPath = '.env',

    [ValidatePattern('^[A-Za-z0-9._-]{0,80}$')]
    [string]$SessionId = '',

    [switch]$Arm,

    [switch]$PublisherWorker
)

<#!
.SYNOPSIS
Arms one bounded, exact-job, read-only CUPS observation session for a demo.

.DESCRIPTION
The foreground process is the CUPS read-only observer running as the fixed
relay-observer WSL identity.  A hidden companion process can only publish the
observer's already-canonical outbox entries to the private telemetry route and
acknowledge them after Cloud Run accepts the exact observation ID.

Neither process can submit, hold, release, cancel, restart, pause, or otherwise
operate a CUPS queue or endpoint.  This script never creates IAM grants,
changes Drive, or stores a password, service-account key, or identity token.
The caller must deliberately provide -Arm and must have separately prepared
the short-lived, scoped token-impersonation authority required by the existing
telemetry CLI.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ObserverUser = 'relay-observer'
$JournalRelativePath = 'work/live-bridge/journal.sqlite3'
$StatusSchema = 'demo-monitor-publisher-status.v1'

function Get-NonSecretEnvironment {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'A generated non-secret local configuration file is required.'
    }
    $values = @{}
    foreach ($rawLine in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }
        $pair = $line.Split('=', 2)
        if ($pair.Count -ne 2 -or $pair[0] -notmatch '^[A-Z][A-Z0-9_]*$') {
            throw 'The local configuration contains an invalid key.'
        }
        $values[$pair[0]] = $pair[1]
    }
    return $values
}

function Get-RequiredConfigValue {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $value = [string]$Values[$Name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "The local configuration is missing required $Name."
    }
    return $value.Trim()
}

function Assert-SafeIdentifier {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Value -notmatch '^[A-Za-z0-9._-]{1,128}$') {
        throw "$Label contains unsupported characters."
    }
}

function Assert-PrivateHttpsOrigin {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )

    try {
        $uri = [Uri]$Value
    }
    catch {
        throw "$Label is not a valid HTTPS origin."
    }
    if (
        $uri.Scheme -ne 'https' -or -not $uri.Host -or $uri.UserInfo -or
        $uri.Query -or $uri.Fragment -or ($uri.AbsolutePath -notin @('', '/'))
    ) {
        throw "$Label is not a credential-free HTTPS origin."
    }
}

function Read-JsonObject {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw 'A local monitor status record is malformed.'
    }
    return $value
}

function Write-PublisherStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Session,
        [int]$PublishedCount = 0,
        [string]$LastObservationId = '',
        [string]$LastObservedAt = '',
        [string]$ObserverStatus = '',
        [string]$BlockingReason = ''
    )

    $record = [ordered]@{
        schema_version = $StatusSchema
        status = $Status
        session_id = $Session
        published_count = $PublishedCount
    }
    if ($LastObservationId) { $record.last_observation_id = $LastObservationId }
    if ($LastObservedAt) { $record.last_observed_at = $LastObservedAt }
    if ($ObserverStatus) { $record.observer_status = $ObserverStatus }
    if ($BlockingReason) { $record.blocking_reason = $BlockingReason }
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = "$Path.part"
    [IO.File]::WriteAllText(
        $temporary,
        ($record | ConvertTo-Json -Compress),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$configCandidate = if ([IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
}
else {
    Join-Path $repoRoot $ConfigPath
}
$config = (Resolve-Path -LiteralPath $configCandidate).Path
$environment = Get-NonSecretEnvironment -Path $config
$serviceUrl = Get-RequiredConfigValue -Values $environment -Name 'RELAY_API_BASE_URL'
$audience = Get-RequiredConfigValue -Values $environment -Name 'RELAY_API_AUDIENCE'
$telemetryIdentity = Get-RequiredConfigValue -Values $environment -Name 'INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL'
$siteId = Get-RequiredConfigValue -Values $environment -Name 'SITE_ID'
$queueName = Get-RequiredConfigValue -Values $environment -Name 'QUEUE_NAME'
$bridgeId = Get-RequiredConfigValue -Values $environment -Name 'LOCAL_BRIDGE_ID'
Assert-PrivateHttpsOrigin -Value $serviceUrl -Label 'RELAY_API_BASE_URL'
Assert-PrivateHttpsOrigin -Value $audience -Label 'RELAY_API_AUDIENCE'
if ($telemetryIdentity -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com$') {
    throw 'INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL is malformed.'
}
foreach ($entry in @(
        @{ value = $siteId; label = 'SITE_ID' },
        @{ value = $queueName; label = 'QUEUE_NAME' },
        @{ value = $bridgeId; label = 'LOCAL_BRIDGE_ID' }
    )) {
    Assert-SafeIdentifier -Value ([string]$entry.value) -Label ([string]$entry.label)
}

$wsl = Get-Command wsl.exe -ErrorAction Stop
$uv = Get-Command uv -ErrorAction Stop
$wslRepoRoot = ([string](& $wsl.Source -d $Distro --exec wslpath -a $repoRoot)).Trim()
if ($LASTEXITCODE -ne 0 -or $wslRepoRoot -notmatch '^/') {
    throw 'The repository could not be resolved safely inside the selected WSL distribution.'
}

if (-not $SessionId) {
    $SessionId = 'demo-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ')
}
Assert-SafeIdentifier -Value $SessionId -Label 'Session ID'
$sessionDirectory = Join-Path (Join-Path $repoRoot 'work\live-bridge') ("demo-monitor-$SessionId")
$observerStatusPath = Join-Path $sessionDirectory 'observer-status.json'
$publisherStatusPath = Join-Path $sessionDirectory 'publisher-status.json'
$observerStatusRelative = "work/live-bridge/demo-monitor-$SessionId/observer-status.json"

function Invoke-Bridge {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $command = @(
        '-d', $Distro,
        '-u', $ObserverUser,
        '--cd', $wslRepoRoot,
        '--exec', 'env', 'PYTHONPATH=local_bridge/src',
        'python3', '-m', 'relay_bridge.main'
    ) + $Arguments
    $output = @(& $wsl.Source @command)
    if ($LASTEXITCODE -ne 0) {
        throw 'The read-only local bridge did not complete its requested operation.'
    }
    return $output
}

function Get-PendingBridgeObservations {
    $output = Invoke-Bridge -Arguments @('pending-outbox', '--journal', $JournalRelativePath)
    if ($output.Count -lt 1) {
        throw 'The local bridge returned no durable outbox record.'
    }
    try {
        $pending = ([string]$output[-1]) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw 'The local bridge returned an invalid durable outbox record.'
    }
    if ($pending.schema_version -ne 'bridge-pending-observations.v1' -or $null -eq $pending.observations) {
        throw 'The local bridge outbox has an unexpected schema.'
    }
    return @($pending.observations)
}

function Publish-PendingObservations {
    $published = 0
    $lastId = ''
    $lastObservedAt = ''
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($MaxRuntimeSeconds + 90)
    try {
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            $observer = Read-JsonObject -Path $observerStatusPath
            if ($null -eq $observer) {
                Start-Sleep -Seconds 1
                continue
            }
            $entries = Get-PendingBridgeObservations
            foreach ($entry in $entries) {
                $observationId = [string]$entry.observation_id
                if ($observationId -notmatch '^[0-9a-f]{64}$' -or $null -eq $entry.payload) {
                    throw 'The local bridge outbox contains an invalid observation identity.'
                }
                $temporaryObservation = Join-Path $sessionDirectory ("observation-$observationId.json")
                try {
                    $payloadJson = $entry.payload | ConvertTo-Json -Depth 16 -Compress
                    [IO.File]::WriteAllText(
                        $temporaryObservation,
                        $payloadJson,
                        [Text.UTF8Encoding]::new($false)
                    )
                    $publishOutput = @(& $uv.Source run --frozen braille-relay publish-site-observation `
                        --service-url $serviceUrl `
                        --audience $audience `
                        --impersonate-service-account $telemetryIdentity `
                        --observation $temporaryObservation 2>&1)
                    $publishExit = $LASTEXITCODE
                    try {
                        $publishResult = ([string]$publishOutput[-1]) | ConvertFrom-Json -ErrorAction Stop
                    }
                    catch {
                        throw 'Private telemetry publication produced no valid sanitized receipt.'
                    }
                    if ($publishExit -ne 0 -or $publishResult.status -ne 'ACCEPTED' -or $publishResult.observation_id -ne $observationId) {
                        throw 'Private telemetry admission did not accept the exact canonical observation.'
                    }
                    [void](Invoke-Bridge -Arguments @(
                            'acknowledge-published', '--journal', $JournalRelativePath,
                            '--observation-id', $observationId
                        ))
                    $published++
                    $lastId = $observationId
                    $lastObservedAt = [string]$entry.payload.observed_at
                }
                finally {
                    if (Test-Path -LiteralPath $temporaryObservation -PathType Leaf) {
                        [IO.File]::Delete($temporaryObservation)
                    }
                }
            }
            $observer = Read-JsonObject -Path $observerStatusPath
            $observerState = if ($null -eq $observer) { '' } else { [string]$observer.status }
            if ($observerState -eq 'BLOCKED') {
                Write-PublisherStatus -Path $publisherStatusPath -Status 'BLOCKED' -Session $SessionId `
                    -PublishedCount $published -LastObservationId $lastId -LastObservedAt $lastObservedAt `
                    -ObserverStatus $observerState -BlockingReason 'OBSERVER_BLOCKED'
                return 3
            }
            if ($observerState -in @('COMPLETED', 'STOPPED_BY_HUMAN')) {
                $remaining = Get-PendingBridgeObservations
                if ($remaining.Count -eq 0) {
                    $finalStatus = if ($observerState -eq 'COMPLETED') {
                        'PUBLISHED_AND_COMPLETED'
                    }
                    else {
                        'PUBLISHED_AND_STOPPED'
                    }
                    Write-PublisherStatus -Path $publisherStatusPath -Status $finalStatus -Session $SessionId `
                        -PublishedCount $published -LastObservationId $lastId -LastObservedAt $lastObservedAt `
                        -ObserverStatus $observerState
                    return 0
                }
            }
            Start-Sleep -Seconds 2
        }
    }
    catch {
        Write-PublisherStatus -Path $publisherStatusPath -Status 'BLOCKED' -Session $SessionId `
            -PublishedCount $published -LastObservationId $lastId -LastObservedAt $lastObservedAt `
            -BlockingReason 'TELEMETRY_ADMISSION_UNAVAILABLE'
        return 1
    }
    Write-PublisherStatus -Path $publisherStatusPath -Status 'BLOCKED' -Session $SessionId `
        -PublishedCount $published -LastObservationId $lastId -LastObservedAt $lastObservedAt `
        -BlockingReason 'MONITOR_RUNTIME_EXPIRED'
    return 1
}

if (-not $Arm) {
    throw 'Pass -Arm only after the human has independently prepared the exact CUPS job and temporary telemetry authorization.'
}

if ($PublisherWorker) {
    exit (Publish-PendingObservations)
}

if (Test-Path -LiteralPath $sessionDirectory) {
    throw 'The requested demo-monitor session already exists; preserve it and choose a new session ID.'
}
New-Item -ItemType Directory -Force -Path $sessionDirectory | Out-Null

$powershell = (Get-Process -Id $PID).Path
$workerArguments = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', $PSCommandPath,
    '-SchedulerJobId', $SchedulerJobId,
    '-Distro', $Distro,
    '-IntervalSeconds', $IntervalSeconds,
    '-MaxRuntimeSeconds', $MaxRuntimeSeconds,
    '-ConfigPath', $config,
    '-SessionId', $SessionId,
    '-Arm', '-PublisherWorker'
)
$publisher = Start-Process -FilePath $powershell -ArgumentList $workerArguments -WorkingDirectory $repoRoot `
    -WindowStyle Hidden -PassThru

Write-Output 'READY: read-only observation monitor is armed; enter the observer CUPS password only at the local prompt.'
$bridgeArguments = @(
    '-d', $Distro,
    '-u', $ObserverUser,
    '--cd', $wslRepoRoot,
    '--exec', 'env', 'PYTHONPATH=local_bridge/src',
    'python3', '-m', 'relay_bridge.main', 'observe-loop',
    '--server', 'localhost:631',
    '--queue', $queueName,
    '--site-id', $siteId,
    '--bridge-id', $bridgeId,
    '--journal', $JournalRelativePath,
    '--require-job-id', $SchedulerJobId,
    '--interval-seconds', $IntervalSeconds,
    '--max-runtime-seconds', $MaxRuntimeSeconds,
    '--status-path', $observerStatusRelative
)
& $wsl.Source @bridgeArguments
$observerExit = $LASTEXITCODE

$graceDeadline = [DateTimeOffset]::UtcNow.AddSeconds(100)
while (-not $publisher.HasExited -and [DateTimeOffset]::UtcNow -lt $graceDeadline) {
    Start-Sleep -Seconds 1
    $publisher.Refresh()
}
if (-not $publisher.HasExited) {
    throw 'The publisher has not drained the canonical outbox; inspect its sanitized status before another demo attempt.'
}
$publisherStatus = Read-JsonObject -Path $publisherStatusPath
if ($null -eq $publisherStatus) {
    throw 'The publisher did not produce a sanitized status record.'
}
if ($observerExit -notin @(0, 130) -or $publisher.ExitCode -ne 0) {
    throw 'The demo monitor stopped fail-closed; inspect the sanitized local status before editing Drive.'
}
Write-Output "PASS: canonical observations were admitted without manual per-snapshot publishing (session $SessionId)."
Write-Output "PASS: observer status $($publisherStatus.observer_status); publisher status $($publisherStatus.status)."
