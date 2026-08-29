from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from braille_errata_relay import cli


class FakeResponse:
    status_code = 201

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "status": "REGISTERED",
            "duplicate": False,
            "record": {
                "baseline": {
                    "baseline_id": "a" * 64,
                    "approved_brf_sha256": "b" * 64,
                }
            },
        }


def test_demo_baseline_cli_uses_oidc_and_never_exposes_a_production_control(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.id_token, "fetch_id_token", lambda _request, _audience: "secret")

    def post(url: str, **values: object) -> FakeResponse:
        captured.update({"url": url, **values})
        return FakeResponse()

    monkeypatch.setattr(cli.httpx, "post", post)

    exit_code = cli.main(
        [
            "register-demo-baseline",
            "--service-url",
            "https://relay.example.run.app/",
            "--audience",
            "https://relay.example.run.app",
            "--file-id",
            "drive-file",
            "--revision-id",
            "drive:drive-file:62:" + "c" * 64,
        ]
    )

    assert exit_code == 0
    assert captured["url"] == "https://relay.example.run.app/api/v1/baselines"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["production_id"] == "WO-DEMO-001"
    assert payload["production_id_origin"] == "EXTERNAL_REFERENCE"
    assert payload["approval_label"] == "DEMO_FIXTURE_APPROVED"
    assert set(payload).isdisjoint({"submit", "cancel", "hold", "release", "restart"})
    output = json.loads(capsys.readouterr().out)
    assert output["baseline_id"] == "a" * 64
    assert "secret" not in repr(output)


def test_link_cli_posts_only_read_only_lineage(monkeypatch: Any, capsys: Any) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_identity_token", lambda **_values: "secret")

    class LinkResponse:
        status_code = 201
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "status": "PRODUCTION_LINK_VERIFIED",
                "duplicate": False,
                "production_link": {"link_id": "d" * 64, "scheduler_job_id": 42},
            }

    def post(url: str, **values: object) -> LinkResponse:
        captured.update({"url": url, **values})
        return LinkResponse()

    monkeypatch.setattr(cli.httpx, "post", post)
    exit_code = cli.main(
        [
            "link-baseline-production",
            "--service-url",
            "https://relay.example.run.app",
            "--audience",
            "https://relay.example.run.app",
            "--baseline-id",
            "a" * 64,
            "--scheduler-job-id",
            "42",
        ]
    )

    assert exit_code == 0
    assert captured["url"] == (
        "https://relay.example.run.app/api/v1/baselines/" + "a" * 64 + "/production-links"
    )
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert set(payload) == {
        "schema_version",
        "scheduler_job_id",
        "expected_state_version",
        "idempotency_key",
    }
    assert set(payload).isdisjoint({"submit", "cancel", "hold", "release", "restart"})
    assert "secret" not in capsys.readouterr().out


def test_telemetry_cli_publishes_completed_json_without_command_surface(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    observation = tmp_path / "observation.json"
    observation.write_text(
        json.dumps({"schema_version": "site-observation.v1", "observation_id": "a" * 64}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_identity_token", lambda **_values: "secret")

    class TelemetryResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"status": "ACCEPTED", "observation_id": "a" * 64, "duplicate": False}

    def post(url: str, **values: object) -> TelemetryResponse:
        captured.update({"url": url, **values})
        return TelemetryResponse()

    monkeypatch.setattr(cli.httpx, "post", post)
    exit_code = cli.main(
        [
            "publish-site-observation",
            "--service-url",
            "https://relay.example.run.app",
            "--audience",
            "https://relay.example.run.app",
            "--observation",
            str(observation),
        ]
    )

    assert exit_code == 0
    assert captured["url"] == "https://relay.example.run.app/internal/site-observations"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert set(payload).isdisjoint({"command", "submit", "cancel", "hold", "release"})
    assert "secret" not in capsys.readouterr().out
