"""Request Logs keyset pagination on SQLite and the scratch Postgres backend."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

TS = 1776420000000000


@pytest.fixture
def cursor_api(api_module, fresh_db, monkeypatch):
    from src.database.models import ToolCall, Usage

    db = fresh_db.database_module
    with Session(db.get_engine(fresh_db.db_path)) as session:
        for i in range(18):
            row = Usage(
                id=f"row-{i:02}",
                user_id="test-user",
                device_id="test-device",
                ts=TS + i // 6,
                provider="test-provider" if i % 2 else "other-provider",
                model="test-model",
                client_source="codex",
                session_id="session",
                endpoint="otlp",
            )
            session.add(row)
            session.add(
                ToolCall(
                    tool_use_id=f"tool-{i}",
                    usage_id=row.id,
                    user_id="test-user",
                    tool_name="read",
                    ts=TS,
                )
            )
        session.add(
            Usage(
                id="foreign-row",
                user_id="other-user",
                ts=TS,
                provider="test-provider",
                model="test-model",
                endpoint="otlp",
            )
        )
        # Tool names must remain tenant-scoped even for a mismatched link.
        session.add(
            ToolCall(
                tool_use_id="foreign-tool",
                usage_id="row-17",
                user_id="other-user",
                tool_name="private-tool",
                ts=TS,
            )
        )
        session.commit()
    monkeypatch.setattr(api_module, "_request_user_id", lambda _: "test-user")
    monkeypatch.setattr(api_module, "enrich_rows", lambda rows: rows)
    return TestClient(api_module.app), fresh_db


def test_cursor_pages_equal_timestamps_once_and_offset_compatibility(cursor_api):
    client, fresh_db = cursor_api
    rows = []
    params = {"limit": 4}
    while True:
        response = client.get("/usage", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        if not page:
            break
        rows.extend(page)
        last = page[-1]
        params["before"] = f"{last['ts']}:{last['id']}"
        # Inserts while paging do not shift the cursor boundary.
        if len(rows) == 4:
            from src.database.models import Usage

            with Session(fresh_db.database_module.get_engine(fresh_db.db_path)) as s:
                s.add(
                    Usage(
                        id="new-row",
                        user_id="test-user",
                        ts=TS + 100,
                        provider="test-provider",
                        model="test-model",
                        endpoint="otlp",
                    )
                )
                s.commit()
    assert [row["id"] for row in rows] == [f"row-{i:02}" for i in reversed(range(18))]
    assert all(row["tool_names"] == "read" for row in rows)
    assert len({row["id"] for row in rows}) == 18
    response = client.get("/usage", params={"limit": 4, "offset": 5})
    assert [row["id"] for row in response.json()] == [row["id"] for row in rows[4:8]]


def test_cursor_combines_with_filters(cursor_api):
    client, _ = cursor_api
    params = {
        "limit": 2,
        "provider": "test-provider",
        "model": "test-model",
        "client_source": "codex",
        "device_id": "test-device",
        "session_id": "session",
        "tool_name": "read",
        "since": "2026-04-17T00:00:00Z",
        "until": "2026-04-18T00:00:00Z",
    }
    ids = []
    while True:
        response = client.get("/usage", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        if not page:
            break
        ids.extend(row["id"] for row in page)
        params["before"] = f"{page[-1]['ts']}:{page[-1]['id']}"
    assert ids == [f"row-{i:02}" for i in reversed(range(1, 18, 2))]


@pytest.mark.parametrize(
    "before",
    [
        "",
        "invalid",
        "123",
        "abc:row",
        "1:",
        "1:a:b",
        "1: ",
        "9223372036854775808:row",
        "9" * 5000 + ":row",
    ],
)
def test_bad_cursor_returns_400(api_module, before):
    response = TestClient(api_module.app).get("/usage", params={"before": before})
    assert response.status_code == 400


def test_usage_cursor_index_migration_is_idempotent(fresh_db):
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_usage_user_ts_id"))
    changes = fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path)
    assert "usage.ix_usage_user_ts_id" in changes
    index = next(
        i
        for i in inspect(engine).get_indexes("usage")
        if i["name"] == "ix_usage_user_ts_id"
    )
    assert index["column_names"] == ["user_id", "ts", "id"]
    assert fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path) == []
