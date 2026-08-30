[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$ConfigPath = '.env',
    [string]$ApiBaseUrl,
    [string]$Audience,
    [string]$ImpersonateServiceAccount,
    [switch]$NoBrowser,
    [switch]$SkipDoctor
)

$ErrorActionPreference = 'Stop'

function Get-LocalEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $line = $lines[$index]
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $pair = $trimmed.Split('=', 2)
        if ($pair.Count -eq 2 -and $pair[0] -eq $Name) {
            return $pair[1]
        }
    }
    return $null
}

function New-EphemeralSessionSecret {
    $bytes = New-Object byte[] 48
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $repoRoot
$config = Join-Path $repoRoot $ConfigPath
$uvCommand = Get-Command uv -ErrorAction Stop

if (-not $ApiBaseUrl) {
    $ApiBaseUrl = Get-LocalEnvValue -Path $config -Name 'RELAY_API_BASE_URL'
}
if (-not $Audience) {
    $Audience = Get-LocalEnvValue -Path $config -Name 'RELAY_API_AUDIENCE'
}
if (-not $ImpersonateServiceAccount) {
    $ImpersonateServiceAccount = Get-LocalEnvValue -Path $config -Name 'DEMONSTRATOR_PRINCIPAL_EMAIL'
}
if (-not $ApiBaseUrl -or -not $Audience -or -not $ImpersonateServiceAccount) {
    throw 'Missing non-secret presentation configuration. Run braille-relay init-local-config first.'
}

if (-not $SkipDoctor) {
    & $uvCommand.Source run --frozen braille-relay doctor --config $config
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Doctor reported one or more blockers. The launcher will not change cloud, Drive, CUPS, or IAM; resolve blockers before relying on live data.'
    }
}

$sessionSecret = New-EphemeralSessionSecret
$workDirectory = Join-Path $repoRoot 'work\demo'
New-Item -ItemType Directory -Path $workDirectory -Force | Out-Null
$stdout = Join-Path $workDirectory 'presentation.stdout.log'
$stderr = Join-Path $workDirectory 'presentation.stderr.log'
$arguments = @(
    'run', '--frozen', 'python', '-m', 'braille_errata_relay.presentation.app',
    '--api-base-url', $ApiBaseUrl,
    '--audience', $Audience,
    '--impersonate-service-account', $ImpersonateServiceAccount,
    '--session-secret', $sessionSecret,
    '--port', $Port
)

$process = Start-Process -FilePath $uvCommand.Source -ArgumentList $arguments `
    -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$url = "http://127.0.0.1:$Port/watch"
$ready = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        if ($process.HasExited) {
            break
        }
    }
}

if (-not $ready) {
    throw 'The local presentation server did not become ready. It was not granted any cloud, Drive, CUPS, or IAM authority by this launcher.'
}

Write-Output "PASS: loopback watch floor is ready at $url"
Write-Output "To stop it later, end local presentation process ID $($process.Id) using your normal process manager."
Write-Output 'The presentation server is read-only with respect to CUPS and production devices.'
if (-not $NoBrowser) {
    Start-Process -FilePath $url
}
