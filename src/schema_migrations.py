from __future__ import annotations

from sqlalchemy import inspect, text

from .database import get_engine, init_db
from .database.models import metadata

TENANT_TABLES = ("usage", "usage_daily", "sessions", "tool_calls", "evaluation_jobs")
LEGACY_USAGE_DAILY_INDEXES = ("uq_usage_daily_local", "uq_usage_daily_user")


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
    existing = {
        table: {index["name"] for index in indexes}
        for (_, table), indexes in insp.get_multi_indexes().items()
    }

    # create_all skips indexes of tables that already exist.
    for table in metadata.sorted_tables:
        for index in table.indexes:
            if index.name not in existing.get(table.name, ()):
                index.create(engine, checkfirst=True)
                applied.append(f"{table.name}.{index.name}")

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
