"""Queue consumer acceptance: at-least-once delivery, retry, poison isolation and lifecycle."""

import logging
import threading

import pytest
from sqlalchemy import text


def test_poison_event_does_not_block_200_events(
    api_module, fresh_db, monkeypatch, caplog
):
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    token, user = mint_token(
        "poison@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, auth_token = resolve_token(token)
    user_id, device_id = user.id, auth_token.device_id
    store = get_shared_state()
    for n in range(200):
        store.publish(
            user_id,
            [
                {
                    "kind": "usage",
                    "user_id": user_id,
                    "device_id": device_id,
                    "fields": {
                        "n": n,
                        "model": "test-model",
                        "provider": "anthropic",
                        "endpoint": "test",
                        "prompt_tokens": 1,
                    },
                }
            ],
        )
    consumer = Consumer(store)
    process_usage = consumer._process

    def process(event):
        if event.payload["fields"].pop("n") == 73:
            raise ValueError("SECRET PROMPT")
        process_usage(event)

    monkeypatch.setattr(consumer, "_process", process)
    with caplog.at_level(logging.WARNING):
        assert consumer.drain() == 200
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM usage")).scalar_one() == 199
    assert "SECRET PROMPT" not in caplog.text
    assert "ValueError" in caplog.text
    assert store.status()[partition_for(user_id)].depth == 0


def test_graceful_close_finishes_and_acks_inflight_event(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)
    started, release = threading.Event(), threading.Event()
    threads = []

    def process(event):
        threads.append(threading.current_thread())
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(consumer, "_process", process)
    consumer.start()
    closer = threading.Thread(target=consumer.close)
    try:
        assert started.wait(5)
        closer.start()
        assert consumer.stop_event.wait(5)
        assert store.status()[partition_for("user")].depth == 2
        assert closer.is_alive()
        release.set()
        closer.join(5)
        assert not closer.is_alive()
        assert threads[0] is not threading.main_thread()
        assert len(threads) == 1
        status = store.status()[partition_for("user")]
        assert status.depth == 1
        assert status.owner is None
    finally:
        release.set()
        consumer.close()
        closer.join(5)


def test_tool_and_usage_correlate_across_consumers(api_module, fresh_db):
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    token, user = mint_token(
        "tools@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, auth_token = resolve_token(token)
    store = get_shared_state()

    def enqueue(values):
        record = {
            "timeUnixNano": "1000000000",
            "attributes": [
                {
                    "key": key,
                    "value": {
                        "intValue" if isinstance(value, int) else "stringValue": value
                    },
                }
                for key, value in values.items()
            ],
        }
        store.publish(
            user.id,
            [
                {
                    "kind": "otlp",
                    "user_id": user.id,
                    "device_id": auth_token.device_id,
                    "record": [record, "opencode", "session"],
                }
            ],
        )

    enqueue(
        {
            "event.name": "opencode.tool_decision",
            "message.id": "message",
            "call_id": "tool",
            "tool_name": "Bash",
        }
    )
    first = Consumer(store)
    assert first.drain() == 1
    enqueue(
        {
            "event.name": "opencode.message_completed",
            "message.id": "message",
            "model": "test-model",
            "input_token_count": 10,
            "output_token_count": 5,
        }
    )
    second = Consumer(type(store)(store.path))
    assert second.drain() == 1
    assert second.drain() == 0
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage")).scalar_one() == 1
        assert (
            conn.execute(
                text("SELECT count(*) FROM tool_calls WHERE usage_id IS NOT NULL")
            ).scalar_one()
            == 1
        )


def test_server_status_reports_queue_without_http(
    api_module, monkeypatch, tmp_path, capsys
):
    from src import ops
    from src.shared_state import get_shared_state, partition_for

    conf = tmp_path / "supervisord.conf"
    conf.touch()
    monkeypatch.setattr(ops, "_conf", lambda: conf)
    monkeypatch.setattr(ops, "_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(ops, "_ctl", lambda *a: None)
    store = get_shared_state()
    store.publish("status-user", [{}])
    store.claim(partition_for("status-user"), "test-owner")
    assert ops.status() == 0
    output = capsys.readouterr().out
    assert "depth=1" in output
    assert "lag=" in output
    assert "owner=test-owner" in output


def test_standalone_server_config_does_not_import_runtime(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from src.config.server_config import load_server_config; assert load_server_config().api_port == 4001; assert 'src.config.app' not in sys.modules",
        ],
        env={**os.environ, "TOKENAGE_CONFIG": str(tmp_path / "missing.yaml")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_transient_db_failure_keeps_event_pending(api_module, fresh_db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)

    def fail(event):
        raise OperationalError("", {}, Exception("connection interrupted"))

    monkeypatch.setattr(consumer, "_process", fail)
    assert consumer.drain() == 0
    assert store.status()[partition_for("user")].owner is None
    assert store.status()[partition_for("user")].depth == 1
    monkeypatch.setattr(consumer, "_process", lambda event: None)
    assert consumer.drain() == 1


def test_queued_otlp_contains_only_parser_metadata(api_module, fresh_db):
    import json

    from fastapi.testclient import TestClient

    from src.auth.tokens import mint_token
    from src.shared_state import get_shared_state, partition_for

    token, user = mint_token(
        "private@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    record = {
        "body": {"stringValue": "SECRET PROMPT"},
        "attributes": [
            {"key": "prompt", "value": {"stringValue": "SECRET PROMPT"}},
            {"key": "tool_parameters", "value": {"stringValue": "SECRET COMMAND"}},
            {"key": "prompt_length", "value": {"intValue": 13}},
            {"key": "event.name", "value": {"stringValue": "user_prompt"}},
        ],
    }
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "claude-code"}}
                    ]
                },
                "scopeLogs": [{"logRecords": [record]}],
            }
        ]
    }
    response = TestClient(api_module.app).post(
        "/otlp/v1/logs", json=body, headers={"x-tokenage-token": token}
    )
    assert response.status_code == 200
    queued = get_shared_state().read(partition_for(user.id))
    assert len(queued) == 1
    assert "SECRET" not in json.dumps(queued[0].payload)
    assert queued[0].payload["record"][0]["attributes"][0] == {
        "key": "prompt_length",
        "value": {"intValue": 13},
    }


@pytest.mark.parametrize("error_name", ["QueueFull", "StoreUnavailable"])
@pytest.mark.parametrize("path", ["/ingest/v1/usage", "/otlp/v1/logs"])
def test_queue_unavailable_returns_retryable_503(
    api_module, fresh_db, monkeypatch, error_name, path
):
    from fastapi.testclient import TestClient

    from src import shared_state
    from src.auth.tokens import mint_token
    from src.shared_state import get_shared_state

    token, _ = mint_token(
        "busy-usage@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    def full(*args):
        raise getattr(shared_state, error_name)()

    monkeypatch.setattr(get_shared_state(), "publish", full)
    from tests.test_otlp import _minimal_otlp_body

    response = TestClient(api_module.app).post(
        path,
        json={"provider": "test", "model": "test-model", "endpoint": "test"}
        if path == "/ingest/v1/usage"
        else _minimal_otlp_body(),
        headers={"x-tokenage-token": token},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


@pytest.mark.parametrize(
    "error_name",
    ["OperationalError", "DisconnectionError", "InterfaceError", "TimeoutError"],
)
@pytest.mark.parametrize(
    "app_name,path",
    [
        ("api", "/ingest/v1/usage"),
        ("api", "/otlp/v1/logs"),
        ("api", "/usage"),
        ("otlp", "/v1/logs"),
    ],
)
def test_auth_db_failure_returns_retryable_503(
    api_module, fresh_db, monkeypatch, error_name, app_name, path
):
    from fastapi.testclient import TestClient
    from sqlalchemy import exc

    from src import otlp
    from src.auth import tokens
    from tests.test_otlp import _minimal_otlp_body

    token, _ = tokens.mint_token(
        "outage@example.com",
        kind="cli" if path == "/usage" else "ingest",
        db_path=fresh_db.db_path,
    )
    error_type = getattr(exc, error_name)
    error = (
        error_type("SELECT secret", {}, Exception("private connection details"))
        if issubclass(error_type, exc.DBAPIError)
        else error_type("private connection details")
    )

    def unavailable(*args):
        raise error

    monkeypatch.setattr(tokens, "get_engine", unavailable)
    client = TestClient(api_module.app if app_name == "api" else otlp.app)
    response = (
        client.get(path, headers={"Authorization": f"Bearer {token}"})
        if path == "/usage"
        else client.post(
            path,
            json={"provider": "test", "model": "test-model", "endpoint": "test"}
            if path == "/ingest/v1/usage"
            else _minimal_otlp_body(),
            headers={"x-tokenage-token": token},
        )
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "secret" not in response.text
    assert "private" not in response.text


@pytest.mark.parametrize("path", ["/usage", "/ingest/v1/usage", "/otlp/v1/logs"])
def test_rate_store_unavailable_returns_503(api_module, fresh_db, monkeypatch, path):
    from fastapi.testclient import TestClient

    from src.auth.tokens import mint_token
    from src.shared_state import StoreUnavailable, get_shared_state

    token, _ = mint_token(
        "rate@example.com",
        kind="cli" if path == "/usage" else "ingest",
        db_path=fresh_db.db_path,
    )

    def unavailable(*args):
        raise StoreUnavailable()

    monkeypatch.setattr(get_shared_state(), "rate_limit", unavailable)
    client = TestClient(api_module.app)
    response = (
        client.get(path, headers={"Authorization": f"Bearer {token}"})
        if path == "/usage"
        else client.post(
            path,
            json={"provider": "test", "model": "test-model", "endpoint": "test"},
            headers={"x-tokenage-token": token},
        )
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_failed_partition_does_not_starve_later_partition(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer
    from src.shared_state import StoreUnavailable, get_shared_state, partition_for

    store = get_shared_state()
    users = sorted({str(i) for i in range(100)}, key=partition_for)
    first, last = users[0], users[-1]
    assert partition_for(first) < partition_for(last)
    for user in (first, last):
        store.publish(user, [{"kind": "usage", "user_id": user}])
    consumer = Consumer(store)
    processed = []

    def process(event):
        if event.payload["user_id"] == first:
            raise StoreUnavailable()
        processed.append(event)

    monkeypatch.setattr(consumer, "_process", process)
    assert consumer.drain() == 1
    assert len(processed) == 1
    assert store.status()[partition_for(first)].depth == 1


def _queue_otlp(store, user_id, device_id, values, service="opencode"):
    records = []
    for fields in values:
        records.append(
            [
                {
                    "timeUnixNano": "1776384000000000000",
                    "attributes": [
                        {
                            "key": key,
                            "value": {
                                "intValue"
                                if isinstance(value, int)
                                else "stringValue": value
                            },
                        }
                        for key, value in fields.items()
                    ],
                },
                service,
                "retry-session",
            ]
        )
    store.publish(
        user_id,
        [
            {
                "kind": "otlp",
                "user_id": user_id,
                "device_id": device_id,
                "record": record,
            }
            for record in records
        ],
    )


def test_otlp_transient_failure_keeps_failed_record(api_module, fresh_db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    import src.otlp as otlp
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    token, user = mint_token(
        "record-retry@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, auth = resolve_token(token)
    store = get_shared_state()
    _queue_otlp(
        store,
        user.id,
        auth.device_id,
        [
            {
                "event.name": "opencode.message_completed",
                "message.id": str(n),
                "model": f"retry-{n}",
                "input_token_count": 10,
                "output_token_count": 5,
            }
            for n in range(2)
        ],
    )
    original = otlp.record_usage
    attempts = []

    def flaky(**fields):
        attempts.append(fields["model"])
        if attempts == ["retry-0", "retry-1"]:
            raise OperationalError("", {}, Exception("temporary failure"))
        return original(**fields)

    monkeypatch.setattr(otlp, "record_usage", flaky)
    consumer = Consumer(store)
    assert consumer.drain() == 1
    assert store.status()[partition_for(user.id)].depth == 1
    assert consumer.drain() == 1
    assert attempts == ["retry-0", "retry-1", "retry-1"]
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage")).scalar_one() == 2
        assert (
            conn.execute(
                text("SELECT sum(request_count) FROM usage_hourly")
            ).scalar_one()
            == 2
        )
        for column in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "total_cost_usd",
        ):
            usage = conn.execute(text(f"SELECT sum({column}) FROM usage")).scalar_one()
            hourly = conn.execute(
                text(f"SELECT sum({column}) FROM usage_hourly")
            ).scalar_one()
            assert usage == hourly


@pytest.mark.parametrize("service", ["opencode", "codex_cli_rs", "claude-code"])
def test_correlation_survives_transient_usage_failure(
    api_module, fresh_db, monkeypatch, service
):
    from sqlalchemy.exc import OperationalError

    import src.otlp as otlp
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    token, user = mint_token(
        "correlation-retry@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, auth = resolve_token(token)
    store = get_shared_state()
    correlation = {
        "message.id": "message",
        "conversation.id": "conversation",
        "prompt.id": "prompt",
    }
    tool_event = {
        "opencode": "opencode.tool_decision",
        "codex_cli_rs": "codex.tool_decision",
        "claude-code": "tool_decision",
    }[service]
    events = [
        {
            **correlation,
            "event.name": tool_event,
            "call_id": "tool",
            "tool_use_id": "tool",
            "tool_name": "Bash",
        }
    ]
    if service != "opencode":
        events.append(
            {
                **correlation,
                "event.name": "user_prompt"
                if service == "claude-code"
                else "codex.user_prompt",
                "prompt_length": 42,
            }
        )
    if service == "codex_cli_rs":
        events.append(
            {**correlation, "event.name": "codex.api_request", "duration_ms": 123}
        )
    _queue_otlp(store, user.id, auth.device_id, events, service)
    consumer = Consumer(store)
    assert consumer.drain() == len(events)
    usage_event = {
        "opencode": "opencode.message_completed",
        "codex_cli_rs": "codex.sse_event",
        "claude-code": "api_request",
    }[service]
    _queue_otlp(
        store,
        user.id,
        auth.device_id,
        [
            {
                **correlation,
                "event.name": usage_event,
                "event.kind": "response.completed",
                "model": "test-model",
                "input_token_count": 10,
                "output_token_count": 5,
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ],
        service,
    )
    original = otlp.record_usage
    failed = False

    def flaky(**fields):
        nonlocal failed
        if not failed:
            failed = True
            raise OperationalError("", {}, Exception("temporary failure"))
        return original(**fields)

    monkeypatch.setattr(otlp, "record_usage", flaky)
    assert consumer.drain() == 0
    assert consumer.drain() == 1
    with fresh_db.database_module.get_engine().connect() as conn:
        row = conn.execute(text("SELECT id,prompt_length,latency_ms FROM usage")).one()
        # Claude associates a tool result with the last successful usage.
        if service != "claude-code":
            assert (
                conn.execute(
                    text("SELECT count(*) FROM tool_calls WHERE usage_id=:u"),
                    {"u": row.id},
                ).scalar_one()
                == 1
            )
        if service != "opencode":
            assert row.prompt_length == 42
        if service == "codex_cli_rs":
            assert row.latency_ms == 123
    if service == "claude-code":
        _queue_otlp(
            store,
            user.id,
            auth.device_id,
            [
                {
                    **correlation,
                    "event.name": "tool_result",
                    "tool_use_id": "tool",
                    "duration_ms": 12,
                }
            ],
            service,
        )
        assert consumer.drain() == 1
        with fresh_db.database_module.get_engine().connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM tool_calls WHERE usage_id=:u"),
                    {"u": row.id},
                ).scalar_one()
                == 1
            )


def test_drained_partition_is_available_to_new_consumer(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    partition = partition_for("user")
    store.publish("user", [{"kind": "test"}])
    first = Consumer(store)
    monkeypatch.setattr(first, "_process", lambda event: None)
    assert first.drain() == 1
    assert store.status()[partition].owner is None
    store.publish("user", [{"kind": "test"}])
    second = Consumer(store)
    seen = []
    monkeypatch.setattr(second, "_process", seen.append)
    assert second.drain() == 1
    assert len(seen) == 1
    assert store.status()[partition].depth == 0


def test_close_timeout_leaves_unfinished_event_pending(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)
    started, release = threading.Event(), threading.Event()

    def process(event):
        started.set()
        release.wait(5)

    monkeypatch.setattr(consumer, "_process", process)
    consumer.start()
    assert consumer.worker is not None
    join = consumer.worker.join
    waits = []
    monkeypatch.setattr(consumer.worker, "join", lambda timeout: waits.append(timeout))
    try:
        assert started.wait(5)
        consumer.close()
        assert waits == [3]
        status = store.status()[partition_for("user")]
        assert status.depth == 1
        assert status.owner == consumer.owner
    finally:
        release.set()
        join(5)
    assert not consumer.worker.is_alive()
    assert store.status()[partition_for("user")].depth == 0


def test_otlp_poison_is_skipped_without_body_in_logs(
    api_module, fresh_db, monkeypatch, caplog
):
    import src.otlp as otlp
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    _queue_otlp(store, "user", "device", [{}, {}])
    calls = []

    def parse(record, *args, **kwargs):
        calls.append(record)
        if len(calls) == 1:
            raise ValueError("SECRET BODY and SECRET TOKEN")

    monkeypatch.setattr(otlp, "_parse_log_record", parse)
    with caplog.at_level(logging.WARNING):
        assert Consumer(store).drain() == 2
    assert len(calls) == 2
    assert store.status()[partition_for("user")].depth == 0
    assert "ValueError" in caplog.text
    assert "kind=otlp" in caplog.text
    assert "SECRET" not in caplog.text


def test_pass_stops_at_deadline_and_next_pass_reclaims(
    api_module, fresh_db, monkeypatch
):
    from unittest.mock import Mock

    from src.ingest_queue import Consumer
    from src.shared_state import LEASE_SECONDS, get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}] * 4)
    now = 1000.0
    monkeypatch.setattr("src.ingest_queue.time.monotonic", lambda: now)
    consumer = Consumer(store)
    seen = []

    def process(event):
        nonlocal now
        seen.append(event.position)
        now += LEASE_SECONDS / 4

    ack = Mock(wraps=store.ack)
    monkeypatch.setattr(store, "ack", ack)
    monkeypatch.setattr(consumer, "_process", process)
    assert consumer.drain() == 2
    assert ack.call_count == 1
    status = store.status()[partition_for("user")]
    assert status.depth == 2
    assert status.owner is None
    assert consumer.drain() == 2
    assert len(set(seen)) == 4
    assert store.status()[partition_for("user")].depth == 0


def test_worker_stops_pass_after_lease_takeover(api_module, fresh_db, monkeypatch):
    import src.otlp as otlp
    from src.ingest_queue import Consumer
    from src.shared_state import LEASE_SECONDS, get_shared_state, partition_for

    store = get_shared_state()
    partition = partition_for("user")
    _queue_otlp(store, "user", "device", [{}, {}])
    now = 1000.0
    monkeypatch.setattr("src.ingest_queue.time.monotonic", lambda: now)
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    calls = []

    def parse(*args, **kwargs):
        nonlocal now
        calls.append(1)
        now += LEASE_SECONDS + 1
        assert store.claim(partition, "successor")

    monkeypatch.setattr(otlp, "_parse_log_record", parse)
    assert Consumer(store).drain() == 1
    assert calls == [1]
    assert store.status()[partition].depth == 1
    assert store.status()[partition].owner == "successor"


def test_status_handles_store_unavailable(api_module, monkeypatch, tmp_path, capsys):
    from src import ops
    from src.shared_state import StoreUnavailable, get_shared_state

    conf = tmp_path / "supervisord.conf"
    conf.touch()
    monkeypatch.setattr(ops, "_conf", lambda: conf)
    monkeypatch.setattr(ops, "_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(ops, "_ctl", lambda *args: None)

    def unavailable():
        raise StoreUnavailable()

    monkeypatch.setattr(get_shared_state(), "status", unavailable)
    assert ops.status() == 1
    assert "Queue unavailable (StoreUnavailable)" in capsys.readouterr().out


def test_ack_failure_keeps_events_pending_and_releases(api_module, monkeypatch):
    from src.ingest_queue import Consumer
    from src.shared_state import StoreUnavailable, get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)
    monkeypatch.setattr(consumer, "_process", lambda event: None)

    def unavailable(*args):
        raise StoreUnavailable()

    monkeypatch.setattr(store, "ack", unavailable)
    with pytest.raises(StoreUnavailable):
        consumer.drain()
    status = store.status()[partition_for("user")]
    assert status.depth == 1
    assert status.owner is None


def test_consumer_waits_after_transient_failure_then_resumes_polling(
    api_module, monkeypatch
):
    from src.ingest_queue import POLL_SECONDS, Consumer
    from src.shared_state import StoreUnavailable, get_shared_state

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)
    waits = []

    def process(event):
        if not waits:
            raise StoreUnavailable()

    def wait(delay):
        waits.append(delay)
        if len(waits) == 2:
            consumer.stop_event.set()

    monkeypatch.setattr(consumer, "_process", process)
    monkeypatch.setattr(consumer.stop_event, "wait", wait)
    consumer._run()
    assert waits == [5, POLL_SECONDS]


@pytest.mark.parametrize(
    "path,provider",
    [
        ("/auth/local/login?code=x", "local"),
        ("/auth/cli/exchange", "local"),
        ("/auth/google/callback?state=x", "google"),
    ],
)
def test_signin_store_unavailable_returns_503(api_module, monkeypatch, path, provider):
    from fastapi.testclient import TestClient

    from src.shared_state import StoreUnavailable, get_shared_state

    monkeypatch.setitem(api_module.CONFIG["auth"], "provider", provider)

    def unavailable(*args):
        raise StoreUnavailable()

    monkeypatch.setattr(get_shared_state(), "pop", unavailable)
    client = TestClient(api_module.app)
    response = (
        client.post(
            path, json={"code": "x", "code_verifier": "x", "installation_key": "a" * 43}
        )
        if path == "/auth/cli/exchange"
        else client.get(path)
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
