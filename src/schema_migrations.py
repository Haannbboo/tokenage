from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from .database import get_engine, init_db
from .database.models import metadata

TENANT_TABLES = ("usage", "usage_hourly", "sessions", "tool_calls")
DEVICE_ID_TABLES = ("usage", "usage_hourly")
RETIRED_SESSION_COLUMNS = (
    "project",
    "outcome",
    "source",
    "confidence",
    "task_title",
    "task_title_zh",
    "summary",
    "evidence_json",
    "failure_reason",
    "evaluated_at",
)

# Run in one transaction. Rebuild the rollup from raw usage, which is kept, so
# the backfill is exact even if ingest already wrote hourly rows. Old
# opencode/kilo latency includes tool time (the plugin no longer reports it);
# the rollup takes it out per row, raw usage.latency_ms stays as recorded.
HOURLY_BACKFILL = (
    "DELETE FROM usage_hourly",
    """
    INSERT INTO usage_hourly (
        user_id, hour, provider, model, client_source, device_id,
        request_count, prompt_tokens, completion_tokens, reasoning_tokens,
        cached_tokens, total_tokens, tool_tokens, cache_creation_tokens,
        prompt_length, input_cost_usd, output_cost_usd, total_cost_usd,
        successful_requests, failed_requests, latency_sum_ms,
        status_429, status_4xx, status_5xx, status_unknown
    )
    SELECT
        user_id, ts / 3600000000 * 3600, provider, model,
        COALESCE(client_source, ''), device_id,
        COUNT(*),
        COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0),
        COALESCE(SUM(reasoning_tokens), 0), COALESCE(SUM(cached_tokens), 0),
        COALESCE(SUM(total_tokens), 0), COALESCE(SUM(tool_tokens), 0),
        COALESCE(SUM(cache_creation_tokens), 0), COALESCE(SUM(prompt_length), 0),
        SUM(input_cost_usd), SUM(output_cost_usd), SUM(total_cost_usd),
        SUM(CASE WHEN status IS NULL OR status < 400 THEN 1 ELSE 0 END),
        SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END),
        COALESCE(SUM(CASE
            WHEN client_source IN ('opencode', 'kilo')
                AND latency_ms > COALESCE(tools.ms, 0)
            THEN latency_ms - COALESCE(tools.ms, 0)
            WHEN client_source IN ('opencode', 'kilo') THEN 0
            ELSE latency_ms END), 0),
        SUM(CASE WHEN status = 429 THEN 1 ELSE 0 END),
        SUM(CASE WHEN status >= 400 AND status < 500 AND status <> 429
            THEN 1 ELSE 0 END),
        SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END),
        0
    FROM usage
    LEFT JOIN (
        SELECT usage_id, SUM(duration_ms) AS ms FROM tool_calls GROUP BY usage_id
    ) tools ON tools.usage_id = usage.id
    GROUP BY user_id, ts / 3600000000 * 3600, provider, model,
        COALESCE(client_source, ''), device_id
    """,
    "DROP TABLE usage_daily",
)


def _require_no_null_user_ids(engine, insp) -> None:
    """Fail before tenant backfill DDL with a named error."""
    with engine.connect() as connection:
        for (_, table), columns in insp.get_multi_columns(
            filter_names=list(TENANT_TABLES)
        ).items():
            if not any(c["name"] == "user_id" and c["nullable"] for c in columns):
                continue
            count = connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")
            ).scalar()
            if count:
                raise RuntimeError(
                    f"{table} has {count} rows with a NULL user_id; "
                    "assign or delete them before migrating"
                )


def _nullable_user_id_tables(insp) -> list[str]:
    return sorted(
        table
        for (_, table), columns in insp.get_multi_columns(
            filter_names=list(TENANT_TABLES)
        ).items()
        for column in columns
        if column["name"] == "user_id" and column["nullable"]
    )


def _ensure_postgres_usage_cursor_index(engine) -> bool:
    """Build or repair the potentially large usage index without a table lock."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        valid = connection.execute(
            text(
                "SELECT i.indisvalid FROM pg_index i "
                "JOIN pg_class idx ON idx.oid = i.indexrelid "
                "WHERE i.indrelid = 'usage'::regclass "
                "AND idx.relname = 'ix_usage_user_ts_id'"
            )
        ).scalar()
        if valid is True:
            return False
        if valid is False:
            connection.execute(
                text("DROP INDEX CONCURRENTLY IF EXISTS ix_usage_user_ts_id")
            )
        connection.execute(
            text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_usage_user_ts_id "
                "ON usage (user_id, ts, id)"
            )
        )
        return True


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """True for the PostgreSQL lock_timeout error (SQLSTATE 55P03)."""
    return (
        getattr(exc.orig, "pgcode", None) == "55P03"
        or getattr(exc.orig, "sqlstate", None) == "55P03"
    )


def _drop_evaluator_schema(engine) -> list[str]:
    """Retire evaluator storage before any tenant/device backfill touches it.

    Operators back up the database before rollout. Every node must run the code
    from this release before the migration runs: older code selects the dropped
    columns.
    Keep the drops transactional and bounded: another node may still hold a lock.
    """
    applied: list[str] = []
    try:
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            elif engine.dialect.name == "sqlite":
                # Serialize SQLite schema inspection and DDL with other writers.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            insp = inspect(connection)
            if insp.has_table("evaluation_jobs"):
                connection.execute(text("DROP TABLE IF EXISTS evaluation_jobs"))
                applied.append("evaluation_jobs.drop")
            if not insp.has_table("sessions"):
                return applied
            columns = {c["name"] for c in insp.get_columns("sessions")}
            retiring = columns.intersection(RETIRED_SESSION_COLUMNS)
            for name in RETIRED_SESSION_COLUMNS:
                if name in retiring:
                    # IF EXISTS also tolerates two Postgres nodes restarting
                    # against the same database with a stale inspection result.
                    guard = "IF EXISTS " if engine.dialect.name == "postgresql" else ""
                    connection.execute(
                        text(f"ALTER TABLE sessions DROP COLUMN {guard}{name}")
                    )
                    applied.append(f"sessions.drop_{name}")
    except DBAPIError as exc:
        if _is_lock_timeout(exc):
            raise RuntimeError(
                "Evaluator schema removal hit the PostgreSQL lock timeout (2s); "
                "no evaluator drops were committed. Release the blocking transaction "
                "and retry the migration."
            ) from exc
        raise
    return applied


def migrate_database(db_path: str | None = None) -> list[str]:
    """Bring a database up to the models: create missing indexes, replace the
    daily rollup with the hourly one, and make user_id NOT NULL (Postgres).

    There are no upgrade steps for schemas older than this one.
    """
    engine = get_engine(db_path)
    init_db(db_path)  # create_all: missing tables, with their indexes
    applied = _drop_evaluator_schema(engine)
    insp = inspect(engine)
    if engine.dialect.name == "postgresql":
        _require_no_null_user_ids(engine, insp)
    elif stale := _nullable_user_id_tables(insp):
        # Reject before tenant/device backfills: success would hide NULL-owner rows.
        raise RuntimeError(
            f"SQLite tables {stale} have a nullable user_id; this schema has "
            "no upgrade path. Rebuild the database before migrating."
        )

    # create_all does not add columns to tables that already exist.
    if not any(c["name"] == "project_name" for c in insp.get_columns("sessions")):
        with engine.begin() as connection:
            guard = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
            connection.execute(
                text(
                    f"ALTER TABLE sessions ADD COLUMN {guard}project_name VARCHAR(100)"
                )
            )
        applied.append("sessions.project_name")
        insp = inspect(engine)

    if not any(c["name"] == "picture" for c in insp.get_columns("users")):
        with engine.begin() as connection:
            # Two nodes can migrate at once; SQLite has no IF NOT EXISTS here.
            guard = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
            connection.execute(
                text(f"ALTER TABLE users ADD COLUMN {guard}picture VARCHAR")
            )
        applied.append("users.picture")
        insp = inspect(engine)
    if not any(c["name"] == "token_epoch" for c in insp.get_columns("users")):
        try:
            with engine.begin() as connection:
                if engine.dialect.name == "postgresql":
                    # Bound the wait for ACCESS EXCLUSIVE: a long transaction on
                    # users would otherwise queue every request that joins it.
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                guard = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
                connection.execute(
                    text(
                        f"ALTER TABLE users ADD COLUMN {guard}token_epoch INTEGER "
                        "NOT NULL DEFAULT 0"
                    )
                )
        except DBAPIError as exc:
            if _is_lock_timeout(exc):
                raise RuntimeError(
                    "Adding users.token_epoch hit the PostgreSQL lock timeout (2s); "
                    "this release needs the column, so nothing was committed. "
                    "Release the blocking transaction and retry the migration."
                ) from exc
            raise
        applied.append("users.token_epoch")
        insp = inspect(engine)

    missing_device_id = [
        table
        for table in DEVICE_ID_TABLES
        if not any(c["name"] == "device_id" for c in insp.get_columns(table))
    ]
    if missing_device_id:
        with engine.begin() as connection:
            for name in missing_device_id:
                connection.execute(
                    text(
                        f"ALTER TABLE {name} ADD COLUMN device_id VARCHAR "
                        "REFERENCES devices (id) ON DELETE SET NULL"
                    )
                )
                applied.append(f"{name}.device_id")
        insp = inspect(engine)  # drop the cached pre-ALTER view

    # usage.client_ip was dropped once devices replaced it (privacy); the index
    # goes first because SQLite cannot drop an indexed column.
    if any(c["name"] == "client_ip" for c in insp.get_columns("usage")):
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX IF EXISTS ix_usage_client_ip"))
            connection.execute(text("ALTER TABLE usage DROP COLUMN client_ip"))
        applied.append("usage.drop_client_ip")
        insp = inspect(engine)

    # usage_daily → usage_hourly, once: the old table's presence is the marker.
    if insp.has_table("usage_daily"):
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                # Hold off live hourly upserts until the rebuild commits.
                connection.execute(text("LOCK TABLE usage_hourly IN EXCLUSIVE MODE"))
            for statement in HOURLY_BACKFILL:
                connection.execute(text(statement))
        applied.append("usage_hourly.backfill")
        insp = inspect(engine)

    existing = {
        table: {index["name"] for index in indexes}
        for (_, table), indexes in insp.get_multi_indexes().items()
    }

    if engine.dialect.name == "postgresql" and _ensure_postgres_usage_cursor_index(
        engine
    ):
        applied.append("usage.ix_usage_user_ts_id")

    # create_all skips indexes of tables that already exist, including
    # ix_usage_user_ts_id for Request Logs keyset pagination.
    for table in metadata.sorted_tables:
        for index in table.indexes:
            if (
                engine.dialect.name == "postgresql"
                and index.name == "ix_usage_user_ts_id"
            ):
                continue
            if index.name not in existing.get(table.name, ()):
                index.create(engine, checkfirst=True)
                applied.append(f"{table.name}.{index.name}")

    # ponytail: SQLite cannot ALTER a column to NOT NULL; its databases get the
    # constraint from create_all (a rebuilt/new file). Postgres is altered here.
    if engine.dialect.name == "postgresql":
        nullable = _nullable_user_id_tables(insp)
        sessions_fk = any(
            fk["constrained_columns"] == ["user_id"]
            for fk in insp.get_foreign_keys("sessions")
        )
        if nullable or not sessions_fk:
            with engine.begin() as connection:
                for name in nullable:
                    connection.execute(
                        text(f"ALTER TABLE {name} ALTER COLUMN user_id SET NOT NULL")
                    )
                    applied.append(f"{name}.user_id_not_null")
                if not sessions_fk:
                    connection.execute(
                        text(
                            "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_user_id "
                            "FOREIGN KEY (user_id) REFERENCES users (id)"
                        )
                    )
                    applied.append("sessions.user_id_fk")

    return applied
