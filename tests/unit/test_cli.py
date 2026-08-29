from __future__ import annotations

import json
from typing import Any

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
