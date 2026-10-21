from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.ingest_queue import drain


def _ratelimit():
    import importlib

    return importlib.import_module("src.ratelimit")


def _attrs(values: dict[str, int | str]) -> list[dict]:
    attrs = []
    for key, value in values.items():
        if isinstance(value, int):
            payload = {"intValue": value}
        else:
            payload = {"stringValue": value}
        attrs.append({"key": key, "value": payload})
    return attrs


def _authed_client(otlp_module, fresh_db):
    from src.auth.tokens import mint_token

    token, _ = mint_token("test@example.com", kind="ingest", db_path=fresh_db.db_path)
    return TestClient(otlp_module.app, headers={"x-tokenage-token": token})


def _capture_usage(target: dict):
    return lambda **fields: target.update({"usage": SimpleNamespace(**fields)})


def test_health_routes_return_service_status(otlp_module):
    client = TestClient(otlp_module.app)

    for path in ("/health", "/"):
        response = client.get(path)

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "tokenage-otlp"}


def test_retired_gemini_logs_are_ignored_without_debug_dumps(
    otlp_module, monkeypatch, tmp_path: Path, fresh_db
):
    debug_file = tmp_path / "otlp-debug.json"
    captured = []
    monkeypatch.setattr(otlp_module, "CODEX_DEBUG_FILE", str(debug_file))
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: captured.append(fields),
    )

    response = _authed_client(otlp_module, fresh_db).post(
        "/v1/logs",
        json={
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "gemini-cli"},
                            }
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "attributes": _attrs(
                                        {
                                            "event.name": "gemini_cli.api_response",
                                            "model": "gemini-3-flash-preview",
                                        }
                                    )
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )
    drain()

    assert response.status_code == 200
    assert captured == []
    assert not debug_file.exists()
    assert not Path(f"{debug_file}.resource").exists()


def test_extract_claude_fields_basic(otlp_module):
    attrs = _attrs(
        {
            "input_tokens": 80,
            "output_tokens": 40,
            "cache_read_tokens": 20,
            "cache_creation_tokens": 10,
            "model": "claude-sonnet-4-20250514",
            "status_code": 200,
            "duration_ms": 300,
            "session.id": "claude-sess-1",
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}

    fields = otlp_module._extract_claude_fields(
        record, attrs, "sess-1", user_id="test-user"
    )

    assert fields["model"] == "claude-sonnet-4-20250514"
    assert fields["prompt_tokens"] == 100
    assert fields["completion_tokens"] == 40
    assert fields["cached_tokens"] == 20
    assert fields["cache_creation_tokens"] == 10
    assert fields["client_source"] == "claude-code"
    assert fields["session_id"] == "claude-sess-1"


def test_extract_codex_fields_basic(otlp_module):
    attrs = _attrs(
        {
            "event.kind": "response.completed",
            "input_token_count": 200,
            "output_token_count": 100,
            "cached_token_count": 50,
            "reasoning_token_count": 30,
            "tool_token_count": 10,
            "duration_ms": 800,
            "http.response.status_code": 200,
            "model": "o3",
            "conversation.id": "codex-conv-1",
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}
    otlp_module.codex_state["test-user:codex-conv-1"] = {
        "ts": 0,
        "duration_ms": 800,
        "ttft_ms": 200,
    }

    fields = otlp_module._extract_codex_fields(
        record, attrs, "codex_cli_rs", user_id="test-user"
    )

    assert fields["model"] == "o3"
    assert fields["prompt_tokens"] == 200
    assert fields["completion_tokens"] == 100
    assert fields["cached_tokens"] == 50
    assert fields["reasoning_tokens"] == 30
    assert fields["tool_tokens"] == 10
    assert fields["latency_ms"] == 800
    assert fields["ttft_ms"] == 200
    assert fields["client_source"] == "codex"


def test_extract_codex_fields_prefers_inline_ttft(otlp_module):
    attrs = _attrs(
        {
            "event.kind": "response.completed",
            "input_token_count": 16268,
            "output_token_count": 5,
            "cached_token_count": 11008,
            "ttft_ms": 794,
            "conversation.id": "codex-conv-2",
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}
    otlp_module.codex_state["test-user:codex-conv-2"] = {"ts": 0, "duration_ms": 1800}

    fields = otlp_module._extract_codex_fields(
        record, attrs, "codex-app-server", user_id="test-user"
    )

    assert fields["ttft_ms"] == 794
    assert fields["latency_ms"] == 1800


def test_extract_opencode_fields_basic(otlp_module):
    attrs = _attrs(
        {
            "input_token_count": 150,
            "output_token_count": 80,
            "reasoning_token_count": 20,
            "cached_token_count": 30,
            "cache_creation_token_count": 5,
            "total_token_count": 285,
            "prompt_length": 4321,
            "model": "claude-sonnet-4-5",
            "provider": "anthropic",
            "duration_ms": 1200,
            "ttft_ms": 345,
            "session.id": "oc-sess-1",
            "message.id": "msg-1",
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}

    fields = otlp_module._extract_opencode_fields(
        record, attrs, "oc-sess-1", user_id="test-user"
    )

    assert fields["model"] == "claude-sonnet-4-5"
    assert fields["provider"] == "anthropic"
    assert fields["prompt_tokens"] == 180
    assert fields["completion_tokens"] == 100
    assert fields["reasoning_tokens"] == 20
    assert fields["cached_tokens"] == 30
    assert fields["cache_creation_tokens"] == 5
    assert fields["total_tokens"] == 285  # prompt+completion+cache_creation
    assert fields["latency_ms"] == 1200
    assert fields["ttft_ms"] == 345
    assert fields["client_source"] == "opencode"
    assert fields["session_id"] == "oc-sess-1"
    assert fields["endpoint"] == "otlp"
    assert fields["tool_tokens"] is None
    assert fields["prompt_length"] == 4321


def test_extract_opencode_fields_normalizes_total_when_raw_total_excludes_cache(
    otlp_module,
):
    attrs = _attrs(
        {
            "input_token_count": 807_100,
            "output_token_count": 0,
            "reasoning_token_count": 0,
            "cached_token_count": 10_000_000,
            "total_token_count": 807_100,
            "model": "deepseek-v4-flash-free",
            "provider": "deepseek",
            "session.id": "oc-sess-cache",
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}

    fields = otlp_module._extract_opencode_fields(
        record, attrs, "oc-sess-cache", user_id="test-user"
    )

    assert fields["prompt_tokens"] == 10_807_100
    assert fields["cached_tokens"] == 10_000_000
    assert fields["total_tokens"] == 10_807_100


def test_parse_opencode_record_routes_to_record_usage(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    record = {"timeUnixNano": "1800000000000000000"}
    attrs = _attrs(
        {
            "event.name": "opencode.message_completed",
            "session.id": "oc-sess-1",
            "message.id": "msg-1",
            "model": "claude-sonnet-4-5",
            "provider": "anthropic",
            "input_token_count": 100,
            "output_token_count": 50,
            "reasoning_token_count": 10,
            "cached_token_count": 20,
            "total_token_count": 180,
            "duration_ms": 800,
            "ttft_ms": 250,
        }
    )
    record["attributes"] = attrs

    otlp_module._parse_log_record(
        record, "opencode", "oc-sess-1", user_id="test-user", device_id="test-device"
    )

    assert captured["usage"].client_source == "opencode"
    assert captured["usage"].model == "claude-sonnet-4-5"
    assert captured["usage"].provider == "anthropic"
    assert captured["usage"].prompt_tokens == 120
    assert captured["usage"].completion_tokens == 60
    assert captured["usage"].reasoning_tokens == 10
    assert captured["usage"].cached_tokens == 20
    assert captured["usage"].latency_ms == 800
    assert captured["usage"].ttft_ms == 250


def test_parse_opencode_record_uses_otlp_status_code(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    record = {"timeUnixNano": "1800000000000000000"}
    attrs = _attrs(
        {
            "event.name": "opencode.message_completed",
            "session.id": "oc-sess-1",
            "message.id": "msg-1",
            "model": "claude-sonnet-4-5",
            "provider": "anthropic",
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "duration_ms": 800,
            "status_code": 429,
        }
    )
    record["attributes"] = attrs

    otlp_module._parse_log_record(
        record, "opencode", "oc-sess-1", user_id="test-user", device_id="test-device"
    )

    assert captured["usage"].client_source == "opencode"
    assert captured["usage"].status == 429


def test_parse_opencode_record_uses_http_response_status_code(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    record = {"timeUnixNano": "1800000000000000000"}
    attrs = _attrs(
        {
            "event.name": "opencode.message_completed",
            "session.id": "oc-sess-1",
            "message.id": "msg-1",
            "model": "claude-sonnet-4-5",
            "provider": "anthropic",
            "duration_ms": 800,
            "http.response.status_code": 500,
        }
    )
    record["attributes"] = attrs

    otlp_module._parse_log_record(
        record, "opencode", "oc-sess-1", user_id="test-user", device_id="test-device"
    )

    assert captured["usage"].client_source == "opencode"
    assert captured["usage"].status == 500


def test_extract_opencode_fields_derives_total_with_reasoning_when_missing(
    otlp_module,
):
    attrs = _attrs(
        {
            "input_token_count": 150,
            "output_token_count": 80,
            "reasoning_token_count": 20,
            "cache_creation_token_count": 5,
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}

    fields = otlp_module._extract_opencode_fields(
        record, attrs, "oc-sess-1", user_id="test-user"
    )

    assert fields["total_tokens"] == 255  # prompt+completion+cache_creation
    assert fields["prompt_length"] == 0


def test_extract_opencode_fields_resolves_base_url_for_event_provider(
    otlp_module, isolated_home: Path
):
    config_path = isolated_home / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "anthropic": {"options": {"baseURL": "https://api.anthropic.com"}},
                    "openai": {"options": {"baseURL": "https://api.openai.com/v1"}},
                }
            }
        ),
        encoding="utf-8",
    )
    attrs = _attrs(
        {
            "provider": "openai",
            "model": "gpt-5.4",
            "input_token_count": 10,
            "output_token_count": 5,
        }
    )
    record = {"timeUnixNano": "1800000000000000000"}

    fields = otlp_module._extract_opencode_fields(
        record, attrs, "oc-sess-1", user_id="test-user"
    )

    assert fields["provider"] == "openai"
    assert fields["base_url"] == "https://api.openai.com/v1"
    assert fields["base_url_provider"] == "openai"
    assert fields["base_url_source"] == "opencode_config"


def test_prompt_length_tracker_records_and_consumes_matching_prompt_event(otlp_module):
    # This verifies the basic tracker contract: a prompt-only event stores the length,
    # and the later usage event for the same prompt/session consumes that exact value once.
    tracker = otlp_module.PromptLengthTracker({"codex.user_prompt"})

    prompt_attrs = _attrs(
        {
            "event.name": "codex.user_prompt",
            "session.id": "tracker-session-1",
            "prompt_id": "prompt-1",
            "prompt_length": 3210,
        }
    )
    tracker.record_prompt_event(
        "codex", prompt_attrs, "tracker-session-1", user_id="test-user"
    )

    response_attrs = _attrs(
        {
            "event.name": "codex.sse_event",
            "session.id": "tracker-session-1",
            "prompt_id": "prompt-1",
        }
    )

    assert (
        tracker.consume_for_usage_event(
            "codex", response_attrs, "tracker-session-1", user_id="test-user"
        )
        == 3210
    )
    assert (
        tracker.consume_for_usage_event(
            "codex", response_attrs, "tracker-session-1", user_id="test-user"
        )
        == 0
    )


def test_parse_claude_record_uses_prompt_length_from_prior_prompt_event(
    otlp_module, monkeypatch
):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    prompt_record = {
        "timeUnixNano": "0",
        "attributes": _attrs(
            {
                "event.name": "user_prompt",
                "session.id": "claude-session-1",
                "prompt.id": "prompt-1",
                "prompt_length": 2468,
            }
        ),
    }
    otlp_module._parse_log_record(
        prompt_record,
        "claude-code",
        "claude-session-1",
        user_id="test-user",
        device_id="test-device",
    )

    response_ts = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "api_request",
                "session.id": "claude-session-1",
                "prompt.id": "prompt-1",
                "model": "claude-test",
                "input_tokens": 120,
                "output_tokens": 20,
                "cache_read_tokens": 5,
                "cache_creation_tokens": 0,
                "duration_ms": 900,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "claude-code",
        "claude-session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].prompt_length == 2468
    assert captured["usage"].client_source == "claude-code"
    assert captured["usage"].session_id == "claude-session-1"
    assert captured["usage"].status is None


def test_parse_claude_record_uses_otlp_status_code(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))

    response_ts = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "api_request",
                "session.id": "claude-session-1",
                "prompt.id": "prompt-1",
                "model": "claude-test",
                "input_tokens": 120,
                "output_tokens": 20,
                "cache_read_tokens": 5,
                "cache_creation_tokens": 0,
                "duration_ms": 900,
                "status_code": 400,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "claude-code",
        "claude-session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].status == 400


def test_parse_codex_record_uses_prompt_length_from_prior_prompt_event(
    otlp_module, monkeypatch
):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    prompt_record = {
        "timeUnixNano": "0",
        "attributes": _attrs(
            {
                "event.name": "codex.user_prompt",
                "conversation.id": "conv-1",
                "model": "gpt-5.4",
                "prompt_length": 88,
            }
        ),
    }
    otlp_module._parse_log_record(
        prompt_record, "codex_cli_rs", "", user_id="test-user", device_id="test-device"
    )

    response_ts = datetime(2026, 4, 22, 21, 5, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "conv-1",
                "model": "gpt-5.4",
                "input_token_count": 500,
                "output_token_count": 100,
                "cached_token_count": 10,
                "reasoning_token_count": 20,
                "tool_token_count": 5,
                "duration_ms": 2000,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].prompt_length == 88
    assert captured["usage"].client_source == "codex"
    assert captured["usage"].session_id == "conv-1"
    assert captured["usage"].status is None


def test_parse_codex_exec_record_uses_same_usage_parser(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        _capture_usage(captured),
    )

    prompt_record = {
        "timeUnixNano": "0",
        "attributes": _attrs(
            {
                "event.name": "codex.user_prompt",
                "conversation.id": "exec-conv-1",
                "model": "gpt-5.5",
                "prompt_length": 42,
            }
        ),
    }
    otlp_module._parse_log_record(
        prompt_record, "codex_exec", "", user_id="test-user", device_id="test-device"
    )

    response_ts = datetime(2026, 5, 3, 17, 0, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "exec-conv-1",
                "model": "gpt-5.5",
                "input_token_count": 21742,
                "output_token_count": 6,
                "cached_token_count": 6528,
                "reasoning_token_count": 0,
                "duration_ms": 12338,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record, "codex_exec", "", user_id="test-user", device_id="test-device"
    )

    assert captured["usage"].client_source == "codex"
    assert captured["usage"].session_id == "exec-conv-1"
    assert captured["usage"].prompt_length == 42
    assert captured["usage"].prompt_tokens == 21742
    assert captured["usage"].completion_tokens == 6
    assert captured["usage"].cached_tokens == 6528


def test_parse_codex_app_server_record_uses_same_usage_parser(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))

    response_ts = datetime(2026, 9, 25, 6, 35, 12, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "app-server-conv-1",
                "model": "gpt-5.6-luna",
                "input_token_count": 20531,
                "output_token_count": 35,
                "cached_token_count": 11008,
                "reasoning_token_count": 19,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "codex-app-server",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].client_source == "codex"
    assert captured["usage"].session_id == "app-server-conv-1"
    assert captured["usage"].prompt_tokens == 20531
    assert captured["usage"].completion_tokens == 35


def test_codex_websocket_request_sets_request_latency(otlp_module, monkeypatch):
    """response.completed has no duration; subtract the send timestamp instead."""
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))
    otlp_module.codex_state.clear()

    otlp_module._parse_log_record(
        {
            "timeUnixNano": "0",
            "attributes": _attrs(
                {
                    "event.name": "codex.websocket_request",
                    "conversation.id": "conv-ws",
                    "event.timestamp": "2026-09-25T08:12:03.213Z",
                }
            ),
        },
        "codex_exec",
        "",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        {
            "timeUnixNano": "0",
            "attributes": _attrs(
                {
                    "event.name": "codex.sse_event",
                    "event.kind": "response.completed",
                    "conversation.id": "conv-ws",
                    "model": "gpt-6-luna",
                    "input_token_count": 100,
                    "output_token_count": 10,
                    "ttft_ms": 933,
                    "event.timestamp": "2026-09-25T08:12:05.215Z",
                }
            ),
        },
        "codex_exec",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].latency_ms == 2002
    assert captured["usage"].ttft_ms == 933
    # ts comes from event.timestamp, not the batch flush time.
    assert captured["usage"].ts == 1790323925215000


def test_codex_api_request_duration_sets_request_latency(otlp_module, monkeypatch):
    """HTTP mode: api_request carries the request's own duration."""
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))
    otlp_module.codex_state.clear()

    otlp_module._parse_log_record(
        {
            "timeUnixNano": "0",
            "attributes": _attrs(
                {
                    "event.name": "codex.api_request",
                    "conversation.id": "conv-http",
                    "endpoint": "/responses",
                    "duration_ms": 2533,
                }
            ),
        },
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        {
            "timeUnixNano": "0",
            "attributes": _attrs(
                {
                    "event.name": "codex.sse_event",
                    "event.kind": "response.completed",
                    "conversation.id": "conv-http",
                    "model": "gpt-5",
                    "input_token_count": 100,
                    "output_token_count": 10,
                }
            ),
        },
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].latency_ms == 2533


def test_codex_latency_stays_empty_without_request_start(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))
    otlp_module.codex_state.clear()

    otlp_module._parse_log_record(
        {
            "timeUnixNano": "0",
            "attributes": _attrs(
                {
                    "event.name": "codex.sse_event",
                    "event.kind": "response.completed",
                    "conversation.id": "conv-none",
                    "model": "gpt-5",
                    "input_token_count": 100,
                    "output_token_count": 10,
                }
            ),
        },
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].latency_ms is None


def test_usage_session_id_casts_codex_conversation_id_to_string(otlp_module):
    attrs = [{"key": "conversation.id", "value": {"intValue": "123"}}]

    assert (
        otlp_module._usage_session_id(
            service_name="codex_cli_rs",
            attrs=attrs,
            resource_session_id="ignored",
        )
        == "123"
    )


def test_parse_codex_record_persists_integer_conversation_id_as_string(
    otlp_module,
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))

    response_ts = datetime(2026, 4, 22, 21, 5, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": 123,
                "model": "gpt-5.4",
                "input_token_count": 500,
                "output_token_count": 100,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].session_id == "123"


def test_parse_codex_record_uses_http_response_status_code(otlp_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(otlp_module, "record_usage", _capture_usage(captured))

    response_ts = datetime(2026, 4, 22, 21, 5, 0, tzinfo=UTC)
    response_record = {
        "timeUnixNano": str(int(response_ts.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "conv-1",
                "model": "gpt-5.4",
                "input_token_count": 500,
                "output_token_count": 100,
                "cached_token_count": 10,
                "reasoning_token_count": 20,
                "tool_token_count": 5,
                "duration_ms": 2000,
                "http.response.status_code": 429,
            }
        ),
    }

    otlp_module._parse_log_record(
        response_record,
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured["usage"].status == 429


def test_inline_prompt_length_is_not_queued_for_next_request(otlp_module, monkeypatch):
    captured = []
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: captured.append(SimpleNamespace(**fields)),
    )

    response_ts_1 = datetime(2026, 4, 22, 21, 15, 0, tzinfo=UTC)
    response_record_1 = {
        "timeUnixNano": str(int(response_ts_1.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "conv-inline",
                "model": "gpt-5.4",
                "prompt_length": 999,
                "input_token_count": 700,
                "output_token_count": 90,
            }
        ),
    }
    otlp_module._parse_log_record(
        response_record_1,
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    response_ts_2 = datetime(2026, 4, 22, 21, 16, 0, tzinfo=UTC)
    response_record_2 = {
        "timeUnixNano": str(int(response_ts_2.timestamp() * 1_000_000_000)),
        "attributes": _attrs(
            {
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "conversation.id": "conv-inline",
                "model": "gpt-5.4",
                "input_token_count": 710,
                "output_token_count": 95,
            }
        ),
    }
    otlp_module._parse_log_record(
        response_record_2,
        "codex_cli_rs",
        "",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured[0].prompt_length == 999
    assert captured[1].prompt_length == 0


def test_claude_tool_decision_missing_tool_use_id_gets_random_fallback(otlp_module):
    """A missing tool_use_id must not fall back to a shared "" PK, which would
    collide across unrelated sessions and silently drop tool calls."""
    record = {
        "attributes": _attrs(
            {"event.name": "tool_decision", "prompt.id": "p1", "tool_name": "Bash"}
        ),
        "timeUnixNano": "1000000000",
    }

    otlp_module._parse_log_record(
        record, "claude-code", "session-1", user_id="test-user", device_id="test-device"
    )

    buffered = otlp_module._claude_tool_buffer["test-user:p1"]
    assert len(buffered) == 1
    assert buffered[0]["tool_use_id"]
    assert buffered[0]["tool_use_id"].startswith("generated:")


def test_claude_tool_decision_missing_tool_use_id_ids_are_unique(otlp_module):
    record = {
        "attributes": _attrs(
            {"event.name": "tool_decision", "prompt.id": "p1", "tool_name": "Bash"}
        ),
        "timeUnixNano": "1000000000",
    }

    otlp_module._parse_log_record(
        record, "claude-code", "session-1", user_id="test-user", device_id="test-device"
    )
    otlp_module._parse_log_record(
        record, "claude-code", "session-1", user_id="test-user", device_id="test-device"
    )

    ids = [
        entry["tool_use_id"]
        for entry in otlp_module._claude_tool_buffer["test-user:p1"]
    ]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_tool_buffers_are_scoped_by_authenticated_user(otlp_module):
    for user_id in ("user-a", "user-b"):
        record = {
            "attributes": _attrs(
                {
                    "event.name": "tool_decision",
                    "prompt.id": "shared-prompt",
                    "tool_name": "Bash",
                }
            ),
            "timeUnixNano": "1000000000",
        }
        otlp_module._parse_log_record(
            record, "claude-code", "session-1", user_id=user_id, device_id="test-device"
        )

    assert "user-a:shared-prompt" in otlp_module._claude_tool_buffer
    assert "user-b:shared-prompt" in otlp_module._claude_tool_buffer


def test_tool_buffer_entries_expire_after_ten_minutes(
    otlp_module, fresh_db, monkeypatch
):
    client = _authed_client(otlp_module, fresh_db)
    now = time.time()
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    otlp_module._claude_tool_buffer["test-user:stale"] = [
        {"tool_name": "bash", "tool_use_id": "toolu_1", "ts": int(now * 1000)}
    ]
    now += 601
    otlp_module._claude_tool_buffer["test-user:fresh"] = [
        {"tool_name": "bash", "tool_use_id": "toolu_2", "ts": int(now * 1000)}
    ]
    response = client.post("/v1/logs", json={})
    drain()
    assert response.status_code == 200
    assert "test-user:stale" not in otlp_module._claude_tool_buffer
    assert "test-user:fresh" in otlp_module._claude_tool_buffer


def _stub_tool_recording(otlp_module, monkeypatch, captured_tools: list) -> None:
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: SimpleNamespace(id="u1", session_id="session-1"),
    )
    monkeypatch.setattr(
        otlp_module, "record_tool_call", lambda **fields: captured_tools.append(fields)
    )


def test_claude_tool_result_duration_flows_to_record(otlp_module, monkeypatch):
    captured_tools: list[dict] = []
    _stub_tool_recording(otlp_module, monkeypatch, captured_tools)

    def record(event_name: str, **extra) -> dict:
        return {
            "attributes": _attrs(
                {"event.name": event_name, "prompt.id": "prompt-dur", **extra}
            ),
            "timeUnixNano": "1000000000",
        }

    otlp_module._parse_log_record(
        record(
            "tool_decision",
            tool_name="Bash",
            tool_use_id="tool-dur-1",
        ),
        "claude-code",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "tool_result",
            tool_name="Bash",
            tool_use_id="tool-dur-1",
            duration_ms=42,
        ),
        "claude-code",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "claude_code.api_request",
            input_tokens=10,
            output_tokens=5,
            status_code=200,
        ),
        "claude-code",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert len(captured_tools) == 1
    assert captured_tools[0]["tool_name"] == "Bash"
    assert captured_tools[0]["duration_ms"] == 42


def test_claude_tools_attach_to_the_request_that_produced_them(
    otlp_module, monkeypatch
):
    """prompt.id spans a whole turn: each request must own only its own tools."""
    captured_tools: list[dict] = []
    usage_ids = iter(["u1", "u2"])
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: SimpleNamespace(id=next(usage_ids), session_id="session-1"),
    )
    monkeypatch.setattr(
        otlp_module, "record_tool_call", lambda **fields: captured_tools.append(fields)
    )

    def record(event_name: str, ts_ms: int, **extra) -> dict:
        return {
            "attributes": _attrs(
                {"event.name": event_name, "prompt.id": "turn-1", **extra}
            ),
            "timeUnixNano": str(ts_ms * 1_000_000),
        }

    otlp_module._parse_log_record(
        record("tool_decision", 1000, tool_name="Bash", tool_use_id="t1"),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "claude_code.api_request",
            1010,
            input_tokens=10,
            output_tokens=5,
            duration_ms=500,
        ),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record("tool_result", 1050, tool_name="Bash", tool_use_id="t1", duration_ms=40),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record("tool_decision", 5000, tool_name="Read", tool_use_id="t2"),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "claude_code.api_request",
            5010,
            input_tokens=20,
            output_tokens=5,
            duration_ms=800,
        ),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record("tool_result", 5040, tool_name="Read", tool_use_id="t2", duration_ms=15),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )

    by_id = {t["tool_use_id"]: t for t in captured_tools}
    assert by_id["test-user:t1"]["usage_id"] == "u1"
    assert by_id["test-user:t1"]["duration_ms"] == 40
    assert by_id["test-user:t2"]["usage_id"] == "u2"
    assert by_id["test-user:t2"]["duration_ms"] == 15


def test_claude_result_less_tool_flushes_to_next_request(otlp_module, monkeypatch):
    """A decision with no tool_result (e.g. rejected) must still be recorded."""
    captured_tools: list[dict] = []
    usage_ids = iter(["u1", "u2"])
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: SimpleNamespace(id=next(usage_ids), session_id="session-1"),
    )
    monkeypatch.setattr(
        otlp_module, "record_tool_call", lambda **fields: captured_tools.append(fields)
    )

    def record(event_name: str, ts_ms: int, **extra) -> dict:
        return {
            "attributes": _attrs(
                {"event.name": event_name, "prompt.id": "turn-1", **extra}
            ),
            "timeUnixNano": str(ts_ms * 1_000_000),
        }

    otlp_module._parse_log_record(
        record("tool_decision", 1000, tool_name="Bash", tool_use_id="t1"),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "claude_code.api_request",
            1010,
            input_tokens=10,
            output_tokens=5,
            duration_ms=200,
        ),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "claude_code.api_request",
            5000,
            input_tokens=20,
            output_tokens=5,
            duration_ms=300,
        ),
        "claude-code",
        "s1",
        user_id="test-user",
        device_id="test-device",
    )

    by_id = {t["tool_use_id"]: t for t in captured_tools}
    assert by_id["test-user:t1"]["usage_id"] == "u2"
    assert by_id["test-user:t1"]["duration_ms"] is None


def test_codex_tool_result_duration_flows_to_record(otlp_module, monkeypatch):
    captured_tools: list[dict] = []
    _stub_tool_recording(otlp_module, monkeypatch, captured_tools)

    def record(event_name: str, **extra) -> dict:
        return {
            "attributes": _attrs(
                {
                    "event.name": event_name,
                    "conversation.id": "conv-dur",
                    **extra,
                }
            ),
            "timeUnixNano": "1000000000",
        }

    otlp_module._parse_log_record(
        record("codex.tool_decision", tool_name="exec_command", call_id="call-dur-1"),
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "codex.tool_result",
            tool_name="exec_command",
            call_id="call-dur-1",
            duration_ms=577,
        ),
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        record(
            "codex.sse_event",
            **{
                "event.kind": "response.completed",
                "input_token_count": 10,
                "output_token_count": 5,
                "http.response.status_code": 200,
            },
        ),
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert len(captured_tools) == 1
    assert captured_tools[0]["tool_name"] == "exec_command"
    assert captured_tools[0]["duration_ms"] == 577


def test_codex_tool_result_without_decision_is_not_recorded(otlp_module, monkeypatch):
    """Enriching by call_id must not add tools the decision stream skipped."""
    captured_tools: list[dict] = []
    duration_updates: list[dict] = []
    _stub_tool_recording(otlp_module, monkeypatch, captured_tools)
    monkeypatch.setattr(
        otlp_module,
        "update_tool_call_duration",
        lambda **fields: duration_updates.append(fields),
    )

    otlp_module._parse_log_record(
        {
            "attributes": _attrs(
                {
                    "event.name": "codex.tool_result",
                    "conversation.id": "conv-result-only",
                    "tool_name": "exec",
                    "call_id": "call-only-result",
                    "duration_ms": 12,
                }
            ),
            "timeUnixNano": "1000000000",
        },
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        {
            "attributes": _attrs(
                {
                    "event.name": "codex.sse_event",
                    "event.kind": "response.completed",
                    "conversation.id": "conv-result-only",
                    "input_token_count": 10,
                    "output_token_count": 5,
                    "http.response.status_code": 200,
                }
            ),
            "timeUnixNano": "2000000000",
        },
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert captured_tools == []
    assert duration_updates == [
        {
            "tool_use_id": "test-user:call-only-result",
            "user_id": "test-user",
            "client_source": "codex",
            "duration_ms": 12,
        }
    ]


def test_opencode_tool_decision_duration_flows_to_record(otlp_module, monkeypatch):
    captured_tools: list[dict] = []
    _stub_tool_recording(otlp_module, monkeypatch, captured_tools)

    otlp_module._parse_log_record(
        {
            "attributes": _attrs(
                {
                    "event.name": "opencode.tool_decision",
                    "message.id": "msg-dur",
                    "call_id": "call-oc-1",
                    "tool_name": "bash",
                    "duration_ms": 150,
                }
            ),
            "timeUnixNano": "1000000000",
        },
        "opencode",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )
    otlp_module._parse_log_record(
        {
            "attributes": _attrs(
                {
                    "event.name": "opencode.message_completed",
                    "message.id": "msg-dur",
                    "session.id": "session-1",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "input_token_count": 10,
                    "output_token_count": 5,
                }
            ),
            "timeUnixNano": "2000000000",
        },
        "opencode",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )

    assert len(captured_tools) == 1
    assert captured_tools[0]["tool_name"] == "bash"
    assert captured_tools[0]["duration_ms"] == 150


def test_codex_tool_event_uses_observed_timestamp_when_time_is_zero(otlp_module):
    """Codex tool events send timeUnixNano=0; using it verbatim makes the
    buffer entry look stale and it is evicted before the usage event lands."""
    record = {
        "attributes": _attrs(
            {
                "event.name": "codex.tool_decision",
                "conversation.id": "conv-observed",
                "tool_name": "exec_command",
                "call_id": "call-observed",
            }
        ),
        "timeUnixNano": "0",
        "observedTimeUnixNano": "1789976902511803910",
    }

    otlp_module._parse_log_record(
        record,
        "codex_cli_rs",
        "session-1",
        user_id="test-user",
        device_id="test-device",
    )

    entry = otlp_module._codex_tool_buffer["test-user:conv-observed"][0]
    assert entry["ts"] == 1789976902511


def test_claude_tool_decision_redelivery_does_not_duplicate_buffer(otlp_module):
    record = {
        "attributes": _attrs(
            {
                "event.name": "tool_decision",
                "prompt.id": "prompt-dup",
                "tool_name": "Bash",
                "tool_use_id": "tool-dup",
            }
        ),
        "timeUnixNano": "1000000000",
    }

    otlp_module._parse_log_record(
        record, "claude-code", "session-1", user_id="test-user", device_id="test-device"
    )
    otlp_module._parse_log_record(
        record, "claude-code", "session-1", user_id="test-user", device_id="test-device"
    )

    buffered = otlp_module._claude_tool_buffer["test-user:prompt-dup"]
    assert len(buffered) == 1


# === Auth tests ===


def _minimal_otlp_body(
    service_name="claude-code", event_name="claude_code.api_request"
):
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}},
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "attributes": _attrs(
                                    {
                                        "event.name": event_name,
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "model": "test-model",
                                        "status_code": 200,
                                    }
                                ),
                                "timeUnixNano": "1800000000000000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }


def test_auth_disabled_no_token_records_usage(otlp_module, monkeypatch, fresh_db):
    """auth.enabled=false: POST /v1/logs with no token header records usage."""
    captured = []
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: (
            captured.append(fields) or type("U", (), {"id": "u1", "session_id": "s1"})()
        ),
    )
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "local")

    client = _authed_client(otlp_module, fresh_db)
    response = client.post("/v1/logs", json=_minimal_otlp_body())
    drain()

    assert response.status_code == 200
    assert len(captured) == 1


def test_auth_enabled_valid_ingest_token_records_user_id(
    otlp_module, monkeypatch, fresh_db
):
    """auth.enabled=true, valid ingest token: recorded usage carries user_id."""
    from src.auth.tokens import mint_token

    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")
    monkeypatch.setitem(otlp_module.CONFIG.get("otlp", {}), "max_body_bytes", 2_000_000)

    token, user = mint_token(
        "test@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    captured = []

    def capture_usage(**fields):
        captured.append(fields)
        return type("U", (), {"id": "u1", "session_id": "s1"})()

    monkeypatch.setattr(otlp_module, "record_usage", capture_usage)

    client = TestClient(otlp_module.app)
    response = client.post(
        "/v1/logs",
        json=_minimal_otlp_body(),
        headers={"x-tokenage-token": token},
    )
    drain()

    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0]["user_id"] == user.id


def test_authenticated_otlp_tool_calls_keep_user_id(otlp_module, monkeypatch, fresh_db):
    from src.auth.tokens import mint_token

    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")
    token, user = mint_token(
        "test@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    captured_tools = []
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: SimpleNamespace(id="u1", session_id="session-1"),
    )
    monkeypatch.setattr(
        otlp_module, "record_tool_call", lambda **fields: captured_tools.append(fields)
    )

    body = _minimal_otlp_body()
    records = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    records[0]["attributes"] = _attrs(
        {
            "event.name": "tool_decision",
            "prompt.id": "prompt-1",
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "session.id": "session-1",
        }
    )
    records.append(
        {
            "attributes": _attrs(
                {
                    "event.name": "claude_code.api_request",
                    "prompt.id": "prompt-1",
                    "session.id": "session-1",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "status_code": 200,
                }
            ),
            "timeUnixNano": "1800000000000000000",
        }
    )
    records.append(
        {
            "attributes": _attrs(
                {
                    "event.name": "tool_result",
                    "prompt.id": "prompt-1",
                    "session.id": "session-1",
                    "tool_name": "Bash",
                    "tool_use_id": "tool-1",
                    "duration_ms": 12,
                }
            ),
            "timeUnixNano": "1800000000000000000",
        }
    )

    response = TestClient(otlp_module.app).post(
        "/v1/logs", json=body, headers={"x-tokenage-token": token}
    )
    drain()

    assert response.status_code == 200
    assert captured_tools[0]["user_id"] == user.id
    assert captured_tools[0]["tool_use_id"] == f"{user.id}:tool-1"
    assert captured_tools[0]["usage_id"] == "u1"
    assert captured_tools[0]["duration_ms"] == 12


def test_auth_lookup_timeout_returns_503(otlp_module, monkeypatch):
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")
    monkeypatch.setattr(_ratelimit(), "AUTH_LOOKUP_TIMEOUT_SECONDS", 0.01)

    def blocked_lookup(_request):
        time.sleep(0.05)

    monkeypatch.setattr(_ratelimit(), "resolve_ingest_user", blocked_lookup)

    response = TestClient(otlp_module.app).post("/v1/logs", json=_minimal_otlp_body())
    drain()

    assert response.status_code == 503
    assert response.json() == {"detail": "authentication unavailable"}


def test_auth_enabled_missing_header_returns_401(otlp_module, monkeypatch):
    """auth.enabled=true, missing header → 401, no row recorded."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")

    captured = []
    monkeypatch.setattr(
        otlp_module, "record_usage", lambda **fields: captured.append(fields)
    )

    client = TestClient(otlp_module.app)
    response = client.post("/v1/logs", json=_minimal_otlp_body())
    drain()

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert len(captured) == 0


def test_repeated_invalid_token_skips_db_lookup(otlp_module, monkeypatch):
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")
    _ratelimit()._invalid_token_cache.clear()
    lookups = []
    monkeypatch.setattr(
        _ratelimit(), "resolve_token", lambda token: lookups.append(token) or None
    )

    client = TestClient(otlp_module.app)
    headers = {"x-tokenage-token": "tokenage_ingest_invalid"}

    for _ in range(3):
        response = client.post("/v1/logs", json=_minimal_otlp_body(), headers=headers)
        drain()
        assert response.status_code == 401
    assert len(lookups) == 1


def test_auth_enabled_wrong_kind_token_returns_401(otlp_module, monkeypatch, fresh_db):
    """auth.enabled=true, valid cli token → 401 (kind mismatch)."""
    from src.auth.tokens import mint_token

    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")

    token, _ = mint_token("test@example.com", kind="cli", db_path=fresh_db.db_path)

    captured = []
    monkeypatch.setattr(
        otlp_module, "record_usage", lambda **fields: captured.append(fields)
    )

    client = TestClient(otlp_module.app)
    response = client.post(
        "/v1/logs",
        json=_minimal_otlp_body(),
        headers={"x-tokenage-token": token},
    )
    drain()

    assert response.status_code == 401
    assert len(captured) == 0


def test_auth_enabled_revoked_token_returns_401(otlp_module, monkeypatch, fresh_db):
    """auth.enabled=true, revoked ingest token → 401."""
    from src.auth.tokens import mint_token, revoke_token

    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")

    token, user = mint_token(
        "test@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    # Get token_id from the database
    from src.auth.tokens import resolve_token

    _, auth_token = resolve_token(token, db_path=fresh_db.db_path)
    revoke_token(auth_token.id, user.id, db_path=fresh_db.db_path)

    captured = []
    monkeypatch.setattr(
        otlp_module, "record_usage", lambda **fields: captured.append(fields)
    )

    client = TestClient(otlp_module.app)
    response = client.post(
        "/v1/logs",
        json=_minimal_otlp_body(),
        headers={"x-tokenage-token": token},
    )
    drain()

    assert response.status_code == 401
    assert len(captured) == 0


def test_body_too_large_returns_413(otlp_module, monkeypatch, fresh_db):
    """Body larger than otlp.max_body_bytes → 413; record_usage is never called."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "local")
    monkeypatch.setitem(otlp_module.CONFIG.get("otlp", {}), "max_body_bytes", 100)

    captured = []
    monkeypatch.setattr(
        otlp_module, "record_usage", lambda **fields: captured.append(fields)
    )

    client = _authed_client(otlp_module, fresh_db)
    # Create a body larger than 100 bytes
    large_body = _minimal_otlp_body()
    large_body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(
        {"key": "padding", "value": {"stringValue": "x" * 200}}
    )

    response = client.post("/v1/logs", json=large_body)
    drain()

    assert response.status_code == 413
    assert len(captured) == 0


def test_record_count_cap_limits_db_work_per_request(
    otlp_module, monkeypatch, fresh_db
):
    """A 2 MB body of many records must not run unbounded per-record DB work."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "local")
    monkeypatch.setitem(
        otlp_module.CONFIG.get("otlp", {}), "max_records_per_request", 2
    )

    duration_updates: list[dict] = []
    monkeypatch.setattr(
        otlp_module,
        "update_tool_call_duration",
        lambda **fields: duration_updates.append(fields),
    )
    monkeypatch.setattr(otlp_module, "record_usage", lambda **fields: None)

    body = _minimal_otlp_body(service_name="codex_cli_rs")
    scope_log = body["resourceLogs"][0]["scopeLogs"][0]
    for i in range(20):
        scope_log["logRecords"].append(
            {
                "attributes": _attrs(
                    {
                        "event.name": "codex.tool_result",
                        "conversation.id": "conv-cap",
                        "tool_name": "exec",
                        "call_id": f"call-cap-{i}",
                        "duration_ms": 10 + i,
                    }
                ),
                "timeUnixNano": "1800000000000000000",
            }
        )

    client = _authed_client(otlp_module, fresh_db)
    response = client.post("/v1/logs", json=body)
    drain()

    assert response.status_code == 200
    # 1 minimal claude record + 1 codex record processed, not all 21.
    assert len(duration_updates) == 1
    # The rest are reported, not silently dropped.
    assert response.json() == {
        "partialSuccess": {
            "rejectedLogRecords": 19,
            "errorMessage": "record count exceeds otlp.max_records_per_request",
        }
    }


def test_batch_within_cap_returns_no_partial_success(
    otlp_module, monkeypatch, fresh_db
):
    """A batch under the cap is a plain success, so clients see no rejection."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "local")
    monkeypatch.setattr(otlp_module, "record_usage", lambda **fields: None)

    client = _authed_client(otlp_module, fresh_db)
    response = client.post("/v1/logs", json=_minimal_otlp_body())
    drain()

    assert response.status_code == 200
    assert response.json() == {}


def test_body_too_large_streaming_cap_returns_413(otlp_module, monkeypatch, fresh_db):
    """Streaming body cap enforces limit when Content-Length is missing or understated."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "local")
    monkeypatch.setitem(otlp_module.CONFIG.get("otlp", {}), "max_body_bytes", 100)

    captured = []
    monkeypatch.setattr(
        otlp_module, "record_usage", lambda **fields: captured.append(fields)
    )

    client = _authed_client(otlp_module, fresh_db)
    large_body = _minimal_otlp_body()
    large_body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(
        {"key": "padding", "value": {"stringValue": "x" * 200}}
    )
    body_bytes = json.dumps(large_body).encode()

    # Send with no Content-Length (chunked transfer)
    response = client.post(
        "/v1/logs",
        content=body_bytes,
        headers={"Content-Type": "application/json"},
    )
    drain()

    assert response.status_code == 413
    assert len(captured) == 0


def test_rate_limit_exceeded_returns_429(otlp_module, monkeypatch, fresh_db):
    """N+1th request within the window → 429; different token unaffected."""
    from src.auth.tokens import mint_token

    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")
    monkeypatch.setitem(otlp_module.CONFIG["rate_limits"], "ingest_per_minute", 2)

    token1, user1 = mint_token(
        "user1@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    token2, user2 = mint_token(
        "user2@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    captured = []

    def capture_usage(**fields):
        captured.append(fields)
        return type("U", (), {"id": "u1", "session_id": "s1"})()

    monkeypatch.setattr(otlp_module, "record_usage", capture_usage)

    client = TestClient(otlp_module.app)

    # First two requests from token1 should succeed
    for _ in range(2):
        response = client.post(
            "/v1/logs",
            json=_minimal_otlp_body(),
            headers={"x-tokenage-token": token1},
        )
        drain()
        assert response.status_code == 200

    # Third request from token1 should be rate limited
    response = client.post(
        "/v1/logs",
        json=_minimal_otlp_body(),
        headers={"x-tokenage-token": token1},
    )
    drain()
    assert response.status_code == 429

    # token2 should still work
    response = client.post(
        "/v1/logs",
        json=_minimal_otlp_body(),
        headers={"x-tokenage-token": token2},
    )
    drain()
    assert response.status_code == 200


def test_health_routes_stay_unauthenticated(otlp_module, monkeypatch):
    """/health, /v1/metrics, /v1/traces stay unauthenticated regardless of auth.enabled."""
    monkeypatch.setitem(otlp_module.CONFIG.get("auth", {}), "provider", "google")

    client = TestClient(otlp_module.app)

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/v1/metrics", json={}).status_code == 200
    assert client.post("/v1/traces", json={}).status_code == 200


def test_logs_are_shed_when_too_many_batches_are_pending(
    otlp_module, fresh_db, monkeypatch
):
    from src.auth.tokens import mint_token

    token, user = mint_token(
        "busy@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    with store._connection(True) as conn:
        conn.executemany(
            "INSERT INTO events(partition,payload,created) VALUES (?, '{}', 1)",
            [(partition_for(user.id),)] * 10001,
        )

    response = TestClient(otlp_module.app).post(
        "/v1/logs", json=_minimal_otlp_body(), headers={"x-tokenage-token": token}
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert store.status()[partition_for(user.id)].depth == 10001


def test_logs_are_processed_off_the_event_loop(otlp_module, fresh_db, monkeypatch):
    """A record stuck in the database must not stop the collector answering."""
    import asyncio
    import threading

    import httpx

    from src.auth.tokens import mint_token

    token, _ = mint_token("loop@example.com", kind="ingest", db_path=fresh_db.db_path)
    started, release = threading.Event(), threading.Event()
    threads = []

    def blocked_record(*_args, **_kwargs):
        threads.append(threading.current_thread())
        started.set()
        release.wait(30)

    monkeypatch.setattr(otlp_module, "_parse_log_record", blocked_record)
    body = {
        "resourceLogs": [
            {
                "resource": {"attributes": _attrs({"service.name": "claude-code"})},
                "scopeLogs": [{"logRecords": [{"attributes": []}]}],
            }
        ]
    }

    async def run_inner():
        transport = httpx.ASGITransport(app=otlp_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://collector",
            headers={"x-tokenage-token": token},
        ) as client:
            post = asyncio.create_task(client.post("/v1/logs", json=body))
            try:
                await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
                assert (await asyncio.wait_for(post, timeout=2)).status_code == 200
                health = await asyncio.wait_for(client.get("/health"), timeout=2)
                assert health.status_code == 200
            finally:
                release.set()
            assert (await post).status_code == 200

    async def run():
        async with otlp_module.lifespan(otlp_module.app):
            await run_inner()

    asyncio.run(run())
    assert threads and threads[0] is not threading.main_thread()
