from __future__ import annotations

import json
from pathlib import Path

import pytest

from braille_errata_relay import local_setup
from braille_errata_relay.cli import main


def _config() -> local_setup.LocalRelayConfig:
    return local_setup.LocalRelayConfig(
        google_cloud_project="private-demo-project-1",
        drive_file_id="1AbCdEfGhIjKlMnOpQrStUv",
        site_id="demo-site",
        queue_name="Braille-Embosser-Sim",
        local_bridge_id="single-pc-bridge",
        demonstrator_principal_email=(
            "relay-demonstrator@private-demo-project-1.iam.gserviceaccount.com"
        ),
        telemetry_principal_email=(
            "relay-telemetry@private-demo-project-1.iam.gserviceaccount.com"
        ),
        relay_api_url="https://private-relay.example.test",
        relay_audience="https://private-relay.example.test",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("1AbCdEfGhIjKlMnOpQrStUv", "1AbCdEfGhIjKlMnOpQrStUv"),
        (
            "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view?usp=sharing",
            "1AbCdEfGhIjKlMnOpQrStUv",
        ),
        (
            "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUv",
            "1AbCdEfGhIjKlMnOpQrStUv",
        ),
        (
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit",
            "1AbCdEfGhIjKlMnOpQrStUv",
        ),
    ),
)
def test_extract_drive_file_id_accepts_standard_direct_and_url_forms(
    value: str, expected: str
) -> None:
    assert local_setup.extract_drive_file_id(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "http://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view",
        "https://evil.example.test/file/d/1AbCdEfGhIjKlMnOpQrStUv/view",
        "https://user:pass@drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view",
        "https://drive.google.com/file/d/too-short/view",
    ),
)
def test_extract_drive_file_id_rejects_unsafe_or_invalid_urls(value: str) -> None:
    with pytest.raises(ValueError):
        local_setup.extract_drive_file_id(value)


def test_local_config_is_non_secret_portable_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    config = _config()

    local_setup.write_local_config(path=path, config=config, force=False)
    rendered = path.read_text(encoding="utf-8")

    assert "GOOGLE_CLOUD_PROJECT=private-demo-project-1" in rendered
    assert "password" in rendered.lower()
    assert "access_token" not in rendered
    assert "service-account.json" not in rendered
    assert local_setup.load_local_config(path) == config
    with pytest.raises(FileExistsError):
        local_setup.write_local_config(path=path, config=config, force=False)


def test_cli_init_shows_sanitized_preview_writes_dotenv_and_requires_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".env"
    args = [
        "init-local-config",
        "--project-id",
        "private-demo-project-1",
        "--drive-source",
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view",
        "--site-id",
        "demo-site",
        "--queue-name",
        "Braille-Embosser-Sim",
        "--bridge-id",
        "single-pc-bridge",
        "--relay-api-url",
        "https://private-relay.example.test",
        "--relay-audience",
        "https://private-relay.example.test",
        "--output",
        str(path),
    ]

    assert main(args) == 0
    output = capsys.readouterr().out
    assert '"status": "PREVIEW"' in output
    assert "private-demo-project-1" not in output
    assert main(args) == 1
    assert "BLOCKED" in capsys.readouterr().err


def test_local_config_accepts_the_closed_native_google_docs_provider_mime_type() -> None:
    values = _config().model_dump()
    config = local_setup.LocalRelayConfig.model_validate(
        {**values, "drive_source_mime_type": "application/vnd.google-apps.document"}
    )

    assert config.drive_source_mime_type == "application/vnd.google-apps.document"
    with pytest.raises(ValueError, match="source MIME type"):
        local_setup.LocalRelayConfig.model_validate(
            {**values, "drive_source_mime_type": "application/pdf"}
        )


def test_doctor_is_sanitized_and_non_mutating_without_optional_remote_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    local_setup.write_local_config(path=path, config=_config(), force=False)
    monkeypatch.setattr(
        local_setup,
        "_ordinary_adc_check",
        lambda: local_setup.DoctorCheck("ordinary_adc", "PASS", "available"),
    )
    monkeypatch.setattr(
        local_setup,
        "_liblouis_check",
        lambda: local_setup.DoctorCheck("liblouis", "PASS", "ready"),
    )
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("doctor must not spawn a process without optional local-floor checks")

    monkeypatch.setattr(local_setup.subprocess, "run", fail_if_called)

    checks = local_setup.run_doctor(
        config_path=path,
        check_drive=False,
        check_wsl_cups=False,
        command_exists=lambda _name: "available",
    )
    payload = json.loads(local_setup.doctor_json(checks))

    assert called is False
    assert payload["status"] == "PASS"
    assert payload["checks"]
    serialized = json.dumps(payload)
    for private_value in (
        "private-demo-project-1",
        "1AbCdEfGhIjKlMnOpQrStUv",
        "private-relay.example.test",
    ):
        assert private_value not in serialized


def test_doctor_requires_valid_local_configuration_without_accessing_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_setup,
        "_ordinary_adc_check",
        lambda: local_setup.DoctorCheck("ordinary_adc", "PASS", "available"),
    )
    monkeypatch.setattr(
        local_setup,
        "_liblouis_check",
        lambda: local_setup.DoctorCheck("liblouis", "PASS", "ready"),
    )

    checks = local_setup.run_doctor(
        config_path=tmp_path / ".env",
        check_drive=True,
        check_wsl_cups=False,
        command_exists=lambda _name: "available",
    )

    by_name = {check.name: check for check in checks}
    assert by_name["local_configuration"].status == "BLOCKED"
    assert by_name["drive_read"].status == "BLOCKED"
