"""The client proxy: routing, forwarding, usage extraction, reports, ports, config."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from client.proxy import config as proxy_config
from client.proxy import report as proxy_report
from client.proxy import runtime
from client.proxy.app import (
    NoProvider,
    build_forward_headers,
    build_upstream_url,
    create_app,
    resolve_provider,
)
from client.proxy.config import ProviderConfig, parse_proxy_config
from client.proxy.usage import (
    compute_prompt_length,
    extract_usage,
    find_stream_usage,
    parse_client_source,
)

RAW = {
    "port": 4000,
    "providers": {
        "Test-Provider": {
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-up",
            "models": ["test-model"],
        },
        "Claude": {
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant",
            "auth_scheme": "x-api-key",
            "models": {"claude-x": {}},
        },
        "Off": {
            "enabled": False,
            "base_url": "https://off.example.com",
            "models": ["off-model"],
        },
    },
}


@pytest.fixture
def cfg():
    return parse_proxy_config(RAW)


class Upstream:
    """A mock provider that records what it was sent."""

    def __init__(self, respond):
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


def make_client(cfg, upstream, reports):
    app = create_app(cfg, sink=reports.append, transport=httpx.MockTransport(upstream))
    return TestClient(app)


def ok_json(request):
    return httpx.Response(
        200,
        json={
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [
                {"message": {"tool_calls": [{"id": "t1", "function": {"name": "ls"}}]}}
            ],
        },
    )


# ---------------------------------------------------------------- routes/config


def test_registers_v1_and_compatibility_paths(cfg):
    app = create_app(cfg)
    post = {r.path for r in app.routes if "POST" in getattr(r, "methods", set())}
    get = {r.path for r in app.routes if "GET" in getattr(r, "methods", set())}
    assert {
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/responses",
        "/responses",
        "/v1/messages",
        "/messages",
    } <= post
    assert {
        "/api/v1/models",
        "/v1/models",
        "/models",
        "/v1/models/{model_id:path}",
        "/models/{model_id:path}",
    } <= get
    assert not {"/props", "/config/refresh"} & (post | get)


def test_routing_by_model_prefix_and_disabled(cfg):
    assert resolve_provider(cfg, "test-model")[0].name == "test-provider"
    assert resolve_provider(cfg, "claude-x")[0].name == "claude"  # mapping form
    p, up = resolve_provider(cfg, "TEST-PROVIDER/gpt-4.1-mini")
    assert (p.name, up) == ("test-provider", "gpt-4.1-mini")
    p, up = resolve_provider(cfg, "test-provider.gpt-4.1-mini")
    assert (p.name, up) == ("test-provider", "gpt-4.1-mini")
    for model in ("missing", "off-model", "off/anything"):
        with pytest.raises(NoProvider):
            resolve_provider(cfg, model)


def test_exact_mapping_beats_prefix_stripping():
    c = parse_proxy_config(
        {
            "providers": {
                "p": {"base_url": "https://p", "models": ["p/special"]},
                "q": {"base_url": "https://q", "models": ["x"]},
            }
        }
    )
    assert resolve_provider(c, "p/special")[1] == "p/special"


def test_models_endpoints(cfg):
    client = TestClient(create_app(cfg))
    ids = {m["id"]: m["owned_by"] for m in client.get("/v1/models").json()["data"]}
    assert ids == {"test-model": "test-provider", "claude-x": "claude"}
    assert client.get("/v1/models/test-model").json()["owned_by"] == "test-provider"
    assert client.get("/models/missing").status_code == 404


@pytest.mark.parametrize(
    ("base_url", "path", "expected"),
    [
        (
            "https://a.com/v1/",
            "/v1/chat/completions",
            "https://a.com/v1/chat/completions",
        ),
        ("https://a.com/openai", "v1/responses", "https://a.com/openai/responses"),
        ("https://a.com", "/messages", "https://a.com/messages"),
    ],
)
def test_build_upstream_url(base_url, path, expected):
    assert build_upstream_url(base_url, path) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "x",
        {"port": "4000"},
        {"port": 0},
        {"providers": []},
        {"providers": {"p": {}}},
        {"providers": {"p": {"base_url": "https://p", "auth_scheme": "basic"}}},
        {"providers": {"p": {"base_url": "https://p", "models": "m"}}},
    ],
)
def test_invalid_proxy_config_rejected(raw):
    with pytest.raises(ValueError):
        parse_proxy_config(raw)


def test_api_key_never_in_repr():
    assert "sk-up" not in repr(parse_proxy_config(RAW))


# ----------------------------------------------------------------- headers


def test_forward_headers_never_pass_inbound_credentials():
    inbound = {
        "authorization": "Bearer llmt_ingest_secret",
        "x-api-key": "llmt_ingest_secret",
        "X-Tokenage-Token": "tokenage_ingest_secret",
        "anthropic-version": "2023-06-01",
        "host": "localhost:4000",
        "content-length": "9",
    }
    bearer = ProviderConfig("p", "https://up", api_key="sk-up")
    assert build_forward_headers(inbound, bearer) == {
        "anthropic-version": "2023-06-01",
        "authorization": "Bearer sk-up",
    }
    native = ProviderConfig("p", "https://up", api_key="sk-up", auth_scheme="x-api-key")
    assert build_forward_headers(inbound, native) == {
        "anthropic-version": "2023-06-01",
        "x-api-key": "sk-up",
    }
    keyless = ProviderConfig("p", "https://up")
    assert build_forward_headers(inbound, keyless) == {
        "anthropic-version": "2023-06-01"
    }


def test_end_to_end_headers_and_model_rewrite(cfg):
    up, reports = Upstream(ok_json), []
    client = make_client(cfg, up, reports)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "test-provider/gpt-x"},
        headers={"authorization": "Bearer inbound", "x-api-key": "inbound"},
    )
    assert r.status_code == 200
    sent = up.requests[0]
    assert str(sent.url) == "https://api.example.com/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer sk-up"
    assert "x-api-key" not in sent.headers
    assert json.loads(sent.content)["model"] == "gpt-x"


def test_anthropic_scheme_end_to_end(cfg):
    up, reports = Upstream(ok_json), []
    make_client(cfg, up, reports).post("/v1/messages", json={"model": "claude-x"})
    sent = up.requests[0]
    assert sent.headers["x-api-key"] == "sk-ant"
    assert "authorization" not in sent.headers


def test_unknown_model_is_404_and_not_forwarded(cfg):
    up, reports = Upstream(ok_json), []
    r = make_client(cfg, up, reports).post(
        "/v1/chat/completions", json={"model": "nope"}
    )
    assert r.status_code == 404
    assert not up.requests and not reports


def test_disabled_provider_not_forwarded(cfg):
    up, reports = Upstream(ok_json), []
    r = make_client(cfg, up, reports).post(
        "/v1/chat/completions", json={"model": "off-model"}
    )
    assert r.status_code == 404 and not up.requests


# ------------------------------------------------------------------ reports


def test_non_streaming_report_fields(cfg):
    reports = []
    client = make_client(cfg, Upstream(ok_json), reports)
    client.post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"user-agent": "opencode/1.0"},
    )
    (rep,) = reports
    assert (rep.provider, rep.model, rep.endpoint) == (
        "test-provider",
        "test-model",
        "/v1/chat/completions",
    )
    assert rep.base_url == "https://api.example.com/v1"
    assert rep.client_source == "opencode"
    assert (rep.prompt_tokens, rep.completion_tokens, rep.total_tokens) == (10, 5, 15)
    assert rep.prompt_length == 5
    assert rep.status == 200 and rep.ttft_ms is None and rep.latency_ms >= 0
    assert [(t.tool_use_id, t.tool_name) for t in rep.tool_calls] == [("t1", "ls")]
    assert "hello" not in rep.model_dump_json()


def test_upstream_error_relayed_and_reported(cfg):
    reports = []
    up = Upstream(lambda r: httpx.Response(429, content=b"\xff"))
    r = make_client(cfg, up, reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    assert r.status_code == 429 and r.json() == {"error": "�"}
    assert reports[0].status == 429


def sse(*events: dict) -> bytes:
    return (
        b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
        + b"data: [DONE]\n\n"
    )


def split_stream(data: bytes, size: int):
    async def gen():
        for i in range(0, len(data), size):
            yield data[i : i + size]

    return gen()


def test_streaming_passthrough_split_chunks_anthropic_merge(cfg):
    data = sse(
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 7, "cache_read_input_tokens": 3}},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu1", "name": "bash"},
        },
        {"type": "message_delta", "usage": {"output_tokens": 9}},
    )
    up = Upstream(lambda r: httpx.Response(200, content=split_stream(data, 5)))
    reports = []
    r = make_client(cfg, up, reports).post(
        "/v1/messages", json={"model": "claude-x", "stream": True}
    )
    assert r.content == data  # byte-for-byte passthrough
    assert "stream_options" not in json.loads(up.requests[0].content)
    (rep,) = reports
    assert (rep.prompt_tokens, rep.cached_tokens, rep.completion_tokens) == (10, 3, 9)
    assert rep.ttft_ms is not None
    assert [(t.tool_use_id, t.tool_name) for t in rep.tool_calls] == [("tu1", "bash")]


@pytest.mark.parametrize("path", ["/v1/responses", "/responses"])
@pytest.mark.parametrize("chunk_size", [None, 3])
def test_streaming_responses_usage_and_prompt_length(cfg, path, chunk_size):
    data = sse(
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "call_id": "tc1", "name": "bash"},
        },
        {"type": "response.function_call_arguments.delta", "delta": '{"cmd":"ls"}'},
        {"type": "response.output_text.delta", "delta": "hello"},
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 10, "output_tokens": 5}},
        },
    )
    # Coalesced events must reach final usage even after string-valued deltas.
    up = Upstream(
        lambda r: httpx.Response(
            200, content=data if chunk_size is None else split_stream(data, chunk_size)
        )
    )
    reports = []
    response = make_client(cfg, up, reports).post(
        path,
        json={
            "model": "test-model",
            "stream": True,
            "input": [{"role": "user", "content": "abcd"}],
        },
    )
    assert response.status_code == 200 and response.content == data
    (report,) = reports
    assert (report.prompt_tokens, report.completion_tokens, report.total_tokens) == (
        10,
        5,
        15,
    )
    assert report.prompt_length == 4
    assert [(t.tool_use_id, t.tool_name) for t in report.tool_calls] == [
        ("tc1", "bash")
    ]


def test_chat_completions_stream_requests_usage_but_keeps_caller_options(cfg):
    up = Upstream(lambda r: httpx.Response(200, content=b"data: [DONE]\n\n"))
    client = make_client(cfg, up, [])
    body = {"model": "test-model", "stream": True}
    client.post("/v1/chat/completions", json=body)
    own = {"include_usage": False}
    client.post("/v1/chat/completions", json={**body, "stream_options": own})
    sent = [json.loads(r.content)["stream_options"] for r in up.requests]
    assert sent == [{"include_usage": True}, own]


def test_streaming_upstream_error_is_relayed(cfg):
    up = Upstream(lambda r: httpx.Response(500, content=b"bad gateway"))
    reports = []
    r = make_client(cfg, up, reports).post(
        "/v1/chat/completions", json={"model": "test-model", "stream": True}
    )
    assert r.status_code == 500 and r.json() == {"error": "bad gateway"}


def test_sink_failure_does_not_fail_the_model_call(cfg):
    def boom(report):
        raise RuntimeError("server down")

    app = create_app(cfg, sink=boom, transport=httpx.MockTransport(Upstream(ok_json)))
    r = TestClient(app).post("/v1/chat/completions", json={"model": "test-model"})
    assert r.status_code == 200 and r.json()["usage"]["prompt_tokens"] == 10


def test_unusable_upstream_usage_drops_report_not_call(cfg):
    up = Upstream(lambda r: httpx.Response(200, json={"usage": {"prompt_tokens": "x"}}))
    reports = []
    r = make_client(cfg, up, reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    assert r.status_code == 200 and not reports


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TOKENAGE_HOME", str(home))
    (home / "credentials.json").write_text(
        json.dumps({"server_url": "https://srv.example/", "ingest_token": "ing-tok"})
    )
    return home


def test_post_report_sends_ingest_token_header(cfg, signed_in, monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(proxy_report.httpx, "post", fake_post)
    reports = []
    make_client(cfg, Upstream(ok_json), reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    proxy_report.post_report(reports[0])
    ((url, kw),) = calls
    assert url == "https://srv.example/ingest/v1/usage"
    assert kw["headers"] == {"x-tokenage-token": "ing-tok"}
    assert (
        kw["json"]["provider"] == "test-provider" and kw["json"]["prompt_tokens"] == 10
    )
    assert "event_id" not in kw["json"]


def test_post_report_failure_logs_without_secrets_and_never_retries(
    cfg, signed_in, monkeypatch, caplog
):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        raise httpx.ConnectError("boom ing-tok")

    monkeypatch.setattr(proxy_report.httpx, "post", fake_post)
    reports = []
    make_client(cfg, Upstream(ok_json), reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    proxy_report.post_report(reports[0])  # must not raise
    assert len(calls) == 1
    assert "ing-tok" not in caplog.text


@pytest.mark.parametrize("status", [429, 503])
def test_post_report_retries_backpressure_after_retry_after(
    cfg, signed_in, monkeypatch, caplog, status
):
    statuses = iter([status, status, 204])
    sleeps = []

    def fake_post(url, **kw):
        return httpx.Response(
            next(statuses),
            headers={"retry-after": "7"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(proxy_report.httpx, "post", fake_post)
    monkeypatch.setattr(proxy_report.time, "sleep", sleeps.append)
    reports = []
    make_client(cfg, Upstream(ok_json), reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    proxy_report.post_report(reports[0])
    assert sleeps == [7.0, 7.0]
    assert "dropped" not in caplog.text


def test_retry_delay_honors_http_date_and_falls_back_on_garbage():
    from email.utils import formatdate

    def delay(value):
        resp = httpx.Response(429, headers={"retry-after": value})
        return proxy_report._retry_delay(resp)

    assert 15 <= delay(formatdate(time.time() + 20, usegmt=True)) <= 21
    assert delay(formatdate(time.time() - 100, usegmt=True)) == 1.0  # past → floor
    assert delay("not a date") == 5.0


@pytest.mark.parametrize("status", [429, 503])
def test_post_report_gives_up_after_bounded_backpressure_retries(
    cfg, signed_in, monkeypatch, caplog, status
):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(proxy_report.httpx, "post", fake_post)
    monkeypatch.setattr(proxy_report.time, "sleep", lambda s: None)
    reports = []
    make_client(cfg, Upstream(ok_json), reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    proxy_report.post_report(reports[0])
    assert len(calls) == proxy_report.RATE_LIMIT_RETRIES + 1
    assert "rate limited" in caplog.text


def test_post_report_dropped_when_signed_out(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path))
    monkeypatch.setattr(
        proxy_report.httpx, "post", lambda *a, **k: pytest.fail("posted")
    )
    reports = []
    make_client(cfg, Upstream(ok_json), reports).post(
        "/v1/chat/completions", json={"model": "test-model"}
    )
    proxy_report.post_report(reports[0])


# ------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("ua", "expected"),
    [
        ("opencode/1.14.24 ai-sdk/provider-utils/4.0.23", "opencode"),
        ("my-wrapper/1.0 anthropic-sdk/0.1", "my-wrapper"),
        ("codex-tui/0.124.0 (Mac OS)", "codex"),
        ("curl/8.7.1", "curl"),
        ("plain-token", None),
        ("", None),
        ("gemini-cli/1.0", None),
    ],
)
def test_parse_client_source(ua, expected):
    assert parse_client_source(ua) == expected


def test_compute_prompt_length():
    assert compute_prompt_length({}) == 0
    msgs = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "Bye"}, {"type": "image"}],
        },
    ]
    assert compute_prompt_length({"messages": msgs}) == 3
    assert compute_prompt_length({"messages": msgs[:2]}) == 0
    assert compute_prompt_length({"input": [{"role": "user", "content": "abc"}]}) == 3


# --------------------------------------------------------------- client.yaml


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path))
    return tmp_path


def write_yaml(home: Path, text: str, mode: int = 0o600) -> Path:
    path = home / "client.yaml"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_no_client_yaml_or_proxy_section_means_disabled(home):
    assert proxy_config.load_proxy_config() is None
    write_yaml(home, "something: else\n")
    assert proxy_config.load_proxy_config() is None
    handle = runtime.start_proxy()
    assert handle.state["state"] == "disabled"
    assert runtime.read_proxy_state()["state"] == "disabled"


def test_loads_client_yaml(home):
    write_yaml(
        home,
        "proxy:\n  port: 4321\n  providers:\n    DeepSeek:\n"
        "      base_url: https://api.deepseek.com\n      api_key: sk-1\n"
        "      models: [a, b]\n",
    )
    c = proxy_config.load_proxy_config()
    assert c.port == 4321 and set(c.models) == {"a", "b"}
    assert c.providers["deepseek"].api_key == "sk-1"


def test_loose_permissions_warn_but_load(home, caplog):
    write_yaml(home, "proxy:\n  providers: {}\n", mode=0o644)
    with caplog.at_level("WARNING", logger="tokenage.client.proxy"):
        assert proxy_config.load_proxy_config() is not None
    assert "chmod 600" in caplog.text
    caplog.clear()
    write_yaml(home, "proxy:\n  providers: {}\n", mode=0o600)
    with caplog.at_level("WARNING", logger="tokenage.client.proxy"):
        proxy_config.load_proxy_config()
    assert "chmod" not in caplog.text


def test_invalid_yaml_records_failed_state(home):
    write_yaml(home, "proxy: [unclosed\n")
    handle = runtime.start_proxy()
    assert handle.state["state"] == "failed" and "invalid YAML" in handle.state["error"]


# --------------------------------------------------------------------- ports


def occupy(port: int = 0) -> socket.socket:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen()
    return s


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_explicit_port_binds_or_fails_never_scans():
    port = free_port()
    runtime.bind_proxy_socket(port, None).close()
    blocker = occupy()
    try:
        busy = blocker.getsockname()[1]
        with pytest.raises(
            runtime.PortError, match=f"port {busy} \\(from client.yaml\\) is in use"
        ):
            runtime.bind_proxy_socket(busy, None)
    finally:
        blocker.close()


def test_recorded_port_preferred_then_scan_upward(monkeypatch):
    monkeypatch.setattr(runtime, "DEFAULT_PORT", free_port())
    recorded = free_port()
    sock = runtime.bind_proxy_socket(None, recorded)
    assert sock.getsockname()[1] == recorded
    sock.close()
    # recorded port busy -> default; default busy -> next one up
    blocker = occupy(recorded)
    first = occupy(runtime.DEFAULT_PORT)
    try:
        sock = runtime.bind_proxy_socket(None, recorded)
        got = sock.getsockname()[1]  # next free one up, whichever that is
        assert got not in (recorded, runtime.DEFAULT_PORT)
        assert runtime.DEFAULT_PORT < got < runtime.DEFAULT_PORT + runtime.SCAN_LIMIT
        sock.close()
    finally:
        blocker.close()
        first.close()


def test_start_proxy_serves_records_state_and_stops(home, monkeypatch):
    port = free_port()
    c = parse_proxy_config(
        {"port": port, "providers": {"p": {"base_url": "https://p", "models": ["m"]}}}
    )
    handle = runtime.start_proxy(c)
    try:
        assert handle.state == {
            "state": "running",
            "port": port,
            "url": f"http://127.0.0.1:{port}",
            "error": None,
        }
        assert runtime.read_proxy_state() == {**handle.state, "pid": os.getpid()}
        data = httpx.get(f"http://127.0.0.1:{port}/v1/models").json()
        assert data["data"][0]["id"] == "m"
    finally:
        handle.stop()


def test_shutdown_closes_the_shared_upstream_client(cfg):
    closed = []

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)

    # A list sink: the default sink posts to the real server in ~/.tokenage.
    app = create_app(cfg, sink=[].append, transport=Transport(Upstream(ok_json)))
    with TestClient(app) as c:
        c.post("/v1/chat/completions", json={"model": "test-model"})
        c.post("/v1/chat/completions", json={"model": "test-model"})
        assert not closed
    assert closed == [True]


def test_start_proxy_port_in_use_records_failed_without_raising(home):
    blocker = occupy()
    try:
        busy = blocker.getsockname()[1]
        c = parse_proxy_config({"port": busy, "providers": {}})
        handle = runtime.start_proxy(c)
        assert handle.state["state"] == "failed"
        assert handle.state["error"] == f"port {busy} (from client.yaml) is in use"
        assert runtime.read_proxy_state()["state"] == "failed"
    finally:
        blocker.close()


def test_proxy_state_keeps_service_keys_and_survives_service_writes(home):
    from client import service

    service._write_state(
        {"last_check_at": 1, "last_report_at": None, "last_report_status": None}
    )
    runtime.record_proxy_state(
        {"state": "running", "port": 4000, "url": "u", "error": None}
    )
    assert service._read_state()["last_check_at"] == 1
    service._write_state(
        {"last_check_at": 2, "last_report_at": None, "last_report_status": None}
    )
    state = service._read_state()
    assert state["last_check_at"] == 2 and state["proxy"]["port"] == 4000
    assert oct(os.stat(service.state_path()).st_mode & 0o777) == "0o600"


def test_extract_usage_supports_responses_format_and_details():
    usage = extract_usage(
        {
            "input_tokens": 11,
            "output_tokens": 7,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 5},
        }
    )

    assert usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "reasoning_tokens": 5,
        "cached_tokens": 3,
        "cache_creation_tokens": 0,
        "total_tokens": 18,
    }


def test_extract_usage_supports_chat_completion_format_and_explicit_total(
    utils_module,
):
    usage = extract_usage(
        {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens_details": {"reasoning_tokens": 6},
        }
    )

    assert usage == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "reasoning_tokens": 6,
        "cached_tokens": 4,
        "cache_creation_tokens": 0,
        "total_tokens": 25,
    }


def test_extract_usage_supports_anthropic_cache_creation_tokens():
    usage = extract_usage(
        {
            "input_tokens": 25,
            "output_tokens": 15,
            "cache_creation_input_tokens": 8,
            "cache_read_input_tokens": 3,
        }
    )

    # Anthropic's input_tokens excludes cache_read/cache_creation tokens (they're
    # disjoint buckets), so cache_read folds into prompt_tokens: 25 + 3 = 28.
    assert usage == {
        "prompt_tokens": 28,
        "completion_tokens": 15,
        "reasoning_tokens": 0,
        "cached_tokens": 3,
        "cache_creation_tokens": 8,
        "total_tokens": 51,
    }


def test_extract_usage_defaults_missing_values_to_zero():
    assert extract_usage({}) == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
    }


def test_find_stream_usage_merges_across_anthropic_events():
    """message_start carries input/cache tokens; message_delta carries the
    final output token count. Callers must merge the raw dicts, not treat
    each event's usage as a complete replacement."""
    raw_usage: dict = {}

    start = find_stream_usage(
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 25,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                    "output_tokens": 1,
                }
            },
        }
    )
    raw_usage.update(start)

    delta = find_stream_usage({"type": "message_delta", "usage": {"output_tokens": 15}})
    raw_usage.update(delta)

    merged = extract_usage(raw_usage)
    assert merged == {
        "prompt_tokens": 28,
        "completion_tokens": 15,
        "reasoning_tokens": 0,
        "cached_tokens": 3,
        "cache_creation_tokens": 8,
        "total_tokens": 51,
    }
