[CmdletBinding()]
param(
    [ValidateRange(1, 60)]
    [int]$MaximumAttempts = 30,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-MatchingOutboxIndex {
    $indexes = & gcloud.cmd firestore indexes composite list --database="(default)" --format=json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Firestore composite indexes."
    }
    foreach ($index in @($indexes | Where-Object { $null -ne $_ })) {
        $resourceName = [string]$index.name
        if (
            $resourceName -notmatch "/collectionGroups/outbox/indexes/" -or
            $index.queryScope -ne "COLLECTION"
        ) {
            continue
        }
        $fields = @($index.fields | Where-Object { $null -ne $_ })
        if (
            $fields.Count -ge 2 -and
            $fields[0].fieldPath -eq "status" -and $fields[0].order -eq "ASCENDING" -and
            $fields[1].fieldPath -eq "created_at" -and $fields[1].order -eq "ASCENDING"
        ) {
            return $index
        }
    }
    return $null
}

$existing = Get-MatchingOutboxIndex
if ($null -eq $existing) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud.cmd firestore indexes composite create `
            '--database=(default)' `
            --collection-group=outbox `
            --query-scope=collection `
            '--field-config=field-path=status,order=ascending' `
            '--field-config=field-path=created_at,order=ascending' `
            --async --quiet *> $null
        $createExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($createExit -ne 0) {
        # A concurrent administrator may have created the same immutable
        # index. Re-read before treating the result as a true failure.
        $existing = Get-MatchingOutboxIndex
        if ($null -eq $existing) {
            throw "Could not create the required Firestore outbox lease index."
        }
    }
}

for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
    $matching = Get-MatchingOutboxIndex
    if ($null -ne $matching -and $matching.state -eq "READY") {
        Write-Output "PASS: Firestore outbox status/created_at composite index is READY"
        exit 0
    }
    Start-Sleep -Seconds $RetryDelaySeconds
}

throw "Firestore outbox lease index did not reach READY before the bounded wait expired."
