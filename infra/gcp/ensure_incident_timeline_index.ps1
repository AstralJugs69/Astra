[CmdletBinding()]
param(
    [ValidateRange(1, 60)]
    [int]$MaximumAttempts = 30,

    [ValidateRange(1, 30)]
    [int]$RetryDelaySeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-MatchingIncidentTimelineIndex {
    $indexes = & gcloud.cmd firestore indexes composite list --database="(default)" --format=json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Firestore composite indexes."
    }
    foreach ($index in @($indexes | Where-Object { $null -ne $PSItem })) {
        $resourceName = [string]$index.name
        if (
            $resourceName -notmatch "/collectionGroups/incident_timeline_events/indexes/" -or
            $index.queryScope -ne "COLLECTION"
        ) {
            continue
        }
        $fields = @($index.fields | Where-Object { $null -ne $PSItem })
        if (
            $fields.Count -ge 2 -and
            $fields[0].fieldPath -eq "record.incident_id" -and
            $fields[0].order -eq "ASCENDING" -and
            $fields[1].fieldPath -eq "record.recorded_at" -and
            $fields[1].order -eq "ASCENDING"
        ) {
            return $index
        }
    }
    return $null
}

$existing = Get-MatchingIncidentTimelineIndex
if ($null -eq $existing) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud.cmd firestore indexes composite create `
            '--database=(default)' `
            --collection-group=incident_timeline_events `
            --query-scope=collection `
            '--field-config=field-path=record.incident_id,order=ascending' `
            '--field-config=field-path=record.recorded_at,order=ascending' `
            --async --quiet *> $null
        $createExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($createExit -ne 0) {
        # A concurrent administrator may have created the same immutable
        # index. Re-read before treating the result as a true failure.
        $existing = Get-MatchingIncidentTimelineIndex
        if ($null -eq $existing) {
            throw "Could not create the required Firestore incident timeline index."
        }
    }
}

for ($attempt = 1; $attempt -le $MaximumAttempts; $attempt++) {
    $matching = Get-MatchingIncidentTimelineIndex
    if ($null -ne $matching -and $matching.state -eq "READY") {
        Write-Output "PASS: Firestore incident timeline composite index is READY"
        exit 0
    }
    Start-Sleep -Seconds $RetryDelaySeconds
}

throw "Firestore incident timeline index did not reach READY before the bounded wait expired."
