"""Final evaluator schema retirement on isolated SQLite and scratch Postgres."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

# Frozen from the schema before evaluator removal: no NOT NULL constraints or
# defaults. Keep this independent of the migration's list to detect omissions.
OLD_COLUMNS = {
    "outcome": "VARCHAR",
    "source": "VARCHAR",
    "confidence": "NUMERIC(5, 4)",
    "task_title": "VARCHAR",
    "task_title_zh": "VARCHAR",
    "summary": "VARCHAR",
    "evidence_json": "VARCHAR",
    "failure_reason": "VARCHAR",
    "evaluated_at": "VARCHAR",
}


@pytest.fixture(params=["sqlite", "postgres"])
def schema_db(request, tmp_path):
    from src.database import get_engine
    from src.schema_migrations import migrate_database

    if request.param == "postgres":
        db_path = request.getfixturevalue("pg_db")
    else:
        db_path = str(tmp_path / "schema.db")
    engine = get_engine(db_path)
    try:
        yield db_path, engine
    finally:
        # These tables are deliberately absent from ORM metadata and would
        # otherwise survive pg_clean if a test assertion failed mid-upgrade.
        with engine.begin() as connection:
            connection.execute(text("DROP VIEW IF EXISTS evaluator_dependency"))
            connection.execute(text("DROP TABLE IF EXISTS evaluation_jobs"))
        migrate_database(db_path)


def _old_schema(engine, *, with_jobs=True, columns=OLD_COLUMNS):
    with engine.begin() as connection:
        for name in columns:
            connection.execute(
                text(f"ALTER TABLE sessions ADD COLUMN {name} {OLD_COLUMNS[name]}")
            )
        if with_jobs:
            # Pre-client schema: nullable user_id, no device_id. The migration
            # must drop it without tenant validation or any legacy queue update.
            connection.execute(
                text(
                    "CREATE TABLE evaluation_jobs (job_id VARCHAR PRIMARY KEY, "
                    "user_id VARCHAR, kind VARCHAR, session_id VARCHAR, "
                    "status VARCHAR, created_at VARCHAR)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX ix_evaluation_jobs_one_active_per_session "
                    "ON evaluation_jobs (kind, session_id) WHERE status = 'queued'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO evaluation_jobs VALUES "
                    "('old-job', NULL, 'evaluate', 'historic', 'queued', '2026-01-01')"
                )
            )


def _assert_retired(engine):
    from src.database.models import SessionRecord

    assert not inspect(engine).has_table("evaluation_jobs")
    assert not set(OLD_COLUMNS).intersection(
        column["name"] for column in inspect(engine).get_columns("sessions")
    )
    assert not set(OLD_COLUMNS).intersection(SessionRecord.__table__.columns.keys())


def _seed_session(engine):
    from src.database.models import SessionRecord, User

    with Session(engine) as session:
        session.add(User(id="owner", email="owner@example.com", created_at=0))
        session.flush()
        session.add(
            SessionRecord(
                session_id="historic",
                user_id="owner",
                client_source="codex",
                started=100,
                ended=900,
                request_count=7,
                successful_requests=5,
                failed_requests=2,
                total_tokens=321,
                prompt_tokens=123,
                completion_tokens=198,
                cached_tokens=11,
                cache_creation_tokens=12,
                total_cost_usd="1.23456789",
                latency_sum_ms=9876,
                avg_latency_ms="1410.8571",
                avg_ttft_ms="3.5",
                primary_provider="openai",
                primary_model="gpt-5",
                providers_json='{"openai": 1.23456789}',
                models_json='{"gpt-5": 1.23456789}',
                tool_calls_json='{"Read": 4}',
                last_usage_id="last",
                updated_at="2026-01-01",
            )
        )
        session.commit()


def _rollups(engine):
    from src.database.models import SessionRecord

    with engine.connect() as connection:
        return connection.execute(select(SessionRecord.__table__)).mappings().all()


def test_fresh_schema_and_rerun(schema_db):
    from src.schema_migrations import migrate_database

    db_path, engine = schema_db
    migrate_database(db_path)
    _assert_retired(engine)
    assert migrate_database(db_path) == []


@pytest.mark.parametrize("with_jobs", [False, True])
def test_already_dropped_columns_with_or_without_jobs(schema_db, with_jobs):
    from src.schema_migrations import migrate_database

    db_path, engine = schema_db
    migrate_database(db_path)
    _seed_session(engine)
    before = _rollups(engine)
    _old_schema(engine, with_jobs=with_jobs, columns=())
    expected = ["evaluation_jobs.drop"] if with_jobs else []
    assert migrate_database(db_path) == expected
    _assert_retired(engine)
    assert _rollups(engine) == before
    assert migrate_database(db_path) == []


@pytest.mark.parametrize("with_jobs", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_upgrade_preserves_every_rollup_and_reruns(schema_db, with_jobs, partial):
    from src.schema_migrations import migrate_database

    db_path, engine = schema_db
    migrate_database(db_path)
    columns = list(OLD_COLUMNS)[:4] if partial else list(OLD_COLUMNS)
    _old_schema(engine, with_jobs=with_jobs, columns=columns)
    _seed_session(engine)
    before = _rollups(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sessions SET outcome = 'solved', source = 'llm', "
                "confidence = 0.9, task_title = 'Historic title'"
            )
        )
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        changes = migrate_database(db_path)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert set(changes) == {f"sessions.drop_{name}" for name in columns} | (
        {"evaluation_jobs.drop"} if with_jobs else set()
    )
    assert not any(
        "evaluation_jobs" in sql and sql.startswith(("UPDATE ", "ALTER TABLE "))
        for sql in statements
    )
    _assert_retired(engine)
    assert _rollups(engine) == before  # no usage exists to rebuild this session
    assert migrate_database(db_path) == []
    assert _rollups(engine) == before


def test_new_orm_reads_and_inserts_before_schema_drop(schema_db):
    from src.database.models import SessionRecord, Usage
    from src.database.sessions import (
        SessionFilters,
        fetch_sessions,
        upsert_session_from_usage,
    )
    from src.schema_migrations import migrate_database

    db_path, engine = schema_db
    migrate_database(db_path)
    _old_schema(engine)
    legacy = {
        c["name"]: c
        for c in inspect(engine).get_columns("sessions")
        if c["name"] in OLD_COLUMNS
    }
    assert set(legacy) == set(OLD_COLUMNS)
    assert all(c["nullable"] or c["default"] is not None for c in legacy.values())
    _seed_session(engine)  # ORM insert omits every retired column
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE sessions SET outcome = 'failed', source = 'llm'")
        )
    usage = Usage(
        user_id="owner",
        session_id="live",
        ts=1000,
        provider="openai",
        model="gpt-5",
        client_source="codex",
        prompt_tokens=3,
        completion_tokens=2,
        total_tokens=5,
        total_cost_usd=0.01,
        status=200,
    )
    upsert_session_from_usage(
        usage, db_path=db_path
    )  # ingest insert and conflict update
    upsert_session_from_usage(usage, db_path=db_path)
    with Session(engine) as session:
        assert session.get(SessionRecord, "historic").request_count == 7
        assert session.get(SessionRecord, "live").request_count == 2
    rows = fetch_sessions(filters=SessionFilters(user_id="owner"), db_path=db_path)
    assert {row["session_id"] for row in rows} == {"historic", "live"}
    assert all("evaluation" not in row for row in rows)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT outcome, source FROM sessions WHERE session_id = 'historic'")
        ).one() == ("failed", "llm")
        assert connection.execute(
            text("SELECT outcome, source FROM sessions WHERE session_id = 'live'")
        ).one() == (None, None)


@pytest.mark.parametrize("locked_table", ["sessions", "evaluation_jobs"])
def test_postgres_lock_timeout_rolls_back_and_retry_completes(
    pg_db, pg_engine, locked_table
):
    from src.schema_migrations import migrate_database

    _old_schema(pg_engine)
    _seed_session(pg_engine)
    before = _rollups(pg_engine)
    try:
        with pg_engine.connect() as blocker:
            blocker.execute(text(f"SELECT * FROM {locked_table}"))
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="lock timeout.*2s.*retry"):
                migrate_database(pg_db)
            assert time.monotonic() - started < 10
            assert inspect(pg_engine).has_table("evaluation_jobs")
            assert set(OLD_COLUMNS).issubset(
                c["name"] for c in inspect(pg_engine).get_columns("sessions")
            )
            blocker.rollback()
        assert "evaluation_jobs.drop" in migrate_database(pg_db)
        _assert_retired(pg_engine)
        assert _rollups(pg_engine) == before
        assert migrate_database(pg_db) == []
    finally:
        migrate_database(pg_db)


def test_postgres_dependency_fails_without_cascade(pg_db, pg_engine):
    from src.schema_migrations import migrate_database

    _old_schema(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(
            text("CREATE VIEW evaluator_dependency AS SELECT outcome FROM sessions")
        )
    try:
        with pytest.raises(DBAPIError, match="depend"):
            migrate_database(pg_db)
        assert inspect(pg_engine).has_table("evaluation_jobs")
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text("DROP VIEW evaluator_dependency"))
        migrate_database(pg_db)
    _assert_retired(pg_engine)
