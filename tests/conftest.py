from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

NO_REMOTE_PRICING_CONFIG = (
    Path(__file__).parent / "fixtures" / "no-remote-pricing-config.yaml"
)
os.environ.setdefault("TOKENAGE_CONFIG", str(NO_REMOTE_PRICING_CONFIG))


# Tests never need SQLite durability; fsync per DDL/commit dominated runtime
# on slow disks (>90% of wall time). Applies to every engine in the process.
# Client-only CI (hosted-install job) has no sqlalchemy, so skip it there.
try:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
except ImportError:
    pass
else:

    @event.listens_for(Engine, "connect")
    def _sqlite_fast_pragmas(dbapi_conn: Any, _record: Any) -> None:
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA synchronous=OFF")
            dbapi_conn.execute("PRAGMA journal_mode=MEMORY")


CONFIG_TEMPLATE = """
pricing:
  auto_fetch: false
server:
  host: 127.0.0.1
  api_port: 4001
db:
  path: {db_path}
models:
  test-model:
    cost:
      input: 2.0
      output: 6.0
      cacheRead: 0.5
  gpt-4.1:
    cost:
      input: 2.0
      output: 8.0
      cacheRead: 0.5
"""


PROJECT_MODULES = [
    "src.config.app",
    "src.config.runtime_ports",
    "src.api",
    "src.auth",
    "src.cli",
    "src.database",
    "src.pricing",
    "src.schema_migrations",
    "src.otlp",
    "src.ratelimit",
    "src.shared_state",
    "src.ingest_queue",
    "src.recorder",
    "src.utils",
    "src.provider_parser",
]


def clear_project_modules() -> None:
    removed_modules = set(PROJECT_MODULES)
    for module_name in PROJECT_MODULES:
        sys.modules.pop(module_name, None)
    # Clear package submodules (e.g. src.database.models)
    for key in list(sys.modules):
        if any(key.startswith(prefix + ".") for prefix in PROJECT_MODULES):
            sys.modules.pop(key, None)
            removed_modules.add(key)

    for module_name in removed_modules:
        package_name, _, attribute = module_name.rpartition(".")
        package = sys.modules.get(package_name)
        if package is not None and hasattr(package, attribute):
            delattr(package, attribute)


@pytest.fixture
def isolated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    config_dir = tmp_path / ".tokenage"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        CONFIG_TEMPLATE.format(db_path=tmp_path / "usage.db"),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENAGE_HOME", str(config_dir))
    monkeypatch.setenv("TOKENAGE_CONFIG", str(config_path))
    clear_project_modules()
    # Patch CONFIG["db"] so cached modules see the test's DB path.
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG,
        "db",
        {
            "path": str(tmp_path / "usage.db"),
            "url": f"sqlite:///{tmp_path / 'usage.db'}",
        },
    )
    yield tmp_path


@pytest.fixture
def load_module(isolated_home: Path) -> Callable[[str], ModuleType]:
    def load(module_name: str) -> ModuleType:
        module = importlib.import_module(module_name)
        if module_name in ("src.api", "src.otlp"):
            # Every request resolves to a user (the local owner is created on
            # demand), so these apps always need the schema.
            importlib.import_module("src.database").init_db()
        return module

    return load


@pytest.fixture
def config_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.config.app")


@pytest.fixture
def pricing_maps_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.pricing.maps")


@pytest.fixture
def api_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.api")


@pytest.fixture
def cli_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.cli")


@pytest.fixture
def costs_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.pricing.costs")


@pytest.fixture
def database_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.database")


@pytest.fixture
def schema_migrations_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.schema_migrations")


@pytest.fixture
def otlp_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.otlp")


@pytest.fixture
def utils_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.utils")


@pytest.fixture
def runtime_ports_module(load_module: Callable[[str], ModuleType]) -> ModuleType:
    return load_module("src.config.runtime_ports")


# ponytail: lightweight DB fixture — one DB per session, truncate tables between tests.
# Use this for database tests that just need a clean DB with latest schema.
# Use isolated_home for tests that need full module teardown or custom schemas.

# Tables to truncate between tests (in dependency order for FK safety).
# IMPORTANT: Add any new table created by schema migrations here, otherwise
# data will leak between fresh_db tests and cause spurious failures.
# Verify with: list of tables should match inspect(engine).get_table_names()
_TRUNCATE_TABLES = [
    "price_snapshots",
    "usage_hourly",
    "tool_calls",
    "evaluation_jobs",
    "sessions",
    "usage",
    "base_urls",
    "auth_tokens",
    "devices",
    "user_model_prices",
    "users",
]


# Postgres test harness (docs/quick/postgres-testing.md): gated on
# TOKENAGE_TEST_PG_URL. Every PG test skips when the env var is unset, which is
# the default locally. The CI "Postgres" job sets it when the database layer
# changed.

PG_URL_ENV_VAR = "TOKENAGE_TEST_PG_URL"


def _pg_test_url() -> str | None:
    return os.environ.get(PG_URL_ENV_VAR)


def _fresh_db_backends() -> list[str]:
    return ["sqlite", "postgres"] if _pg_test_url() else ["sqlite"]


@pytest.fixture(scope="session", params=_fresh_db_backends())
def _session_db(request, tmp_path_factory):
    """Session-scoped DB with latest schema — shared across all fresh_db tests.

    Parametrized over backends: postgres only joins in when
    TOKENAGE_TEST_PG_URL is set, doubling up every fresh_db-based test.
    """
    import src.database as db
    import src.schema_migrations as sm

    if request.param == "postgres":
        import src.database.models as models

        # Reuse pg_engine's skip-on-unset/unreachable guard instead of
        # connecting directly.
        engine = request.getfixturevalue("pg_engine")
        models.Base.metadata.drop_all(engine)
        # SQLite does not enforce foreign keys, and these tests insert rows
        # for users and devices they never create. Skip FK triggers on this
        # backend so they exercise the same logic; FK behaviour itself is
        # covered by the pg_db tests. Needs a superuser role.
        from sqlalchemy.engine import make_url

        url = (
            make_url(_pg_test_url())
            .update_query_dict({"options": "-c session_replication_role=replica"})
            .render_as_string(hide_password=False)
        )
        sm.migrate_database(url)
        return url

    db_path = str(tmp_path_factory.mktemp("dbsession") / "usage.db")
    db.init_db(db_path)
    sm.migrate_database(db_path)
    return db_path


@pytest.fixture
def fresh_db(_session_db: str, monkeypatch: pytest.MonkeyPatch):
    """Function-scoped clean database (truncated, not recreated).

    Returns an object with .db_path, .database_module, .schema_migrations_module.
    The schema is created once per test session; between tests all tables are truncated.
    CONFIG["db"] is patched so default-arg calls hit the test DB.
    """
    # Import each time — fast because sys.modules caches.
    # Only slow after isolated_home clears modules (rare, ~17 tests).
    import src.config.app
    import src.database as db
    import src.schema_migrations as sm

    db_path = _session_db
    db_url = db_path if "://" in db_path else f"sqlite:///{db_path}"

    # Patch CONFIG so functions called without db_path use the test DB
    monkeypatch.setitem(src.config.app.CONFIG["db"], "path", db_path)
    monkeypatch.setitem(src.config.app.CONFIG["db"], "url", db_url)

    # Truncate all tables for a clean slate (much faster than recreating DB)
    engine = db.get_engine(db_path)
    with engine.begin() as conn:
        for table in _TRUNCATE_TABLES:
            conn.execute(db.text(f"DELETE FROM {table}"))
    # The engine is shared across tests; ids it cached point at deleted rows.
    from src.pricing.snapshots import _SNAPSHOT_IDS

    _SNAPSHOT_IDS.pop(engine, None)

    return SimpleNamespace(
        db_path=db_path,
        database_module=db,
        schema_migrations_module=sm,
    )


@pytest.fixture(scope="session")
def pg_engine() -> Generator[Any, None, None]:
    """Session-scoped engine for the scratch Postgres database."""
    url = _pg_test_url()
    if not url:
        pytest.skip(f"{PG_URL_ENV_VAR} not set")
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError

    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"{PG_URL_ENV_VAR} set but unreachable: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_clean(pg_engine: Any) -> Generator[Any, None, None]:
    """Function-scoped: drop all tables so each test starts from empty."""
    import src.database.models as models

    models.Base.metadata.drop_all(pg_engine)
    yield pg_engine


@pytest.fixture
def pg_db(pg_clean: Any) -> Generator[str, None, None]:
    """Function-scoped scratch Postgres with the fresh-schema migration applied."""
    import src.schema_migrations as sm

    url = os.environ[PG_URL_ENV_VAR]
    sm.migrate_database(url)
    yield url


@pytest.fixture(autouse=True)
def _loopback_test_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default TestClient to a direct loopback request (the local owner).

    Starlette's default peer is "testclient" with Host "testserver", which the
    local provider correctly treats as remote. Tests of the remote path pass
    their own ``client=``/``base_url=``/headers. A client-only environment
    (CI's hosted-install job) has no fastapi and no TestClient to patch.
    """
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return

    original = TestClient.__init__

    def init(self, app, *args, **kwargs):
        if not args:
            kwargs.setdefault("client", ("127.0.0.1", 50000))
            kwargs.setdefault("base_url", "http://localhost")
        original(self, app, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def _isolated_shared_state_home(tmp_path, monkeypatch, request):
    """Every test gets its own transient store, including fresh_db-only tests."""
    if "scripts" in request.path.parts or "client" in request.path.parts:
        return
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path / ".tokenage"))
    monkeypatch.delenv("TOKENAGE_QUEUE_URL", raising=False)
