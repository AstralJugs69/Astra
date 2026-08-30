[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$BaselineId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ProductionLinkId,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$SchedulerJobId,

    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 512)]
    [string]$ExpectedJobTitle,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ApprovedBrfSha256,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$CurrentStateVersion,

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$PriorReportId,

    [switch]$CorrectHistoricalLink,

    [switch]$CorrectionOnly,

    [string]$Region = 'europe-west3',

    [string]$ServiceName = 'braille-errata-relay'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($CorrectionOnly -and -not $CorrectHistoricalLink) {
    throw '-CorrectionOnly requires -CorrectHistoricalLink.'
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($bytes)
    }
    finally {
        $algorithm.Dispose()
    }
    return ([BitConverter]::ToString($digest) -replace '-', '').ToLowerInvariant()
}

function Get-IamPolicyBindings {
    param([Parameter(Mandatory = $true)][object]$Policy)
    $property = $Policy.PSObject.Properties['bindings']
    if ($null -eq $property -or $null -eq $property.Value) {
        return @()
    }
    return @($property.Value)
}

function New-AudienceToken {
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [Parameter(Mandatory = $true)][string]$Audience
    )
    for ($attempt = 1; $attempt -le 24; $attempt++) {
        Start-Sleep -Seconds 5
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
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
    throw 'Audience-bound endpoint identity token did not become available.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ((git -C $repoRoot rev-parse --show-toplevel) -ne ($repoRoot -replace '\\', '/')) {
    throw 'Refusing endpoint evidence outside the verified Astra repository root.'
}

$project = [string](gcloud config get-value project 2>$null)
$account = [string](gcloud config get-value account 2>$null)
if (-not $project -or -not $account) {
    throw 'Active gcloud project and account are required.'
}
$service = gcloud run services describe $ServiceName `
    --project=$project `
    --region=$Region `
    --format=json | ConvertFrom-Json
$serviceUrl = [string]$service.status.url
$environment = @{}
$service.spec.template.spec.containers[0].env | ForEach-Object {
    $environment[[string]$_.name] = [string]$_.value
}
$audience = [string]$environment['INTERNAL_OIDC_AUDIENCE']
$endpointPrincipal = [string]$environment['ENDPOINT_EVIDENCE_PRINCIPAL_EMAIL']
if (-not $serviceUrl -or -not $audience -or -not $endpointPrincipal) {
    throw 'The deployed endpoint-evidence identity boundary is incomplete.'
}

$member = "user:$account"
$grantAdded = $false
$token = $null
try {
    $policy = gcloud iam service-accounts get-iam-policy $endpointPrincipal `
        --project=$project `
        --format=json | ConvertFrom-Json
    $existing = @(
        Get-IamPolicyBindings -Policy $policy | Where-Object {
            $_.role -eq 'roles/iam.serviceAccountTokenCreator' -and
            $_.members -contains $member
        }
    )
    if ($existing.Count -eq 0) {
        gcloud iam service-accounts add-iam-policy-binding $endpointPrincipal `
            --project=$project `
            --member=$member `
            --role=roles/iam.serviceAccountTokenCreator `
            --condition=None `
            --quiet | Out-Null
        $grantAdded = $true
    }
    $token = New-AudienceToken -Identity $endpointPrincipal -Audience $audience
    if (-not $token) {
        throw 'Could not mint the short-lived endpoint-evidence identity token.'
    }

    $receiptStateVersion = $CurrentStateVersion
    if ($CorrectHistoricalLink) {
        $correctionCanonical = '{"baseline_id":"' + $BaselineId +
            '","expected_state_version":' + $CurrentStateVersion +
            ',"production_link_id":"' + $ProductionLinkId +
            '","scope":"historical-production-link-correction"}'
        $correction = [ordered]@{
            schema_version = 'baseline-link-correction-request.v1'
            baseline_id = $BaselineId
            production_link_id = $ProductionLinkId
            expected_state_version = $CurrentStateVersion
            prior_report_id = if ($PriorReportId) { $PriorReportId } else { $null }
            idempotency_key = Get-Sha256 -Value $correctionCanonical
        } | ConvertTo-Json -Compress
        $correctionResult = Invoke-RestMethod `
            -Uri ($serviceUrl.TrimEnd('/') + '/internal/baseline-link-corrections') `
            -Method Post `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType 'application/json' `
            -Body $correction `
            -TimeoutSec 60
        if ($correctionResult.status -ne 'PROVISIONAL_PRODUCTION_LINK') {
            throw 'Historical correction did not restore the provisional boundary.'
        }
        $receiptStateVersion = $CurrentStateVersion + 1
        Write-Output 'PASS: append-only historical correction restored provisional status'
        if ($CorrectionOnly) {
            $correctionResult | ConvertTo-Json -Depth 12 -Compress
            return
        }
    }

    $wslRepoRoot = [string](wsl.exe -d Ubuntu-24.04 --exec wslpath -a ($repoRoot -replace '\\', '/'))
    if (-not $wslRepoRoot) {
        throw 'Could not resolve the repository inside WSL.'
    }
    $auditOutput = wsl.exe -d Ubuntu-24.04 --user relay-endpoint-auditor --exec `
        python3 "$wslRepoRoot/infra/wsl/audit_endpoint_receipt.py" `
        --baseline-id $BaselineId `
        --production-link-id $ProductionLinkId `
        --job-id $SchedulerJobId `
        --expected-title $ExpectedJobTitle `
        --approved-brf-sha256 $ApprovedBrfSha256 `
        --expected-state-version $receiptStateVersion
    if ($LASTEXITCODE -ne 0) {
        throw 'The fixed-root WSL endpoint audit failed closed.'
    }
    $submission = [string]($auditOutput | Select-Object -Last 1)
    $parsedSubmission = $submission | ConvertFrom-Json
    if (
        $parsedSubmission.truth_basis -ne 'SIMULATED_DEMO' -or
        $parsedSubmission.endpoint_received_sha256 -ne $ApprovedBrfSha256
    ) {
        throw 'The local audit did not establish exact simulated-endpoint bytes.'
    }
    $receiptResult = Invoke-RestMethod `
        -Uri ($serviceUrl.TrimEnd('/') + '/internal/endpoint-receipts') `
        -Method Post `
        -Headers @{ Authorization = "Bearer $token" } `
        -ContentType 'application/json' `
        -Body $submission `
        -TimeoutSec 60
    if ($receiptResult.status -ne 'PRODUCTION_LINK_VERIFIED') {
        throw 'Endpoint receipt did not complete the exact-byte transition.'
    }
    Write-Output 'PASS: exact endpoint-received bytes confirmed the production link'
    $receiptResult | ConvertTo-Json -Depth 12 -Compress
}
finally {
    $token = $null
    if ($grantAdded) {
        gcloud iam service-accounts remove-iam-policy-binding $endpointPrincipal `
            --project=$project `
            --member=$member `
            --role=roles/iam.serviceAccountTokenCreator `
            --condition=None `
            --quiet | Out-Null
    }
}
