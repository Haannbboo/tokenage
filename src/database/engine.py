"""Engine management for tokenage database connections."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine

from src.config.app import CONFIG

from .models import Base

logger = logging.getLogger(__name__)

DB_URL_ENV_VAR = "TOKENAGE_DB_URL"
DB_CONNECT_TIMEOUT_SECONDS = 5
SQLITE_BUSY_TIMEOUT_MS = 30_000

_engine_cache: dict[str, Engine] = {}


def get_db_url(db_path: str | None = None) -> str:
    """Resolve an explicit DB target, env override, or configured default."""
    if db_path and "://" in db_path:
        return db_path
    if db_path:
        return f"sqlite:///{db_path}"

    env_url = os.environ.get(DB_URL_ENV_VAR)
    if env_url:
        return env_url

    return str(CONFIG["db"]["url"])


def _connect_args(db_url: str) -> dict[str, Any]:
    if db_url.startswith("sqlite:///"):
        return {"check_same_thread": False}
    if db_url.startswith("postgresql"):
        # Bound unacknowledged query/ping bytes during silent network drops;
        # keepalives only detect idle dead peers.
        return {
            "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
            "tcp_user_timeout": 30_000,
        }
    return {}


def get_engine(db_path: str | None = None) -> Engine:
    """Return a cached engine for the requested database URL/path."""
    db_url = get_db_url(db_path)
    if db_url not in _engine_cache:
        if db_url.startswith("sqlite:///"):
            sqlite_path = db_url[10:]
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            db_url,
            future=True,
            pool_pre_ping=True,
            connect_args=_connect_args(db_url),
        )
        if db_url.startswith("sqlite:///"):
            event.listen(engine, "connect", _sqlite_pragmas)
        _engine_cache[db_url] = engine
    return _engine_cache[db_url]


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    """WAL lets readers and the single writer run concurrently; the busy
    timeout makes concurrent writers queue instead of raising "database is
    locked" straight away."""
    cur = dbapi_conn.cursor()
    cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    # WAL persists in the file, so only switch from the default; this also
    # leaves a mode chosen on purpose (tests use MEMORY) alone. Switching
    # needs the write lock and ignores busy_timeout; if another process holds
    # it, stay in the current mode rather than fail the connection.
    try:
        mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
        if mode == "delete":
            mode = cur.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode == "wal":
            cur.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        logger.warning("Could not switch SQLite to WAL (database busy)")
    cur.close()


def init_db(db_path: str | None = None) -> None:
    """Create ORM-managed tables if they do not already exist."""
    engine = get_engine(db_path)
    # A warm startup only needs a read: do not wait behind ingest writers.
    # create_all likewise skips existing tables (index upgrades are migrations).
    if set(Base.metadata.tables).issubset(inspect(engine).get_table_names()):
        return
    # create_all checks before creating, so concurrent cold worker startups
    # need to serialize that check and the DDL on the database itself.
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            # Bound the wait like SQLite's busy_timeout: a stalled holder fails
            # this worker's startup (gunicorn respawns it) instead of hanging it.
            connection.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(1953459813)")
        Base.metadata.create_all(connection)
