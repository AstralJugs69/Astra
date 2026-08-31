[CmdletBinding()]
param(
    [string]$BaselineId = "",

    [int]$SchedulerJobId = 0,

    [string]$ExpectedJobTitle = "",

    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "braille-errata-relay",

    [ValidateNotNullOrEmpty()]
    [string]$Region = "europe-west3",

    [AllowEmptyString()]
    [string]$RepoRoot = "",

    [ValidateRange(1, 30)]
    [int]$MaximumTokenAttempts = 24,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 10,

    [Security.SecureString]$ObserverPassword,

    [switch]$ArchiveUnpublishedLocalJournal,

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
if ($ValidateOnly) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "pyproject.toml"))) {
        throw "Resolved repository root is not a Relay checkout."
    }
    Write-Output "PASS: repository root resolved for live-link harness"
    return
}
if ($BaselineId -notmatch "^[0-9a-f]{64}$") {
    throw "BaselineId must be a lowercase SHA-256 value."
}
if ($SchedulerJobId -lt 1) {
    throw "SchedulerJobId must be positive."
}
if ($ExpectedJobTitle -notmatch "^BER\|[A-Za-z0-9._-]+\|[0-9a-f]{12}\|BASELINE$") {
    throw "ExpectedJobTitle must be the canonical demo baseline title."
}

function Assert-SafeWslArgument {
    param(
        [Parameter(Mandatory)]
        [string]$Value,

        [Parameter(Mandatory)]
        [string]$Label
    )

    if ($Value -notmatch "^[A-Za-z0-9._|:-]+$") {
        throw "$Label contains characters that are unsafe for the fixed WSL bridge command."
    }
}

function Invoke-GcloudQuietly {
    param([Parameter(Mandatory)][scriptblock]$Operation)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Operation *> $null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Invoke-WslWithSecureInput {
    param(
        [Parameter(Mandatory)][Security.SecureString]$Secret,
        [Parameter(Mandatory)][string]$Command
    )

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $plainText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        return @($plainText | & wsl.exe -e bash -lc $Command)
    }
    finally {
        $plainText = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function New-AudienceToken {
    param(
        [Parameter(Mandatory)][string]$Identity,
        [Parameter(Mandatory)][string]$Audience
    )

    for ($attempt = 1; $attempt -le $MaximumTokenAttempts; $attempt++) {
        Start-Sleep -Seconds $RetryDelaySeconds
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $candidate = & gcloud.cmd auth print-identity-token `
                --impersonate-service-account=$Identity `
                --audiences=$Audience `
                --include-email 2>$null
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($exitCode -eq 0 -and $candidate) {
            return ([string]$candidate).Trim()
        }
    }
    throw "Audience-bound identity token generation did not become available."
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$Value)

    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($bytes)
    }
    finally {
        $algorithm.Dispose()
    }
    return (-join ($hash | ForEach-Object { $_.ToString("x2") }))
}

function Get-IamPolicyBindings {
    param([Parameter(Mandatory)][object]$Policy)

    # Google IAM omits the bindings property altogether for an empty policy.
    # Under StrictMode that represents the desired post-cleanup state, not an
    # error, so normalize it to an empty collection before inspection.
    $bindingsProperty = $Policy.PSObject.Properties["bindings"]
    if ($null -eq $bindingsProperty -or $null -eq $bindingsProperty.Value) {
        return @()
    }
    return @($bindingsProperty.Value)
}

function Test-TokenCreatorGrantPresent {
    param(
        [Parameter(Mandatory)][string]$Identity,
        [Parameter(Mandatory)][string]$Member
    )

    $policy = & gcloud.cmd iam service-accounts get-iam-policy $Identity --format=json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the service-account IAM policy."
    }
    foreach ($binding in @(Get-IamPolicyBindings -Policy $policy)) {
        if (
            $binding.role -eq "roles/iam.serviceAccountTokenCreator" -and
            @($binding.members) -contains $Member
        ) {
            return $true
        }
    }
    return $false
}

$service = gcloud run services describe $ServiceName --region=$Region --format=json |
    ConvertFrom-Json
$environment = @{}
foreach ($entry in $service.spec.template.spec.containers[0].env) {
    $environment[$entry.name] = $entry.value
}
$serviceUrl = [string]$service.status.url
$audience = [string]$environment["INTERNAL_OIDC_AUDIENCE"]
$telemetryIdentity = [string]$environment["INTERNAL_TELEMETRY_PUSH_PRINCIPAL_EMAIL"]
$demonstratorIdentity = [string]$environment["DEMONSTRATOR_PRINCIPAL_EMAIL"]
$siteId = [string]$environment["RELAY_SITE_ID"]
$bridgeId = [string]$environment["RELAY_BRIDGE_ID"]
$queueName = [string]$environment["RELAY_CUPS_QUEUE_NAME"]
$activeAccount = (gcloud config get-value account 2>$null).Trim()
if (
    -not $serviceUrl -or -not $audience -or -not $telemetryIdentity -or
    -not $demonstratorIdentity -or -not $siteId -or -not $bridgeId -or
    -not $queueName -or -not $activeAccount
) {
    throw "Live baseline-link configuration is incomplete."
}
foreach ($value in @($siteId, $bridgeId, $queueName, $ExpectedJobTitle)) {
    Assert-SafeWslArgument -Value $value -Label "Bridge argument"
}

$member = if ($activeAccount.EndsWith(".gserviceaccount.com")) {
    "serviceAccount:$activeAccount"
}
else {
    "user:$activeAccount"
}
$repoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
$wslRepoRoot = ([string](& wsl.exe -e wslpath -a $repoRootPath)).Trim()
if (-not $wslRepoRoot.StartsWith("/")) {
    throw "Unable to resolve the repository into a WSL path."
}
$workRoot = Join-Path $repoRootPath "work\live-bridge"
$observationPath = Join-Path $workRoot "site-observation.json"
$journalPath = "work/live-bridge/journal.sqlite3"
$observationPathWsl = "work/live-bridge/site-observation.json"
if ($ArchiveUnpublishedLocalJournal) {
    $journalFile = Join-Path $workRoot "journal.sqlite3"
    if (-not (Test-Path -LiteralPath $journalFile -PathType Leaf)) {
        throw "There is no local observation journal to archive."
    }
    $archiveRoot = Join-Path $repoRootPath "work\live-bridge-unpublished-archive"
    $archiveName = "journal-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $archivePath = Join-Path $archiveRoot $archiveName
    if (Test-Path -LiteralPath $archivePath) {
        throw "A local observation journal archive already exists for this recovery attempt."
    }
    New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
    # This recovery is explicit and preserves the failed local evidence. Use it
    # only after confirming the prior attempt never reached telemetry admission.
    Move-Item -LiteralPath $workRoot -Destination $archivePath
    if ((Test-Path -LiteralPath $workRoot) -or -not (Test-Path -LiteralPath $archivePath)) {
        throw "Could not preserve the unadmitted local observation journal."
    }
    Write-Output "PASS: archived the explicitly unadmitted local observation journal"
}
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null

function Publish-PendingBridgeObservations {
    param([Parameter(Mandatory)][string]$Token)

    $pendingCommand = "cd '$wslRepoRoot' && PYTHONPATH=local_bridge/src python3 -m " +
        "relay_bridge.main pending-outbox --journal '$journalPath'"
    $pendingOutput = & wsl.exe -e bash -lc $pendingCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the durable local observation outbox."
    }
    $pending = ($pendingOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($pending.schema_version -ne "bridge-pending-observations.v1") {
        throw "Local observation outbox has an unexpected schema version."
    }

    $published = 0
    $entries = @($pending.observations | Where-Object { $null -ne $_ })
    foreach ($entry in $entries) {
        $observationId = [string]$entry.observation_id
        if ($observationId -notmatch "^[0-9a-f]{64}$") {
            throw "Local observation outbox contains an invalid observation ID."
        }
        $payloadJson = $entry.payload | ConvertTo-Json -Depth 12 -Compress
        $telemetryResult = Invoke-RestMethod `
            -Uri ($serviceUrl.TrimEnd("/") + "/internal/site-observations") `
            -Method Post `
            -Headers @{Authorization = "Bearer $Token"} `
            -ContentType "application/json" `
            -Body $payloadJson `
            -TimeoutSec 60
        if ($telemetryResult.status -ne "ACCEPTED") {
            throw "Telemetry admission did not accept a durable local observation."
        }
        $acknowledgementOutput = & wsl.exe -e bash -lc (
            "cd '$wslRepoRoot' && PYTHONPATH=local_bridge/src python3 -m relay_bridge.main " +
            "acknowledge-published --journal '$journalPath' --observation-id '$observationId'"
        )
        if ($LASTEXITCODE -ne 0) {
            throw "Local observation outbox acknowledgement failed."
        }
        if (-not $acknowledgementOutput) {
            throw "Local observation outbox acknowledgement produced no receipt."
        }
        $published++
    }
    return $published
}

$telemetryToken = $null
$demonstratorToken = $null
$grantedIdentities = [System.Collections.Generic.List[string]]::new()
try {
    foreach ($identity in @($telemetryIdentity, $demonstratorIdentity)) {
        if (Test-TokenCreatorGrantPresent -Identity $identity -Member $member) {
            throw "Refusing to reuse a pre-existing Token Creator grant."
        }
        $grantExit = Invoke-GcloudQuietly {
            gcloud iam service-accounts add-iam-policy-binding $identity `
                --member=$member `
                --role=roles/iam.serviceAccountTokenCreator `
                --condition=None `
                --quiet
        }
        if ($grantExit -ne 0) {
            throw "Narrow token impersonation grant failed."
        }
        $grantedIdentities.Add($identity)
    }
    $telemetryToken = New-AudienceToken -Identity $telemetryIdentity -Audience $audience
    $demonstratorToken = New-AudienceToken -Identity $demonstratorIdentity -Audience $audience

    # Preserve and publish any prior durable observation before taking a new
    # snapshot, so a retry cannot create a broken cloud hash chain.
    [void](Publish-PendingBridgeObservations -Token $telemetryToken)

    # This invokes only the bridge's pycups Get operations. When a SecureString was
    # supplied by the human caller it travels over stdin, never in argv or logs.
    # It never changes CUPS job state.
    $bridgeCommand = "cd '$wslRepoRoot' && PYTHONPATH=local_bridge/src python3 -m " +
        "relay_bridge.main observe-once --server localhost:631 --queue '$queueName' " +
        "--site-id '$siteId' --bridge-id '$bridgeId' --journal '$journalPath' " +
        "--require-job-id '$SchedulerJobId' --output '$observationPathWsl'"
    if ($null -ne $ObserverPassword) {
        $bridgeCommand += " --password-stdin"
        $bridgeOutput = Invoke-WslWithSecureInput -Secret $ObserverPassword -Command $bridgeCommand
    }
    else {
        $bridgeOutput = @(& wsl.exe -e bash -lc $bridgeCommand)
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Read-only bridge observation failed."
    }
    $bridgeOutput | Write-Output
    $observationJson = Get-Content -Raw -LiteralPath $observationPath
    $observation = $observationJson | ConvertFrom-Json
    if ($observation.schema_version -ne "site-observation.v1") {
        throw "Bridge output has an unexpected schema version."
    }
    $matchedJobs = @($observation.observations | Where-Object {
            $_.scheduler_job_id -eq $SchedulerJobId
        })
    if ($matchedJobs.Count -ne 1) {
        throw "The fresh read-only observation does not contain exactly one expected job."
    }
    $matchedJob = $matchedJobs[0]
    if ($matchedJob.destination -ne $queueName -or $matchedJob.title -ne $ExpectedJobTitle) {
        # This is deliberately limited to the fixed demo identity fields.  It makes
        # an operator-visible CUPS policy mismatch diagnosable without revealing an
        # originating user, host, document, or any other private job attribute.
        throw (
            "The fresh read-only observation does not match the expected queue or title. " +
            "Expected destination='$queueName' title='$ExpectedJobTitle'; " +
            "observed scheduler_job_id='$($matchedJob.scheduler_job_id)' " +
            "destination='$($matchedJob.destination)' title='$($matchedJob.title)' " +
            "state='$($matchedJob.scheduler_state)'."
        )
    }

    $published = Publish-PendingBridgeObservations -Token $telemetryToken
    if ($published -lt 1) {
        throw "The fresh local observation was not available for telemetry admission."
    }

    $idempotencyBody = '{"baseline_id":"' + $BaselineId +
        '","expected_state_version":0,"scheduler_job_id":' + $SchedulerJobId + '}'
    $linkBody = @{
        schema_version = "baseline-production-link-request.v1"
        scheduler_job_id = $SchedulerJobId
        expected_state_version = 0
        idempotency_key = Get-Sha256 -Value $idempotencyBody
    } | ConvertTo-Json -Compress
    $linkResult = Invoke-RestMethod `
        -Uri ($serviceUrl.TrimEnd("/") + "/api/v1/baselines/$BaselineId/production-links") `
        -Method Post `
        -Headers @{Authorization = "Bearer $demonstratorToken"} `
        -ContentType "application/json" `
        -Body $linkBody `
        -TimeoutSec 60
    if ($linkResult.status -ne "PROVISIONAL_PRODUCTION_LINK") {
        throw "Production-link admission did not preserve the advisory boundary."
    }
    Write-Output "PASS: fresh read-only observation accepted"
    Write-Output "PASS: local observation outbox acknowledged after cloud acceptance"
    Write-Output "PASS: advisory production link is provisional pending endpoint bytes"
    $linkResult | ConvertTo-Json -Depth 12 -Compress
}
finally {
    $telemetryToken = $null
    $demonstratorToken = $null
    foreach ($identity in $grantedIdentities) {
        $removeExit = Invoke-GcloudQuietly {
            gcloud iam service-accounts remove-iam-policy-binding $identity `
                --member=$member `
                --role=roles/iam.serviceAccountTokenCreator `
                --condition=None `
                --quiet
        }
        if ($removeExit -ne 0) {
            throw "Temporary token impersonation cleanup failed."
        }
    }
}

foreach ($identity in @($telemetryIdentity, $demonstratorIdentity)) {
    $policy = gcloud iam service-accounts get-iam-policy $identity --format=json | ConvertFrom-Json
    foreach ($binding in @(Get-IamPolicyBindings -Policy $policy)) {
        if (
            $binding.role -eq "roles/iam.serviceAccountTokenCreator" -and
            @($binding.members) -contains $member
        ) {
            throw "Temporary token impersonation authority remains."
        }
    }
}
Write-Output "PASS: temporary token impersonation authority removed"
