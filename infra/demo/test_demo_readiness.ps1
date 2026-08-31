[CmdletBinding()]
param(
    [string]$ConfigPath = '.env',
    [string]$ServiceName = 'braille-errata-relay',
    [string]$AutomationJobName = 'astra-automation-cycle',
    [string]$MonitorStatusPath = ''
)

<#!
.SYNOPSIS
Runs a bounded, read-only readiness check before a live Astra recording.

.DESCRIPTION
This helper does not enable or run Scheduler, edit Drive, write Firestore,
publish telemetry, change IAM, or contact CUPS.  It reports whether locally
configured prerequisites, the private service configuration, and (when a
status path is supplied) the human-armed read-only observation session are
ready.  A result is intentionally a readiness aid, not proof that a future
Drive edit will produce a particular incident.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NonSecretEnvironment {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'A generated non-secret local configuration file is required.'
    }
    $values = @{}
    foreach ($rawLine in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        $pair = $line.Split('=', 2)
        if ($pair.Count -ne 2 -or $pair[0] -notmatch '^[A-Z][A-Z0-9_]*$') {
            throw 'The local configuration contains an invalid key.'
        }
        $values[$pair[0]] = $pair[1]
    }
    return $values
}

function Add-Check {
    param(
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[object]]$Checks,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Detail
    )
    $Checks.Add([ordered]@{ name = $Name; status = $Status; detail = $Detail })
}

function Get-StatusRecord {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw 'The supplied monitor status record is malformed.' }
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
$region = [string]$environment['CLOUD_RUN_REGION']
$project = [string]$environment['GOOGLE_CLOUD_PROJECT']
if ([string]::IsNullOrWhiteSpace($region) -or [string]::IsNullOrWhiteSpace($project)) {
    throw 'CLOUD_RUN_REGION and GOOGLE_CLOUD_PROJECT must be present in the local configuration.'
}
if ($ServiceName -notmatch '^[a-z0-9-]{1,63}$' -or $AutomationJobName -notmatch '^[A-Za-z0-9_-]{1,128}$') {
    throw 'Service and Scheduler names contain unsupported characters.'
}

$checks = [System.Collections.Generic.List[object]]::new()
$uv = Get-Command uv -ErrorAction Stop
$gcloud = Get-Command gcloud.cmd -ErrorAction Stop

$doctorOutput = @(& $uv.Source run --frozen braille-relay doctor --config $config --check-drive --check-wsl-cups)
$doctorExit = $LASTEXITCODE
try { $doctor = ([string]$doctorOutput[-1]) | ConvertFrom-Json -ErrorAction Stop }
catch { throw 'The non-mutating local doctor returned no valid sanitized report.' }
foreach ($check in @($doctor.checks)) {
    Add-Check -Checks $checks -Name ("local_" + [string]$check.name) -Status ([string]$check.status) -Detail ([string]$check.detail)
}
if ($doctorExit -ne 0) {
    Add-Check -Checks $checks -Name 'local_doctor' -Status 'BLOCKED' -Detail 'One or more non-mutating local prerequisites are blocked.'
}

try {
    $service = & $gcloud.Source run services describe $ServiceName --project $project --region $region --format=json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $service.status.url) { throw 'unavailable' }
    $ready = @($service.status.conditions | Where-Object { $_.type -eq 'Ready' -and $_.status -eq 'True' }).Count -eq 1
    Add-Check -Checks $checks -Name 'private_cloud_run' -Status $(if ($ready) { 'PASS' } else { 'BLOCKED' }) `
        -Detail $(if ($ready) { 'private service reports Ready' } else { 'private service is not Ready' })
}
catch {
    Add-Check -Checks $checks -Name 'private_cloud_run' -Status 'BLOCKED' -Detail 'private service configuration could not be read'
}

try {
    $scheduler = & $gcloud.Source scheduler jobs describe $AutomationJobName --project $project --location $region --format=json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'unavailable' }
    $state = [string]$scheduler.state
    Add-Check -Checks $checks -Name 'automatic_drive_scheduler' -Status $(if ($state -eq 'ENABLED') { 'PASS' } else { 'BLOCKED' }) `
        -Detail $(if ($state -eq 'ENABLED') { 'configured automatic reconciliation is enabled' } else { 'automatic reconciliation is not enabled' })
}
catch {
    Add-Check -Checks $checks -Name 'automatic_drive_scheduler' -Status 'BLOCKED' -Detail 'Scheduler configuration could not be read'
}

if ($MonitorStatusPath) {
    $statusCandidate = if ([IO.Path]::IsPathRooted($MonitorStatusPath)) {
        $MonitorStatusPath
    }
    else {
        Join-Path $repoRoot $MonitorStatusPath
    }
    $statusPath = (Resolve-Path -LiteralPath $statusCandidate -ErrorAction Stop).Path
    $monitor = Get-StatusRecord -Path $statusPath
    $fresh = $false
    if ($null -ne $monitor -and $monitor.last_observed_at) {
        try {
            $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse([string]$monitor.last_observed_at)).TotalSeconds
            $fresh = $age -ge 0 -and $age -le 15
        }
        catch { $fresh = $false }
    }
    $state = if ($null -eq $monitor) { 'MISSING' } else { [string]$monitor.status }
    Add-Check -Checks $checks -Name 'fresh_read_only_observation' -Status $(if ($fresh) { 'PASS' } else { 'BLOCKED' }) `
        -Detail $(if ($fresh) { 'human-armed read-only observation is at most 15 seconds old' } else { "observation is not currently fresh (status $state)" })
}
else {
    Add-Check -Checks $checks -Name 'fresh_read_only_observation' -Status 'OPTIONAL' `
        -Detail 'supply the active session status path to check the 15-second freshness gate'
}

$blocked = @($checks | Where-Object { $_.status -eq 'BLOCKED' }).Count -gt 0
[ordered]@{
    schema_version = 'demo-readiness.v1'
    status = if ($blocked) { 'BLOCKED' } else { 'PASS' }
    checks = $checks
} | ConvertTo-Json -Depth 5
if ($blocked) { exit 1 }
