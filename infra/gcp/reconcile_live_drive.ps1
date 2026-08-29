[CmdletBinding()]
param(
    [ValidateSet("INITIALIZE", "RECONCILE")]
    [string]$Operation = "INITIALIZE",

    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "braille-errata-relay",

    [ValidateNotNullOrEmpty()]
    [string]$Region = "europe-west3",

    [AllowEmptyString()]
    [string]$RepoRoot = "",

    [ValidateRange(1, 30)]
    [int]$MaximumTokenAttempts = 24,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 5,

    [switch]$ExecuteDriveRead
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ExecuteDriveRead) {
    throw "Refusing to invoke Drive reconciliation without -ExecuteDriveRead."
}
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Get-IamPolicyBindings {
    param([Parameter(Mandatory)][object]$Policy)

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
        $json = $Value | ConvertTo-Json -Depth 10 -Compress
        [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.Encoding]::UTF8)
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

$service = & gcloud.cmd run services describe $ServiceName --region=$Region --format=json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the private Cloud Run service."
}
$environment = @{}
foreach ($entry in @($service.spec.template.spec.containers[0].env)) {
    $environment[$entry.name] = $entry.value
}
$serviceUrl = [string]$service.status.url
$audience = [string]$environment["INTERNAL_OIDC_AUDIENCE"]
$schedulerIdentity = [string]$environment["INTERNAL_SCHEDULER_PRINCIPAL_EMAIL"]
$activeAccount = (gcloud config get-value account 2>$null).Trim()
if (-not $serviceUrl -or -not $audience -or -not $schedulerIdentity -or -not $activeAccount) {
    throw "Live Drive reconciliation configuration is incomplete."
}
$member = if ($activeAccount.EndsWith(".gserviceaccount.com")) {
    "serviceAccount:$activeAccount"
}
else {
    "user:$activeAccount"
}

if (Test-TokenCreatorGrantPresent -Identity $schedulerIdentity -Member $member) {
    throw "Refusing to reuse a pre-existing Token Creator grant."
}

$grantCreated = $false
$token = $null
try {
    $grantExit = Invoke-GcloudQuietly {
        gcloud iam service-accounts add-iam-policy-binding $schedulerIdentity `
            --member=$member `
            --role=roles/iam.serviceAccountTokenCreator `
            --condition=None `
            --quiet
    }
    if ($grantExit -ne 0) {
        throw "Narrow token impersonation grant failed."
    }
    $grantCreated = $true
    $token = New-AudienceToken -Identity $schedulerIdentity -Audience $audience
    $body = @{schema_version = "cloud-gate0-drive-reconcile.v1"; operation = $Operation} |
        ConvertTo-Json -Compress
    $result = Invoke-RestMethod `
        -Uri ($serviceUrl.TrimEnd("/") + "/internal/drive-reconcile") `
        -Method Post `
        -Headers @{Authorization = "Bearer $token"} `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 120
    if ($result.status -ne "PASS" -or $result.operation -ne $Operation) {
        throw "Drive reconciliation did not return the expected sanitized success record."
    }
    $repoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
    $outputPath = Join-Path $repoRootPath ("work\live-closure\drive-" +
        $Operation.ToLowerInvariant() + ".json")
    Write-JsonAtomic -Path $outputPath -Value $result
    Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
}
finally {
    $token = $null
    if ($grantCreated) {
        $removeExit = Invoke-GcloudQuietly {
            gcloud iam service-accounts remove-iam-policy-binding $schedulerIdentity `
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

if (Test-TokenCreatorGrantPresent -Identity $schedulerIdentity -Member $member) {
    throw "Temporary token impersonation authority remains."
}
Write-Output "PASS: temporary token impersonation authority removed"
