"""Postgres tests for the tenancy schema (docs/quick/postgres-testing.md).

Every test in this module skips unless TOKENAGE_TEST_PG_URL points at a
dedicated scratch database. The CI "Postgres" job sets it.
"""

from __future__ import annotations

import threading

from sqlalchemy import inspect, text

TS_2026_04_17_10 = 1776420000000000


def _table_column_names(engine, table_name: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table_name)}


def _daily_row(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT user_id, request_count, prompt_tokens FROM usage_daily "
                "ORDER BY id"
            )
        ).all()


def _make_usage(*, user_id: str = "u0", **overrides):
    from src.database.models import Usage

    fields = dict(
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        user_id=user_id,
        client_source="claude-code",
        session_id="sess-1",
        endpoint="/v1/messages",
        prompt_tokens=100,
        prompt_length=500,
        completion_tokens=50,
        reasoning_tokens=0,
        cached_tokens=0,
        total_tokens=150,
        latency_ms=200,
        ttft_ms=50,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
        status=200,
    )
    fields.update(overrides)
    return Usage(device_id="test-device", **fields)


def _seed_users(engine, *user_ids: str) -> None:
    # _make_usage rows reference device "test-device"; seed it under the first
    # user so the usage/usage_daily FK resolves.
    with engine.begin() as connection:
        for user_id in user_ids:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, created_at) VALUES (:id, :email, 0)"
                ),
                {"id": user_id, "email": f"{user_id}@example.com"},
            )
        connection.execute(
            text(
                "INSERT INTO devices (id, user_id, installation_hash, device_name, "
                "created_at) VALUES ('test-device', :user_id, 'test-device', "
                "'test-device', 0) ON CONFLICT (id) DO NOTHING"
            ),
            {"user_id": user_ids[0]},
        )


def test_fresh_migrate_creates_tenancy_shapes(pg_db, pg_engine):
    from src.schema_migrations import migrate_database

    migrate_database(pg_db)

    for table in ("usage", "usage_daily", "sessions", "tool_calls", "evaluation_jobs"):
        columns = {c["name"]: c for c in inspect(pg_engine).get_columns(table)}
        assert columns["user_id"]["nullable"] is False

    daily_indexes = {
        index["name"]: index for index in inspect(pg_engine).get_indexes("usage_daily")
    }
    assert daily_indexes["uq_usage_daily"]["unique"]
    assert "uq_usage_daily_local" not in daily_indexes
    assert "uq_usage_daily_user" not in daily_indexes

    for table, index_name in [
        ("usage", "ix_usage_user_id"),
        ("sessions", "ix_sessions_user_id"),
        ("tool_calls", "ix_tool_calls_user_id"),
        ("sessions", "ix_sessions_started_desc"),
    ]:
        assert index_name in {
            index["name"] for index in inspect(pg_engine).get_indexes(table)
        }

    assert migrate_database(pg_db) == []


def test_migrate_tightens_a_nullable_tenancy_schema(pg_db, pg_engine):
    """A pre-NOT-NULL database: nullable user_id, partial usage_daily indexes,
    no sessions FK. migrate_database converges it and is then a no-op."""
    from src.schema_migrations import migrate_database

    migrate_database(pg_db)
    with pg_engine.begin() as connection:
        for table in (
            "usage",
            "usage_daily",
            "sessions",
            "tool_calls",
            "evaluation_jobs",
        ):
            connection.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN user_id DROP NOT NULL")
            )
        for fk in inspect(connection).get_foreign_keys("sessions"):
            if fk["constrained_columns"] == ["user_id"]:
                connection.execute(
                    text(f'ALTER TABLE sessions DROP CONSTRAINT "{fk["name"]}"')
                )
        connection.execute(text("DROP INDEX uq_usage_daily"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_usage_daily_local ON usage_daily "
                "(date, provider, model, client_source) WHERE user_id IS NULL"
            )
        )

    changes = set(migrate_database(pg_db))

    assert "sessions.user_id_fk" in changes
    assert "usage.user_id_not_null" in changes
    assert "usage_daily.drop_uq_usage_daily_local" in changes
    assert "usage_daily.uq_usage_daily" in changes
    for table in ("usage", "usage_daily", "sessions", "tool_calls", "evaluation_jobs"):
        columns = {c["name"]: c for c in inspect(pg_engine).get_columns(table)}
        assert columns["user_id"]["nullable"] is False
    assert migrate_database(pg_db) == []


def test_upsert_daily_aggregate_on_conflict_sums(pg_db, pg_engine):
    _seed_users(pg_engine, "u0", "u1")

    _upsert_daily(_make_usage(), db_path=pg_db)
    _upsert_daily(
        _make_usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        db_path=pg_db,
    )

    rows = _daily_row(pg_engine)
    assert len(rows) == 1
    assert rows[0].user_id == "u0"
    assert rows[0].request_count == 2
    assert rows[0].prompt_tokens == 120

    _upsert_daily(_make_usage(user_id="u1", prompt_tokens=7), db_path=pg_db)
    _upsert_daily(_make_usage(user_id="u1", prompt_tokens=1, status=429), db_path=pg_db)

    rows = _daily_row(pg_engine)
    assert len(rows) == 2
    by_user = {row.user_id: row for row in rows}
    assert by_user["u0"].request_count == 2
    assert by_user["u0"].prompt_tokens == 120
    assert by_user["u1"].request_count == 2
    assert by_user["u1"].prompt_tokens == 8


def test_upsert_daily_aggregate_concurrent_writes_sum(pg_db, pg_engine):
    _seed_users(pg_engine, "u0")

    thread_count = 8

    def worker():
        _upsert_daily(
            _make_usage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            db_path=pg_db,
        )

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = _daily_row(pg_engine)
    assert len(rows) == 1
    assert rows[0].request_count == thread_count
    assert rows[0].prompt_tokens == thread_count


def test_session_upsert_isolates_tenants_and_fails_cleanly_on_collision(
    pg_db, pg_engine
):
    from sqlalchemy.orm import Session as OrmSession

    from src.database.engine import get_engine
    from src.database.sessions import get_session_record, upsert_session_from_usage

    _seed_users(pg_engine, "u1", "u2", "u3")
    upsert_session_from_usage(_make_usage(user_id="u1"), db_path=pg_db)
    upsert_session_from_usage(
        _make_usage(session_id="sess-2", user_id="u2"), db_path=pg_db
    )

    engine = get_engine(pg_db)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT session_id, user_id FROM sessions ORDER BY session_id")
        ).all()
    assert rows == [("sess-1", "u1"), ("sess-2", "u2")]

    with OrmSession(engine) as session:
        record_u1 = get_session_record(session, "sess-1", user_id="u1")
        assert record_u1 is not None
        assert record_u1.user_id == "u1"
        assert get_session_record(session, "sess-2", user_id="u2").user_id == "u2"
        assert get_session_record(session, "sess-1", user_id="u3") is None

    # Tenant-poison guard: a second user writing the same session_id is
    # skipped (bare session_id PK) instead of polluting the first user's row.
    upsert_session_from_usage(_make_usage(user_id="u2"), db_path=pg_db)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT user_id FROM sessions WHERE session_id = 'sess-1'")
        ).one()
    assert row.user_id == "u1"


def _upsert_daily(usage, db_path=None):
    from sqlalchemy.orm import Session

    from src.database import get_engine
    from src.database.usage import upsert_daily_aggregate

    with Session(get_engine(db_path)) as session:
        upsert_daily_aggregate(session, usage)
        session.commit()
