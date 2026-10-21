"""Queue consumer acceptance: fencing, replay, poison isolation and lifecycle."""

import logging
import threading

import pytest
from sqlalchemy import text


def test_poison_event_does_not_block_200_events(
    api_module, fresh_db, monkeypatch, caplog
):
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer, _offset
    from src.shared_state import get_shared_state, partition_for

    token, user = mint_token(
        "poison@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, auth_token = resolve_token(token)
    user_id, device_id = user.id, auth_token.device_id
    store = get_shared_state()
    last = None
    for n in range(200):
        last = store.publish(
            user_id,
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
            },
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
    with fresh_db.database_module.get_engine().connect() as conn:
        position = conn.execute(
            text("SELECT position FROM queue_positions WHERE partition=:p"),
            {"p": partition_for(user_id)},
        ).scalar_one()
    assert _offset(position) == last
    assert "SECRET PROMPT" not in caplog.text
    assert "ValueError" in caplog.text
    assert store.status()[partition_for(user_id)].depth == 0


def test_fence_rejects_stale_owner_before_successor_processes(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer, LeaseLost
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    partition = partition_for("user")
    first, second = Consumer(store), Consumer(store)
    assert store.claim(partition, first.owner)
    old = first._claim_position(partition)
    now += 11
    assert store.claim(partition, second.owner)
    second._claim_position(partition)
    # Even if a caller accidentally believes it still owns the lease, the
    # main DB's compare-and-swap rejects its old claim identity.
    monkeypatch.setattr(store, "renew", lambda *a, **kw: True)
    with pytest.raises(LeaseLost):
        first.commit_position(partition, old, "1")


def test_committed_redelivery_is_skipped(api_module, fresh_db, monkeypatch):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    position = store.publish("user", {"kind": "usage", "user_id": "user"})
    partition = partition_for("user")
    with fresh_db.database_module.get_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO queue_positions VALUES (:p,:v)"),
            {"p": partition, "v": position},
        )
    consumer = Consumer(store)
    monkeypatch.setattr(
        consumer, "_process", lambda _: pytest.fail("committed event redelivered")
    )
    assert consumer.drain() == 0
    assert store.status()[partition].depth == 0


def test_processing_runs_in_thread_and_close_does_not_wait(
    api_module, fresh_db, monkeypatch
):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    store = get_shared_state()
    store.publish("user", {"kind": "usage", "user_id": "user"})
    consumer = Consumer(store)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    threads = []

    def process(event):
        threads.append(threading.current_thread())
        started.set()
        release.wait(5)
        finished.set()

    monkeypatch.setattr(consumer, "_process", process)
    consumer.start()
    try:
        assert started.wait(5)
        consumer.close()
        assert consumer.stop_event.is_set()
        assert threads[0] is not threading.main_thread()
    finally:
        release.set()
        assert finished.wait(5)


def test_main_database_migration_adds_positions(fresh_db):
    db = fresh_db.database_module
    with db.get_engine().begin() as conn:
        conn.execute(text("DROP TABLE queue_positions"))
    applied = fresh_db.schema_migrations_module.migrate_database()
    assert "queue_positions" in applied
    assert "queue_positions" not in fresh_db.schema_migrations_module.migrate_database()


def test_tool_and_usage_survive_consumer_takeover(api_module, fresh_db):
    from src.auth import resolve_token
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

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
            {
                "kind": "otlp",
                "user_id": user.id,
                "device_id": auth_token.device_id,
                "records": [[record, "opencode", "session"]],
            },
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
    first._drop(partition_for(user.id))
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
    store.publish("status-user", {})
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

    from src.ingest_queue import Consumer, _offset
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    store.publish("user", {"kind": "usage", "user_id": "user"})
    consumer = Consumer(store)

    def fail(event):
        raise OperationalError("", {}, Exception("connection interrupted"))

    monkeypatch.setattr(consumer, "_process", fail)
    assert consumer.drain() == 0
    assert _offset(consumer.positions[partition_for("user")]) == "0"
    assert store.status()[partition_for("user")].depth == 1
    monkeypatch.setattr(consumer, "_process", lambda event: None)
    assert consumer.drain() == 1


def test_close_commits_already_finished_event(api_module, fresh_db, monkeypatch):
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    store = get_shared_state()
    store.publish("user", {"kind": "usage", "user_id": "user"})
    consumer = Consumer(store)
    monkeypatch.setattr(consumer, "_process", lambda event: consumer.close())
    assert consumer.drain() == 1
    assert sum(p.depth for p in store.status()) == 0


def test_replacement_store_does_not_trim_new_events(
    api_module, fresh_db, monkeypatch, tmp_path
):
    from src.ingest_queue import Consumer
    from src.shared_state import SQLiteSharedState, get_shared_state

    first_store = get_shared_state()
    first_store.publish("user", {"kind": "usage", "user_id": "user"})
    first = Consumer(first_store)
    monkeypatch.setattr(first, "_process", lambda event: None)
    assert first.drain() == 1
    replacement = SQLiteSharedState(tmp_path / "replacement.db")
    replacement.publish("user", {"kind": "usage", "user_id": "user"})
    assert replacement.identity != first_store.identity
    second = Consumer(replacement)
    written = []
    monkeypatch.setattr(second, "_process", lambda event: written.append(event))
    assert second.drain() == 1
    assert len(written) == 1


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
    assert queued[0].payload["records"][0][0]["attributes"][0] == {
        "key": "prompt_length",
        "value": {"intValue": 13},
    }


def test_usage_queue_full_returns_retryable_503(api_module, fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from src.auth.tokens import mint_token
    from src.shared_state import QueueFull, get_shared_state

    token, _ = mint_token(
        "busy-usage@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    def full(*args):
        raise QueueFull()

    monkeypatch.setattr(get_shared_state(), "publish", full)
    response = TestClient(api_module.app).post(
        "/ingest/v1/usage",
        json={"provider": "test", "model": "test-model", "endpoint": "test"},
        headers={"x-tokenage-token": token},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_rate_store_unavailable_returns_503(api_module, monkeypatch):
    import sqlite3

    from fastapi import HTTPException

    from src.ratelimit import rate_limit_hit
    from src.shared_state import get_shared_state

    def unavailable(*args):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(get_shared_state(), "rate_limit", unavailable)
    with pytest.raises(HTTPException) as exc:
        rate_limit_hit("test", "api_per_minute")
    assert exc.value.status_code == 503


def test_lease_is_renewed_while_processing_blocks(api_module, fresh_db, monkeypatch):
    import src.ingest_queue as queue
    from src.shared_state import get_shared_state, partition_for

    monkeypatch.setattr(queue, "RENEW_SECONDS", 0.01)
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    store = get_shared_state()
    store.publish("user", {"kind": "usage", "user_id": "user"})
    consumer = queue.Consumer(store)
    started, release, renewed, finished = (threading.Event() for _ in range(4))
    original_renew = store.renew

    def renew(*args, **kwargs):
        result = original_renew(*args, **kwargs)
        if threading.current_thread().name == "tokenage-leases":
            renewed.set()
        return result

    def process(event):
        started.set()
        release.wait(5)
        finished.set()

    monkeypatch.setattr(store, "renew", renew)
    monkeypatch.setattr(consumer, "_process", process)
    consumer.start()
    try:
        assert started.wait(5)
        renewed.clear()
        now += 9
        assert renewed.wait(5)
        now += 2
        assert not store.claim(partition_for("user"), "other")
    finally:
        consumer.close()
        release.set()
        assert finished.wait(5)


def test_failed_partition_does_not_starve_later_partition(
    api_module, fresh_db, monkeypatch
):
    import sqlite3

    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state, partition_for

    store = get_shared_state()
    users = sorted({str(i) for i in range(100)}, key=partition_for)
    first, last = users[0], users[-1]
    assert partition_for(first) < partition_for(last)
    for user in (first, last):
        store.publish(user, {"kind": "usage", "user_id": user})
    consumer = Consumer(store)
    processed = []

    def process(event):
        if event.payload["user_id"] == first:
            raise sqlite3.OperationalError("temporarily unavailable")
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
    return store.publish(
        user_id,
        {
            "kind": "otlp",
            "user_id": user_id,
            "device_id": device_id,
            "records": records,
        },
    )


@pytest.mark.parametrize("takeover", [False, True])
def test_otlp_retry_resumes_at_failed_record(
    api_module, fresh_db, monkeypatch, takeover
):
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
    assert consumer.drain() == 0
    if takeover:
        consumer._drop(partition_for(user.id))
        consumer = Consumer(store)
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
    assert consumer.drain() == 1
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
    store.publish("user", {"kind": "test"})
    first = Consumer(store)
    monkeypatch.setattr(first, "_process", lambda event: None)
    assert first.drain() == 1
    assert partition not in first.positions
    assert store.status()[partition].owner is None
    store.publish("user", {"kind": "test"})
    second = Consumer(store)
    seen = []
    monkeypatch.setattr(second, "_process", seen.append)
    assert second.drain() == 1
    assert len(seen) == 1
    assert store.status()[partition].depth == 0


def test_large_export_renews_lease_only_when_due(api_module, fresh_db, monkeypatch):
    from unittest.mock import Mock

    import src.otlp as otlp
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    store = get_shared_state()
    _queue_otlp(store, "user", "device", [{} for _ in range(1000)])
    renew = Mock(wraps=store.renew)
    monkeypatch.setattr(store, "renew", renew)
    monkeypatch.setattr(otlp, "_parse_log_record", lambda *args, **kwargs: None)
    # Freeze the clock: renewals depend on elapsed time, never record count.
    monkeypatch.setattr("src.ingest_queue.time.monotonic", lambda: 1000.0)
    consumer = Consumer(store)
    assert consumer.drain() == 1
    assert renew.call_count <= 1


def _postcommit_setup(fresh_db):
    from src.auth import resolve_token
    from src.auth.tokens import mint_token

    token, user = mint_token("p@example.com", kind="ingest", db_path=fresh_db.db_path)
    _, auth = resolve_token(token)
    return user, auth


def _postcommit_codex(store, user, auth):
    c = {"conversation.id": "conv", "prompt.id": "p"}
    _queue_otlp(
        store,
        user.id,
        auth.device_id,
        [
            {
                **c,
                "event.name": "codex.tool_decision",
                "call_id": "tool",
                "tool_name": "Bash",
            },
            {
                **c,
                "event.name": "codex.sse_event",
                "event.kind": "response.completed",
                "model": "m",
                "input_token_count": 10,
                "output_token_count": 5,
            },
        ],
        "codex_cli_rs",
    )


def _postcommit_counts(fresh_db):
    with fresh_db.database_module.get_engine().connect() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM usage")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM tool_calls")).scalar_one(),
        )


def test_tool_call_write_fails_after_usage_commit(api_module, fresh_db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    import src.otlp as otlp
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    user, auth = _postcommit_setup(fresh_db)
    store = get_shared_state()
    _postcommit_codex(store, user, auth)
    orig, failed = otlp.record_tool_call, []

    def flaky(**kw):
        if not failed:
            failed.append(1)
            raise OperationalError("", {}, Exception("x"))
        return orig(**kw)

    monkeypatch.setattr(otlp, "record_tool_call", flaky)
    c = Consumer(store)
    assert c.drain() == 1
    assert c.drain() == 0
    assert _postcommit_counts(fresh_db)[0] == 1


def test_state_flush_fails_after_usage_commit(api_module, fresh_db, monkeypatch):
    import sqlite3

    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state

    user, auth = _postcommit_setup(fresh_db)
    store = get_shared_state()
    monkeypatch.setattr("src.shared_mapping.get_shared_state", lambda: store)
    _postcommit_codex(store, user, auth)
    c = Consumer(store)
    failed = []
    for name in ("set", "delete"):
        orig = getattr(store, name)

        def flaky(*a, _orig=orig, **k):
            if not failed and _postcommit_counts(fresh_db)[0] == 1:
                failed.append(a[:2])
                raise sqlite3.OperationalError("database is locked")
            return _orig(*a, **k)

        monkeypatch.setattr(store, name, flaky)
    assert c.drain() == 1
    assert c.drain() == 0
    assert failed
    assert _postcommit_counts(fresh_db)[0] == 1
