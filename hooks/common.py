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
_default_log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hook_events.log")
DEBUG_LOG_FILE = os.environ.get("ASTRA_HOOK_DEBUG_LOG", _default_log)


def log_debug(message: str) -> None:
    """Optional debug logger to local file."""
    if DEBUG_LOG_FILE:
        try:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass


def format_hook_entry(event_type: str, raw_payload: Dict[str, Any], decision: Dict[str, Any], status: str = "OK") -> str:
    """Formats hook executions into human-readable, grep-friendly structured log lines."""
    session_id = raw_payload.get("session_id", raw_payload.get("sessionId", "unknown"))[:8]
    
    if event_type == "PostToolUse":
        tool_name = raw_payload.get("tool_name") or raw_payload.get("toolName") or "tool"
        args = raw_payload.get("tool_arguments") or raw_payload.get("toolArguments") or {}
        arg_str = ""
        if isinstance(args, dict):
            arg_str = (
                args.get("CommandLine")
                or args.get("TargetFile")
                or args.get("AbsolutePath")
                or args.get("Query")
                or args.get("Pattern")
                or ""
            )
        elif isinstance(args, str):
            arg_str = args[:40]
        arg_summary = f" {arg_str[:50]}" if arg_str else ""
        return f"[HOOK:PostToolUse] [TOOL:{tool_name}]{arg_summary} | Session:{session_id} | Status:{status} -> Backend: {{}}"

    elif event_type == "Stop":
        dec_val = decision.get("decision", "allow").upper()
        reason = decision.get("reason", "").replace("\n", " ")
        reason_str = f" | Reason: {reason[:100]}" if reason else ""
        return f"[HOOK:Stop] [AUDIT] Session:{session_id} | Status:{status} -> Decision: {dec_val}{reason_str}"

    return f"[HOOK:{event_type}] Session:{session_id} | Status:{status} -> Decision: {decision}"


def get_fail_open_default(event_type: str) -> Dict[str, Any]:
    """Hardcoded fail-open default decision.

    Guarantees that on any timeout, network error, or backend exception,
    the main agent is allowed to proceed or terminate normally.
    """
    if event_type == "PostToolUse":
        return {}
    return {"decision": "allow"}


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
                log_debug(format_hook_entry(event_type, raw_payload, resp_json, status="200_OK"))
                return resp_json
            else:
                default_dec = get_fail_open_default(event_type)
                log_debug(format_hook_entry(event_type, raw_payload, default_dec, status=f"HTTP_{response.status}"))
                return default_dec

    except urllib.error.HTTPError as http_err:
        default_dec = get_fail_open_default(event_type)
        log_debug(format_hook_entry(event_type, raw_payload, default_dec, status=f"HTTP_ERR_{http_err.code}"))
        return default_dec
    except urllib.error.URLError as url_err:
        default_dec = get_fail_open_default(event_type)
        log_debug(format_hook_entry(event_type, raw_payload, default_dec, status=f"URL_ERR_{url_err.reason}"))
        return default_dec
    except Exception as exc:
        default_dec = get_fail_open_default(event_type)
        log_debug(format_hook_entry(event_type, raw_payload, default_dec, status=f"EXC_{exc}"))
        return default_dec


def execute_hook(event_type: str, timeout_seconds: float) -> None:
    """Standard execution flow for any hook script."""
    raw_payload, err = read_stdin_json()
    if err:
        default_dec = get_fail_open_default(event_type)
        log_debug(f"[HOOK:{event_type}] Stdin Error: {err} -> Fallback: {default_dec}")
        sys.stdout.write(json.dumps(default_dec) + "\n")
        sys.stdout.flush()
        return

    decision = relay_event_to_backend(
        event_type=event_type,
        raw_payload=raw_payload,
        timeout_seconds=timeout_seconds,
    )

    sys.stdout.write(json.dumps(decision) + "\n")
    sys.stdout.flush()
