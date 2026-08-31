[CmdletBinding()]
param(
    [ValidateSet('Fixture', 'Live', 'Status', 'Cleanup')]
    [string]$Mode = 'Fixture',
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [string]$ConfigPath = '.env',
    [switch]$EnableAutomaticWatch,
    [ValidatePattern('^[A-Za-z0-9._-]{1,128}$')]
    [string]$SchedulerJobName = 'astra-automation-cycle',
    [switch]$NoBrowser,
    [switch]$SkipDoctor,
    [ValidateRange(30, 300)]
    [int]$IamWaitSeconds = 180
)

<#
.SYNOPSIS
Run one bounded Astra rehearsal and restore its temporary state afterward.

.DESCRIPTION
Fixture is offline and visibly synthetic. Live starts the private read-only
dashboard with a temporary service-account-scoped Token Creator binding. The
optional -EnableAutomaticWatch switch resumes the existing Scheduler job only
for this session. Press Enter to stop; prior Scheduler state and temporary IAM
are restored. Cleanup handles the exact resources in the ignored session file.

No mode edits or manually reconciles Drive, calls a professional-decision API,
or submits, holds, releases, cancels, or otherwise controls CUPS.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $RepoRoot
$WorkDirectory = Join-Path $RepoRoot 'work\demo'
$StatePath = Join-Path $WorkDirectory 'rehearsal-session.json'
$FixtureModule = 'braille_errata_relay.presentation.screenshot_fixture'
$LiveModule = 'braille_errata_relay.presentation.app'

function Read-Config([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Configuration file not found: $Path"
    }
    $values = @{}
    foreach ($rawLine in @(Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        $pair = $line.Split('=', 2)
        if ($pair.Count -ne 2 -or $pair[0] -notmatch '^[A-Z][A-Z0-9_]*$') {
            throw 'The local configuration contains an invalid line.'
        }
        $values[$pair[0]] = $pair[1]
    }
    return $values
}

function Get-RequiredValue([hashtable]$Values, [string]$Name) {
    $value = [string]$Values[$Name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "The local configuration is missing $Name."
    }
    return $value.Trim()
}

function Read-SessionState {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "The rehearsal state file is malformed: $StatePath"
    }
}

function Write-SessionState([hashtable]$State) {
    New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
    $temporary = "$StatePath.part"
    [IO.File]::WriteAllText(
        $temporary,
        ($State | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $StatePath -Force
}

function Get-ProcessCommandLine([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
    if ($null -eq $process) { return $null }
    return [string]$process.CommandLine
}

function Test-TokenCreatorBinding([string]$ServiceAccount, [string]$Member) {
    $policy = gcloud iam service-accounts get-iam-policy $ServiceAccount --format=json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the demonstrator IAM policy.' }
    $bindings = if ($null -eq $policy.PSObject.Properties['bindings']) {
        @()
    } else {
        @($policy.bindings)
    }
    return @(
        $bindings | Where-Object {
            $_.role -eq 'roles/iam.serviceAccountTokenCreator' -and
            @($_.members) -contains $Member
        }
    ).Count -gt 0
}

function Stop-RecordedPresentation($State) {
    $processId = [int]$State.process_id
    $expectedModule = if ([string]$State.mode -eq 'Fixture') { $FixtureModule } else { $LiveModule }
    $recordedPort = [int]$State.port
    $candidateIds = [System.Collections.Generic.HashSet[int]]::new()
    if ($processId -gt 0) { [void]$candidateIds.Add($processId) }
    foreach ($listener in @(
            Get-NetTCPConnection -LocalPort $recordedPort -State Listen -ErrorAction SilentlyContinue
        )) {
        [void]$candidateIds.Add([int]$listener.OwningProcess)
    }
    foreach ($candidateId in $candidateIds) {
        $commandLine = Get-ProcessCommandLine -ProcessId $candidateId
        if (-not $commandLine) { continue }
        if ($commandLine -notlike "*$expectedModule*" -or $commandLine -notlike "*--port $recordedPort*") {
            throw "Refusing to stop PID $candidateId because it is not the recorded Astra presentation process."
        }
        Stop-Process -Id $candidateId -ErrorAction Stop
    }
}

function Restore-RecordedSchedulerState($State) {
    if ($State.mode -ne 'Live' -or $State.scheduler_changed -ne $true) { return }
    $jobName = [string]$State.scheduler_job_name
    $region = [string]$State.scheduler_region
    if ($jobName -notmatch '^[A-Za-z0-9._-]{1,128}$' -or $region -notmatch '^[a-z0-9-]{1,63}$') {
        throw 'Recorded Scheduler cleanup target is malformed.'
    }
    if ([string]$State.scheduler_original_state -eq 'PAUSED') {
        gcloud scheduler jobs pause $jobName --location=$region --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Automatic-watch Scheduler cleanup failed.' }
    }
}

function Remove-RecordedIamBinding($State) {
    if ($State.mode -ne 'Live' -or $State.iam_binding_created -ne $true) { return }
    $serviceAccount = [string]$State.demonstrator_service_account
    $member = [string]$State.iam_member
    if (
        $serviceAccount -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com$' -or
        $member -notmatch '^user:[^\s@]+@[^\s@]+\.[^\s@]+$'
    ) {
        throw 'Recorded IAM cleanup identities are malformed.'
    }
    if (Test-TokenCreatorBinding -ServiceAccount $serviceAccount -Member $member) {
        gcloud iam service-accounts remove-iam-policy-binding $serviceAccount `
            --member=$member --role='roles/iam.serviceAccountTokenCreator' `
            --condition=None --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Temporary demonstrator permission cleanup failed.' }
    }
    if (Test-TokenCreatorBinding -ServiceAccount $serviceAccount -Member $member) {
        throw 'Temporary demonstrator permission remains after cleanup.'
    }
}

function Invoke-Cleanup([switch]$Quiet) {
    $state = Read-SessionState
    if ($null -eq $state) {
        if (-not $Quiet) { Write-Output 'No recorded Astra rehearsal session requires cleanup.' }
        return
    }
    $failures = [System.Collections.Generic.List[string]]::new()
    try { Stop-RecordedPresentation -State $state } catch { $failures.Add($_.Exception.Message) }
    try { Restore-RecordedSchedulerState -State $state } catch { $failures.Add($_.Exception.Message) }
    try { Remove-RecordedIamBinding -State $state } catch { $failures.Add($_.Exception.Message) }
    if ($failures.Count -gt 0) { throw ($failures -join ' ') }
    Remove-Item -LiteralPath $StatePath -Force
    if (-not $Quiet) {
        Write-Output 'PASS: recorded presentation and temporary rehearsal access are cleaned up.'
    }
}

function Assert-NoActiveSession {
    if ($null -ne (Read-SessionState)) {
        throw "A rehearsal record exists. Run .\infra\demo\rehearse.ps1 -Mode Cleanup first."
    }
}

function Wait-ForUrl([string]$Url, [System.Diagnostics.Process]$Process) {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            if ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { return }
        }
        catch {
            if ($Process.HasExited) { throw 'The presentation process exited before becoming ready.' }
        }
    }
    throw "The presentation did not become ready at $Url."
}

function Start-FixtureSession {
    $uv = Get-Command uv -ErrorAction Stop
    $selectedPort = if ($Port -eq 0) { 8877 } else { $Port }
    if (Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $selectedPort is already in use."
    }
    New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
    $process = Start-Process -FilePath $uv.Source -ArgumentList @(
        'run', '--frozen', 'python', '-m', $FixtureModule, '--port', $selectedPort
    ) -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $WorkDirectory 'fixture.stdout.log') `
        -RedirectStandardError (Join-Path $WorkDirectory 'fixture.stderr.log')
    $state = @{
        schema_version = 'astra-rehearsal-session.v1'; mode = 'Fixture'
        process_id = $process.Id; port = $selectedPort
        started_at = [DateTimeOffset]::UtcNow.ToString('o')
        iam_binding_created = $false; scheduler_changed = $false
    }
    Write-SessionState -State $state
    $url = "http://127.0.0.1:$selectedPort/watch/quiet"
    Wait-ForUrl -Url $url -Process $process
    $listener = Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction Stop
    $state.process_id = [int]$listener.OwningProcess
    Write-SessionState -State $state
    if (-not $NoBrowser) { Start-Process -FilePath $url }
    Write-Output "READY: offline quiet view at $url"
    Write-Output "ALERT VIEW: http://127.0.0.1:$selectedPort/watch"
    Write-Output 'This mode is visibly synthetic and makes no live-system claim.'
}

function Start-LiveSession {
    $selectedPort = if ($Port -eq 0) { 8765 } else { $Port }
    if (Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $selectedPort is already in use."
    }
    $configCandidate = if ([IO.Path]::IsPathRooted($ConfigPath)) {
        $ConfigPath
    } else { Join-Path $RepoRoot $ConfigPath }
    $config = (Resolve-Path -LiteralPath $configCandidate).Path
    $values = Read-Config -Path $config
    $demonstrator = Get-RequiredValue -Values $values -Name 'DEMONSTRATOR_PRINCIPAL_EMAIL'
    $audience = Get-RequiredValue -Values $values -Name 'RELAY_API_AUDIENCE'
    $region = if ($values.ContainsKey('CLOUD_RUN_REGION')) { [string]$values['CLOUD_RUN_REGION'] } else { 'europe-west3' }
    if ($demonstrator -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com$') {
        throw 'DEMONSTRATOR_PRINCIPAL_EMAIL is malformed.'
    }
    if ($region -notmatch '^[a-z0-9-]{1,63}$') { throw 'CLOUD_RUN_REGION is malformed.' }
    $activeAccount = (gcloud config get-value account 2>$null).Trim()
    if ($activeAccount -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw 'No human gcloud account is active.' }
    $member = "user:$activeAccount"
    if (Test-TokenCreatorBinding -ServiceAccount $demonstrator -Member $member) {
        throw 'Refusing to reuse a pre-existing Token Creator binding.'
    }
    $schedulerState = 'NOT_REQUESTED'
    if ($EnableAutomaticWatch) {
        $schedulerState = (gcloud scheduler jobs describe $SchedulerJobName `
            --location=$region --format='value(state)').Trim()
        if ($LASTEXITCODE -ne 0 -or $schedulerState -notin @('ENABLED', 'PAUSED')) {
            throw 'The configured automatic-watch Scheduler job is unavailable.'
        }
    }
    $state = @{
        schema_version = 'astra-rehearsal-session.v1'; mode = 'Live'
        process_id = 0; port = $selectedPort
        started_at = [DateTimeOffset]::UtcNow.ToString('o')
        demonstrator_service_account = $demonstrator; iam_member = $member
        iam_binding_created = $false; scheduler_job_name = $SchedulerJobName
        scheduler_region = $region; scheduler_original_state = $schedulerState
        scheduler_changed = $false
    }
    Write-SessionState -State $state
    gcloud iam service-accounts add-iam-policy-binding $demonstrator `
        --member=$member --role='roles/iam.serviceAccountTokenCreator' `
        --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Temporary demonstrator permission could not be created.' }
    $state.iam_binding_created = $true
    Write-SessionState -State $state

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($IamWaitSeconds)
    $tokenReady = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $token = gcloud auth print-identity-token `
            --impersonate-service-account=$demonstrator --audiences=$audience 2>$null
        $tokenExit = $LASTEXITCODE
        $ErrorActionPreference = $oldPreference
        if ($tokenExit -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$token)) {
            $tokenReady = $true; $token = $null; break
        }
        $token = $null
        Start-Sleep -Seconds 5
    }
    if (-not $tokenReady) { throw 'Temporary dashboard access did not propagate in time.' }

    if ($EnableAutomaticWatch -and $schedulerState -eq 'PAUSED') {
        gcloud scheduler jobs resume $SchedulerJobName --location=$region --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The automatic-watch Scheduler could not be enabled.' }
        $state.scheduler_changed = $true
        Write-SessionState -State $state
    }

    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $PSScriptRoot 'start_demo.ps1'), '-Port', $selectedPort,
        '-ConfigPath', $config, '-NoBrowser'
    )
    if ($SkipDoctor) { $arguments += '-SkipDoctor' }
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw 'The existing live dashboard launcher failed.' }
    $listener = Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction Stop
    $state.process_id = [int]$listener.OwningProcess
    Write-SessionState -State $state
    $url = "http://127.0.0.1:$selectedPort/watch"
    if (-not $NoBrowser) { Start-Process -FilePath $url }
    Write-Output "READY: private read-only watch floor at $url"
    if ($EnableAutomaticWatch) {
        Write-Output 'AUTOMATIC WATCH: enabled for this bounded session; no manual reconciliation will run.'
    } else {
        Write-Output 'AUTOMATIC WATCH: unchanged. Add -EnableAutomaticWatch only for a source-edit take.'
    }
}

function Show-Status {
    $state = Read-SessionState
    if ($null -eq $state) {
        Write-Output 'SESSION: none recorded'
    } else {
        $running = -not [string]::IsNullOrWhiteSpace(
            (Get-ProcessCommandLine -ProcessId ([int]$state.process_id))
        )
        Write-Output "SESSION: $($state.mode) $(if ($running) { 'RUNNING' } else { 'NOT_RUNNING' }) on port $($state.port)"
        Write-Output "STATE: $StatePath"
        if ($state.mode -eq 'Live') {
            $jobName = [string]$state.scheduler_job_name
            $region = [string]$state.scheduler_region
            $scheduler = (gcloud scheduler jobs describe $jobName `
                --location=$region --format='value(state)' 2>$null).Trim()
            Write-Output "AUTOMATIC WATCH: $scheduler"
            $binding = Test-TokenCreatorBinding `
                -ServiceAccount ([string]$state.demonstrator_service_account) `
                -Member ([string]$state.iam_member)
            Write-Output "TEMPORARY DASHBOARD ACCESS: $(if ($binding) { 'PRESENT' } else { 'ABSENT' })"
        }
    }
    Write-Output 'AUTHORITY: presentation only; no Drive edit, manual reconcile, human decision, or CUPS control.'
}

if ($Mode -eq 'Cleanup') { Invoke-Cleanup; exit 0 }
if ($Mode -eq 'Status') { Show-Status; exit 0 }

Assert-NoActiveSession
$sessionStarted = $false
try {
    if ($Mode -eq 'Fixture') { Start-FixtureSession } else { Start-LiveSession }
    $sessionStarted = $true
    Write-Output ''
    Write-Output 'Rehearsal ready. Press Enter here when finished; cleanup is automatic.'
    [void](Read-Host)
}
finally {
    Invoke-Cleanup -Quiet
    if ($sessionStarted) {
        Write-Output 'PASS: rehearsal ended with its recorded temporary state restored.'
    }
}
