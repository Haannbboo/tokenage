"""Fresh authoritative validation with best-effort, shared activity throttling."""

import subprocess
import sys
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError


@contextmanager
def _queries(engine):
    counts = {"select": 0, "update": 0, "commit": 0, "checkout": 0}

    def statement(conn, cursor, sql, params, context, many):
        verb = sql.split()[0].lower()
        if verb in counts:
            counts[verb] += 1

    def commit(conn):
        counts["commit"] += 1

    def checkout(*args):
        counts["checkout"] += 1

    listeners = [
        ("before_cursor_execute", statement),
        ("commit", commit),
        ("checkout", checkout),
    ]
    for name, callback in listeners:
        event.listen(engine, name, callback)
    try:
        yield counts
    finally:
        for name, callback in listeners:
            event.remove(engine, name, callback)


def _setup(fresh_db, monkeypatch, kind="ingest"):
    from src.auth import tokens

    now = 100_000_000
    monkeypatch.setattr(tokens, "_now_micros", lambda: now)
    token, user = tokens.mint_token(
        "activity@example.com", kind=kind, db_path=fresh_db.db_path
    )
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    return tokens, token, user, engine, now


@pytest.mark.parametrize("kind", ["ingest", "cli", "web"])
@pytest.mark.parametrize(
    "age", [0, 29_999_999, 30_000_000, 30_000_001, None, -1_000_000]
)
def test_activity_boundary_and_query_counts(fresh_db, monkeypatch, kind, age):
    tokens, token, _, engine, now = _setup(fresh_db, monkeypatch, kind)
    old = None if age is None else now - age
    with engine.begin() as conn:
        conn.execute(text("UPDATE auth_tokens SET last_used_at = :old"), {"old": old})
        conn.execute(text("UPDATE devices SET last_seen_at = :old"), {"old": old})
    due = age is None or age > 30_000_000
    with _queries(engine) as counts:
        result = tokens.resolve_token(token, fresh_db.db_path)
        assert result[0].email == "activity@example.com"
        assert result[1].kind == kind
    assert counts == {
        "select": 1,
        "update": (2 if kind == "ingest" else 1) if due else 0,
        "commit": int(due),
        "checkout": 1,
    }
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT last_used_at FROM auth_tokens")
        ).scalar_one() == (now if due else old)


@pytest.mark.parametrize("device_age", [None, 0, 30_000_000, 30_000_001, -1_000_000])
def test_device_activity_has_independent_guard(fresh_db, monkeypatch, device_age):
    tokens, token, _, engine, now = _setup(fresh_db, monkeypatch)
    old = None if device_age is None else now - device_age
    with engine.begin() as conn:
        conn.execute(text("UPDATE devices SET last_seen_at = :old"), {"old": old})
    tokens.resolve_token(token, fresh_db.db_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT last_seen_at FROM devices")).scalar_one() == (
            now if device_age is None or device_age > 30_000_000 else old
        )


@pytest.mark.parametrize("failure", ["token", "device", "commit"])
def test_activity_failure_keeps_identity_and_retries(fresh_db, monkeypatch, failure):
    tokens, token, user, engine, now = _setup(fresh_db, monkeypatch)

    def fail(conn, cursor, sql, params, context, many):
        if sql.startswith(
            "UPDATE " + ("auth_tokens" if failure == "token" else "devices")
        ):
            raise OperationalError("activity failed", {}, None)

    def fail_commit(session):
        raise OperationalError("commit failed", {}, None)

    with monkeypatch.context() as patch:
        if failure == "commit":
            patch.setattr(tokens.Session, "commit", fail_commit)
        else:
            event.listen(engine, "before_cursor_execute", fail)
        try:
            result = tokens.resolve_token(token, fresh_db.db_path)
            assert result[0].id == user.id
            assert result[1].device_id is not None
        finally:
            if failure != "commit":
                event.remove(engine, "before_cursor_execute", fail)
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT last_used_at FROM auth_tokens")).scalar_one()
            is None
        )
    tokens.resolve_token(token, fresh_db.db_path)
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT last_used_at FROM auth_tokens")).scalar_one()
            == now
        )


@pytest.mark.parametrize("revoke", ["token", "device", "device_row", "rotate"])
@pytest.mark.parametrize("other_process", [False, True])
def test_next_ingest_after_revocation_never_publishes_or_updates(
    api_module, fresh_db, monkeypatch, revoke, other_process
):
    from src.auth.tokens import get_or_create_user, mint_device_tokens, resolve_token
    from src.shared_state import get_shared_state

    user = get_or_create_user("revocation@example.com", fresh_db.db_path)
    _, token, device = mint_device_tokens(
        user.id, "installation", "test", db_path=fresh_db.db_path
    )
    _, auth_token = resolve_token(token, fresh_db.db_path)
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    client = TestClient(api_module.app)
    published = []
    monkeypatch.setattr(api_module, "publish", lambda *args: published.append(args))
    import src.otlp as otlp

    monkeypatch.setattr(otlp, "publish", lambda *args: published.append(args))
    requests = [
        (
            "/ingest/v1/usage",
            {
                "provider": "openai",
                "model": "test",
                "endpoint": "/v1/chat/completions",
                "client_source": "proxy",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "latency_ms": 1,
                "status": 200,
            },
        ),
        (
            "/otlp/v1/logs",
            {
                "resourceLogs": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "claude-code"},
                                }
                            ]
                        },
                        "scopeLogs": [
                            {
                                "logRecords": [
                                    {
                                        "attributes": [
                                            {
                                                "key": "event.name",
                                                "value": {"stringValue": "api_request"},
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
        ),
    ]
    for endpoint, body in requests:
        assert client.post(
            endpoint, json=body, headers={"x-tokenage-token": token}
        ).status_code in (200, 204)
    assert len(published) == 2
    published.clear()
    # Force bookkeeping due, so a stale positive auth result would write.
    with engine.begin() as conn:
        conn.execute(text("UPDATE auth_tokens SET last_used_at = NULL"))
        conn.execute(text("UPDATE devices SET last_seen_at = NULL"))
    code = """
import sys
from src.auth.tokens import revoke_token, revoke_device, mint_device_tokens
from sqlalchemy import create_engine, text
path, mode, token_id, user_id, device_id = sys.argv[1:]
if mode == 'token':
    assert revoke_token(token_id, user_id, path)
elif mode == 'device':
    assert revoke_device(device_id, user_id, path)
elif mode == 'rotate':
    mint_device_tokens(user_id, 'installation', 'test', db_path=path)
else:
    url = path if '://' in path else 'sqlite:///' + path
    with create_engine(url).begin() as conn:
        conn.execute(text('UPDATE devices SET revoked_at = 1 WHERE id = :id'), {'id': device_id})
"""
    args = [fresh_db.db_path, revoke, auth_token.id, user.id, device.id]
    if other_process:
        # Match the test fixture's SQLite journal mode across processes;
        # production WAL initialization would otherwise change the shared file.
        setup = """
import sqlite3
from sqlalchemy import event
from sqlalchemy.engine import Engine
@event.listens_for(Engine, 'connect')
def test_pragmas(conn, record):
    if isinstance(conn, sqlite3.Connection):
        conn.execute('PRAGMA journal_mode=MEMORY')
"""
        subprocess.run(
            [sys.executable, "-c", setup + code, *args], check=True, capture_output=True
        )
    else:
        monkeypatch.setattr(sys, "argv", ["revoke", *args])
        exec(code, {})
    with _queries(engine) as counts:
        for endpoint, body in requests:
            # Each endpoint must independently reach the authoritative DB.
            from src.ratelimit import _invalid_token_cache

            _invalid_token_cache.clear()
            assert (
                client.post(
                    endpoint, json=body, headers={"x-tokenage-token": token}
                ).status_code
                == 401
            )
    assert counts["select"] == 2
    assert published == []
    assert counts["update"] == counts["commit"] == 0
    assert all(partition.depth == 0 for partition in get_shared_state().status())


@pytest.mark.parametrize("race", ["refresh", "token_revoke", "device_revoke"])
def test_due_update_rechecks_concurrent_changes(fresh_db, monkeypatch, race):
    from types import SimpleNamespace

    tokens, token, _, engine, now = _setup(fresh_db, monkeypatch)
    original_execute = tokens.Session.execute
    changed_rows = []
    selected = False

    def execute(session, statement, *args, **kwargs):
        nonlocal selected
        result = original_execute(session, statement, *args, **kwargs)
        if statement.is_select and not selected:
            selected = True
            row = result.first()
            # A competing transaction commits after our read, before refresh.
            with engine.begin() as conn:
                if race == "refresh":
                    conn.execute(
                        text("UPDATE auth_tokens SET last_used_at = :now"), {"now": now}
                    )
                    conn.execute(
                        text("UPDATE devices SET last_seen_at = :now"), {"now": now}
                    )
                elif race == "token_revoke":
                    conn.execute(text("UPDATE auth_tokens SET revoked_at = 1"))
                else:
                    conn.execute(text("UPDATE devices SET revoked_at = 1"))
            return SimpleNamespace(first=lambda: row)
        if statement.is_update:
            changed_rows.append((statement.table.name, result.rowcount))
        return result

    monkeypatch.setattr(tokens.Session, "execute", execute)
    # Existing in-flight requests keep their already validated identity.
    assert tokens.resolve_token(token, fresh_db.db_path) is not None
    if race == "refresh":
        assert changed_rows == [("auth_tokens", 0), ("devices", 0)]
    elif race == "token_revoke":
        assert changed_rows[0] == ("auth_tokens", 0)
    else:
        assert changed_rows[1] == ("devices", 0)
    result = tokens.resolve_token(token, fresh_db.db_path)
    assert (result is None) == (race != "refresh")


@pytest.mark.parametrize("due", [True, False])
@pytest.mark.parametrize("write_fails", [True, False])
def test_session_cleanup_failure_only_swallowed_after_activity(
    fresh_db, monkeypatch, due, write_fails
):
    tokens, token, user, engine, now = _setup(fresh_db, monkeypatch)
    if not due:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE auth_tokens SET last_used_at = :now"), {"now": now}
            )
    original_close = tokens.Session.close

    def close(session):
        original_close(session)
        raise OperationalError("cleanup failed", {}, None)

    def fail(conn, cursor, sql, params, context, many):
        if sql.startswith("UPDATE"):
            raise OperationalError("activity failed", {}, None)

    with monkeypatch.context() as patch:
        patch.setattr(tokens.Session, "close", close)
        if write_fails:
            event.listen(engine, "before_cursor_execute", fail)
        try:
            if due:
                result = tokens.resolve_token(token, fresh_db.db_path)
                assert result[0].id == user.id
                assert result[1].kind == "ingest"
            else:
                with pytest.raises(OperationalError):
                    tokens.resolve_token(token, fresh_db.db_path)
        finally:
            if write_fails:
                event.remove(engine, "before_cursor_execute", fail)


@pytest.mark.parametrize("kind", ["cli", "ingest", "web"])
@pytest.mark.parametrize("state", ["missing", "revoked", "other_user"])
def test_invalid_device_rejected_for_shared_callers(fresh_db, monkeypatch, kind, state):
    tokens, token, _, engine, _ = _setup(fresh_db, monkeypatch)
    if kind != "ingest":
        tokens.mint_token(
            "activity@example.com", kind="ingest", db_path=fresh_db.db_path
        )
    with engine.begin() as conn:
        if kind != "ingest":
            conn.execute(
                text(
                    "UPDATE auth_tokens SET device_id = (SELECT id FROM devices LIMIT 1)"
                )
            )
        if state == "missing":
            conn.execute(text("UPDATE auth_tokens SET device_id = 'missing'"))
        if state == "revoked":
            conn.execute(text("UPDATE devices SET revoked_at = 1"))
        elif state == "other_user":
            conn.execute(text("UPDATE devices SET user_id = 'other'"))
    with _queries(engine) as counts:
        assert tokens.resolve_token(token, fresh_db.db_path) is None
    assert counts["update"] == counts["commit"] == 0


def _activity_racer(db_path, token, now, barrier, results):
    """Spawn-safe worker: all reads finish before any activity refresh starts."""
    import sqlite3
    from types import SimpleNamespace

    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def test_pragmas(conn, record):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA journal_mode=MEMORY")

    from src.auth import tokens

    tokens._now_micros = lambda: now
    original_execute = tokens.Session.execute
    changed = {"auth_tokens": 0, "devices": 0}

    def execute(session, statement, *args, **kwargs):
        result = original_execute(session, statement, *args, **kwargs)
        if statement.is_select:
            row = result.first()
            barrier.wait(timeout=15)
            return SimpleNamespace(first=lambda: row)
        if statement.is_update:
            changed[statement.table.name] += result.rowcount
        return result

    tokens.Session.execute = execute
    assert tokens.resolve_token(token, db_path) is not None
    results.put(changed)


def test_due_refresh_converges_across_processes(fresh_db, monkeypatch):
    import multiprocessing

    _, token, _, engine, now = _setup(fresh_db, monkeypatch)
    with engine.begin() as conn:
        conn.execute(text("UPDATE devices SET last_seen_at = NULL"))
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    results = context.Queue()
    workers = [
        context.Process(
            target=_activity_racer,
            args=(fresh_db.db_path, token, now, barrier, results),
        )
        for _ in range(4)
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
            assert worker.exitcode == 0
        changes = [results.get(timeout=5) for _ in workers]
        assert sum(c["auth_tokens"] for c in changes) == 1
        assert sum(c["devices"] for c in changes) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()
