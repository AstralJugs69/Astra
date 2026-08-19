"""Common utilities and fail-open transport for local Antigravity hook scripts.

This module is strictly STDLIB-ONLY (zero pip dependencies) so it runs
seamlessly on any developer machine without virtualenv activation.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional, Tuple


# Configurable via environment or local defaults
DEFAULT_BACKEND_URL = os.environ.get("ASTRA_ENDPOINT_URL", "http://127.0.0.1:8080/event")
DEFAULT_AUTH_TOKEN = os.environ.get("ASTRA_AUTH_TOKEN", "astra-dev-secret-token-change-in-prod")
DEBUG_LOG_FILE = os.environ.get("ASTRA_HOOK_DEBUG_LOG")


def log_debug(message: str) -> None:
    """Optional debug logger to local file."""
    if DEBUG_LOG_FILE:
        try:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass


def get_fail_open_default(event_type: str) -> Dict[str, Any]:
    """Hardcoded fail-open default decision.

    Guarantees that on any timeout, network error, or backend exception,
    the main agent is allowed to proceed or terminate normally.
    """
    if event_type == "PostToolUse":
        return {}
    return {"decision": "continue"}


def read_stdin_json() -> Tuple[Dict[str, Any], Optional[str]]:
    """Reads raw JSON payload from stdin.

    Returns (payload_dict, error_message).
    """
    try:
        raw_text = sys.stdin.read()
        if not raw_text.strip():
            return {}, "Empty stdin received"
        return json.loads(raw_text), None
    except Exception as exc:
        return {}, f"Failed to parse stdin JSON: {exc}"


def relay_event_to_backend(
    event_type: str,
    raw_payload: Dict[str, Any],
    timeout_seconds: float,
    backend_url: str = DEFAULT_BACKEND_URL,
    auth_token: str = DEFAULT_AUTH_TOKEN,
) -> Dict[str, Any]:
    """Relays the raw hook event to Astra backend over HTTP POST.

    Enforces single attempt with hard timeout. On ANY failure, returns
    the hardcoded fail-open default.
    """
    correlation_id = str(uuid.uuid4())
    log_debug(f"Relaying {event_type} event (correlation_id={correlation_id}) to {backend_url}")

    # Build wrapped envelope for POST /event
    request_body = {
        "event_type": event_type,
        "correlation_id": correlation_id,
        "payload": raw_payload,
        "client_timestamp_ms": int(time.time() * 1000),
    }

    try:
        data = json.dumps(request_body).encode("utf-8")
        req = urllib.request.Request(
            url=backend_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_token}",
                "X-Correlation-ID": correlation_id,
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            if response.status == 200:
                resp_bytes = response.read()
                resp_json = json.loads(resp_bytes.decode("utf-8"))
                log_debug(f"Backend response received: {resp_json}")
                return resp_json
            else:
                log_debug(f"Backend returned non-200 status: {response.status}")
                return get_fail_open_default(event_type)

    except urllib.error.HTTPError as http_err:
        log_debug(f"HTTP error during relay: {http_err.code} {http_err.reason}")
        return get_fail_open_default(event_type)
    except urllib.error.URLError as url_err:
        log_debug(f"URL error during relay (timeout/connection): {url_err.reason}")
        return get_fail_open_default(event_type)
    except Exception as exc:
        log_debug(f"Unexpected error during relay: {exc}")
        return get_fail_open_default(event_type)


def execute_hook(event_type: str, timeout_seconds: float) -> None:
    """Standard execution flow for any hook script."""
    raw_payload, err = read_stdin_json()
    if err:
        log_debug(f"Stdin error: {err} -> using fail-open default")
        sys.stdout.write(json.dumps(get_fail_open_default(event_type)) + "\n")
        sys.stdout.flush()
        return

    decision = relay_event_to_backend(
        event_type=event_type,
        raw_payload=raw_payload,
        timeout_seconds=timeout_seconds,
    )

    sys.stdout.write(json.dumps(decision) + "\n")
    sys.stdout.flush()
