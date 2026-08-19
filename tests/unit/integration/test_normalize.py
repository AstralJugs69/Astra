"""Unit tests for Antigravity raw payload normalizer."""

import json
from pathlib import Path

from astra.domain.events import EventType
from astra.integration.antigravity.normalize import (
    normalize_antigravity_event,
    redact_secrets,
    truncate_text,
)
from astra.integration.antigravity.raw_schema import RawHookEnvelope

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "hook_payloads"


def test_redact_secrets():
    text_with_google_key = "Using AIzaSyD4j5k6L7m8N9o0P1q2R3s4T5u6V7w8X9y in request"
    redacted = redact_secrets(text_with_google_key)
    assert "[REDACTED_SECRET]" in redacted
    assert "AIzaSy" not in redacted

    text_with_bearer = "Authorization: Bearer my-secret-jwt-token-value-12345678"
    redacted_bearer = redact_secrets(text_with_bearer)
    assert "[REDACTED_SECRET]" in redacted_bearer


def test_truncate_text():
    short_text = "short text"
    assert truncate_text(short_text, max_chars=50) == short_text

    long_text = "a" * 100
    truncated = truncate_text(long_text, max_chars=10)
    assert len(truncated) > 10
    assert "...[truncated (100 chars total)]" in truncated


def test_normalize_post_tool_use_success_fixture():
    fixture_path = FIXTURES_DIR / "post_tool_use_success.json"
    raw_dict = json.loads(fixture_path.read_text(encoding="utf-8"))

    envelope = RawHookEnvelope(
        event_type="PostToolUse",
        correlation_id="corr-test-1",
        payload=raw_dict,
        client_timestamp_ms=1000,
    )

    event, warnings = normalize_antigravity_event(envelope, received_at_ms=1050)
    assert event is not None
    assert event.session_id == "1ac030ab-9022-4abd-81de-bf7d552301e1"
    assert event.event_type == EventType.POST_TOOL_USE
    assert event.tool is not None
    assert event.tool.name == "run_command"
    assert not event.is_tool_failure


def test_normalize_post_tool_use_error_fixture():
    fixture_path = FIXTURES_DIR / "post_tool_use_error.json"
    raw_dict = json.loads(fixture_path.read_text(encoding="utf-8"))

    envelope = RawHookEnvelope(
        event_type="PostToolUse",
        correlation_id="corr-test-2",
        payload=raw_dict,
        client_timestamp_ms=2000,
    )

    event, warnings = normalize_antigravity_event(envelope, received_at_ms=2050)
    assert event is not None
    assert event.is_tool_failure
    assert event.tool.had_error


def test_normalize_stop_fixture():
    fixture_path = FIXTURES_DIR / "stop_after_passed_verification.json"
    raw_dict = json.loads(fixture_path.read_text(encoding="utf-8"))

    envelope = RawHookEnvelope(
        event_type="Stop",
        correlation_id="corr-test-3",
        payload=raw_dict,
        client_timestamp_ms=3000,
    )

    event, warnings = normalize_antigravity_event(envelope, received_at_ms=3050)
    assert event is not None
    assert event.event_type == EventType.STOP
