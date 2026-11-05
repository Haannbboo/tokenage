"""Typed SessionStart ingestion, first-name semantics and project queries."""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from tests.conftest import drain


def event(project="orchard", session_id="session-project", **extra):
    return {
        "type": "session.project",
        "agent": "codex",
        "session_id": session_id,
        "ts": time.time_ns() // 1000,
        "data": {"project": project},
        **extra,
    }


def post(client, token, report):
    return client.post(
        "/ingest/v1/events",
        headers={"x-tokenage-token": token},
        json={"events": [report]},
    )


def usage(fresh_db, user_id, session_id="session-project"):
    from src.database import log_usage
    from src.database.models import Device, Usage

    with Session(fresh_db.database_module.get_engine()) as db:
        device_id = db.scalar(select(Device.id).where(Device.user_id == user_id))
        row = Usage(
            user_id=user_id,
            device_id=device_id,
            session_id=session_id,
            model="test-model",
            provider="test",
            endpoint="otlp",
            ts=1776420000000000,
            total_tokens=100,
        )
        log_usage(row, session=db)
        db.commit()


def project_row(fresh_db):
    from src.database.models import SessionRecord

    with Session(fresh_db.database_module.get_engine()) as db:
        return db.scalar(select(SessionRecord))


@pytest.mark.parametrize("hook_first", [True, False])
def test_first_project_before_and_after_usage(api_module, fresh_db, hook_first):
    from src.auth.tokens import mint_token
    from src.shared_state import get_shared_state

    token, user = mint_token(
        "project@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    if not hook_first:
        usage(fresh_db, user.id)
    assert post(client, token, event("  Cafe\u0301  ")).status_code == 204
    assert (
        project_row(fresh_db) is None
        if hook_first
        else project_row(fresh_db).project_name is None
    )
    drain()
    if hook_first:
        assert project_row(fresh_db) is None
        assert (
            get_shared_state().get("hooks:session", f"{user.id}:session-project")
            == "Café"
        )
        assert post(client, token, event("later")).status_code == 204
        drain()
        usage(fresh_db, user.id)
    row = project_row(fresh_db)
    assert row.project_name == "Café"
    assert row.started == 1776420000000000 and row.request_count == 1
    assert get_shared_state().get("hooks:session", f"{user.id}:session-project") is None
    assert post(client, token, event("later")).status_code == 204
    drain()
    assert project_row(fresh_db).project_name == "Café"


def test_cross_user_project_is_dropped(api_module, fresh_db):
    from src.auth.tokens import mint_token
    from src.shared_state import get_shared_state

    token, attacker = mint_token(
        "other@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    _, owner = mint_token("owner@example.com", kind="ingest", db_path=fresh_db.db_path)
    usage(fresh_db, owner.id)
    assert post(TestClient(api_module.app), token, event()).status_code == 204
    drain()
    assert project_row(fresh_db).project_name is None
    assert (
        get_shared_state().get("hooks:session", f"{attacker.id}:session-project")
        is None
    )


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "/work/private",
        "a\\b",
        "a\nb",
        "a\x00b",
        "a\u202eb",
        "a\u2028b",
        "x" * 101,
    ],
)
def test_invalid_project_is_rejected(api_module, fresh_db, name):
    from src.auth.tokens import mint_token

    token, _ = mint_token(
        "project@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    assert post(TestClient(api_module.app), token, event(name)).status_code == 422
    assert project_row(fresh_db) is None


def test_event_schema_is_closed_and_identity_is_token_only(
    api_module, fresh_db, monkeypatch
):
    from src.auth.tokens import mint_token
    from src.config.app import CONFIG

    token, _ = mint_token(
        "project@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    assert (
        client.post("/ingest/v1/events", json={"events": [event()]}).status_code == 401
    )
    for extra in ({"type": "unknown"}, {"user_id": "spoof"}, {"session_id": "a/b"}):
        assert post(client, token, event(**extra)).status_code == 422
    # Rejected bodies count against the limit (auth runs first), so start fresh.
    token, _ = mint_token(
        "project@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    monkeypatch.setitem(CONFIG["rate_limits"], "ingest_per_minute", 1)
    assert post(client, token, event()).status_code == 204
    assert post(client, token, event()).status_code == 429


def test_buffer_ttl_and_rollback(load_module, monkeypatch):
    state = load_module("src.shared_state")
    store = state.get_shared_state()
    calls = []
    original = store.set
    monkeypatch.setattr(
        store,
        "set",
        lambda *args, **kwargs: (
            calls.append((args, kwargs)),
            original(*args, **kwargs),
        )[-1],
    )
    mapping = state.SharedMapping("hooks:session", ttl=86400, cap=100_000)
    with state.defer_updates():
        mapping["u:s"] = "first"
        assert mapping["u:s"] == "first"
    assert calls[-1][0][-1] == 86400
    assert calls[-1][1] == {"cap": 100_000}
    with pytest.raises(RuntimeError), state.defer_updates():
        assert mapping.pop("u:s") == "first"
        raise RuntimeError()
    assert mapping["u:s"] == "first"


def test_migration_restores_and_preserves_project(load_module, tmp_path):
    migrations = load_module("src.schema_migrations")
    db_path = str(tmp_path / "projects.db")
    migrations.migrate_database(db_path)
    engine = migrations.get_engine(db_path)
    with engine.begin() as db:
        db.execute(text("ALTER TABLE sessions DROP COLUMN project_name"))
        db.execute(text("ALTER TABLE sessions ADD COLUMN project VARCHAR(100)"))
    assert "sessions.project_name" in migrations.migrate_database(db_path)
    assert "project" in migrations.RETIRED_SESSION_COLUMNS
    assert "project" not in {c["name"] for c in inspect(engine).get_columns("sessions")}
    assert next(
        c
        for c in inspect(engine).get_columns("sessions")
        if c["name"] == "project_name"
    )["nullable"]
    from src.auth.tokens import get_or_create_user
    from src.database.models import SessionRecord

    user = get_or_create_user("migration@example.com", db_path=db_path)
    with Session(engine) as db:
        db.add(
            SessionRecord(
                session_id="kept-project",
                user_id=user.id,
                project_name="orchard",
                started=1,
                ended=2,
                updated_at="now",
            )
        )
        db.commit()
    with engine.begin() as db:
        db.execute(text("ALTER TABLE sessions ADD COLUMN project VARCHAR(100)"))
        db.execute(text("UPDATE sessions SET project = 'retired-evaluator-name'"))
    migrations.migrate_database(db_path)
    with Session(engine) as db:
        assert db.get(SessionRecord, "kept-project").project_name == "orchard"


def test_project_list_and_filter(api_module, fresh_db, monkeypatch):
    from src.auth.tokens import mint_token

    token, user = mint_token(
        "project@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    monkeypatch.setattr(api_module, "_request_user_id", lambda _: user.id)
    for sid, name in [
        ("one", "orchard"),
        ("two", "orchard"),
        ("three", "grove"),
        ("four", None),
    ]:
        usage(fresh_db, user.id, sid)
        if name:
            assert post(client, token, event(name, sid)).status_code == 204
    drain()
    distribution = client.get("/sessions/projects").json()
    groups = {row["key"]: row for row in distribution["projects"]}
    assert groups["orchard"]["session_count"] == 2
    assert client.get("/sessions?project=orchard").json()["total"] == 2
    assert client.get("/sessions?project=").json()["sessions"][0]["project"] is None
    assert client.get("/sessions?project=missing").json()["total"] == 0


def test_unavailable_project_buffer_retries_usage_and_rollups(
    api_module, fresh_db, monkeypatch
):
    import src.database.sessions as sessions
    from src.auth.tokens import mint_token
    from src.database.models import Device, Usage
    from src.ingest_queue import Consumer, publish
    from src.shared_state import StoreUnavailable, get_shared_state

    _, user = mint_token("retry@example.com", kind="ingest", db_path=fresh_db.db_path)
    with Session(fresh_db.database_module.get_engine()) as db:
        device = db.scalar(select(Device.id).where(Device.user_id == user.id))
    publish(
        user.id,
        [
            {
                "kind": "usage",
                "device_id": device,
                "fields": {
                    "session_id": "retry-project",
                    "provider": "test",
                    "model": "test-model",
                    "endpoint": "otlp",
                    "total_tokens": 100,
                },
            }
        ],
    )
    original = sessions._project_buffer

    def unavailable():
        raise StoreUnavailable()

    monkeypatch.setattr(sessions, "_project_buffer", unavailable)
    assert Consumer().drain() == 0
    assert sum(part.depth for part in get_shared_state().status()) == 1
    with Session(fresh_db.database_module.get_engine()) as db:
        assert db.scalar(select(Usage)) is None
    monkeypatch.setattr(sessions, "_project_buffer", original)
    drain()
    assert project_row(fresh_db).request_count == 1


def test_existing_session_avoids_buffer_and_rebuild_keeps_name(fresh_db, monkeypatch):
    import src.database.sessions as sessions
    from src.auth.tokens import mint_token

    _, user = mint_token("rebuild@example.com", kind="ingest", db_path=fresh_db.db_path)
    usage(fresh_db, user.id)
    with Session(fresh_db.database_module.get_engine()) as db:
        sessions.apply_session_project(
            db, user_id=user.id, session_id="session-project", project="orchard"
        )
        db.commit()
    monkeypatch.setattr(
        sessions,
        "_project_buffer",
        lambda: pytest.fail("existing usage must not read buffer"),
    )
    usage(fresh_db, user.id)
    assert project_row(fresh_db).request_count == 2
    assert sessions.rebuild_sessions_from_usage(fresh_db.db_path) == 1
    assert project_row(fresh_db).project_name == "orchard"


def test_duplicate_merge_propagates_buffer_outage_and_retries(fresh_db, monkeypatch):
    import src.database.sessions as sessions
    from src.auth.tokens import mint_token
    from src.database.models import Device, Usage
    from src.database.usage import log_usage, merge_duplicate_usage
    from src.shared_state import StoreUnavailable

    _, user = mint_token("merge@example.com", kind="ingest", db_path=fresh_db.db_path)
    engine = fresh_db.database_module.get_engine()
    with Session(engine) as db:
        device = db.scalar(select(Device.id).where(Device.user_id == user.id))
        log_usage(
            Usage(
                user_id=user.id,
                device_id=device,
                client_source="codex",
                endpoint="proxy",
                model="test-model",
                provider="test",
                ts=1776420000000000,
                prompt_tokens=10,
                completion_tokens=20,
                cached_tokens=0,
                total_tokens=30,
            ),
            session=db,
        )
        db.commit()
    original = sessions._project_buffer

    def unavailable():
        raise StoreUnavailable()

    monkeypatch.setattr(sessions, "_project_buffer", unavailable)
    fields = dict(
        client_source="codex",
        is_otlp=True,
        model="test-model",
        prompt_tokens=10,
        completion_tokens=20,
        cached_tokens=0,
        total_tokens=30,
        cache_creation_tokens=0,
        reasoning_tokens=0,
        tool_tokens=0,
        latency_ms=None,
        status=200,
        ts=1776420000000000,
        session_id="merged-session",
        user_id=user.id,
        device_id=device,
    )
    with Session(engine) as db, pytest.raises(StoreUnavailable):
        merge_duplicate_usage(**fields, session=db)
    monkeypatch.setattr(sessions, "_project_buffer", original)
    with Session(engine) as db:
        assert merge_duplicate_usage(**fields, session=db) is not None
        db.commit()
    assert project_row(fresh_db).request_count == 1


@pytest.mark.parametrize("deferred", [True, False])
def test_project_buffer_cap_is_enforced(load_module, monkeypatch, deferred):
    from contextlib import nullcontext

    state = load_module("src.shared_state")
    mapping = state.SharedMapping("hooks:session", ttl=86400, cap=2)
    with state.defer_updates() if deferred else nullcontext():
        for index in range(3):
            mapping[f"user:session-{index}"] = "orchard"
    assert mapping.get("user:session-0") is None
    assert mapping.get("user:session-1") == "orchard"
    assert mapping.get("user:session-2") == "orchard"


def test_project_buffer_memory_fallback_first_wins(load_module, monkeypatch):
    from types import SimpleNamespace

    sessions = load_module("src.database.sessions")
    buffer = {}
    monkeypatch.setattr(sessions, "_project_buffer", lambda: buffer)
    db = SimpleNamespace(scalar=lambda query: None)
    sessions.apply_session_project(db, user_id="u", session_id="s", project="first")
    sessions.apply_session_project(db, user_id="u", session_id="s", project="second")
    assert buffer == {"u:s": "first"}
