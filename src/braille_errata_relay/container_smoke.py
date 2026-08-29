"""Installed-wheel smoke check used by the immutable container preflight."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient

import braille_errata_relay
from braille_errata_relay.api.main import create_app
from braille_errata_relay.configuration import resolve_config_path
from braille_errata_relay.domain.recommendation import load_recommendation_policy


def collect() -> dict[str, object]:
    profile_path = resolve_config_path(
        direct_env="TRANSLATION_PROFILE_PATH",
        relative_path="translation_profiles/demo-ueb-40x25-v1.json",
    )
    policy = load_recommendation_policy()
    client = TestClient(create_app(profile_path=profile_path))
    health_response = client.get("/health")
    healthz_response = client.get("/healthz")
    response = client.get("/readyz")
    body = response.json()
    package_path = Path(inspect.getfile(braille_errata_relay)).resolve()
    app_src = Path("/app/src").resolve()
    package_from_app_src = package_path == app_src or app_src in package_path.parents
    return {
        "schema_version": "installed-container-smoke.v1",
        "policy_id": policy.policy_id,
        "profile_id": body.get("profile_id"),
        "health_status": health_response.status_code,
        "healthz_status": healthz_response.status_code,
        "readyz_status": response.status_code,
        "ready": body.get("ready"),
        "liblouis_version": body.get("liblouis_version"),
        "app_src_present": app_src.exists(),
        "package_from_app_src": package_from_app_src,
    }


def main() -> int:
    result = collect()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    passed = (
        result["policy_id"] == "relay-policy.v1"
        and result["profile_id"] == "demo-ueb-40x25-v1"
        and result["health_status"] == 200
        and result["healthz_status"] == 200
        and result["readyz_status"] == 200
        and result["ready"] is True
        and result["liblouis_version"] == "3.38.0"
        and result["app_src_present"] is False
        and result["package_from_app_src"] is False
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
