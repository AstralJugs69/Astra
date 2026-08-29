from __future__ import annotations

import base64
import json
from pathlib import Path

from braille_errata_relay.api.main import OutboxDrainRequest

ROOT = Path(__file__).resolve().parents[2]
BODY = ROOT / "config" / "scheduler" / "outbox-drain-request.v1.json"
SCRIPT = ROOT / "infra" / "gcp" / "configure_outbox_scheduler.ps1"
PRIVATE_ROUTE_SCRIPT = ROOT / "infra" / "gcp" / "test_private_routes.ps1"
LIVE_LINK_SCRIPT = ROOT / "infra" / "gcp" / "link_local_baseline_job.ps1"
SINGLE_RUN_SCRIPT = ROOT / "infra" / "gcp" / "run_single_scheduler_closure.ps1"
COLLECT_EVIDENCE_SCRIPT = ROOT / "infra" / "gcp" / "collect_scheduler_closure_evidence.ps1"


def test_scheduler_body_round_trips_as_strict_json_contract() -> None:
    body = BODY.read_bytes()
    decoded = base64.b64decode(base64.b64encode(body), validate=True)
    payload = json.loads(decoded)

    request = OutboxDrainRequest.model_validate(payload)

    assert request.model_dump(mode="json") == {
        "schema_version": "outbox-drain-request.v1",
        "limit": 10,
    }
    assert decoded.strip() == b'{"schema_version":"outbox-drain-request.v1","limit":10}'


def test_scheduler_configuration_uses_file_body_json_header_and_repauses() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "--message-body-from-file $messageBody" in script
    assert '--update-headers "Content-Type=application/json"' in script
    assert "gcloud scheduler jobs pause $JobName" in script
    assert "--message-body " not in script


def test_private_route_smoke_scopes_and_removes_temporary_token_authority() -> None:
    script = PRIVATE_ROUTE_SCRIPT.read_text(encoding="utf-8")

    assert "gcloud iam service-accounts add-iam-policy-binding $identity" in script
    assert "gcloud iam service-accounts remove-iam-policy-binding $identity" in script
    assert "--role=roles/iam.serviceAccountTokenCreator" in script
    assert "finally {" in script
    assert "temporary_token_creator_absent=" in script
    assert 'PSObject.Properties["bindings"]' in script
    assert "Test-TokenCreatorGrantPresent" in script
    assert "Refusing to reuse a pre-existing Token Creator grant." in script
    assert "Invoke-GcloudQuietly" in script
    assert "gcloud projects add-iam-policy-binding" not in script


def test_private_route_smoke_separates_request_url_from_token_audience() -> None:
    script = PRIVATE_ROUTE_SCRIPT.read_text(encoding="utf-8")

    assert '$audience = [string]$environment["INTERNAL_OIDC_AUDIENCE"]' in script
    assert "$serviceUrl = [string]$service.status.url" in script
    assert "--audiences=$audience" in script
    assert '-Uri ($serviceUrl.TrimEnd("/") + "/health")' in script
    assert '-Uri ($serviceUrl.TrimEnd("/") + "/healthz")' in script
    assert '-Uri ($serviceUrl.TrimEnd("/") + "/readyz")' in script


def test_private_route_empty_outbox_replay_is_opt_in_and_scheduler_scoped() -> None:
    script = PRIVATE_ROUTE_SCRIPT.read_text(encoding="utf-8")

    assert "[switch]$VerifyEmptyOutboxReplay" in script
    assert 'IdentityEnvironmentVariable -ne "INTERNAL_SCHEDULER_PRINCIPAL_EMAIL"' in script
    assert '"config\\scheduler\\outbox-drain-request.v1.json"' in script
    assert '-Uri ($serviceUrl.TrimEnd("/") + "/internal/outbox-drain")' in script
    assert "$outboxReplayLeased -ne 0" in script
    assert "$outboxReplayMessageCount -ne 0" in script
    assert '$outboxReplayNotificationStatus -ne "NOT_CLAIMED"' in script


def test_live_baseline_link_harness_uses_only_readonly_bridge_and_narrow_iam() -> None:
    script = LIVE_LINK_SCRIPT.read_text(encoding="utf-8")

    assert "relay_bridge.main observe-once" in script
    assert "--require-job-id '$SchedulerJobId'" in script
    assert "relay_bridge.main pending-outbox" in script
    assert "acknowledge-published" in script
    assert "$ArchiveUnpublishedLocalJournal" in script
    assert "Move-Item -LiteralPath $workRoot -Destination $archivePath" in script
    assert "/internal/site-observations" in script
    assert "/production-links" in script
    assert "roles/iam.serviceAccountTokenCreator" in script
    assert "remove-iam-policy-binding" in script
    assert "finally {" in script
    assert "ComputeHash" in script
    assert "HashData" not in script
    assert 'PSObject.Properties["bindings"]' in script
    assert "Test-TokenCreatorGrantPresent" in script
    assert "Refusing to reuse a pre-existing Token Creator grant." in script
    assert '[string]$RepoRoot = ""' in script
    assert "if ([string]::IsNullOrWhiteSpace($RepoRoot))" in script
    assert "$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)" in script
    assert "Expected destination='$queueName' title='$ExpectedJobTitle'" in script
    assert "observed scheduler_job_id='" in script
    assert "gcloud projects add-iam-policy-binding" not in script
    for forbidden in (" lp ", "cancel", "hold", "release", "restart", "Print-Job"):
        assert forbidden not in script


def test_one_shot_scheduler_runner_requires_a_paused_scheduler_and_repauses() -> None:
    script = SINGLE_RUN_SCRIPT.read_text(encoding="utf-8")

    assert "-ExecuteSingleRun" in script
    assert 'if ($state -ne "PAUSED")' in script
    assert "gcloud.cmd scheduler jobs resume $JobName" in script
    assert "gcloud.cmd scheduler jobs run $JobName" in script
    assert "gcloud.cmd scheduler jobs pause $JobName" in script
    assert "MinimumQuietWindowSeconds" in script
    assert "single-scheduler-run-attempt.v1" in script
    assert "refusing an unreviewed second execution" in script
    assert 'RecoveryAuthorization = ""' in script
    assert '"RECOVER-FAILED-HTTP-500"' in script
    assert "single-scheduler-recovery-attempt.v1" in script
    assert "A scheduler recovery attempt is already recorded" in script
    assert "A successful one-shot scheduler result already exists" in script
    assert 'resource.type=\\"cloud_scheduler_job\\"' in script
    assert 'textPayload:\\"/internal/outbox-drain\\"' in script
    assert "single-scheduler-run.v1" in script
    assert "work\\live-closure" in script
    assert "single-scheduler-run.json" in script
    for forbidden in ("cups", "lp ", "cancel", "hold", "release", "restart", "print-job"):
        assert forbidden not in script.casefold()


def test_scheduler_evidence_collector_is_read_only_and_requires_attempt_lineage() -> None:
    script = COLLECT_EVIDENCE_SCRIPT.read_text(encoding="utf-8")

    assert "single-scheduler-run-attempt.json" in script
    assert "single-scheduler-recovery-attempt.json" in script
    assert "single-scheduler-run.json" in script
    assert 'if ($LASTEXITCODE -ne 0 -or $state -ne "PAUSED")' in script
    assert 'resource.type=\\"cloud_scheduler_job\\"' in script
    assert 'textPayload:\\"/internal/outbox-drain\\"' in script
    assert "exactly one HTTP 200 in both log streams" in script
    assert "collected_from_existing_run = $true" in script
    for forbidden in (
        "scheduler jobs run",
        "scheduler jobs resume",
        "scheduler jobs pause",
        "cups",
        "cancel",
        "hold",
        "release",
        "restart",
        "print-job",
    ):
        assert forbidden not in script.casefold()
