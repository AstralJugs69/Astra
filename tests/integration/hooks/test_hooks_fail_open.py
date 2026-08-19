"""Integration tests for local hook dispatchers guaranteeing fail-open behavior."""

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading
import pytest

HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
POST_TOOL_USE_SCRIPT = HOOKS_DIR / "post_tool_use.py"
STOP_SCRIPT = HOOKS_DIR / "stop.py"


def run_hook_subprocess(script_path: Path, stdin_data: str, env_overrides: dict = None) -> subprocess.CompletedProcess:
    """Executes a hook script via subprocess and returns result."""
    import os
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    return subprocess.run(
        [sys.executable, str(script_path)],
        input=stdin_data,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def test_post_tool_use_fail_open_when_backend_unreachable():
    """When backend URL is down, PostToolUse must exit cleanly with empty JSON {}."""
    payload = {
        "conversationId": "test-session-123",
        "stepIdx": 1,
        "toolCall": {"name": "view_file", "args": {"AbsolutePath": "foo.py"}},
    }
    result = run_hook_subprocess(
        POST_TOOL_USE_SCRIPT,
        stdin_data=json.dumps(payload),
        env_overrides={"ASTRA_ENDPOINT_URL": "http://127.0.0.1:59999/event"},
    )
    assert result.returncode == 0
    output_json = json.loads(result.stdout.strip())
    assert output_json == {}


def test_stop_hook_fail_open_when_backend_unreachable():
    """When backend URL is down, Stop hook must return {'decision': 'continue'} to allow completion."""
    payload = {
        "conversationId": "test-session-123",
        "terminationReason": "model_stop",
        "error": "",
    }
    result = run_hook_subprocess(
        STOP_SCRIPT,
        stdin_data=json.dumps(payload),
        env_overrides={"ASTRA_ENDPOINT_URL": "http://127.0.0.1:59999/event"},
    )
    assert result.returncode == 0
    output_json = json.loads(result.stdout.strip())
    assert output_json == {"decision": "continue"}


def test_hooks_fail_open_on_empty_or_malformed_stdin():
    """Hook scripts must not crash on malformed/empty stdin and must fail open."""
    # Empty stdin
    res_post = run_hook_subprocess(POST_TOOL_USE_SCRIPT, stdin_data="")
    assert res_post.returncode == 0
    assert json.loads(res_post.stdout.strip()) == {}

    # Invalid JSON
    res_stop = run_hook_subprocess(STOP_SCRIPT, stdin_data="{not valid json}")
    assert res_stop.returncode == 0
    assert json.loads(res_stop.stdout.strip()) == {"decision": "continue"}


class MockBackendHandler(BaseHTTPRequestHandler):
    mode = "success"

    def do_POST(self):
        if self.mode == "error_500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal Error")
        elif self.mode == "invalid_json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"not-json-response")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"decision": "block_stop", "reason": "Testing block"}).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress server logging during tests


@pytest.fixture
def mock_backend():
    server = HTTPServer(("127.0.0.1", 0), MockBackendHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/event", MockBackendHandler
    server.shutdown()


def test_hook_relays_valid_backend_response(mock_backend):
    url, handler_cls = mock_backend
    handler_cls.mode = "success"

    payload = {"conversationId": "test-session", "terminationReason": "model_stop"}
    result = run_hook_subprocess(
        STOP_SCRIPT,
        stdin_data=json.dumps(payload),
        env_overrides={"ASTRA_ENDPOINT_URL": url},
    )
    assert result.returncode == 0
    output_json = json.loads(result.stdout.strip())
    assert output_json == {"decision": "block_stop", "reason": "Testing block"}


def test_hook_fails_open_on_500_backend_response(mock_backend):
    url, handler_cls = mock_backend
    handler_cls.mode = "error_500"

    payload = {"conversationId": "test-session", "terminationReason": "model_stop"}
    result = run_hook_subprocess(
        STOP_SCRIPT,
        stdin_data=json.dumps(payload),
        env_overrides={"ASTRA_ENDPOINT_URL": url},
    )
    assert result.returncode == 0
    output_json = json.loads(result.stdout.strip())
    assert output_json == {"decision": "continue"}


def test_hook_fails_open_on_malformed_backend_response(mock_backend):
    url, handler_cls = mock_backend
    handler_cls.mode = "invalid_json"

    payload = {"conversationId": "test-session", "terminationReason": "model_stop"}
    result = run_hook_subprocess(
        STOP_SCRIPT,
        stdin_data=json.dumps(payload),
        env_overrides={"ASTRA_ENDPOINT_URL": url},
    )
    assert result.returncode == 0
    output_json = json.loads(result.stdout.strip())
    assert output_json == {"decision": "continue"}
