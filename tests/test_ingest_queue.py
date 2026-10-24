"""Queue consumer acceptance: at-least-once delivery, retry, poison isolation and lifecycle."""

import logging
import threading

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _no_pass_deadline(api_module, monkeypatch):
    # Exact drain counts must not depend on runner speed.
    monkeypatch.setattr("src.ingest_queue.PASS_SECONDS", 3600)
    monkeypatch.setattr("src.ingest_queue.SQLITE_PASS_SECONDS", 3600)


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
    from src.shared_state import SharedMapping

    correlation = SharedMapping("batch-test")

    def process(event, session):
        n = event.payload["fields"].pop("n")
        process_usage(event, session)
        correlation[str(n)] = n
        if n == 73:
            # Restore an earlier event's value as well as dropping new keys.
            correlation.pop("72")
            raise ValueError("SECRET PROMPT")

    monkeypatch.setattr(consumer, "_process", process)
    with caplog.at_level(logging.WARNING):
        assert consumer.drain() == 200
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM usage")).scalar_one() == 199
    assert correlation.get("73") is None
    assert correlation["72"] == 72
    assert correlation["199"] == 199
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

    def process(event, session):
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


@pytest.mark.parametrize("backend", ["sqlite", "redis"])
def test_server_status_reports_queue_without_http(
    api_module, monkeypatch, tmp_path, capsys, backend, redis_store_factory
):
    from src import ops
    from src.shared_state import get_shared_state, partition_for

    conf = tmp_path / "supervisord.conf"
    conf.touch()
    monkeypatch.setattr(ops, "_conf", lambda: conf)
    monkeypatch.setattr(ops, "_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(ops, "_ctl", lambda *a: None)
    if backend == "redis":
        import src.shared_state as shared_state

        monkeypatch.setattr(shared_state, "get_shared_state", redis_store_factory)
    store = get_shared_state() if backend == "sqlite" else redis_store_factory()
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

    def fail(event, session):
        raise OperationalError("", {}, Exception("connection interrupted"))

    monkeypatch.setattr(consumer, "_process", fail)
    assert consumer.drain() == 0
    assert store.status()[partition_for("user")].owner is None
    assert store.status()[partition_for("user")].depth == 1
    monkeypatch.setattr(consumer, "_process", lambda event, session: None)
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
    api_module, fresh_db, monkeypatch, caplog, error_name, app_name, path
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
    assert ("src.ingest_queue", logging.WARNING, f"Returning 503: {error_name}") in (
        caplog.record_tuples
    )
    assert "SELECT secret" not in caplog.text
    assert "private connection details" not in caplog.text


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

    def process(event, session):
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
            for n in range(100)
        ],
    )
    original = otlp.record_usage
    attempts = []

    from src.shared_state import SharedMapping

    correlation = SharedMapping("transient-test")

    def flaky(**fields):
        correlation[fields["model"]] = True
        attempts.append(fields["model"])
        if len(attempts) == 50:
            raise OperationalError("", {}, Exception("temporary failure"))
        return original(**fields)

    monkeypatch.setattr(otlp, "record_usage", flaky)
    consumer = Consumer(store)
    assert consumer.drain() == 0
    assert store.status()[partition_for(user.id)].depth == 100
    assert correlation.get("retry-0") is None
    assert correlation.get("retry-49") is None
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage")).scalar_one() == 0
    assert consumer.drain() == 100
    assert attempts == [f"retry-{n}" for n in range(50)] + [
        f"retry-{n}" for n in range(100)
    ]
    assert correlation["retry-99"] is True
    with fresh_db.database_module.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage")).scalar_one() == 100
        assert (
            conn.execute(
                text("SELECT sum(request_count) FROM usage_hourly")
            ).scalar_one()
            == 100
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
@pytest.mark.parametrize("same_pass", [False, True])
def test_correlation_survives_transient_usage_failure(
    api_module, fresh_db, monkeypatch, service, same_pass
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
    if not same_pass:
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
    if same_pass and service == "claude-code":
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
    assert consumer.drain() == (
        len(events) + 1 + (service == "claude-code") if same_pass else 1
    )
    with fresh_db.database_module.get_engine().connect() as conn:
        row = conn.execute(text("SELECT id,prompt_length,latency_ms FROM usage")).one()
        # Claude associates a tool result with the last successful usage.
        if service != "claude-code" or same_pass:
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
    monkeypatch.setattr(first, "_process", lambda event, session: None)
    assert first.drain() == 1
    assert store.status()[partition].owner is None
    store.publish("user", [{"kind": "test"}])
    second = Consumer(store)
    seen = []
    monkeypatch.setattr(second, "_process", lambda event, session: seen.append(event))
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

    def process(event, session):
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

    monkeypatch.setattr("src.ingest_queue.PASS_SECONDS", 0.05)
    monkeypatch.setattr("src.ingest_queue.SQLITE_PASS_SECONDS", 0.05)
    from src.ingest_queue import PASS_SECONDS
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}] * 4)
    now = 1000.0
    monkeypatch.setattr("src.ingest_queue.time.monotonic", lambda: now)
    consumer = Consumer(store)
    seen = []

    def process(event, session):
        nonlocal now
        seen.append(event.position)
        now += PASS_SECONDS / 2

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
    monkeypatch.setattr("src.ingest_queue.PASS_SECONDS", 1)
    monkeypatch.setattr("src.ingest_queue.SQLITE_PASS_SECONDS", 0.05)
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
    monkeypatch.setattr(consumer, "_process", lambda event, session: None)

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
    from src.ingest_queue import POLL_SECONDS, SQLITE_PAUSE_SECONDS, Consumer
    from src.shared_state import StoreUnavailable, get_shared_state

    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}])
    consumer = Consumer(store)
    waits = []

    def process(event, session):
        if not waits:
            raise StoreUnavailable()

    def wait(delay):
        waits.append(delay)
        if len(waits) == 5:
            consumer.stop_event.set()

    monkeypatch.setattr(consumer, "_process", process)
    monkeypatch.setattr(consumer.stop_event, "wait", wait)
    consumer._run()
    assert waits == [SQLITE_PAUSE_SECONDS, 5, SQLITE_PAUSE_SECONDS, 0, POLL_SECONDS]


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


@pytest.mark.parametrize("fail_commit", [False, True])
def test_pass_commits_once_before_correlation_and_ack(
    api_module, fresh_db, monkeypatch, fail_commit
):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from src.ingest_queue import Consumer
    from src.shared_state import SharedMapping, get_shared_state, partition_for

    store = get_shared_state()
    _queue_otlp(
        store,
        "user",
        "device",
        [
            {
                "event.name": "opencode.message_completed",
                "message.id": str(n),
                "model": "test-model",
                "input_token_count": n + 1,
                "output_token_count": 5,
            }
            for n in range(5)
        ],
    )
    engine = fresh_db.database_module.get_engine()
    commits = []

    def committed(connection):
        commits.append(1)

    event.listen(engine, "commit", committed)
    correlation = SharedMapping("commit-test")
    consumer = Consumer(store)
    process = consumer._process

    def write(event, session):
        process(event, session)
        correlation["written"] = True

    monkeypatch.setattr(consumer, "_process", write)
    ack = store.ack

    def acknowledge(partition, position):
        assert commits == [1]
        assert correlation["written"] is True
        ack(partition, position)

    monkeypatch.setattr(store, "ack", acknowledge)
    if fail_commit:

        def fail(session):
            raise OperationalError("SECRET SQL", {}, Exception("SECRET VALUE"))

        monkeypatch.setattr(Session, "commit", fail)
    try:
        assert consumer.drain() == (0 if fail_commit else 5)
    finally:
        event.remove(engine, "commit", committed)
    assert commits == ([] if fail_commit else [1])
    assert correlation.get("written") == (None if fail_commit else True)
    assert store.status()[partition_for("user")].depth == (5 if fail_commit else 0)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage")).scalar_one() == (
            0 if fail_commit else 5
        )


def test_batched_fractional_cost_rollups_equal_stored_rows(
    api_module, fresh_db, monkeypatch
):
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database.models import SessionRecord, Usage, UsageHourly
    from src.ingest_queue import Consumer
    from src.pricing import costs
    from src.pricing.models import ModelCost
    from src.shared_state import get_shared_state

    monkeypatch.setitem(
        costs.MODEL_COSTS,
        "batch-tiny",
        ModelCost(input=0.06, output=0.14, cache_read=0.02),
    )
    store = get_shared_state()
    store.publish(
        "user",
        [
            {
                "kind": "usage",
                "user_id": "user",
                "device_id": "device",
                "fields": {
                    "provider": "test",
                    "model": "batch-tiny",
                    "endpoint": "test",
                    "client_source": "test",
                    "session_id": f"batch-{n % 2}",
                    "prompt_tokens": n + 1,
                    "completion_tokens": n % 3,
                    "total_tokens": n + 1 + n % 3,
                    "cached_tokens": n % 2,
                    "status": 200 if n % 4 else 500,
                },
            }
            for n in range(50)
        ],
    )
    assert Consumer(store).drain() == 50
    with Session(fresh_db.database_module.get_engine()) as session:
        rows = session.scalars(select(Usage)).all()
        hourly = session.scalars(select(UsageHourly)).all()
        sessions = session.scalars(select(SessionRecord)).all()
        for column in (
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "total_tokens",
            "total_cost_usd",
        ):
            total = sum(getattr(row, column) or 0 for row in rows)
            assert sum(getattr(row, column) for row in hourly) == total
            assert sum(getattr(row, column) for row in sessions) == total
        for column in ("input_cost_usd", "output_cost_usd"):
            assert sum(getattr(row, column) for row in hourly) == sum(
                getattr(row, column) for row in rows
            )
        assert sum(row.request_count for row in hourly) == len(rows)
        assert sum(row.request_count for row in sessions) == len(rows)
        assert sum(row.total_cost_usd for row in rows) > Decimal(0)


def test_sqlite_yields_to_api_writer_between_partitions(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import SQLITE_PAUSE_SECONDS, Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    users = ["user"]
    users.append(
        next(
            str(n) for n in range(100) if partition_for(str(n)) != partition_for("user")
        )
    )
    for user in users:
        store.publish(user, [{"kind": "usage", "user_id": user}])
    engine = fresh_db.database_module.get_engine()
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite writer scheduling")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE api_writes (value INTEGER)"))
    consumer = Consumer(store)
    writing = threading.Event()
    written = threading.Event()
    writer = None
    pauses = []

    def api_write():
        writing.set()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO api_writes VALUES (1)"))
        written.set()

    def process(event, session):
        nonlocal writer
        if writer is None:
            writer = threading.Thread(target=api_write, daemon=True)
            writer.start()
            assert writing.wait(1)
        else:
            assert written.is_set()

    def pause(seconds):
        pauses.append(seconds)
        assert written.wait(2)

    monkeypatch.setattr(consumer, "_process", process)
    monkeypatch.setattr(consumer.stop_event, "wait", pause)
    try:
        assert consumer.drain() == 2
        assert pauses == [SQLITE_PAUSE_SECONDS] * 2
    finally:
        if writer is not None:
            writer.join(2)
    assert written.is_set()


def test_sqlite_worker_continues_backlog_before_idle_poll(api_module, monkeypatch):
    from types import SimpleNamespace

    from src.ingest_queue import POLL_SECONDS, SQLITE_PAUSE_SECONDS, Consumer
    from src.shared_state import get_shared_state, partition_for

    monkeypatch.setattr("src.ingest_queue.SQLITE_PASS_SECONDS", 0.05)
    store = get_shared_state()
    store.publish("user", [{"kind": "usage", "user_id": "user"}] * 4)
    consumer = Consumer(store)
    now = 1000.0
    monkeypatch.setattr("src.ingest_queue.time", SimpleNamespace(monotonic=lambda: now))
    waits = []

    def process(event, session):
        nonlocal now
        now += 0.03

    def wait(delay):
        depth = store.status()[partition_for("user")].depth
        waits.append((delay, depth))
        if delay == POLL_SECONDS:
            consumer.stop_event.set()

    monkeypatch.setattr(consumer, "_process", process)
    monkeypatch.setattr(consumer.stop_event, "wait", wait)
    consumer._run()
    assert waits == [
        (SQLITE_PAUSE_SECONDS, 2),
        (0, 2),
        (SQLITE_PAUSE_SECONDS, 0),
        (0, 0),
        (POLL_SECONDS, 0),
    ]
