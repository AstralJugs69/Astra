[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "braille-errata-relay",

    [ValidateNotNullOrEmpty()]
    [string]$Region = "europe-west3",

    [ValidateSet("DEMONSTRATOR_PRINCIPAL_EMAIL", "INTERNAL_SCHEDULER_PRINCIPAL_EMAIL")]
    [string]$IdentityEnvironmentVariable = "DEMONSTRATOR_PRINCIPAL_EMAIL",

    [ValidateRange(1, 30)]
    [int]$MaximumTokenAttempts = 24,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 10,

    [switch]$VerifyEmptyOutboxReplay
)

if (
    $VerifyEmptyOutboxReplay -and
    $IdentityEnvironmentVariable -ne "INTERNAL_SCHEDULER_PRINCIPAL_EMAIL"
) {
    throw "Empty outbox replay requires the dedicated scheduler principal."
}

function Get-IamPolicyBindings {
    param([Parameter(Mandatory)][object]$Policy)

    # An empty service-account policy has no bindings property. That is a
    # successful post-cleanup condition, including under StrictMode.
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

$service = gcloud run services describe $ServiceName --region=$Region --format=json |
    ConvertFrom-Json
$environment = @{}
foreach ($entry in $service.spec.template.spec.containers[0].env) {
    $environment[$entry.name] = $entry.value
}
$audience = [string]$environment["INTERNAL_OIDC_AUDIENCE"]
$serviceUrl = [string]$service.status.url
$identity = [string]$environment[$IdentityEnvironmentVariable]
$activeAccount = (gcloud config get-value account 2>$null).Trim()
if (-not $audience -or -not $serviceUrl -or -not $identity -or -not $activeAccount) {
    throw "Private route identity configuration is incomplete."
}
$member = if ($activeAccount.EndsWith(".gserviceaccount.com")) {
    "serviceAccount:$activeAccount"
}
else {
    "user:$activeAccount"
}

$grantPresent = $false
$token = ""
$healthStatus = 0
$healthServerlessStatus = 0
$reservedHealthzStatus = 0
$readyStatus = 0
$ready = $false
$outboxReplayStatus = 0
$outboxReplayLeased = $null
$outboxReplayCompleted = $null
$outboxReplayRetried = $null
$outboxReplayMessageCount = $null
$outboxReplayNotificationStatus = $null
try {
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
    $grantPresent = $true
    for ($attempt = 1; $attempt -le $MaximumTokenAttempts -and -not $token; $attempt++) {
        Start-Sleep -Seconds $RetryDelaySeconds
        $candidate = & gcloud.cmd auth print-identity-token `
            --impersonate-service-account=$identity `
            --audiences=$audience `
            --include-email 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate) {
            $token = ([string]$candidate).Trim()
        }
    }
    if (-not $token) {
        throw "Audience-bound token generation did not become available."
    }
    $headers = @{Authorization = "Bearer $token"}
    try {
        $healthResponse = Invoke-WebRequest -UseBasicParsing `
            -Uri ($serviceUrl.TrimEnd("/") + "/health") `
            -Headers $headers `
            -Method Get
        $healthStatus = [int]$healthResponse.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            $healthStatus = [int]$_.Exception.Response.StatusCode
        }
    }
    if ($healthStatus -ne 200) {
        $serverlessHeaders = @{"X-Serverless-Authorization" = "Bearer $token"}
        try {
            $serverlessHealthResponse = Invoke-WebRequest -UseBasicParsing `
                -Uri ($serviceUrl.TrimEnd("/") + "/health") `
                -Headers $serverlessHeaders `
                -Method Get
            $healthServerlessStatus = [int]$serverlessHealthResponse.StatusCode
        }
        catch {
            if ($_.Exception.Response) {
                $healthServerlessStatus = [int]$_.Exception.Response.StatusCode
            }
        }
    }
    try {
        $reservedHealthzResponse = Invoke-WebRequest -UseBasicParsing `
            -Uri ($serviceUrl.TrimEnd("/") + "/healthz") `
            -Headers $headers `
            -Method Get
        $reservedHealthzStatus = [int]$reservedHealthzResponse.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            $reservedHealthzStatus = [int]$_.Exception.Response.StatusCode
        }
    }
    try {
        $readyResponse = Invoke-WebRequest -UseBasicParsing `
            -Uri ($serviceUrl.TrimEnd("/") + "/readyz") `
            -Headers $headers `
            -Method Get
        $readyStatus = [int]$readyResponse.StatusCode
        $ready = [bool](($readyResponse.Content | ConvertFrom-Json).ready)
    }
    catch {
        if ($_.Exception.Response) {
            $readyStatus = [int]$_.Exception.Response.StatusCode
        }
    }
    if ($VerifyEmptyOutboxReplay) {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $requestPath = Join-Path $repoRoot "config\scheduler\outbox-drain-request.v1.json"
        if (-not (Test-Path -LiteralPath $requestPath -PathType Leaf)) {
            throw "Canonical scheduler request body is missing."
        }
        try {
            $outboxResponse = Invoke-WebRequest -UseBasicParsing `
                -Uri ($serviceUrl.TrimEnd("/") + "/internal/outbox-drain") `
                -Headers $headers `
                -ContentType "application/json" `
                -Body ([IO.File]::ReadAllText($requestPath, [Text.Encoding]::UTF8)) `
                -Method Post
            $outboxReplayStatus = [int]$outboxResponse.StatusCode
            $outboxResult = $outboxResponse.Content | ConvertFrom-Json
            $outboxReplayLeased = [int]$outboxResult.leased
            $outboxReplayCompleted = [int]$outboxResult.completed
            $outboxReplayRetried = [int]$outboxResult.retried
            $outboxReplayMessageCount = @($outboxResult.message_ids).Count
            $outboxReplayNotificationStatus = [string]$outboxResult.notification_status
        }
        catch {
            if ($_.Exception.Response) {
                $outboxReplayStatus = [int]$_.Exception.Response.StatusCode
            }
        }
    }
}
finally {
    $token = $null
    if ($grantPresent) {
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

$policy = gcloud iam service-accounts get-iam-policy $identity --format=json |
    ConvertFrom-Json
$temporaryGrantRemaining = $false
foreach ($binding in @(Get-IamPolicyBindings -Policy $policy)) {
    if (
        $binding.role -eq "roles/iam.serviceAccountTokenCreator" -and
        @($binding.members) -contains $member
    ) {
        $temporaryGrantRemaining = $true
    }
}

Write-Output "health_status=$healthStatus"
Write-Output "health_serverless_status=$healthServerlessStatus"
Write-Output "reserved_healthz_status=$reservedHealthzStatus"
Write-Output "ready_status=$readyStatus"
Write-Output "ready=$ready"
if ($VerifyEmptyOutboxReplay) {
    Write-Output "outbox_replay_status=$outboxReplayStatus"
    Write-Output "outbox_replay_leased=$outboxReplayLeased"
    Write-Output "outbox_replay_completed=$outboxReplayCompleted"
    Write-Output "outbox_replay_retried=$outboxReplayRetried"
    Write-Output "outbox_replay_message_count=$outboxReplayMessageCount"
    Write-Output "outbox_replay_notification_status=$outboxReplayNotificationStatus"
}
Write-Output "temporary_token_creator_absent=$(-not $temporaryGrantRemaining)"

if (
    ($healthStatus -ne 200 -and $healthServerlessStatus -ne 200) -or
    $reservedHealthzStatus -notin @(200, 404) -or
    $readyStatus -ne 200 -or
    -not $ready -or
    (
        $VerifyEmptyOutboxReplay -and
        (
            $outboxReplayStatus -ne 200 -or
            $outboxReplayLeased -ne 0 -or
            $outboxReplayCompleted -ne 0 -or
            $outboxReplayRetried -ne 0 -or
            $outboxReplayMessageCount -ne 0 -or
            $outboxReplayNotificationStatus -ne "NOT_CLAIMED"
        )
    ) -or
    $temporaryGrantRemaining
) {
    exit 1
}
