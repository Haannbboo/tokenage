from __future__ import annotations

from sqlalchemy import inspect, text

from .database import get_engine, init_db
from .database.models import metadata

TENANT_TABLES = ("usage", "usage_daily", "sessions", "tool_calls", "evaluation_jobs")
LEGACY_USAGE_DAILY_INDEXES = ("uq_usage_daily_local", "uq_usage_daily_user")
DEVICE_ID_TABLES = ("usage", "usage_daily", "evaluation_jobs")
LEGACY_EVALUATION_JOB_INDEX = "ix_evaluation_jobs_one_active_per_session"


def _require_no_null_user_ids(engine, insp) -> None:
    """Fail before any DDL, with a named error, rather than half-converging."""
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


def migrate_database(db_path: str | None = None) -> list[str]:
    """Bring a database up to the models: create missing indexes, swap the
    legacy usage_daily partial indexes, and make user_id NOT NULL (Postgres).

    There are no upgrade steps for schemas older than this one.
    """
    engine = get_engine(db_path)
    init_db(db_path)  # create_all: missing tables, with their indexes
    applied: list[str] = []
    insp = inspect(engine)
    if engine.dialect.name == "postgresql":
        _require_no_null_user_ids(engine, insp)
    elif stale := _nullable_user_id_tables(insp):
        # Checked before any DDL: success here would hide NULL-owner rows.
        raise RuntimeError(
            f"SQLite tables {stale} have a nullable user_id; this schema has "
            "no upgrade path. Rebuild the database before migrating."
        )

    # create_all does not add columns to tables that already exist.
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

    existing = {
        table: {index["name"] for index in indexes}
        for (_, table), indexes in insp.get_multi_indexes().items()
    }

    # The daily unique key gained device_id; drop the old-shaped index so the
    # loop below recreates it from the model.
    for reflected in insp.get_indexes("usage_daily"):
        if (
            reflected["name"] == "uq_usage_daily"
            and "device_id" not in (reflected["column_names"])
        ):
            with engine.begin() as connection:
                connection.execute(text("DROP INDEX uq_usage_daily"))
            existing["usage_daily"].discard("uq_usage_daily")
            applied.append("usage_daily.drop_uq_usage_daily_without_device")

    # create_all skips indexes of tables that already exist.
    for table in metadata.sorted_tables:
        for index in table.indexes:
            if index.name not in existing.get(table.name, ()):
                index.create(engine, checkfirst=True)
                applied.append(f"{table.name}.{index.name}")

    # Evaluation moved to the client: the old server queue's one-active-job
    # index would block a second client run on the same session, and any
    # queued/running rows it guarded can never finish without the worker.
    if LEGACY_EVALUATION_JOB_INDEX in existing.get("evaluation_jobs", ()):
        with engine.begin() as connection:
            connection.execute(
                text(f"DROP INDEX IF EXISTS {LEGACY_EVALUATION_JOB_INDEX}")
            )
        applied.append("evaluation_jobs.drop_one_active_index")

    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE evaluation_jobs SET status = 'failed', "
                "error = 'Evaluation moved to the client', "
                "finished_at = COALESCE(finished_at, created_at) "
                "WHERE device_id IS NULL AND status NOT IN ('succeeded', 'failed')"
            )
        )
        if result.rowcount:  # type: ignore[attr-defined]
            applied.append("evaluation_jobs.fail_legacy_active")

    # Config prices were recorded as source 'yaml' before being renamed
    # 'server'. A 'yaml' snapshot whose 'server' twin (same date, model and
    # rates) already exists can't be renamed past the unique key: repoint its
    # usage rows at the twin and drop it, then rename the rest.
    twin = (
        "SELECT s.id FROM price_snapshots s WHERE s.source = 'server' "
        "AND s.date = y.date AND s.model = y.model AND s.rates_hash = y.rates_hash"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE usage SET price_snapshot_id = (SELECT ({twin}) "
                "FROM price_snapshots y WHERE y.id = usage.price_snapshot_id) "
                "WHERE price_snapshot_id IN (SELECT y.id FROM price_snapshots y "
                f"WHERE y.source = 'yaml' AND EXISTS ({twin}))"
            )
        )
        connection.execute(
            text(
                "DELETE FROM price_snapshots WHERE id IN (SELECT y.id FROM "
                f"price_snapshots y WHERE y.source = 'yaml' AND EXISTS ({twin}))"
            )
        )
        result = connection.execute(
            text("UPDATE price_snapshots SET source = 'server' WHERE source = 'yaml'")
        )
        if result.rowcount:  # type: ignore[attr-defined]
            applied.append("price_snapshots.rename_yaml_source")

    # The pre-NOT-NULL pair of partial unique indexes (NULL-user / real-user).
    legacy = [
        name
        for name in LEGACY_USAGE_DAILY_INDEXES
        if name in existing.get("usage_daily", ())
    ]
    if legacy:
        with engine.begin() as connection:
            for name in legacy:
                connection.execute(text(f"DROP INDEX IF EXISTS {name}"))
        applied.extend(f"usage_daily.drop_{name}" for name in legacy)

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
