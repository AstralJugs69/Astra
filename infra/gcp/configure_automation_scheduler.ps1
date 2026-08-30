[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ServiceUrl,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SchedulerServiceAccount,

    [ValidateNotNullOrEmpty()]
    [string]$JobName = "astra-automation-cycle",

    [ValidateNotNullOrEmpty()]
    [string]$Location = "europe-west3",

    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "braille-errata-relay",

    [ValidateNotNullOrEmpty()]
    [string]$Schedule = "* * * * *",

    [AllowEmptyString()]
    [string]$InitializationReceiptId = "",

    [switch]$EnableAutomaticWatch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$serviceUri = [Uri]$ServiceUrl.TrimEnd("/")
if ($serviceUri.Scheme -ne "https") {
    throw "The private Cloud Run service URL must use HTTPS."
}
if (
    $EnableAutomaticWatch -and
    $InitializationReceiptId -notmatch "^[0-9a-f]{64}$"
) {
    throw "Enabling automatic watch requires the SHA-256 receipt ID from the one-time INITIALIZE run."
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$messageBody = Join-Path $repositoryRoot "config\scheduler\automation-cycle-request.v1.json"
if (-not (Test-Path -LiteralPath $messageBody -PathType Leaf)) {
    throw "Canonical automation scheduler request body is missing."
}

$body = Get-Content -Raw -LiteralPath $messageBody -Encoding UTF8 | ConvertFrom-Json
if ($body.schema_version -ne "automation-cycle-request.v1" -or $body.outbox_limit -ne 1) {
    throw "Canonical automation scheduler request body is invalid."
}

# The deployed request URL and the OIDC audience can intentionally differ.
# Read the audience from the private service rather than silently assuming that
# the status URL is the token audience. The request target itself must be that
# exact deployed status URL: Scheduler attaches an OIDC bearer token, so an
# arbitrary HTTPS target would be an unacceptable token-exfiltration path.
$service = & gcloud.cmd run services describe $ServiceName --region $Location --format=json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the private Cloud Run service."
}
$deployedServiceUrl = [string]$service.status.url
try {
    $deployedServiceUri = [Uri]$deployedServiceUrl
}
catch {
    throw "The deployed private Cloud Run service has no valid status URL."
}
if ($deployedServiceUri.Scheme -ne "https") {
    throw "The deployed private Cloud Run service URL must use HTTPS."
}
$requestedServiceUrl = $serviceUri.AbsoluteUri.TrimEnd("/")
$canonicalServiceUrl = $deployedServiceUri.AbsoluteUri.TrimEnd("/")
if (-not [string]::Equals(
    $requestedServiceUrl,
    $canonicalServiceUrl,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "ServiceUrl must exactly match the deployed private Cloud Run status URL."
}
$target = $canonicalServiceUrl + "/internal/automation-cycle"
$environment = @{}
foreach ($entry in @($service.spec.template.spec.containers[0].env)) {
    $environment[[string]$entry.name] = [string]$entry.value
}
$audience = [string]$environment["INTERNAL_OIDC_AUDIENCE"]
if ([string]::IsNullOrWhiteSpace($audience)) {
    throw "The deployed service has no INTERNAL_OIDC_AUDIENCE."
}
try {
    $audienceUri = [Uri]$audience
}
catch {
    throw "The deployed INTERNAL_OIDC_AUDIENCE is invalid."
}
if ($audienceUri.Scheme -ne "https") {
    throw "The deployed INTERNAL_OIDC_AUDIENCE must use HTTPS."
}
$audience = $audienceUri.AbsoluteUri.TrimEnd("/")

function Test-SchedulerJobExists {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud.cmd scheduler jobs describe $JobName --location $Location --format="value(name)" *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if (-not $PSCmdlet.ShouldProcess(
    "$JobName in $Location",
    "configure private automatic Drive reconciliation"
)) {
    Write-Output "WHATIF: automatic scheduler configuration was validated but not changed"
    return
}

function Set-SchedulerJobConfiguration {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("create", "update")]
        [string]$Mode,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$EffectiveSchedule
    )

    $headerArgument = if ($Mode -eq "create") { "--headers" } else { "--update-headers" }
    $commonArguments = @(
        "scheduler", "jobs", $Mode, "http", $JobName,
        "--location", $Location,
        "--schedule", $EffectiveSchedule,
        "--time-zone", "Etc/UTC",
        "--uri", $target,
        "--http-method", "POST",
        "--oidc-service-account-email", $SchedulerServiceAccount,
        "--oidc-token-audience", $audience,
        $headerArgument, "Content-Type=application/json",
        "--message-body-from-file", $messageBody,
        "--attempt-deadline", "300s",
        "--max-retry-attempts", "3",
        "--min-backoff", "10s",
        "--max-backoff", "60s",
        "--max-doublings", "3",
        "--quiet"
    )
    & gcloud.cmd @commonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Cloud Scheduler $Mode failed."
    }
}

$created = $false
try {
    if (Test-SchedulerJobExists) {
        # Stop an existing enabled job before changing its target/body/schedule.
        # This avoids a one-tick window under partly updated configuration.
        & gcloud.cmd scheduler jobs pause $JobName --location $Location --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Cloud Scheduler could not be paused before configuration."
        }
        Set-SchedulerJobConfiguration -Mode "update" -EffectiveSchedule $Schedule
    }
    else {
        # Create under an inert annual schedule first, then update it below. This
        # prevents a newly created job from firing before its configuration is complete.
        Set-SchedulerJobConfiguration -Mode "create" -EffectiveSchedule "0 0 1 1 *"
        $created = $true
        & gcloud.cmd scheduler jobs pause $JobName --location $Location --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "New Cloud Scheduler job could not be paused before configuration."
        }
        Set-SchedulerJobConfiguration -Mode "update" -EffectiveSchedule $Schedule
    }

    # Background Drive access is explicitly opt-in. The route remains private and
    # this script neither changes IAM nor grants device or Drive write authority.
    if ($EnableAutomaticWatch) {
        & gcloud.cmd scheduler jobs resume $JobName --location $Location --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Cloud Scheduler was configured but could not be resumed."
        }
        Write-Output "PASS: automatic private Drive reconciliation is enabled"
    }
    else {
        & gcloud.cmd scheduler jobs pause $JobName --location $Location --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Cloud Scheduler was configured but could not be paused."
        }
        Write-Output "PASS: automatic scheduler is configured and remains paused; rerun with -EnableAutomaticWatch to enable it"
    }
}
catch {
    if ($created) {
        # A just-created job has an inert schedule, but pausing it preserves the
        # safe default if subsequent configuration fails.
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & gcloud.cmd scheduler jobs pause $JobName --location $Location --quiet *> $null
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    throw
}
