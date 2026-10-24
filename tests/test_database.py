from datetime import UTC, datetime

import pytest

from tests.conftest import ingest_write

# Epoch constants for test timestamps
TS_2026_04_17_00 = 1776384000000000  # 2026-04-17T00:00:00+00:00
TS_2026_04_17_00_01 = 1776384060000000  # 2026-04-17T00:01:00+00:00
TS_2026_04_17_00_02 = 1776384120000000  # 2026-04-17T00:02:00+00:00
TS_2026_04_17_00_10 = 1776384600000000  # 2026-04-17T00:10:00+00:00
TS_2026_04_17_00_20 = 1776385200000000  # 2026-04-17T00:20:00+00:00
TS_2026_04_17_01 = 1776387600000000  # 2026-04-17T01:00:00+00:00
TS_2026_04_17_10 = 1776420000000000  # 2026-04-17T10:00:00+00:00
TS_2026_04_17_11 = 1776423600000000  # 2026-04-17T11:00:00+00:00
TS_2026_04_18_00 = 1776470400000000  # 2026-04-18T00:00:00+00:00
TS_2026_04_18_10 = 1776506400000000  # 2026-04-18T10:00:00+00:00
TS_2026_04_20_10 = 1776679200000000  # 2026-04-20T10:00:00+00:00
TS_2026_05_03_18 = 1777831200000000  # 2026-05-03T18:00:00+00:00
TS_2026_05_05_03_30 = 1777951800000000  # 2026-05-05T03:30:00+00:00
TS_2026_05_05_10 = 1777975200000000  # 2026-05-05T10:00:00+00:00
TS_2026_05_05_10_01 = 1777975260000000  # 2026-05-05T10:01:00+00:00
TS_2026_05_05_10_02 = 1777975320000000  # 2026-05-05T10:02:00+00:00
TS_2026_05_05_10_15 = 1777976100000000  # 2026-05-05T10:15:00+00:00
TS_2026_05_05_10_30 = 1777977000000000  # 2026-05-05T10:30:00+00:00
TS_2026_05_05_10_45 = 1777977900000000  # 2026-05-05T10:45:00+00:00
TS_2026_05_05_11_45 = 1777981500000000  # 2026-05-05T11:45:00+00:00
TS_2026_05_07_10 = 1778148000000000  # 2026-05-07T10:00:00+00:00
TS_2026_05_08_10 = 1778234400000000  # 2026-05-08T10:00:00+00:00
TS_2026_05_09_10 = 1778320800000000  # 2026-05-09T10:00:00+00:00
TS_2026_05_09_10_01 = 1778320860000000  # 2026-05-09T10:01:00+00:00
TS_2026_05_09_10_02 = 1778320920000000  # 2026-05-09T10:02:00+00:00
TS_2026_05_09_10_05 = 1778321100000000  # 2026-05-09T10:05:00+00:00
TS_2026_05_09_11 = 1778324400000000  # 2026-05-09T11:00:00+00:00
TS_2026_05_19_00 = 1779148800000000  # 2026-05-19T00:00:00+00:00
TS_2024_01_01_00 = 1704067200000000  # 2024-01-01T00:00:00Z
TS_2024_01_01_01 = 1704067201000000  # 2024-01-01T00:00:01Z


def test_init_db_log_usage_and_fetch_rows(database_module, isolated_home):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    base_url_id = ingest_write(
        database_module.get_or_create_base_url,
        "https://api.example.com/v1",
        db_path=db_path,
        provider_name="test-provider",
        source="proxy_config",
    )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="test-provider",
            model="test-model",
            client_source="proxy-client",
            session_id="session-1",
            endpoint="/v1/responses",
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=1,
            cached_tokens=2,
            total_tokens=15,
            latency_ms=123,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.00002,
            output_cost_usd=0.00003,
            total_cost_usd=0.00005,
            status=200,
            base_url_id=base_url_id,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    rows = database_module.fetch_recent_usage(limit=10, user_id="test-user")

    assert len(rows) == 1
    assert isinstance(rows[0]["id"], str)
    assert len(rows[0]["id"]) == 36

    expected = dict(rows[0])
    expected.pop("id")
    assert expected == {
        "ts": TS_2026_04_17_00,
        "provider": "test-provider",
        "model": "test-model",
        "client_source": "proxy-client",
        "session_id": "session-1",
        "endpoint": "/v1/responses",
        "prompt_tokens": 10,
        "prompt_length": 0,
        "completion_tokens": 5,
        "reasoning_tokens": 1,
        "cached_tokens": 2,
        "total_tokens": 15,
        "latency_ms": 123,
        "ttft_ms": None,
        "tool_tokens": None,
        "tool_names": None,
        "cache_creation_tokens": None,
        "input_cost_usd": 2e-05,
        "output_cost_usd": 3e-05,
        "total_cost_usd": 5e-05,
        "status": 200,
        "device_id": "test-device",
        "device_name": None,
        "base_url_id": base_url_id,
        "price_snapshot_id": None,
        "base_url": "https://api.example.com/v1",
    }


def test_log_usage_normalizes_model_before_rollups(fresh_db):
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database import get_engine, log_usage
    from src.database.models import SessionRecord, Usage, UsageHourly

    usage = Usage(
        device_id="test-device",
        ts=TS_2026_05_19_00,
        provider="CuStOm",
        model=" Space-Bunny-Free ",
        client_source="opencode",
        session_id="normalized-session",
        endpoint="otlp",
        user_id="test-user",
    )
    ingest_write(log_usage, usage, db_path=fresh_db.db_path)

    with Session(get_engine(fresh_db.db_path)) as session:
        assert session.get(Usage, usage.id).provider == "custom"
        assert session.get(Usage, usage.id).model == "stealth/space-bunny-alpha"
        assert session.scalars(select(UsageHourly)).one().provider == "custom"
        assert (
            session.scalars(select(UsageHourly)).one().model
            == "stealth/space-bunny-alpha"
        )
        record = session.get(SessionRecord, "normalized-session")
        assert record.primary_provider == "custom"
        assert json.loads(record.providers_json) == {"custom": 0}
        assert record.primary_model == "stealth/space-bunny-alpha"
        assert json.loads(record.models_json) == {"stealth/space-bunny-alpha": 0}


def test_fetch_recent_usage_returns_expected_row_shape(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="test-provider",
            model="test-model",
            client_source="proxy-client",
            session_id="session-shape",
            endpoint="/v1/responses",
            prompt_tokens=10,
            prompt_length=123,
            completion_tokens=5,
            reasoning_tokens=1,
            cached_tokens=2,
            total_tokens=15,
            latency_ms=123,
            ttft_ms=45,
            tool_tokens=3,
            cache_creation_tokens=4,
            input_cost_usd=0.00002,
            output_cost_usd=0.00003,
            total_cost_usd=0.00005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    row = database_module.fetch_recent_usage(limit=1, user_id="test-user")[0]

    assert set(row) == {
        "id",
        "ts",
        "provider",
        "model",
        "client_source",
        "session_id",
        "endpoint",
        "prompt_tokens",
        "prompt_length",
        "completion_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
        "latency_ms",
        "ttft_ms",
        "tool_tokens",
        "tool_names",
        "cache_creation_tokens",
        "input_cost_usd",
        "output_cost_usd",
        "total_cost_usd",
        "status",
        "device_id",
        "device_name",
        "base_url_id",
        "price_snapshot_id",
        "base_url",
    }


def test_fetch_recent_usage_aggregates_tool_names(fresh_db):
    """Test that tool_names aggregates multiple tool calls per usage into comma-separated string."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    from src.recorder import record_tool_call

    database_module.init_db(db_path)

    # Insert a usage row (log_usage doesn't return the object, but populates its id)
    usage_obj = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_00,
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        client_source="claude-code",
        session_id="session-tools",
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=50,
        reasoning_tokens=None,
        cached_tokens=None,
        total_tokens=150,
        latency_ms=200,
        ttft_ms=None,
        tool_tokens=None,
        cache_creation_tokens=None,
        input_cost_usd=0.002,
        output_cost_usd=0.003,
        total_cost_usd=0.005,
        status=200,
        base_url_id=None,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage_obj, db_path=db_path)

    # Insert multiple tool calls for this usage
    for i, tool_name in enumerate(["read_file", "edit_file", "bash"]):
        ingest_write(
            record_tool_call,
            tool_use_id=f"tool_{i}",
            usage_id=usage_obj.id,
            session_id=usage_obj.session_id,
            tool_name=tool_name,
            client_source="claude-code",
            ts=TS_2026_04_17_00 + i,
            db_path=db_path,
            user_id="test-user",
        )

    # Fetch and verify aggregated tool names
    rows = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert rows[0]["tool_names"] is not None
    # Should contain all three tool names (order may vary)
    tool_names_set = set(rows[0]["tool_names"].split(","))
    assert tool_names_set == {"read_file", "edit_file", "bash"}


def test_fetch_recent_usage_no_tool_calls(fresh_db):
    """Test that tool_names is None when no tool calls exist."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            client_source="claude-code",
            session_id="session-no-tools",
            endpoint="/v1/messages",
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=150,
            latency_ms=200,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.002,
            output_cost_usd=0.003,
            total_cost_usd=0.005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    rows = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert rows[0]["tool_names"] is None


def test_usage_filters_includes_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for source in ["claude-code", "codex"]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_04_17_00,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=source,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=10,
                completion_tokens=5,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=15,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.00002,
                output_cost_usd=0.00003,
                total_cost_usd=0.00005,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    rows = database_module.fetch_recent_usage(
        limit=10, client_source="claude-code", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert rows[0]["client_source"] == "claude-code"

    rows = database_module.fetch_recent_usage(
        limit=10, client_source="codex", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert rows[0]["client_source"] == "codex"

    rows = database_module.fetch_recent_usage(
        limit=10, db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 2


def test_count_usage_filters_by_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for source in ["claude-code", "claude-code", "codex"]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_04_17_00,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=source,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=10,
                completion_tokens=5,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=15,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.00002,
                output_cost_usd=0.00003,
                total_cost_usd=0.00005,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    assert (
        database_module.count_usage(client_source="claude-code", user_id="test-user")
        == 2
    )
    assert database_module.count_usage(client_source="codex", user_id="test-user") == 1
    assert database_module.count_usage(user_id="test-user") == 3


def test_aggregate_usage_by_period_filters_by_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, source, tokens in [
        (TS_2026_04_17_00, "claude-code", 150),
        (TS_2026_04_18_00, "codex", 300),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=source,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=100,
                completion_tokens=50,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=tokens,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.002,
                output_cost_usd=0.003,
                total_cost_usd=0.005,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_usage_by_period(
        client_source="claude-code", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["total_tokens"] == 150

    result = database_module.aggregate_usage_by_period(user_id="test-user")
    assert len(result) == 2


def test_summarize_usage_by_model_includes_avg_effective_price(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="test-provider",
            model="test-model",
            client_source="proxy-client",
            session_id=None,
            endpoint="/v1/responses",
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=0,
            total_tokens=150,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.0002,
            output_cost_usd=0.0003,
            total_cost_usd=0.0005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_01,
            provider="test-provider",
            model="test-model",
            client_source="proxy-client",
            session_id=None,
            endpoint="/v1/responses",
            prompt_tokens=200,
            completion_tokens=100,
            reasoning_tokens=None,
            cached_tokens=0,
            total_tokens=300,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.0004,
            output_cost_usd=0.0006,
            total_cost_usd=0.001,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    rows = database_module.summarize_usage_by_model(user_id="test-user")
    assert rows[0]["avg_effective_price_usd"] == pytest.approx(0.0015 / 450, abs=1e-8)


def test_summarize_usage_by_provider_includes_avg_effective_price(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for model, total_tokens, total_cost in [
        ("test-model", 150, 0.0005),
        ("gpt-4.1", 300, 0.0010),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_04_17_00,
                provider="test-provider",
                model=model,
                client_source="proxy-client",
                session_id=None,
                endpoint="/v1/responses",
                prompt_tokens=100,
                completion_tokens=50,
                reasoning_tokens=None,
                cached_tokens=0,
                total_tokens=total_tokens,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=total_cost / 2,
                output_cost_usd=total_cost / 2,
                total_cost_usd=total_cost,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    rows = database_module.summarize_usage_by_provider(user_id="test-user")
    assert rows[0]["avg_effective_price_usd"] == pytest.approx(0.0015 / 450, abs=1e-8)


def test_init_db_does_not_mutate_existing_usage_table(database_module, isolated_home):
    import sqlite3

    db_file = isolated_home / "usage.db"
    connection = sqlite3.connect(db_file)
    connection.execute(
        """
        CREATE TABLE usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            reasoning_tokens INTEGER,
            cached_tokens INTEGER,
            total_tokens INTEGER,
            latency_ms INTEGER,
            ttft_ms INTEGER,
            tool_tokens INTEGER,
            cache_creation_tokens INTEGER,
            input_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
            output_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
            total_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
            status INTEGER
        )
        """
    )
    connection.execute(
        """
        INSERT INTO usage (
            ts,
            provider,
            model,
            endpoint,
            prompt_tokens,
            completion_tokens,
            reasoning_tokens,
            cached_tokens,
            total_tokens,
            latency_ms,
            ttft_ms,
            tool_tokens,
            cache_creation_tokens,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-04-17T00:00:00+00:00",
            "test-provider",
            "test-model",
            "/v1/responses",
            10,
            5,
            1,
            2,
            15,
            123,
            None,
            None,
            None,
            200,
        ),
    )
    connection.commit()
    connection.close()

    database_module.init_db(str(db_file))

    connection = sqlite3.connect(db_file)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(usage)").fetchall()
    }
    connection.close()

    assert "prompt_length" not in columns
    assert "base_url_id" not in columns
    assert "client_source" not in columns
    assert "session_id" not in columns


def test_get_usage_high_watermark_ts_returns_latest_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    assert (
        database_module.get_usage_high_watermark_ts(
            db_path=db_path, user_id="test-user"
        )
        == 0
    )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="test-provider",
            model="test-model",
            client_source="codex",
            session_id="session-1",
            endpoint="otlp",
            prompt_tokens=10,
            prompt_length=0,
            completion_tokens=5,
            reasoning_tokens=1,
            cached_tokens=2,
            total_tokens=15,
            latency_ms=100,
            ttft_ms=20,
            tool_tokens=3,
            cache_creation_tokens=4,
            input_cost_usd=0.1,
            output_cost_usd=0.2,
            total_cost_usd=0.3,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    assert (
        database_module.get_usage_high_watermark_ts(
            db_path=db_path, user_id="test-user"
        )
        == TS_2026_04_17_00
    )


def test_get_db_url_prefers_env_override_without_explicit_db(
    database_module, isolated_home, monkeypatch
):
    override = f"sqlite:///{isolated_home / 'run.db'}"
    monkeypatch.setenv("TOKENAGE_DB_URL", override)

    assert database_module.get_db_url() == override


def test_get_db_url_explicit_path_wins_over_env_override(
    database_module, isolated_home, monkeypatch
):
    override = f"sqlite:///{isolated_home / 'run.db'}"
    explicit = isolated_home / "main.db"
    monkeypatch.setenv("TOKENAGE_DB_URL", override)

    assert database_module.get_db_url(str(explicit)) == f"sqlite:///{explicit}"


def test_get_db_url_explicit_url_wins_over_env_override(
    database_module, isolated_home, monkeypatch
):
    override = f"sqlite:///{isolated_home / 'run.db'}"
    explicit = f"sqlite:///{isolated_home / 'main.db'}"
    monkeypatch.setenv("TOKENAGE_DB_URL", override)

    assert database_module.get_db_url(explicit) == explicit


def test_postgres_connect_args_bound_database_waits(database_module):
    from src.database.engine import _connect_args

    assert _connect_args("postgresql+psycopg://db.example/usage") == {
        "connect_timeout": 5,
        "tcp_user_timeout": 30_000,
    }
    assert _connect_args("postgresql://db.example/usage")["tcp_user_timeout"] == 30_000
    assert _connect_args("sqlite:///usage.db") == {"check_same_thread": False}
    assert _connect_args("mysql://db.example/usage") == {}


def test_reprice_estimated_rows_rebinds_and_updates_costs(
    database_module, isolated_home
):
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice.db")
    database_module.init_db(db_path)
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_03_18,
            provider="test-provider",
            model="test-model",
            client_source="codex",
            endpoint="otlp",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=0,
            total_tokens=1500,
            input_cost_usd=0.0,
            output_cost_usd=0.0,
            total_cost_usd=0.0,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    model_costs = {"test-model": ModelCost(input=2.0, output=4.0, cache_read=0.5)}
    summary = database_module.reprice_estimated_rows(
        user_id="test-user",
        model_costs=model_costs,
        model_cost_sources={},
        db_path=db_path,
    )

    assert summary == {"candidates": 1, "repriced": 1, "skipped": 0}
    rows = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )
    assert rows[0]["price_snapshot_id"] is not None
    # 1000*2/1e6 + 500*4/1e6 = 0.004
    assert rows[0]["total_cost_usd"] == pytest.approx(0.004)

    # Already bound: a second pass has nothing to do.
    again = database_module.reprice_estimated_rows(
        user_id="test-user",
        model_costs=model_costs,
        model_cost_sources={},
        db_path=db_path,
    )
    assert again == {"candidates": 0, "repriced": 0, "skipped": 0}


def test_reprice_estimated_rows_respects_filters_and_skips_unresolvable(
    database_module, isolated_home
):
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice-filter.db")
    database_module.init_db(db_path)
    for provider, model in (("p1", "m1"), ("p2", "m2")):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_03_18,
                provider=provider,
                model=model,
                client_source="codex",
                endpoint="otlp",
                prompt_tokens=1000,
                completion_tokens=0,
                cached_tokens=0,
                total_tokens=1000,
                input_cost_usd=0.0,
                output_cost_usd=0.0,
                total_cost_usd=0.0,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    model_costs = {"m1": ModelCost(input=2.0, output=4.0, cache_read=0.5)}
    common = {
        "user_id": "test-user",
        "model_costs": model_costs,
        "model_cost_sources": {},
        "db_path": db_path,
    }

    # Only p1 is in scope and its model resolves.
    assert database_module.reprice_estimated_rows(provider="p1", **common) == {
        "candidates": 1,
        "repriced": 1,
        "skipped": 0,
    }
    # p2 is in scope but its model does not resolve: skipped, left unbound.
    assert database_module.reprice_estimated_rows(provider="p2", **common) == {
        "candidates": 1,
        "repriced": 0,
        "skipped": 1,
    }

    rows = database_module.fetch_recent_usage(
        limit=10, db_path=db_path, user_id="test-user"
    )
    by_provider = {row["provider"]: row for row in rows}
    assert by_provider["p1"]["price_snapshot_id"] is not None
    assert by_provider["p2"]["price_snapshot_id"] is None


def test_reprice_counts_snapshot_failure_as_skipped(
    database_module, isolated_home, monkeypatch
):
    from src.pricing import snapshots
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice-snapshot-fail.db")
    database_module.init_db(db_path)
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_03_18,
            provider="p1",
            model="m1",
            client_source="codex",
            endpoint="otlp",
            prompt_tokens=1000,
            completion_tokens=0,
            cached_tokens=0,
            total_tokens=1000,
            input_cost_usd=0.0,
            output_cost_usd=0.0,
            total_cost_usd=0.0,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    def boom(**_kwargs):
        raise RuntimeError("snapshot store unavailable")

    monkeypatch.setattr(snapshots, "ensure_price_snapshot", boom)

    summary = database_module.reprice_estimated_rows(
        user_id="test-user",
        model_costs={"m1": ModelCost(input=2.0, output=4.0, cache_read=0.5)},
        model_cost_sources={},
        db_path=db_path,
    )

    # Costs were recomputed but the row stays unbound, so it is not "repriced".
    assert summary == {"candidates": 1, "repriced": 0, "skipped": 1}
    rows = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )
    assert rows[0]["price_snapshot_id"] is None


def _batch_prices():
    from src.pricing.models import ModelCost, ModelTier, TimeRate

    return {
        "m-flat": ModelCost(input=2.0, output=4.0, cache_read=0.5),
        "m-tier": ModelCost(
            input=1.0,
            output=1.0,
            cache_read=0.0,
            tiers=(
                ModelTier(
                    min_tokens=0, max_tokens=1000, input=1.0, output=1.0, cache_read=0.0
                ),
                ModelTier(
                    min_tokens=1000,
                    max_tokens=None,
                    input=3.0,
                    output=3.0,
                    cache_read=0.0,
                ),
            ),
        ),
        "m-tod": ModelCost(
            input=1.0,
            output=1.0,
            cache_read=0.0,
            time_rates=(
                TimeRate(
                    days=None,
                    start_minute=9 * 60,
                    end_minute=12 * 60,
                    cost=ModelCost(input=5.0, output=5.0, cache_read=0.0),
                ),
            ),
        ),
    }


# (ts, model, session suffix, prompt_tokens): two dates, two sessions plus a
# session-less row, a tier boundary, a time-of-day window and unresolvable rows.
_BATCH_ROWS = (
    (TS_2026_05_05_10, "m-flat", "s1", 500),
    (TS_2026_05_05_10_01, "m-tier", "s1", 5000),
    (TS_2026_05_05_10_02, "m-missing", "s1", 100),
    (TS_2026_05_05_10_15, "m-flat", "s2", 800),
    (TS_2026_05_05_10_30, "m-tod", "s2", 300),
    (TS_2026_05_05_11_45, "m-flat", "s1", 200),
    (TS_2026_05_07_10, "m-tier", "s1", 400),
    (TS_2026_05_07_10, "m-tod", "s2", 700),
    (TS_2026_05_08_10, "m-flat", None, 900),
    (TS_2026_05_08_10, "m-missing", "s2", 50),
)


def _batch_user(database_module, db_path, user_id):
    """Real users and devices rows (device id ``<user>-device``), so the
    usage/session foreign keys hold on Postgres."""
    from sqlalchemy.orm import Session

    from src.database.models import Device, User

    with Session(database_module.get_engine(db_path)) as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, email=f"{user_id}@example.com", created_at=0))
            session.flush()
            session.add(
                Device(
                    id=f"{user_id}-device",
                    user_id=user_id,
                    installation_hash=user_id,
                    device_name=user_id,
                    created_at=0,
                )
            )
            session.commit()


def _log_batch_rows(database_module, db_path, user_id):
    _batch_user(database_module, db_path, user_id)
    for i, (ts, model, session, prompt) in enumerate(_BATCH_ROWS):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                id=f"{user_id}-{i}",
                device_id=f"{user_id}-device",
                ts=ts,
                provider="p-" + model,
                model=model,
                client_source="codex",
                session_id=f"{user_id}-{session}" if session else None,
                endpoint="otlp",
                prompt_tokens=prompt,
                completion_tokens=100,
                cached_tokens=0,
                total_tokens=prompt + 100,
                input_cost_usd=0.25,
                output_cost_usd=0.75,
                total_cost_usd=1.0,
                status=200,
                user_id=user_id,
            ),
            db_path=db_path,
        )


def _batch_state(database_module, db_path, user_id):
    """Usage, usage_hourly and session state with the user id stripped out."""
    import json

    from sqlalchemy import select

    from src.database.models import SessionRecord, UsageHourly

    with database_module.Session(database_module.get_engine(db_path)) as session:
        usage = {
            u.id.removeprefix(user_id): (
                float(u.input_cost_usd),
                float(u.output_cost_usd),
                float(u.total_cost_usd),
                u.price_snapshot_id,
            )
            for u in session.scalars(
                select(database_module.Usage).where(
                    database_module.Usage.user_id == user_id
                )
            )
        }
        daily = {
            (d.hour, d.model): (
                float(d.input_cost_usd),
                float(d.output_cost_usd),
                float(d.total_cost_usd),
            )
            for d in session.scalars(
                select(UsageHourly).where(UsageHourly.user_id == user_id)
            )
        }
        sessions = {
            s.session_id.removeprefix(user_id): (
                float(s.total_cost_usd),
                json.loads(s.models_json),
                json.loads(s.providers_json),
                s.primary_model,
                s.primary_provider,
            )
            for s in session.scalars(
                select(SessionRecord).where(SessionRecord.user_id == user_id)
            )
        }
    return usage, daily, sessions


def test_reprice_batches_match_per_row_recalculation(fresh_db, monkeypatch):
    """Chunked repricing ends in the same state as one row per transaction
    (the old path), and that state is internally consistent."""
    from src.database import usage as usage_module
    from src.pricing.costs import calculate_costs, resolve_pricing

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    prices = _batch_prices()
    common = {"model_costs": prices, "model_cost_sources": {}, "db_path": db_path}
    states = {}
    for user_id, chunk in (("per-row", 1), ("chunked", 3), ("one-chunk", 500)):
        _log_batch_rows(database_module, db_path, user_id)
        monkeypatch.setattr(usage_module, "_REPRICE_CHUNK", chunk)
        summary = database_module.reprice_estimated_rows(user_id=user_id, **common)
        assert summary == {"candidates": 10, "repriced": 8, "skipped": 2}
        states[user_id] = _batch_state(database_module, db_path, user_id)

    usage, daily, sessions = states["per-row"]
    for other in ("chunked", "one-chunk"):
        o_usage, o_daily, o_sessions = states[other]
        assert o_usage == usage
        assert o_daily.keys() == daily.keys()
        for key in daily:
            assert o_daily[key] == pytest.approx(daily[key])
        # Cost maps are applied row by row in order, so they match exactly.
        assert {k: v[1:] for k, v in o_sessions.items()} == {
            k: v[1:] for k, v in sessions.items()
        }
        assert {k: v[0] for k, v in o_sessions.items()} == pytest.approx(
            {k: v[0] for k, v in sessions.items()}
        )

    # Independent check of the end state against the pricing functions.
    expected_daily: dict = {}
    expected_sessions: dict = {}
    for i, (ts, model, session, prompt) in enumerate(_BATCH_ROWS):
        resolved = resolve_pricing(model, ts, prices, {}, {})
        if resolved.match is None:
            assert usage[f"-{i}"] == (0.25, 0.75, 1.0, None)
            total = 1.0
        else:
            costs = calculate_costs(
                prompt_tokens=prompt,
                completion_tokens=100,
                cached_tokens=0,
                cache_creation_tokens=None,
                model_cost=resolved.cost,
            )
            total = float(costs["total_cost_usd"])
            assert usage[f"-{i}"][:3] == pytest.approx(
                (float(costs["input_cost_usd"]), float(costs["output_cost_usd"]), total)
            )
            assert usage[f"-{i}"][3] is not None
        hour = usage_module._usage_hour(database_module.Usage(ts=ts))
        expected_daily[(hour, model)] = expected_daily.get((hour, model), 0) + total
        if session:
            models = expected_sessions.setdefault(f"-{session}", {})
            models[model] = models.get(model, 0) + total
    # The time-of-day window prices the 10:30 m-tod row at 5/M, not 1/M.
    assert usage["-4"][2] == pytest.approx((300 + 100) * 5 / 1e6)
    assert {k: v[2] for k, v in daily.items()} == pytest.approx(expected_daily)
    assert set(sessions) == set(expected_sessions)
    for key, models in expected_sessions.items():
        total, models_json, providers_json, primary_model, primary_provider = sessions[
            key
        ]
        assert total == pytest.approx(sum(models.values()))
        assert models_json == pytest.approx(models)
        assert providers_json == pytest.approx(
            {"p-" + m: cost for m, cost in models.items()}
        )
        assert primary_model == max(models, key=models.get)
        assert primary_provider == "p-" + primary_model

    # A second run finds only the unresolvable rows and changes nothing.
    for user_id in states:
        again = database_module.reprice_estimated_rows(user_id=user_id, **common)
        assert again == {"candidates": 2, "repriced": 0, "skipped": 2}
        assert _batch_state(database_module, db_path, user_id) == states[user_id]


def test_reprice_batch_snapshot_failure_still_updates_costs(fresh_db, monkeypatch):
    from src.database import usage as usage_module
    from src.pricing import snapshots

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _log_batch_rows(database_module, db_path, "u")
    monkeypatch.setattr(usage_module, "_REPRICE_CHUNK", 3)

    def boom(**_kwargs):
        raise RuntimeError("snapshot store unavailable")

    monkeypatch.setattr(snapshots, "ensure_price_snapshot", boom)

    summary = database_module.reprice_estimated_rows(
        user_id="u",
        model_costs=_batch_prices(),
        model_cost_sources={},
        db_path=db_path,
    )

    assert summary == {"candidates": 10, "repriced": 0, "skipped": 10}
    usage, _daily, sessions = _batch_state(database_module, db_path, "u")
    assert all(row[3] is None for row in usage.values())
    # m-flat row 0: 500*2/1e6 + 100*4/1e6, written despite the unbound snapshot.
    assert usage["-0"][2] == pytest.approx(0.0014)
    s1_rows = [i for i, row in enumerate(_BATCH_ROWS) if row[2] == "s1"]
    assert sessions["-s1"][0] == pytest.approx(sum(usage[f"-{i}"][2] for i in s1_rows))


def test_summarize_usage_window_groups_by_session_source_and_model(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    rows = [
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="openai",
            model="gpt-test",
            client_source="codex",
            session_id="conv-1",
            endpoint="otlp",
            prompt_tokens=100,
            prompt_length=0,
            completion_tokens=20,
            reasoning_tokens=5,
            cached_tokens=40,
            total_tokens=120,
            latency_ms=1000,
            ttft_ms=100,
            tool_tokens=3,
            cache_creation_tokens=0,
            input_cost_usd=0.10,
            output_cost_usd=0.20,
            total_cost_usd=0.30,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00_01,
            provider="openai",
            model="gpt-test",
            client_source="codex",
            session_id="conv-1",
            endpoint="otlp",
            prompt_tokens=50,
            prompt_length=0,
            completion_tokens=10,
            reasoning_tokens=2,
            cached_tokens=10,
            total_tokens=60,
            latency_ms=3000,
            ttft_ms=None,
            tool_tokens=1,
            cache_creation_tokens=0,
            input_cost_usd=0.05,
            output_cost_usd=0.10,
            total_cost_usd=0.15,
            status=500,
            base_url_id=None,
            user_id="test-user",
        ),
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00_02,
            provider="anthropic",
            model="claude-test",
            client_source="claude-code",
            session_id="claude-session",
            endpoint="otlp",
            prompt_tokens=200,
            prompt_length=0,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=100,
            total_tokens=250,
            latency_ms=None,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=30,
            input_cost_usd=0.20,
            output_cost_usd=0.50,
            total_cost_usd=0.70,
            status=None,
            base_url_id=None,
            user_id="test-user",
        ),
    ]

    for row in rows:
        ingest_write(database_module.log_usage, row, db_path=db_path)

    summary = database_module.summarize_usage_window(
        after_ts=0, db_path=db_path, user_id="test-user"
    )

    assert summary["window"] == {
        "after_ts": 0,
        "until_ts": TS_2026_04_17_00_02,
        "row_count": 3,
    }
    assert summary["summary"]["requests"] == 3
    assert summary["summary"]["successful_requests"] == 2
    assert summary["summary"]["failed_requests"] == 1
    assert summary["summary"]["prompt_tokens"] == 350
    assert summary["summary"]["completion_tokens"] == 80
    assert summary["summary"]["reasoning_tokens"] == 7
    assert summary["summary"]["cached_tokens"] == 150
    assert summary["summary"]["tool_tokens"] == 4
    assert summary["summary"]["cache_creation_tokens"] == 30
    assert summary["summary"]["total_tokens"] == 430
    # cache_hit_rate = cached / (prompt + cache_creation), since cache_creation
    # tokens are a disjoint bucket on top of prompt_tokens (Anthropic semantics).
    assert summary["summary"]["cache_hit_rate"] == 150 / (350 + 30)
    assert summary["summary"]["avg_latency_ms"] == 2000
    assert summary["summary"]["avg_ttft_ms"] == 100
    assert summary["summary"]["total_cost_usd"] == 1.15

    assert summary["sessions"][0]["session_id"] == "claude-session"
    assert summary["sessions"][1]["session_id"] == "conv-1"
    assert summary["client_sources"][0]["client_source"] == "claude-code"
    assert summary["client_sources"][1]["client_source"] == "codex"
    assert summary["models"][0]["provider"] == "anthropic"
    assert summary["models"][0]["model"] == "claude-test"
    assert summary["models"][1]["provider"] == "openai"
    assert summary["models"][1]["model"] == "gpt-test"


def test_hour_bounds_hour_aligned_until_ends_previous_bucket():
    from src.database.usage import _hour_bounds

    h = 1776384000  # 2026-04-17T00:00:00Z
    assert _hour_bounds(None, "2026-04-17T00:00:00Z") == (None, h - 3600)
    assert _hour_bounds(None, "2026-04-17T00:00:01Z") == (None, h)
    assert _hour_bounds("2026-04-17T00:30:00Z", "2026-04-17") == (h, h + 82800)


def test_summarize_usage_window_filters_after_and_until_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for index in range(3):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_04_17_00 + index * 60_000_000,
                provider="test-provider",
                model="test-model",
                client_source="codex",
                session_id=f"session-{index}",
                endpoint="otlp",
                prompt_tokens=10,
                prompt_length=0,
                completion_tokens=5,
                reasoning_tokens=0,
                cached_tokens=1,
                total_tokens=15,
                latency_ms=100,
                ttft_ms=10,
                tool_tokens=0,
                cache_creation_tokens=0,
                input_cost_usd=0,
                output_cost_usd=0,
                total_cost_usd=0,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    summary = database_module.summarize_usage_window(
        after_ts=TS_2026_04_17_00,
        until_ts=TS_2026_04_17_00_01,
        db_path=db_path,
        user_id="test-user",
    )

    assert summary["window"]["row_count"] == 1
    assert summary["summary"]["requests"] == 1
    assert summary["sessions"][0]["session_id"] == "session-1"


def test_summarize_usage_window_filters_metadata_and_includes_rows(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    rows = [
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="openai",
            model="gpt-test",
            client_source="codex",
            session_id="conv-1",
            endpoint="otlp",
            prompt_tokens=10,
            prompt_length=0,
            completion_tokens=5,
            reasoning_tokens=0,
            cached_tokens=1,
            total_tokens=15,
            latency_ms=100,
            ttft_ms=10,
            tool_tokens=0,
            cache_creation_tokens=0,
            input_cost_usd=0,
            output_cost_usd=0,
            total_cost_usd=0,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_18_00,
            provider="openai",
            model="gpt-other",
            client_source="codex",
            session_id="conv-2",
            endpoint="otlp",
            prompt_tokens=20,
            prompt_length=0,
            completion_tokens=10,
            reasoning_tokens=0,
            cached_tokens=2,
            total_tokens=30,
            latency_ms=200,
            ttft_ms=20,
            tool_tokens=0,
            cache_creation_tokens=0,
            input_cost_usd=0,
            output_cost_usd=0,
            total_cost_usd=0,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
    ]

    for row in rows:
        ingest_write(database_module.log_usage, row, db_path=db_path)

    summary = database_module.summarize_usage_window(
        after_ts=0,
        since="2026-04-17T00:00:00+00:00",
        until="2026-04-17T23:59:59+00:00",
        client_source="codex",
        session_id="conv-1",
        provider="openai",
        model="gpt-test",
        include_rows=True,
        db_path=db_path,
        user_id="test-user",
    )

    assert summary["window"]["row_count"] == 1
    assert summary["summary"]["total_tokens"] == 15
    assert summary["rows"][0]["session_id"] == "conv-1"
    assert summary["rows"][0]["model"] == "gpt-test"


def test_summarize_usage_window_empty_window(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    summary = database_module.summarize_usage_window(
        after_ts=TS_2026_04_17_00, db_path=db_path, user_id="test-user"
    )

    assert summary["window"] == {
        "after_ts": TS_2026_04_17_00,
        "until_ts": TS_2026_04_17_00,
        "row_count": 0,
    }
    assert summary["summary"]["requests"] == 0
    assert summary["summary"]["cache_hit_rate"] == 0.0
    assert summary["sessions"] == []
    assert summary["client_sources"] == []
    assert summary["models"] == []


def test_aggregate_usage_by_period_includes_cost_totals(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00_10,
            provider="test-provider",
            model="test-model",
            endpoint="/v1/responses",
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=None,
            cached_tokens=2,
            total_tokens=15,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.00002,
            output_cost_usd=0.00003,
            total_cost_usd=0.00005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00_20,
            provider="test-provider",
            model="test-model",
            endpoint="/v1/responses",
            prompt_tokens=20,
            completion_tokens=10,
            reasoning_tokens=None,
            cached_tokens=4,
            total_tokens=30,
            latency_ms=200,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.00004,
            output_cost_usd=0.00006,
            total_cost_usd=0.0001,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    daily = database_module.aggregate_usage_by_period(
        granularity="day", user_id="test-user"
    )

    expected = {
        "period": "2026-04-17",
        "requests": 2,
        "prompt_tokens": 30,
        "completion_tokens": 15,
        "avg_throughput": 50.0,
        "cached_tokens": 6,
        "cache_creation_tokens": 0,
        "total_tokens": 45,
        "input_cost_usd": 6e-05,
        "output_cost_usd": 9e-05,
        "total_cost_usd": 0.00015,
        "successful_requests": 2,
        "failed_requests": 0,
        "latency_sum_ms": 300,
        "avg_latency_ms": 150.0,
    }
    assert len(daily) == 1
    assert {k: daily[0][k] for k in expected} == expected


def test_throughput_ignores_tool_calls(fresh_db):
    """Every source reports latency without tool time, so throughput is
    completion_tokens / latency_sum and never reads tool_calls."""
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="opencode",
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=100,
        total_tokens=200,
        latency_ms=1000,
        status=200,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
        total_cost_usd=0.0,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage, db_path=db_path)
    ingest_write(
        record_tool_call,
        tool_use_id="tool-throughput-1",
        usage_id=usage.id,
        tool_name="bash",
        duration_ms=500,
        ts=TS_2026_04_17_10 // 1000,
        db_path=db_path,
        user_id="test-user",
    )

    for rows in (
        database_module.aggregate_usage_by_period(user_id="test-user"),
        database_module.summarize_usage_by_model(user_id="test-user"),
        database_module.summarize_usage_by_source(user_id="test-user"),
        database_module.summarize_usage_by_provider(user_id="test-user"),
        database_module.aggregate_daily_by_dimension(
            user_id="test-user", dimension="model"
        ),
    ):
        assert rows[0]["avg_throughput"] == 100.0
        assert "tool_duration_sum_ms" not in rows[0]


def test_fetch_tool_calls_has_no_row_relative_start(fresh_db):
    """Latency excludes tool time for every source, so no start is derived."""
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="opencode",
        endpoint="otlp",
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        latency_ms=1000,
        status=200,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
        total_cost_usd=0.0,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage, db_path=db_path)
    ingest_write(
        record_tool_call,
        tool_use_id="tool-short",
        usage_id=usage.id,
        tool_name="read",
        duration_ms=300,
        ts=usage.ts // 1000 - 100,
        db_path=db_path,
        user_id="test-user",
    )

    calls = database_module.fetch_tool_calls(
        usage_id=usage.id, db_path=db_path, user_id="test-user"
    )
    assert ["start_ms" in call for call in calls] == [False]


def test_get_or_create_base_url_reuses_exact_url_and_updates_metadata(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    base_url_id = ingest_write(
        database_module.get_or_create_base_url,
        "https://gateway.example/v1",
        db_path=db_path,
    )
    same_id = ingest_write(
        database_module.get_or_create_base_url,
        "https://gateway.example/v1",
        db_path=db_path,
        provider_name="OpenAI",
        source="codex_config",
    )

    assert same_id == base_url_id

    with database_module.get_engine(db_path).connect() as connection:
        rows = connection.execute(database_module.select(database_module.BaseUrl)).all()

    assert len(rows) == 1
    row = rows[0]
    assert row.base_url == "https://gateway.example/v1"
    assert row.provider_name == "openai"
    assert row.source == "codex_config"


def test_get_or_create_base_url_retries_savepoint_after_duplicate_insert(
    fresh_db, monkeypatch
):
    from sqlalchemy.orm import Session

    db = fresh_db.database_module
    url = "https://race.example/v1"
    winner = ingest_write(db.get_or_create_base_url, url, db_path=fresh_db.db_path)
    with Session(db.get_engine(fresh_db.db_path)) as session:
        scalar = session.scalar
        calls = []

        def race(statement):
            calls.append(1)
            # Simulate a concurrent insertion after the first lookup.
            return None if len(calls) == 1 else scalar(statement)

        monkeypatch.setattr(session, "scalar", race)
        assert (
            db.get_or_create_base_url(
                url, provider_name="OpenAI", source="proxy_config", session=session
            )
            == winner
        )
        assert len(calls) == 2
        assert session.get(db.BaseUrl, winner).provider_name == "openai"
        session.commit()


def test_upsert_hourly_aggregate_inserts_and_updates(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage1 = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
        session_id="s1",
        endpoint="/v1/messages",
        prompt_tokens=100,
        prompt_length=500,
        completion_tokens=50,
        reasoning_tokens=10,
        cached_tokens=20,
        total_tokens=160,
        latency_ms=200,
        ttft_ms=50,
        tool_tokens=5,
        cache_creation_tokens=0,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
        status=200,
        base_url_id=None,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage1, db_path=db_path)

    # Verify insert
    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageHourly)))
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert rows[0].prompt_tokens == 100
    assert rows[0].latency_sum_ms == 200
    assert rows[0].successful_requests == 1
    assert rows[0].failed_requests == 0

    # Same hour, provider, model, client_source -> upsert
    usage2 = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_10 + 1_800_000_000,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
        session_id="s1",
        endpoint="/v1/messages",
        prompt_tokens=200,
        prompt_length=1000,
        completion_tokens=100,
        reasoning_tokens=20,
        cached_tokens=40,
        total_tokens=320,
        latency_ms=300,
        ttft_ms=80,
        tool_tokens=10,
        cache_creation_tokens=0,
        input_cost_usd=0.002,
        output_cost_usd=0.004,
        total_cost_usd=0.006,
        status=500,
        base_url_id=None,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage2, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageHourly)))
    assert len(rows) == 1
    assert rows[0].request_count == 2
    assert rows[0].prompt_tokens == 300
    assert rows[0].completion_tokens == 150
    assert rows[0].latency_sum_ms == 500
    assert rows[0].successful_requests == 1
    assert rows[0].failed_requests == 1
    assert float(rows[0].total_cost_usd) == 0.009


def test_upsert_hourly_aggregate_handles_null_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_04_17_10,
        provider="openai",
        model="gpt-4",
        client_source=None,
        session_id=None,
        endpoint="/v1/chat/completions",
        prompt_tokens=50,
        prompt_length=200,
        completion_tokens=25,
        reasoning_tokens=None,
        cached_tokens=None,
        total_tokens=75,
        latency_ms=100,
        ttft_ms=None,
        tool_tokens=None,
        cache_creation_tokens=None,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
        status=200,
        base_url_id=None,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageHourly)))
    assert len(rows) == 1
    assert rows[0].client_source == ""
    assert rows[0].request_count == 1


def test_upsert_hourly_aggregate_separates_by_hour(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts in [TS_2026_04_17_10, TS_2026_04_17_11]:
        usage = database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="s1",
            endpoint="/v1/messages",
            prompt_tokens=100,
            prompt_length=500,
            completion_tokens=50,
            reasoning_tokens=10,
            cached_tokens=20,
            total_tokens=160,
            latency_ms=200,
            ttft_ms=50,
            tool_tokens=5,
            cache_creation_tokens=0,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            base_url_id=None,
            user_id="test-user",
        )
        ingest_write(database_module.log_usage, usage, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageHourly)))
    assert len(rows) == 2
    assert {r.hour for r in rows} == {
        TS_2026_04_17_10 // 1_000_000,
        TS_2026_04_17_11 // 1_000_000,
    }


def test_log_usage_automaticaly_upserts_hourly_aggregate(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="s1",
            endpoint="/v1/messages",
            prompt_tokens=100,
            prompt_length=500,
            completion_tokens=50,
            reasoning_tokens=10,
            cached_tokens=20,
            total_tokens=160,
            latency_ms=200,
            ttft_ms=50,
            tool_tokens=5,
            cache_creation_tokens=0,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageHourly)))
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert rows[0].prompt_tokens == 100


def test_summarize_usage_by_model_reads_from_aggregate_table(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Insert two rows on same day, same provider/model
    for prompt, comp, latency, status in [
        (100, 50, 200, 200),
        (200, 100, 300, 500),
    ]:
        usage = database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="s1",
            endpoint="/v1/messages",
            prompt_tokens=prompt,
            prompt_length=500,
            completion_tokens=comp,
            reasoning_tokens=10,
            cached_tokens=20,
            total_tokens=prompt + comp,
            latency_ms=latency,
            ttft_ms=50,
            tool_tokens=5,
            cache_creation_tokens=0,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=status,
            base_url_id=None,
            user_id="test-user",
        )
        ingest_write(database_module.log_usage, usage, db_path=db_path)

    summary = database_module.summarize_usage_by_model(user_id="test-user")

    assert len(summary) == 1
    row = summary[0]
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["requests"] == 2
    assert row["prompt_tokens"] == 300
    assert row["completion_tokens"] == 150
    assert row["avg_latency_ms"] == 250.0
    assert row["successful_requests"] == 1
    assert row["failed_requests"] == 1


def test_summarize_usage_by_model_filters_by_provider(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for provider, model, ts in [
        ("anthropic", "claude-sonnet-4-6", TS_2026_04_17_10),
        ("openai", "gpt-4", TS_2026_04_17_11),
    ]:
        usage = database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider=provider,
            model=model,
            client_source=None,
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=100,
            prompt_length=500,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=150,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            base_url_id=None,
            user_id="test-user",
        )
        ingest_write(database_module.log_usage, usage, db_path=db_path)

    summary = database_module.summarize_usage_by_model(
        provider="anthropic", user_id="test-user"
    )
    assert len(summary) == 1
    assert summary[0]["provider"] == "anthropic"


def test_summarize_usage_by_model_filters_by_since(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts in [TS_2026_04_17_10, TS_2026_04_20_10]:
        usage = database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source=None,
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=100,
            prompt_length=500,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=150,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            base_url_id=None,
            user_id="test-user",
        )
        ingest_write(database_module.log_usage, usage, db_path=db_path)

    summary = database_module.summarize_usage_by_model(
        since="2026-04-20", user_id="test-user"
    )
    assert len(summary) == 1
    assert summary[0]["requests"] == 1


def _log_usage_row(
    database_module,
    db_path,
    ts,
    prompt,
    comp,
    tokens,
    status=200,
    provider="anthropic",
    model="claude-sonnet-4-6",
    source=None,
    latency_ms=100,
    input_cost=0.001,
    output_cost=0.002,
    total_cost=0.003,
):
    """Helper to insert a usage row for regression tests."""
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider=provider,
            model=model,
            client_source=source,
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=prompt,
            prompt_length=500,
            completion_tokens=comp,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=tokens,
            latency_ms=latency_ms,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            total_cost_usd=total_cost,
            status=status,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )


def test_summarize_usage_by_model_sub_day_range(fresh_db):
    """A sub-day range is exact to the hour."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Row at 10:00 on April 17 — inside the 2h window
    _log_usage_row(
        database_module,
        db_path,
        TS_2026_04_17_10,
        100,
        50,
        150,
        latency_ms=100,
        status=200,
        input_cost=0.001,
        output_cost=0.002,
        total_cost=0.003,
    )
    # Row at 11:00 on April 17 — inside the 2h window
    _log_usage_row(
        database_module,
        db_path,
        TS_2026_04_17_11,
        200,
        100,
        300,
        latency_ms=200,
        status=500,
        input_cost=0.004,
        output_cost=0.006,
        total_cost=0.010,
    )
    # Row at 10:00 on April 18 — outside the 2h window
    _log_usage_row(database_module, db_path, TS_2026_04_18_10, 400, 200, 600)

    # 2-hour window on April 17 (sub-day) — should only include first two rows
    summary = database_module.summarize_usage_by_model(
        since="2026-04-17T09:00:00",
        until="2026-04-17T12:00:00",
        user_id="test-user",
    )
    assert len(summary) == 1
    row = summary[0]
    assert row["provider"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["requests"] == 2
    assert row["prompt_tokens"] == 300
    assert row["completion_tokens"] == 150
    assert row["total_tokens"] == 450
    assert row["successful_requests"] == 1
    assert row["failed_requests"] == 1
    assert row["avg_latency_ms"] == 150.0  # (100 + 200) / 2
    assert abs(row["total_cost_usd"] - 0.013) < 0.0001


def test_count_usage_sub_day_range(fresh_db):
    """count_usage with sub-day range must count only rows in the window."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _log_usage_row(database_module, db_path, TS_2026_04_17_10, 100, 50, 150)
    _log_usage_row(database_module, db_path, TS_2026_04_17_11, 200, 100, 300)
    _log_usage_row(database_module, db_path, TS_2026_04_18_10, 400, 200, 600)

    count = database_module.count_usage(
        since="2026-04-17T09:00:00",
        until="2026-04-17T12:00:00",
        user_id="test-user",
    )
    assert count == 2


def test_summarize_usage_by_source_sub_day_range(fresh_db):
    """by-source honours a sub-day range."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _log_usage_row(
        database_module, db_path, TS_2026_04_17_10, 100, 50, 150, source="claude-code"
    )
    _log_usage_row(
        database_module, db_path, TS_2026_04_17_11, 200, 100, 300, source="claude-code"
    )
    _log_usage_row(
        database_module, db_path, TS_2026_04_18_10, 400, 200, 600, source="codex"
    )

    # Sub-day window — should only include the two claude-code rows
    summary = database_module.summarize_usage_by_source(
        since="2026-04-17T09:00:00",
        until="2026-04-17T12:00:00",
        user_id="test-user",
    )
    assert len(summary) == 1
    assert summary[0]["client_source"] == "claude-code"
    assert summary[0]["requests"] == 2
    assert summary[0]["total_tokens"] == 450


def test_summarize_usage_by_provider_sub_day_range(fresh_db):
    """by-provider honours a sub-day range."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _log_usage_row(
        database_module, db_path, TS_2026_04_17_10, 100, 50, 150, provider="anthropic"
    )
    _log_usage_row(
        database_module, db_path, TS_2026_04_17_11, 200, 100, 300, provider="anthropic"
    )
    _log_usage_row(
        database_module, db_path, TS_2026_04_18_10, 400, 200, 600, provider="openai"
    )

    # Sub-day window — should only include the two anthropic rows
    summary = database_module.summarize_usage_by_provider(
        since="2026-04-17T09:00:00",
        until="2026-04-17T12:00:00",
        user_id="test-user",
    )
    assert len(summary) == 1
    assert summary[0]["provider"] == "anthropic"
    assert summary[0]["requests"] == 2
    assert summary[0]["total_tokens"] == 450


@pytest.mark.parametrize(
    ("tz_offset", "days", "hours"),
    [
        (
            "+00:00",
            {"2026-05-05": 2, "2026-05-06": 2},
            [
                "2026-05-05 18:00",
                "2026-05-05 23:00",
                "2026-05-06 00:00",
                "2026-05-06 06:00",
            ],
        ),
        (
            "-07:00",
            {"2026-05-05": 4},
            [
                "2026-05-05 11:00",
                "2026-05-05 16:00",
                "2026-05-05 17:00",
                "2026-05-05 23:00",
            ],
        ),
        # Half-hour offsets shift whole UTC hours (documented approximation):
        # 18:45 UTC is 00:15 local on the 6th but its hour lands on the 5th.
        (
            "+05:30",
            {"2026-05-05": 1, "2026-05-06": 3},
            [
                "2026-05-05 23:00",
                "2026-05-06 04:00",
                "2026-05-06 05:00",
                "2026-05-06 11:00",
            ],
        ),
    ],
)
def test_aggregate_usage_by_period_local_buckets_around_midnight(
    fresh_db, tz_offset, days, hours
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for utc in [(5, 18, 45), (5, 23, 30), (6, 0, 30), (6, 6, 30)]:
        ts = int(datetime(2026, 5, *utc, tzinfo=UTC).timestamp()) * 1_000_000
        _log_usage_row(database_module, db_path, ts, 10, 5, 15)

    by_day = database_module.aggregate_usage_by_period(
        granularity="day", tz_offset=tz_offset, user_id="test-user"
    )
    assert {row["period"]: row["requests"] for row in by_day} == days
    by_hour = database_module.aggregate_usage_by_period(
        granularity="hour", tz_offset=tz_offset, user_id="test-user"
    )
    assert [row["period"] for row in by_hour] == hours


def test_dimension_days_honour_tz_offset_and_naive_bounds_are_utc(fresh_db):
    """tz_offset makes dimension periods local days; naive bounds are UTC."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for utc in [(5, 23, 30), (6, 6, 30)]:
        ts = int(datetime(2026, 5, *utc, tzinfo=UTC).timestamp()) * 1_000_000
        _log_usage_row(database_module, db_path, ts, 10, 5, 15)

    def days(**kwargs):
        rows = database_module.aggregate_daily_by_dimension(
            user_id="test-user", dimension="model", **kwargs
        )
        return {row["period"]: row["requests"] for row in rows}

    assert days() == {"2026-05-05": 1, "2026-05-06": 1}
    assert days(tz_offset="-07:00") == {"2026-05-05": 2}

    for summarize in (
        database_module.summarize_usage_by_model,
        database_module.summarize_usage_by_source,
        database_module.summarize_usage_by_provider,
    ):
        # A date-only until covers that whole UTC day.
        utc_day = summarize(user_id="test-user", until="2026-05-05")
        assert utc_day[0]["requests"] == 1, summarize
        naive = summarize(user_id="test-user", since="2026-05-06T00:00:00")
        assert naive[0]["requests"] == 1, summarize


def test_aggregate_usage_by_period_hourly_merges_same_hour(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Two rows in the same UTC hour
    for ts, prompt, completion in [
        (TS_2026_05_05_10_15, 50, 50),
        (TS_2026_05_05_10_45, 30, 70),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=prompt,
                completion_tokens=completion,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=prompt + completion,
                latency_ms=200,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.003,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_usage_by_period(
        granularity="hour", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["period"] == "2026-05-05 10:00"
    assert result[0]["requests"] == 2
    assert result[0]["prompt_tokens"] == 80
    assert result[0]["completion_tokens"] == 120
    assert result[0]["total_tokens"] == 200
    assert result[0]["avg_latency_ms"] == 200


def test_aggregate_usage_by_period_hourly_counts_failures(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, status in [
        (TS_2026_05_05_10, 200),
        (TS_2026_05_05_10_01, 500),
        (TS_2026_05_05_10_02, 200),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=10,
                completion_tokens=10,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=20,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=status,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_usage_by_period(
        granularity="hour", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["requests"] == 3
    assert result[0]["successful_requests"] == 2
    assert result[0]["failed_requests"] == 1


def test_aggregate_usage_by_period_hourly_negative_tz_offset(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_05_03_30,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            client_source=None,
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=10,
            completion_tokens=10,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=20,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            total_cost_usd=0.002,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    # 03:30 UTC with -05:00 offset = 22:30 previous day (2026-05-04 22:00)
    result = database_module.aggregate_usage_by_period(
        granularity="hour", tz_offset="-05:00", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["period"] == "2026-05-04 22:00"
    assert result[0]["requests"] == 1
    assert result[0]["total_tokens"] == 20


def test_aggregate_usage_by_period_with_epoch_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=1776384000000000,  # 2026-04-17T00:00:00 UTC (micros)
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            client_source="claude-code",
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=150,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.002,
            output_cost_usd=0.003,
            total_cost_usd=0.005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.aggregate_usage_by_period(
        granularity="day", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["period"] == "2026-04-17"
    assert result[0]["requests"] == 1


def test_aggregate_daily_by_dimension_groups_by_model(fresh_db):
    """aggregate_daily_by_dimension returns daily data grouped by model."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Insert test data across 2 days for 2 models
    for ts, provider, model, tokens, cost in [
        (TS_2026_05_07_10, "anthropic", "claude-sonnet-4-6", 1000, 0.01),
        (TS_2026_05_08_10, "anthropic", "claude-sonnet-4-6", 2000, 0.02),
        (TS_2026_05_07_10, "openai", "gpt-4o", 500, 0.005),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider=provider,
                model=model,
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=tokens // 2,
                completion_tokens=tokens // 2,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=tokens,
                latency_ms=500,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=cost / 2,
                output_cost_usd=cost / 2,
                total_cost_usd=cost,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_daily_by_dimension(
        dimension="model",
        since="2026-05-07T00:00:00Z",
        until="2026-05-09T00:00:00Z",
        user_id="test-user",
    )

    # Should have 3 entries: claude-sonnet-4-6 on 05-07, claude-sonnet-4-6 on 05-08, gpt-4o on 05-07
    assert len(result) == 3
    by_model_and_date = {(r["dimension"], r["period"]): r for r in result}
    assert (
        by_model_and_date[("claude-sonnet-4-6", "2026-05-07")]["total_tokens"] == 1000
    )
    assert (
        by_model_and_date[("claude-sonnet-4-6", "2026-05-08")]["total_tokens"] == 2000
    )
    assert by_model_and_date[("gpt-4o", "2026-05-07")]["total_tokens"] == 500


def test_aggregate_daily_by_dimension_can_bucket_by_hour(fresh_db):
    """aggregate_daily_by_dimension buckets by local hour when asked."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, tokens in [
        (TS_2026_05_07_10, 1000),
        (TS_2026_05_07_10, 500),
        (TS_2026_05_08_10, 2000),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=tokens // 2,
                completion_tokens=tokens // 2,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=tokens,
                latency_ms=500,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.01,
                output_cost_usd=0.01,
                total_cost_usd=0.02,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_daily_by_dimension(
        dimension="model",
        since="2026-05-07T00:00:00Z",
        until="2026-05-09T00:00:00Z",
        user_id="test-user",
        granularity="hour",
    )

    periods = {r["period"] for r in result}
    assert periods == {"2026-05-07 10:00", "2026-05-08 10:00"}
    by_period = {r["period"]: r for r in result}
    assert by_period["2026-05-07 10:00"]["total_tokens"] == 1500


def test_aggregate_daily_by_dimension_groups_by_provider(fresh_db):
    """aggregate_daily_by_dimension returns daily data grouped by provider."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, provider, model, tokens, cost in [
        (TS_2026_05_07_10, "anthropic", "claude-sonnet-4-6", 1000, 0.01),
        (TS_2026_05_07_10, "openai", "gpt-4o", 500, 0.005),
    ]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts,
                provider=provider,
                model=model,
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=tokens // 2,
                completion_tokens=tokens // 2,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=tokens,
                latency_ms=300,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=cost / 2,
                output_cost_usd=cost / 2,
                total_cost_usd=cost,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.aggregate_daily_by_dimension(
        dimension="provider",
        since="2026-05-07T00:00:00Z",
        until="2026-05-08T00:00:00Z",
        user_id="test-user",
    )

    assert len(result) == 2
    by_provider = {r["dimension"]: r for r in result}
    assert by_provider["anthropic"]["total_tokens"] == 1000
    assert by_provider["openai"]["total_tokens"] == 500


def test_fetch_sessions_groups_by_session_id(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for i in range(3):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 60,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id="sess-1",
                endpoint="/v1/messages",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                latency_ms=200,
                ttft_ms=100,
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.003,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_11,
            provider="openai",
            model="gpt-4o",
            client_source="codex",
            session_id="sess-2",
            endpoint="/v1/chat/completions",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=300,
            ttft_ms=150,
            input_cost_usd=0.005,
            output_cost_usd=0.01,
            total_cost_usd=0.015,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")
    assert len(result) == 2
    assert result[0]["session_id"] == "sess-2"
    assert result[0]["request_count"] == 1
    assert result[0]["total_tokens"] == 300
    assert result[1]["session_id"] == "sess-1"
    assert result[1]["request_count"] == 3
    assert result[1]["total_tokens"] == 450


def test_fetch_sessions_excludes_null_session_id(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=100,
            total_tokens=100,
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            total_cost_usd=0.002,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")
    assert len(result) == 0


def test_fetch_sessions_filters_by_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for source, sid in [("claude-code", "s1"), ("codex", "s2")]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source=source,
                session_id=sid,
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.fetch_sessions(
        client_source="codex", db_path=db_path, user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["session_id"] == "s2"


def test_fetch_sessions_hide_noop_keeps_single_request_opencode(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for source, sid in [("opencode", "oc-1"), ("codex", "codex-1")]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source=source,
                session_id=sid,
                endpoint="otlp",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    database_module.upsert_session_evaluation(
        "oc-1", "no_op", source="heuristic", db_path=db_path, user_id="test-user"
    )
    result = database_module.fetch_sessions(
        hide_noop=True, db_path=db_path, user_id="test-user"
    )
    assert result == []

    database_module.delete_session_evaluation(
        "oc-1", db_path=db_path, user_id="test-user"
    )
    result = database_module.fetch_sessions(
        hide_noop=True, db_path=db_path, user_id="test-user"
    )
    assert [row["session_id"] for row in result] == ["oc-1"]


def test_fetch_sessions_hide_noop_keeps_single_request_kilo(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for source, sid in [("kilo", "kilo-1"), ("codex", "codex-1")]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source=source,
                session_id=sid,
                endpoint="otlp",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    database_module.upsert_session_evaluation(
        "kilo-1", "no_op", source="heuristic", db_path=db_path, user_id="test-user"
    )
    result = database_module.fetch_sessions(
        hide_noop=True, db_path=db_path, user_id="test-user"
    )
    assert result == []

    database_module.delete_session_evaluation(
        "kilo-1", db_path=db_path, user_id="test-user"
    )
    result = database_module.fetch_sessions(
        hide_noop=True, db_path=db_path, user_id="test-user"
    )
    assert [row["session_id"] for row in result] == ["kilo-1"]


def test_fetch_sessions_accepts_browser_iso_boundary_filters(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="boundary",
            endpoint="/v1/messages",
            prompt_tokens=100,
            total_tokens=100,
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            total_cost_usd=0.002,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.fetch_sessions(
        since="2026-05-09T10:00:00.000Z",
        until="2026-05-09T10:00:00.000Z",
        db_path=db_path,
        user_id="test-user",
    )

    assert [session["session_id"] for session in result] == ["boundary"]


def test_fetch_sessions_sorting(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for i, sid in enumerate(["s-low", "s-high"]):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 3_600_000_000,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id=sid,
                endpoint="/v1/messages",
                prompt_tokens=100 * (i + 1),
                total_tokens=100 * (i + 1),
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.fetch_sessions(
        sort_by="total_tokens", sort_order="asc", db_path=db_path, user_id="test-user"
    )
    assert result[0]["session_id"] == "s-low"
    assert result[1]["session_id"] == "s-high"


def test_fetch_sessions_sort_by_duration(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Short session: single request
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="short",
            endpoint="/v1/messages",
            prompt_tokens=100,
            total_tokens=100,
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            total_cost_usd=0.002,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    # Long session: spans 10 minutes
    for i in range(2):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 300_000_000,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id="long",
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.fetch_sessions(
        sort_by="duration_s", sort_order="desc", db_path=db_path, user_id="test-user"
    )
    assert result[0]["session_id"] == "long"
    assert result[1]["session_id"] == "short"


def test_fetch_sessions_pagination(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for i in range(5):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 3600,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id=f"s{i}",
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.fetch_sessions(
        limit=2, offset=1, db_path=db_path, user_id="test-user"
    )
    assert len(result) == 2


def test_fetch_session_selector_rows_returns_only_dropdown_fields(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="sess-selector",
            endpoint="/v1/messages",
            prompt_tokens=100,
            total_tokens=100,
            input_cost_usd=0.001,
            output_cost_usd=0.001,
            total_cost_usd=0.002,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.fetch_session_selector_rows(
        db_path=db_path, user_id="test-user"
    )

    assert result == [
        {
            "session_id": "sess-selector",
            "client_source": "claude-code",
            "request_count": 1,
            "started": TS_2026_05_09_10,
        }
    ]
    assert "total_tokens" not in result[0]
    assert "evaluation" not in result[0]


def test_fetch_session_selector_rows_applies_filters_sort_and_pagination(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for index, source in enumerate(["claude-code", "codex", "codex"]):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + index * 3_600_000_000,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source=source,
                session_id=f"s{index}",
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    result = database_module.fetch_session_selector_rows(
        client_source="codex",
        since="2026-05-09T10:30:00.000Z",
        sort_by="started",
        sort_order="desc",
        limit=1,
        offset=0,
        db_path=db_path,
        user_id="test-user",
    )

    assert [row["session_id"] for row in result] == ["s2"]


def test_count_sessions(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for sid in ["s1", "s2", "s1"]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id=sid,
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    assert database_module.count_sessions(db_path=db_path, user_id="test-user") == 2


def test_count_usage_with_session_id(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for sid in ["s1", "s1", "s2"]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id=sid,
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    assert (
        database_module.count_usage(
            session_id="s1", db_path=db_path, user_id="test-user"
        )
        == 2
    )
    assert (
        database_module.count_usage(
            session_id="s2", db_path=db_path, user_id="test-user"
        )
        == 1
    )
    assert (
        database_module.count_usage(
            session_id="nonexistent", db_path=db_path, user_id="test-user"
        )
        == 0
    )


def test_count_usage_with_session_id_and_other_filters(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Same session, two different providers
    for provider in ["anthropic", "openai"]:
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10,
                provider=provider,
                model="test-model",
                client_source="claude-code",
                session_id="s1",
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    # session_id + provider filter should narrow to 1
    assert (
        database_module.count_usage(
            session_id="s1", provider="anthropic", db_path=db_path, user_id="test-user"
        )
        == 1
    )
    # session_id + client_source filter
    assert (
        database_module.count_usage(
            session_id="s1",
            client_source="claude-code",
            db_path=db_path,
            user_id="test-user",
        )
        == 2
    )
    # session_id + mismatched provider
    assert (
        database_module.count_usage(
            session_id="s1", provider="google", db_path=db_path, user_id="test-user"
        )
        == 0
    )


def test_summarize_sessions(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for i in range(3):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 60_000_000,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id="s1",
                endpoint="/v1/messages",
                prompt_tokens=100,
                total_tokens=100,
                latency_ms=200,
                input_cost_usd=0.001,
                output_cost_usd=0.001,
                total_cost_usd=0.002,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_11,
            provider="openai",
            model="gpt-4o",
            client_source="codex",
            session_id="s2",
            endpoint="/v1/chat/completions",
            prompt_tokens=200,
            total_tokens=200,
            latency_ms=300,
            input_cost_usd=0.005,
            output_cost_usd=0.005,
            total_cost_usd=0.01,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    result = database_module.summarize_sessions(db_path=db_path, user_id="test-user")
    assert result["session_count"] == 2
    assert result["total_tokens"] == 500
    assert result["total_cost_usd"] == pytest.approx(0.016)
    # s1 spans 10:00-10:02 (120s), s2 is a single request (0s), avg = 60s
    assert result["avg_duration_s"] == 60


def test_usage_hourly_status_breakdown(database_module, isolated_home):
    db_path = str(isolated_home / "usage_status.db")
    database_module.init_db(db_path)

    # Record 4 requests with different status codes
    codes = [200, 429, 403, 500]
    for i, code in enumerate(codes):
        usage = database_module.Usage(
            device_id="test-device",
            ts=TS_2024_01_01_00 + i * 1_000_000,
            provider="p1",
            model="m1",
            endpoint="/chat",
            status=code,
            input_cost_usd=0,
            output_cost_usd=0,
            total_cost_usd=0,
            prompt_length=0,
            user_id="test-user",
        )
        _upsert_hourly(usage, db_path=db_path)

    # Check aggregation
    summary = database_module.summarize_usage_by_model(
        since="2024-01-01", db_path=db_path, user_id="test-user"
    )
    assert len(summary) == 1
    row = summary[0]
    assert row["requests"] == 4
    assert row["successful_requests"] == 1
    assert row["failed_requests"] == 3
    assert row["status_429"] == 1
    assert row["status_4xx"] == 1  # 403
    assert row["status_5xx"] == 1
    assert row["status_unknown"] == 0


# ---------------------------------------------------------------------------
# Slice 0: Persist Sessions as First-Class Rows
# ---------------------------------------------------------------------------


def test_session_record_created_from_usage_ingestion(fresh_db):
    """TDD step 1: Ingesting two usage rows with same session_id creates one
    sessions row with summed tokens/cost/request count and min/max timestamps."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="sess-1",
            endpoint="/v1/messages",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=200,
            ttft_ms=100,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10_05,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="sess-1",
            endpoint="/v1/messages",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=300,
            ttft_ms=150,
            input_cost_usd=0.002,
            output_cost_usd=0.004,
            total_cost_usd=0.006,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    from sqlalchemy import select
    from sqlalchemy.orm import Session as OrmSession

    engine = database_module.get_engine(db_path)
    with OrmSession(engine) as orm:
        session = orm.scalar(
            select(database_module.SessionRecord).where(
                database_module.SessionRecord.session_id == "sess-1"
            )
        )

    assert session is not None, "SessionRecord should exist after ingesting usage rows"
    assert session.request_count == 2
    assert session.total_tokens == 450
    assert session.prompt_tokens == 300
    assert session.completion_tokens == 150
    assert float(session.total_cost_usd) == pytest.approx(0.009, abs=1e-6)
    assert session.started == TS_2026_05_09_10
    assert session.ended == TS_2026_05_09_10_05
    assert session.successful_requests == 2
    assert session.failed_requests == 0


def test_session_record_primary_model_picks_higher_cost(fresh_db):
    """TDD step 2: Two models in one session; primary_model picks the one
    with higher cumulative cost."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Model A: cost 0.003
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            session_id="sess-1",
            endpoint="/v1/messages",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=200,
            ttft_ms=100,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    # Model B: cost 0.015 (higher)
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_10_01,
            provider="openai",
            model="gpt-4o",
            client_source="claude-code",
            session_id="sess-1",
            endpoint="/v1/chat/completions",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=300,
            ttft_ms=150,
            input_cost_usd=0.005,
            output_cost_usd=0.01,
            total_cost_usd=0.015,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    from sqlalchemy import select
    from sqlalchemy.orm import Session as OrmSession

    engine = database_module.get_engine(db_path)
    with OrmSession(engine) as orm:
        session = orm.scalar(
            select(database_module.SessionRecord).where(
                database_module.SessionRecord.session_id == "sess-1"
            )
        )

    assert session.primary_model == "gpt-4o"
    assert session.primary_provider == "openai"

    import json

    models = json.loads(session.models_json)
    providers = json.loads(session.providers_json)
    assert set(models.keys()) == {"claude-sonnet-4-6", "gpt-4o"}
    assert set(providers.keys()) == {"anthropic", "openai"}


def test_fetch_sessions_reads_from_persisted_table(fresh_db):
    """TDD step 3: fetch_sessions() reads from the persisted sessions table,
    not freshly grouped usage."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Directly insert a SessionRecord (bypassing log_usage)
    from sqlalchemy.orm import Session

    engine = database_module.get_engine(db_path)
    with Session(engine) as session:
        session.add(
            database_module.SessionRecord(
                session_id="manual-sess",
                client_source="claude-code",
                started=1778313600000000,
                ended=1778317200000000,
                request_count=5,
                successful_requests=5,
                failed_requests=0,
                total_tokens=1000,
                prompt_tokens=800,
                completion_tokens=200,
                cached_tokens=0,
                total_cost_usd=0.05,
                latency_sum_ms=5000,
                avg_latency_ms=1000.0,
                avg_ttft_ms=200.0,
                primary_provider="anthropic",
                primary_model="claude-sonnet-4-6",
                models_json='{"claude-sonnet-4-6": 0.05}',
                providers_json='{"anthropic": 0.05}',
                last_usage_id=None,
                updated_at="2026-05-09T09:00:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")
    assert len(result) == 1
    assert result[0]["session_id"] == "manual-sess"
    assert result[0]["request_count"] == 5
    assert result[0]["total_tokens"] == 1000
    assert result[0]["total_cost_usd"] == pytest.approx(0.05, abs=1e-6)
    assert result[0]["duration_s"] == 3600


def test_rebuild_sessions_from_usage(fresh_db):
    """TDD step 4: Seed usage rows, run rebuild_sessions_from_usage(),
    assert persisted rows match old aggregation behavior."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Seed usage rows for two sessions
    for i in range(3):
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=TS_2026_05_09_10 + i * 60_000_000,
                provider="anthropic",
                model="claude-sonnet-4-6",
                client_source="claude-code",
                session_id="sess-1",
                endpoint="/v1/messages",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                latency_ms=200,
                ttft_ms=100,
                input_cost_usd=0.001,
                output_cost_usd=0.002,
                total_cost_usd=0.003,
                status=200,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_05_09_11,
            provider="openai",
            model="gpt-4o",
            client_source="codex",
            session_id="sess-2",
            endpoint="/v1/chat/completions",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            latency_ms=300,
            ttft_ms=150,
            input_cost_usd=0.005,
            output_cost_usd=0.01,
            total_cost_usd=0.015,
            status=200,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    # Clear sessions table and rebuild from scratch
    count = database_module.rebuild_sessions_from_usage(db_path=db_path)
    assert count == 2

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")
    assert len(result) == 2

    by_id = {r["session_id"]: r for r in result}
    s1 = by_id["sess-1"]
    assert s1["request_count"] == 3
    assert s1["total_tokens"] == 450
    assert float(s1["total_cost_usd"]) == pytest.approx(0.009, abs=1e-6)
    assert s1["started"] == TS_2026_05_09_10
    assert s1["ended"] == TS_2026_05_09_10_02
    assert s1["successful_requests"] == 3

    s2 = by_id["sess-2"]
    assert s2["request_count"] == 1
    assert s2["total_tokens"] == 300
    assert float(s2["total_cost_usd"]) == pytest.approx(0.015, abs=1e-6)


def test_rebuild_sessions_from_usage_recomputes_tool_calls_json(fresh_db):
    """Tool calls must survive a rebuild via the tool_calls table, not be
    dropped just because rebuild only used to read from usage rows."""
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_05_09_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
        session_id="sess-tools",
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        latency_ms=200,
        ttft_ms=100,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
        status=200,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage, db_path=db_path)

    for tool_id, tool_name in [("t1", "bash"), ("t2", "bash"), ("t3", "read")]:
        ingest_write(
            record_tool_call,
            tool_use_id=tool_id,
            usage_id=usage.id,
            session_id="sess-tools",
            tool_name=tool_name,
            ts=TS_2026_05_09_10,
            db_path=db_path,
            user_id="test-user",
        )

    count = database_module.rebuild_sessions_from_usage(db_path=db_path)
    assert count == 1

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")
    assert len(result) == 1
    assert result[0]["tool_calls_json"] == {"bash": 2, "read": 1}


# ---------------------------------------------------------------------------
# Slice 1: Store Manual Session Evaluations
# ---------------------------------------------------------------------------


def _insert_session_record(database_module, db_path, session_id="sess-1", **overrides):
    """Helper: insert a minimal SessionRecord for evaluation tests."""
    from sqlalchemy.orm import Session

    values = {
        "session_id": session_id,
        "user_id": "test-user",
        "client_source": "test",
        "started": 1778493600000000,
        "ended": 1778495400000000,
        "updated_at": "2026-05-11T10:30:00+00:00",
    }
    values.update(overrides)

    engine = database_module.get_engine(db_path)
    with Session(engine) as session:
        session.add(database_module.SessionRecord(**values))
        session.commit()


def test_upsert_session_evaluation_creates_new(fresh_db):
    """Manual upsert clears task titles even when callers provide them."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="solved",
        source="manual",
        confidence=None,
        task_title="Fixed dashboard navigation",
        task_title_zh="修复仪表板导航",
        summary=None,
        evidence=["User marked solved"],
        failure_reason=None,
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.get_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert result is not None
    assert result["outcome"] == "solved"
    assert result["source"] == "manual"
    assert result["task_title"] is None
    assert result["task_title_zh"] is None
    assert result["evidence"] == ["User marked solved"]
    assert result["failure_reason"] is None
    assert result["confidence"] is None
    assert result["evaluated_at"] is not None


def test_upsert_session_evaluation_preserves_llm_task_titles(fresh_db):
    """LLM upsert preserves English and localized task titles."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="solved",
        source="llm",
        task_title="Fixed dashboard navigation",
        task_title_zh="修复仪表板导航",
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.get_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert result is not None
    assert result["task_title"] == "Fixed dashboard navigation"
    assert result["task_title_zh"] == "修复仪表板导航"


def test_fetch_sessions_includes_task_title_zh_in_evaluation(fresh_db):
    """fetch_sessions includes localized task title in nested evaluation."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="partial",
        source="llm",
        task_title="Tuned filters",
        task_title_zh="调整筛选器",
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.fetch_sessions(db_path=db_path, user_id="test-user")

    assert result[0]["evaluation"]["task_title"] == "Tuned filters"
    assert result[0]["evaluation"]["task_title_zh"] == "调整筛选器"


def test_upsert_session_evaluation_overwrites_existing(fresh_db):
    """Second upsert replaces the first evaluation."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="solved",
        source="manual",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="failed",
        source="manual",
        failure_reason="Agent got stuck",
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.get_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert result is not None
    assert result["outcome"] == "failed"
    assert result["failure_reason"] == "Agent got stuck"


def test_upsert_session_evaluation_raises_when_session_not_found(fresh_db):
    """Upsert raises ValueError when session doesn't exist."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    with pytest.raises(ValueError, match="Session not found"):
        database_module.upsert_session_evaluation(
            session_id="nonexistent",
            outcome="solved",
            source="manual",
            db_path=db_path,
            user_id="test-user",
        )


def test_get_session_evaluation_returns_none_when_not_evaluated(fresh_db):
    """GET returns None when session exists but has no evaluation."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    result = database_module.get_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert result is None


def test_get_session_evaluation_returns_none_when_session_absent(fresh_db):
    """GET returns None when session doesn't exist."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    result = database_module.get_session_evaluation(
        "nonexistent", db_path=db_path, user_id="test-user"
    )
    assert result is None


def test_delete_session_evaluation_clears_columns(fresh_db):
    """DELETE clears evaluation columns and returns True."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path)

    database_module.upsert_session_evaluation(
        session_id="sess-1",
        outcome="solved",
        source="manual",
        db_path=db_path,
        user_id="test-user",
    )

    deleted = database_module.delete_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert deleted is True

    result = database_module.get_session_evaluation(
        "sess-1", db_path=db_path, user_id="test-user"
    )
    assert result is None

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    engine = database_module.get_engine(db_path)
    with Session(engine) as session:
        record = session.scalar(
            select(database_module.SessionRecord).where(
                database_module.SessionRecord.session_id == "sess-1"
            )
        )
    assert record.task_title is None
    assert record.task_title_zh is None


def test_delete_session_evaluation_returns_false_when_session_absent(fresh_db):
    """DELETE returns False when session doesn't exist."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    deleted = database_module.delete_session_evaluation(
        "nonexistent", db_path=db_path, user_id="test-user"
    )
    assert deleted is False


# ---------------------------------------------------------------------------
# Slice 4: Model Effectiveness Aggregation API
# ---------------------------------------------------------------------------


def test_model_effectiveness_groups_by_primary_model(fresh_db):
    """Aggregation attributes each session to its primary_model only."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-solved",
        client_source="codex",
        primary_provider="openai",
        primary_model="gpt-5.5",
        models_json='{"gpt-5.5": 0.50, "claude-sonnet": 0.05}',
        providers_json='{"openai": 0.50, "anthropic": 0.05}',
        total_cost_usd=0.50,
        outcome="solved",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-partial",
        client_source="codex",
        primary_provider="openai",
        primary_model="gpt-5.5",
        total_cost_usd=0.25,
        outcome="partial",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-failed",
        client_source="claude-code",
        primary_provider="anthropic",
        primary_model="claude-sonnet",
        total_cost_usd=0.75,
        outcome="failed",
        source="manual",
    )

    result = database_module.aggregate_model_effectiveness(
        group_by="model",
        db_path=db_path,
        user_id="test-user",
    )

    by_key = {group["key"]: group for group in result["groups"]}
    assert set(by_key) == {"gpt-5.5", "claude-sonnet"}

    gpt = by_key["gpt-5.5"]
    assert gpt["session_count"] == 2
    assert gpt["evaluated_count"] == 2
    assert gpt["solved_count"] == 1
    assert gpt["partial_count"] == 1
    assert gpt["failed_count"] == 0
    assert gpt["solve_rate"] == pytest.approx(0.5)
    assert gpt["total_cost_usd"] == pytest.approx(0.75)
    assert gpt["cost_per_solved"] == pytest.approx(0.75)


def test_model_effectiveness_unknown_and_no_op_do_not_pollute_solve_rate(fresh_db):
    """Unknown and no-op sessions are visible but excluded from solve_rate."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-solved",
        primary_model="gpt-5.5",
        total_cost_usd=0.40,
        outcome="solved",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-unknown-explicit",
        primary_model="gpt-5.5",
        total_cost_usd=0.20,
        outcome="unknown",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-unknown-null",
        primary_model="gpt-5.5",
        total_cost_usd=0.20,
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-no-op",
        primary_model="gpt-5.5",
        total_cost_usd=0.20,
        outcome="no_op",
        source="manual",
    )

    result = database_module.aggregate_model_effectiveness(
        group_by="model",
        db_path=db_path,
        user_id="test-user",
    )

    group = result["groups"][0]
    assert group["session_count"] == 4
    assert group["evaluated_count"] == 1
    assert group["solved_count"] == 1
    assert group["unknown_count"] == 2
    assert group["no_op_count"] == 1
    assert group["solve_rate"] == pytest.approx(1.0)
    assert group["cost_per_solved"] == pytest.approx(0.40)


def test_model_effectiveness_groups_by_source_and_filters(fresh_db):
    """Aggregation supports client source grouping with date/source filters."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-in-range",
        client_source="codex",
        started=1778493600000000,
        ended=1778494800000000,
        total_cost_usd=0.25,
        outcome="solved",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-other-source",
        client_source="claude-code",
        started=1778497200000000,
        ended=1778499000000000,
        total_cost_usd=0.25,
        outcome="failed",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-out-of-range",
        client_source="codex",
        started=1778407200000000,
        ended=1778409000000000,
        total_cost_usd=0.25,
        outcome="failed",
        source="manual",
    )

    result = database_module.aggregate_model_effectiveness(
        group_by="source",
        since="2026-05-11T10:00:00.000Z",
        until="2026-05-11T10:20:00.000Z",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    assert [group["key"] for group in result["groups"]] == ["codex"]
    group = result["groups"][0]
    assert group["session_count"] == 1
    assert group["solved_count"] == 1
    assert group["failed_count"] == 0


def test_model_effectiveness_cost_per_solved_null_when_zero_solved(fresh_db):
    """cost_per_solved is null when there are no solved sessions."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-failed",
        primary_provider="anthropic",
        total_cost_usd=0.25,
        outcome="failed",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-stuck",
        primary_provider="anthropic",
        total_cost_usd=0.25,
        outcome="stuck",
        source="manual",
    )

    result = database_module.aggregate_model_effectiveness(
        group_by="provider",
        db_path=db_path,
        user_id="test-user",
    )

    group = result["groups"][0]
    assert group["key"] == "anthropic"
    assert group["evaluated_count"] == 2
    assert group["stuck_count"] == 1
    assert group["solve_rate"] == pytest.approx(0.0)
    assert group["cost_per_solved"] is None


# ---------------------------------------------------------------------------
# Slice 8: Daily Effectiveness Report
# ---------------------------------------------------------------------------


def test_daily_effectiveness_report_aggregates_day_metrics_in_sql(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-solved",
        client_source="codex",
        primary_model="gpt-5.5",
        started=1778403600000000,
        ended=1778404800000000,
        total_cost_usd=0.40,
        outcome="solved",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-stuck",
        client_source="gemini",
        primary_model="gemini-flash",
        started=1778407200000000,
        ended=1778408400000000,
        total_cost_usd=0.10,
        outcome="stuck",
        source="llm",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-no-op",
        client_source="claude-code",
        primary_model="claude-sonnet",
        started=1778410800000000,
        ended=1778411100000000,
        total_cost_usd=0.05,
        outcome="no_op",
        source="llm",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-unknown",
        client_source="codex",
        primary_model="gpt-5.5",
        started=1778414400000000,
        ended=1778414700000000,
        total_cost_usd=0.05,
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-other-day",
        client_source="codex",
        primary_model="gpt-5.5",
        started=1778490000000000,
        ended=1778491200000000,
        total_cost_usd=9.00,
        outcome="failed",
        source="manual",
    )

    report = database_module.daily_session_effectiveness_report(
        date="2026-05-10",
        db_path=db_path,
        user_id="test-user",
    )

    assert report["date"] == "2026-05-10"
    assert report["session_count"] == 4
    assert report["evaluated_count"] == 2
    assert report["classified_count"] == 3
    assert report["solved_count"] == 1
    assert report["stuck_count"] == 1
    assert report["no_op_count"] == 1
    assert report["unknown_count"] == 1
    assert report["total_cost_usd"] == pytest.approx(0.60)
    assert "You ran 4 AI sessions" in report["summary"]
    assert report["highlights"]
    assert report["needs_attention"] == ["gemini / gemini-flash had 1 stuck session"]


def test_daily_effectiveness_report_returns_sql_model_source_groups(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_session_record(
        database_module,
        db_path,
        "sess-gpt-solved",
        client_source="codex",
        primary_model="gpt-5.5",
        started=1778403600000000,
        total_cost_usd=0.20,
        outcome="solved",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-gpt-failed",
        client_source="codex",
        primary_model="gpt-5.5",
        started=1778407200000000,
        total_cost_usd=0.30,
        outcome="failed",
        source="manual",
    )
    _insert_session_record(
        database_module,
        db_path,
        "sess-claude-solved",
        client_source="claude-code",
        primary_model="claude-sonnet",
        started=1778410800000000,
        total_cost_usd=0.60,
        outcome="solved",
        source="manual",
    )

    report = database_module.daily_session_effectiveness_report(
        date="2026-05-10",
        db_path=db_path,
        user_id="test-user",
    )

    groups = {
        (group["client_source"], group["model"]): group for group in report["groups"]
    }
    assert groups[("codex", "gpt-5.5")]["session_count"] == 2
    assert groups[("codex", "gpt-5.5")]["evaluated_count"] == 2
    assert groups[("codex", "gpt-5.5")]["solved_count"] == 1
    assert groups[("codex", "gpt-5.5")]["failed_count"] == 1
    assert groups[("codex", "gpt-5.5")]["solve_rate"] == pytest.approx(0.5)
    assert groups[("codex", "gpt-5.5")]["cost_per_solved"] == pytest.approx(0.50)
    assert (
        "claude-code / claude-sonnet solved 1/1 evaluated sessions"
        in report["model_takeaways"]
    )


def test_daily_effectiveness_report_rejects_invalid_date(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    with pytest.raises(ValueError, match="Invalid date"):
        database_module.daily_session_effectiveness_report(
            date="2026-02-30",
            db_path=db_path,
            user_id="test-user",
        )


def test_upsert_hourly_aggregate_with_epoch_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=1776384000000000,  # 2026-04-17T00:00:00+00:00 (micros)
            provider="test-provider",
            model="test-model",
            client_source=None,
            session_id=None,
            endpoint="/v1/chat/completions",
            prompt_tokens=10,
            completion_tokens=5,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=15,
            latency_ms=100,
            ttft_ms=None,
            tool_tokens=None,
            cache_creation_tokens=None,
            input_cost_usd=0.002,
            output_cost_usd=0.003,
            total_cost_usd=0.005,
            status=200,
            base_url_id=None,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    # Daily aggregate should have been created
    from sqlalchemy.orm import Session

    from src.database.engine import get_engine
    from src.database.models import UsageHourly

    with Session(get_engine(db_path)) as session:
        daily = session.query(UsageHourly).first()
        assert daily is not None
        assert daily.hour == 1776384000


def test_fetch_recent_usage_filters_by_since_until_with_epoch_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Insert two rows at different times
    for ts_val in [1776384000000000, 1776470400000000]:  # Apr 17, Apr 18 (micros)
        ingest_write(
            database_module.log_usage,
            database_module.Usage(
                device_id="test-device",
                ts=ts_val,
                provider="test-provider",
                model="test-model",
                client_source=None,
                session_id=None,
                endpoint="/v1/chat/completions",
                prompt_tokens=10,
                completion_tokens=5,
                reasoning_tokens=None,
                cached_tokens=None,
                total_tokens=15,
                latency_ms=100,
                ttft_ms=None,
                tool_tokens=None,
                cache_creation_tokens=None,
                input_cost_usd=0.002,
                output_cost_usd=0.003,
                total_cost_usd=0.005,
                status=200,
                base_url_id=None,
                user_id="test-user",
            ),
            db_path=db_path,
        )

    # Filter with ISO string since/until (API boundary stays ISO)
    since_iso = "2026-04-17T12:00:00+00:00"
    rows = database_module.fetch_recent_usage(
        limit=100, since=since_iso, db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1  # only Apr 18 row


# ─── Tool name filtering ─────────────────────────────────────────────────────


def _insert_usage_with_tools(
    database_module,
    db_path,
    ts,
    tools,
    session_id="s1",
    client_source="claude-code",
):
    """Helper: insert one usage row and its tool calls.

    *ts* is in **microseconds** (same as Usage.ts).  Tool calls are stored with
    millisecond timestamps (as the OTLP layer does), so we divide by 1000.
    """
    from src.recorder import record_tool_call

    usage_obj = database_module.Usage(
        device_id="test-device",
        ts=ts,
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        client_source=client_source,
        session_id=session_id,
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=50,
        reasoning_tokens=None,
        cached_tokens=None,
        total_tokens=150,
        latency_ms=200,
        ttft_ms=None,
        tool_tokens=None,
        cache_creation_tokens=None,
        input_cost_usd=0.002,
        output_cost_usd=0.003,
        total_cost_usd=0.005,
        status=200,
        base_url_id=None,
        user_id="test-user",
    )
    ingest_write(database_module.log_usage, usage_obj, db_path=db_path)
    ts_ms = ts // 1000
    for i, tool_name in enumerate(tools):
        ingest_write(
            record_tool_call,
            tool_use_id=f"tool_{usage_obj.id}_{i}",
            usage_id=usage_obj.id,
            session_id=session_id,
            tool_name=tool_name,
            client_source=client_source,
            ts=ts_ms + i,
            db_path=db_path,
            user_id="test-user",
        )
    return usage_obj


def test_distinct_tool_names(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash", "read"],
        session_id="s1",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00_01,
        ["edit", "bash"],
        session_id="s2",
    )

    result = database_module.distinct_tool_names(db_path=db_path, user_id="test-user")
    assert result == ["bash", "edit", "read"]  # sorted, deduped


def test_distinct_tool_names_with_time_range(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash"],
        session_id="s1",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_18_00,
        ["edit"],
        session_id="s2",
    )

    # Only tools from Apr 17
    result = database_module.distinct_tool_names(
        since="2026-04-17T00:00:00+00:00",
        until="2026-04-17T23:59:59+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    assert result == ["bash"]

    # All tools
    result = database_module.distinct_tool_names(db_path=db_path, user_id="test-user")
    assert result == ["bash", "edit"]


def test_summarize_tool_calls(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash", "read"],
        session_id="s1",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00_01,
        ["Bash"],
        session_id="s2",
    )

    result = database_module.summarize_tool_calls(db_path=db_path, user_id="test-user")
    counts = {row["tool_name"]: row["count"] for row in result}
    assert counts == {"bash": 2, "read": 1}


def test_fetch_recent_usage_filters_by_tool_name(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash", "read"],
        session_id="s1",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00_01,
        ["edit"],
        session_id="s2",
    )

    rows = database_module.fetch_recent_usage(
        limit=10, tool_name="bash", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert "bash" in rows[0]["tool_names"]

    rows = database_module.fetch_recent_usage(
        limit=10, tool_name="edit", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1
    assert "edit" in rows[0]["tool_names"]

    rows = database_module.fetch_recent_usage(
        limit=10, tool_name="nonexistent", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 0

    rows = database_module.fetch_recent_usage(
        limit=10, db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 2


def test_fetch_recent_usage_filters_by_tool_name_case_insensitive(fresh_db):
    """tool_name is normalized to lowercase at write time; the filter must
    normalize on read too, or original-cased callers match nothing."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["Bash"],
        session_id="s1",
    )

    rows = database_module.fetch_recent_usage(
        limit=10, tool_name="Bash", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 1

    assert (
        database_module.count_usage(
            tool_name="Bash", db_path=db_path, user_id="test-user"
        )
        == 1
    )


def test_count_usage_filters_by_tool_name(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash"],
        session_id="s1",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00_01,
        ["edit"],
        session_id="s2",
    )
    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00_02,
        ["bash", "read"],
        session_id="s3",
    )

    assert (
        database_module.count_usage(
            tool_name="bash", db_path=db_path, user_id="test-user"
        )
        == 2
    )
    assert (
        database_module.count_usage(
            tool_name="edit", db_path=db_path, user_id="test-user"
        )
        == 1
    )
    assert (
        database_module.count_usage(
            tool_name="nonexistent", db_path=db_path, user_id="test-user"
        )
        == 0
    )
    assert database_module.count_usage(db_path=db_path, user_id="test-user") == 3


def test_usage_filters_tool_name_empty_string(fresh_db):
    """Empty string tool_name should be treated as a real filter (matching nothing), not skipped."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    _insert_usage_with_tools(
        database_module,
        db_path,
        TS_2026_04_17_00,
        ["bash"],
        session_id="s1",
    )

    rows = database_module.fetch_recent_usage(
        limit=10, tool_name="", db_path=db_path, user_id="test-user"
    )
    assert len(rows) == 0  # no tool calls with empty name


def test_recalculate_usage_cost_updates_row_and_hourly_rollup(fresh_db):
    """Recalculating a row must update its cost columns and delta-adjust (not
    replace) the matching usage_hourly rollup, since other rows share it."""
    from decimal import Decimal

    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="recalc-provider",
            model="recalc-model",
            client_source="proxy-client",
            session_id="session-recalc",
            endpoint="/v1/responses",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=0,
            total_tokens=2_000_000,
            status=200,
            # Stale cost, as if recorded under an old/wrong price.
            input_cost_usd=1.0,
            output_cost_usd=1.0,
            total_cost_usd=2.0,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    # A second row sharing the same usage_hourly bucket (same hour/provider/
    # model/client_source), to prove the rollup is delta-adjusted rather than
    # clobbered by the single recalculated row.
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_01,
            provider="recalc-provider",
            model="recalc-model",
            client_source="proxy-client",
            session_id="session-other",
            endpoint="/v1/responses",
            prompt_tokens=100,
            completion_tokens=100,
            cached_tokens=0,
            total_tokens=200,
            status=200,
            input_cost_usd=0.1,
            output_cost_usd=0.1,
            total_cost_usd=0.2,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    usage_id = next(
        r["id"]
        for r in database_module.fetch_recent_usage(
            limit=10, db_path=db_path, user_id="test-user"
        )
        if r["session_id"] == "session-recalc"
    )

    new_cost = ModelCost(input=2.0, output=4.0, cache_read=0.0)
    result = database_module.recalculate_usage_cost(
        usage_id,
        model_costs={"recalc-model": new_cost},
        db_path=db_path,
        user_id="test-user",
    )

    assert result is not None
    assert result.skipped is False
    # 1,000,000 tokens * $2/1M input + 1,000,000 tokens * $4/1M output.
    assert result.new_costs["input_cost_usd"] == Decimal("2")
    assert result.new_costs["output_cost_usd"] == Decimal("4")
    assert result.new_costs["total_cost_usd"] == Decimal("6")
    assert result.pricing is not None
    assert result.pricing["source"] == "server"
    assert result.pricing["input"] == 2.0
    assert result.pricing["output"] == 4.0
    assert result.pricing["tier"] is None
    assert result.pricing["snapshot_id"] is not None
    assert result.pricing["estimated"] is False

    row = next(
        r
        for r in database_module.fetch_recent_usage(
            limit=10, db_path=db_path, user_id="test-user"
        )
        if r["id"] == usage_id
    )
    assert float(row["total_cost_usd"]) == 6.0

    daily = database_module.summarize_usage_by_model(
        db_path=db_path, user_id="test-user"
    )
    bucket = next(d for d in daily if d["model"] == "recalc-model")
    # Old combined total was 2.0 + 0.2 = 2.2; the recalculated row's total
    # went 2.0 -> 6.0, so the bucket should read 6.0 + 0.2 = 6.2, not 6.0.
    assert bucket["total_cost_usd"] == pytest.approx(6.2)


def test_recalculate_usage_cost_adjusts_session_rollup(fresh_db):
    """Recalculating a session-linked row must delta-adjust sessions.total_cost_usd
    and its per-model cost map (and re-derive primary_model), not just usage_hourly —
    upsert_session_from_usage maintains this rollup at record time, so recalculation
    must keep it in sync too instead of letting it silently go stale."""
    import json
    from decimal import Decimal

    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="recalc-provider",
            model="recalc-model-a",
            client_source="proxy-client",
            session_id="session-rollup",
            endpoint="/v1/responses",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=0,
            total_tokens=2_000_000,
            status=200,
            input_cost_usd=1.0,
            output_cost_usd=1.0,
            total_cost_usd=2.0,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    # A second, differently-priced model in the same session, so the primary
    # model starts out as "recalc-model-b" (5.0 > 2.0) and should flip once
    # "recalc-model-a" is recalculated past it.
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_01,
            provider="recalc-provider",
            model="recalc-model-b",
            client_source="proxy-client",
            session_id="session-rollup",
            endpoint="/v1/responses",
            prompt_tokens=100,
            completion_tokens=100,
            cached_tokens=0,
            total_tokens=200,
            status=200,
            input_cost_usd=2.5,
            output_cost_usd=2.5,
            total_cost_usd=5.0,
            user_id="test-user",
        ),
        db_path=db_path,
    )

    usage_id = next(
        r["id"]
        for r in database_module.fetch_recent_usage(
            limit=10, db_path=db_path, user_id="test-user"
        )
        if r["model"] == "recalc-model-a"
    )

    new_cost = ModelCost(input=2.0, output=4.0, cache_read=0.0)
    result = database_module.recalculate_usage_cost(
        usage_id,
        model_costs={"recalc-model-a": new_cost},
        db_path=db_path,
        user_id="test-user",
    )
    assert result is not None
    assert result.skipped is False
    assert result.new_costs["total_cost_usd"] == Decimal("6")

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session_row = session.get(database_module.SessionRecord, "session-rollup")

    assert session_row is not None
    # Old combined total was 2.0 + 5.0 = 7.0; row a's total went 2.0 -> 6.0
    # (delta +4.0), so the session total should read 11.0, not just 6.0.
    assert float(session_row.total_cost_usd) == pytest.approx(11.0)
    models_costs = json.loads(session_row.models_json)
    assert models_costs["recalc-model-a"] == pytest.approx(6.0)
    assert models_costs["recalc-model-b"] == pytest.approx(5.0)
    # 6.0 > 5.0 now, so the primary model must have flipped.
    assert session_row.primary_model == "recalc-model-a"


def test_recalculate_usage_cost_skips_when_no_pricing_match(fresh_db):
    """A model with no resolvable pricing must be left untouched, not zeroed."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="test-provider",
            model="deprecated-model",
            client_source="proxy-client",
            session_id="session-skip",
            endpoint="/v1/responses",
            prompt_tokens=100,
            completion_tokens=100,
            total_tokens=200,
            status=200,
            input_cost_usd=0.5,
            output_cost_usd=0.5,
            total_cost_usd=1.0,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    usage_id = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )[0]["id"]

    result = database_module.recalculate_usage_cost(
        usage_id, model_costs={}, db_path=db_path, user_id="test-user"
    )

    assert result is not None
    assert result.skipped is True
    row = database_module.fetch_recent_usage(
        limit=1, db_path=db_path, user_id="test-user"
    )[0]
    assert float(row["total_cost_usd"]) == 1.0


def test_recalculate_usage_cost_returns_none_for_missing_row(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    assert (
        database_module.recalculate_usage_cost(
            "nonexistent-id", db_path=db_path, user_id="test-user"
        )
        is None
    )


# === PR 4 tenancy schema ===


def test_init_db_creates_tenancy_shapes(fresh_db):
    """AC 2: init_db alone (no migrate_database) creates the new shapes."""
    from sqlalchemy import inspect

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path

    engine = database_module.get_engine(db_path)
    inspector = inspect(engine)
    for table in ("usage", "usage_hourly", "sessions", "tool_calls", "evaluation_jobs"):
        assert "user_id" in {c["name"] for c in inspector.get_columns(table)}

    hourly_indexes = {i["name"]: i for i in inspector.get_indexes("usage_hourly")}
    assert hourly_indexes["uq_usage_hourly"]["unique"]

    for table, index_name in [
        ("usage", "ix_usage_user_id"),
        ("sessions", "ix_sessions_user_id"),
        ("tool_calls", "ix_tool_calls_user_id"),
    ]:
        assert index_name in {i["name"] for i in inspector.get_indexes(table)}


def test_upsert_hourly_aggregate_separates_user_rows(fresh_db):
    """Same (date, provider, model, client_source) for users u0 and u1
    produces two rows; increments land in the right row (both ON CONFLICT
    branches)."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    def make_usage(user_id):
        return database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_10,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            user_id=user_id,
            session_id="s1",
            endpoint="/v1/messages",
            prompt_tokens=100,
            prompt_length=500,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=200,
            status=200,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
        )

    _upsert_hourly(make_usage("u0"), db_path=db_path)
    _upsert_hourly(make_usage("u0"), db_path=db_path)
    _upsert_hourly(make_usage("u1"), db_path=db_path)
    _upsert_hourly(make_usage("u1"), db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(
            conn.execute(
                database_module.select(database_module.UsageHourly).order_by(
                    database_module.UsageHourly.id
                )
            )
        )
    assert len(rows) == 2
    by_user = {row.user_id: row for row in rows}
    assert set(by_user) == {"u0", "u1"}
    assert by_user["u0"].request_count == 2
    assert by_user["u0"].prompt_tokens == 200
    assert by_user["u1"].request_count == 2
    assert by_user["u1"].prompt_tokens == 200


def test_upsert_session_from_usage_scopes_tenant(fresh_db):
    """Users with distinct session_ids get distinct rows and the helper
    returns only the (user_id, session_id) match.

    The tenant guard (bare session_id PK): a second user writing the same
    session_id is skipped instead of polluting the first user's row.
    """
    from sqlalchemy.orm import Session as OrmSession

    from src.database.sessions import get_session_record

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    def make_usage(session_id, user_id, ts=TS_2026_04_17_10):
        return database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source="claude-code",
            user_id=user_id,
            session_id=session_id,
            endpoint="/v1/messages",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100,
            status=200,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
        )

    database_module.upsert_session_from_usage(
        make_usage("local-session", "u0"), db_path=db_path
    )
    database_module.upsert_session_from_usage(
        make_usage("tenant-session", "u1"), db_path=db_path
    )
    database_module.upsert_session_from_usage(
        make_usage("tenant-session", "u1", ts=TS_2026_04_17_11), db_path=db_path
    )

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(
            conn.execute(
                database_module.text(
                    "SELECT session_id, user_id, request_count FROM sessions "
                    "ORDER BY session_id"
                )
            )
        )
    assert rows == [("local-session", "u0", 1), ("tenant-session", "u1", 2)]

    engine = database_module.get_engine(db_path)
    with OrmSession(engine) as session:
        local_record = get_session_record(session, "local-session", user_id="u0")
        assert local_record is not None
        assert local_record.user_id == "u0"
        assert local_record.request_count == 1
        tenant_record = get_session_record(session, "tenant-session", user_id="u1")
        assert tenant_record is not None
        assert tenant_record.request_count == 2
        assert get_session_record(session, "tenant-session", user_id="u0") is None
        assert get_session_record(session, "tenant-session", user_id="u3") is None
        assert get_session_record(session, "local-session", user_id="u1") is None

    # Cross-tenant session_id collision: skipped, no pollution.
    database_module.upsert_session_from_usage(
        make_usage("local-session", "u1"), db_path=db_path
    )
    with database_module.get_engine(db_path).connect() as conn:
        local_row = conn.execute(
            database_module.text(
                "SELECT user_id, request_count FROM sessions WHERE session_id = 'local-session'"
            )
        ).one()
    assert local_row.user_id == "u0"
    assert local_row.request_count == 1


def test_log_usage_keeps_row_and_hourly_when_session_rollup_fails(
    fresh_db, monkeypatch
):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    import src.database.sessions as sessions_module
    from src.database import get_engine, log_usage
    from src.database.models import Usage, UsageHourly

    def boom(*_args, **_kwargs):
        raise RuntimeError("session rollup failed")

    monkeypatch.setattr(sessions_module, "_upsert_session_from_usage", boom)
    ingest_write(
        log_usage,
        Usage(
            device_id="test-device",
            ts=TS_2026_05_19_00,
            provider="openai",
            model="gpt-4.1",
            client_source="opencode",
            session_id="s1",
            endpoint="otlp",
            user_id="test-user",
        ),
        db_path=fresh_db.db_path,
    )

    with Session(get_engine(fresh_db.db_path)) as session:
        assert len(session.scalars(select(Usage)).all()) == 1
        assert len(session.scalars(select(UsageHourly)).all()) == 1


def test_upsert_session_adds_to_row_created_by_a_concurrent_first_usage(
    fresh_db, monkeypatch
):
    """Two first usages of a session can both see no row; the one whose insert
    loses must add to the winner's row, not fail or drop its increment."""
    import src.database.sessions as sessions_module

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path

    def make_usage(ts):
        return database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider="openai",
            model="gpt-4.1",
            client_source="codex",
            user_id="u1",
            session_id="racy",
            endpoint="otlp",
            total_tokens=15,
            status=200,
            total_cost_usd=0.003,
        )

    database_module.upsert_session_from_usage(
        make_usage(TS_2026_04_17_10), db_path=db_path
    )

    real = sessions_module.get_session_record
    calls = []

    def blind_first_lookup(*args, **kwargs):
        calls.append(1)
        return None if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(sessions_module, "get_session_record", blind_first_lookup)
    database_module.upsert_session_from_usage(
        make_usage(TS_2026_04_17_11), db_path=db_path
    )

    with database_module.get_engine(db_path).connect() as conn:
        row = conn.execute(
            database_module.text(
                "SELECT request_count, total_tokens FROM sessions "
                "WHERE session_id = 'racy'"
            )
        ).one()
    assert (row.request_count, row.total_tokens) == (2, 30)


def test_rebuild_sessions_from_usage_stamps_user_id(fresh_db):
    """AC 8: rebuild stamps user_id from the usage rows and still recomputes
    tool_calls_json from the tool_calls table."""
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        device_id="test-device",
        ts=TS_2026_05_09_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
        user_id="u1",
        session_id="sess-tools",
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        latency_ms=200,
        ttft_ms=100,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
        status=200,
    )
    ingest_write(database_module.log_usage, usage, db_path=db_path)

    ingest_write(
        record_tool_call,
        tool_use_id="t1",
        usage_id=usage.id,
        session_id="sess-tools",
        user_id="u1",
        tool_name="bash",
        ts=TS_2026_05_09_10,
        db_path=db_path,
    )

    count = database_module.rebuild_sessions_from_usage(db_path=db_path)
    assert count == 1

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(
            conn.execute(
                database_module.text(
                    "SELECT session_id, user_id, tool_calls_json FROM sessions"
                )
            )
        )
    assert len(rows) == 1
    assert rows[0].session_id == "sess-tools"
    assert rows[0].user_id == "u1"
    assert rows[0].tool_calls_json == '{"bash": 1}'


def test_recalculate_usage_cost_targets_user_slice(fresh_db):
    """The hourly delta UPDATE lands only in the row matching the usage row's
    user_id when both a NULL-slice and a real-user-slice row exist."""
    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="recalc-provider",
            model="recalc-model",
            client_source="proxy-client",
            session_id="session-recalc-local",
            endpoint="/v1/responses",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=0,
            total_tokens=2_000_000,
            status=200,
            input_cost_usd=0.1,
            output_cost_usd=0.1,
            total_cost_usd=0.2,
            user_id="test-user",
        ),
        db_path=db_path,
    )
    ingest_write(
        database_module.log_usage,
        database_module.Usage(
            device_id="test-device",
            ts=TS_2026_04_17_00,
            provider="recalc-provider",
            model="recalc-model",
            client_source="proxy-client",
            user_id="u1",
            endpoint="/v1/responses",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            cached_tokens=0,
            total_tokens=2_000_000,
            status=200,
            input_cost_usd=1.0,
            output_cost_usd=1.0,
            total_cost_usd=2.0,
        ),
        db_path=db_path,
    )

    usage_id = next(
        r["id"]
        for r in database_module.fetch_recent_usage(
            limit=10, db_path=db_path, user_id="test-user"
        )
        if r["session_id"] == "session-recalc-local"
    )

    new_cost = ModelCost(input=2.0, output=4.0, cache_read=0.0)
    result = database_module.recalculate_usage_cost(
        usage_id,
        model_costs={"recalc-model": new_cost},
        db_path=db_path,
        user_id="test-user",
    )
    assert result is not None
    assert result.skipped is False

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(
            conn.execute(
                database_module.text(
                    "SELECT user_id, total_cost_usd FROM usage_hourly ORDER BY user_id"
                )
            )
        )
    assert len(rows) == 2
    by_user = {row.user_id: row for row in rows}
    # test-user slice: 0.2 -> 6.0; the u1 slice is untouched.
    assert float(by_user["test-user"].total_cost_usd) == pytest.approx(6.0)
    assert float(by_user["u1"].total_cost_usd) == pytest.approx(2.0)


def test_migrate_database_rejects_sqlite_with_nullable_user_id(fresh_db):
    import pytest

    if "://" in fresh_db.db_path:
        pytest.skip("SQLite-only: rebuilds tool_calls with a SQLite-shaped schema")
    schema_migrations = fresh_db.schema_migrations_module
    database_module = fresh_db.database_module
    engine = database_module.get_engine(fresh_db.db_path)
    with engine.begin() as conn:
        conn.execute(database_module.text("DROP TABLE tool_calls"))
        conn.execute(
            database_module.text(
                "CREATE TABLE tool_calls (tool_use_id TEXT PRIMARY KEY, user_id TEXT)"
            )
        )
        conn.execute(database_module.text("DROP INDEX uq_usage_hourly"))

    try:
        with pytest.raises(RuntimeError, match="tool_calls"):
            schema_migrations.migrate_database(fresh_db.db_path)

        # Rejected before any DDL: the dropped index was not recreated.
        from sqlalchemy import inspect

        names = {i["name"] for i in inspect(engine).get_indexes("usage_hourly")}
        assert "uq_usage_hourly" not in names
    finally:  # the test schema is shared across the session
        from src.database.models import ToolCall

        with engine.begin() as conn:
            conn.execute(database_module.text("DROP TABLE tool_calls"))
        ToolCall.__table__.create(engine)
        schema_migrations.migrate_database(fresh_db.db_path)


def test_migrate_database_replaces_usage_daily_with_hourly_backfill(fresh_db):
    """With usage_daily present, migration rebuilds usage_hourly from raw usage
    over the rows ingest already wrote, nets tool time out of opencode/kilo
    hourly latency per row (raw latency_ms untouched) and drops usage_daily; a
    second run is a no-op."""
    from src.recorder import record_tool_call

    schema_migrations = fresh_db.schema_migrations_module
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    engine = database_module.get_engine(db_path)

    def log(user_id, source, ts, latency_ms, status, tool_ms=()):
        usage = database_module.Usage(
            device_id="test-device",
            ts=ts,
            provider="anthropic",
            model="m1",
            client_source=source,
            endpoint="/v1/messages",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=latency_ms,
            status=status,
            input_cost_usd=0.001,
            output_cost_usd=0.002,
            total_cost_usd=0.003,
            user_id=user_id,
        )
        ingest_write(database_module.log_usage, usage, db_path=db_path)
        for n, duration_ms in enumerate(tool_ms):
            ingest_write(
                record_tool_call,
                tool_use_id=f"{usage.id}-{n}",
                usage_id=usage.id,
                tool_name="bash",
                duration_ms=duration_ms,
                ts=ts // 1000,
                db_path=db_path,
                user_id=user_id,
            )
        return usage.id

    half_hour = 1_800_000_000
    ids = {
        "opencode": log("u0", "opencode", TS_2026_04_17_10, 1000, 200, (300, 400)),
        "opencode-2": log("u0", "opencode", TS_2026_04_17_10 + half_hour, 200, 500),
        "kilo": log("u0", "kilo", TS_2026_04_17_11, 500, 200, (800,)),
        "claude": log("u0", "claude-code", TS_2026_04_17_10, 1000, 429, (500,)),
        "none": log("u1", None, TS_2026_04_17_11, None, 200),
    }

    def hourly_rows():
        with engine.connect() as conn:
            return {
                (r.user_id, r.hour, r.client_source): {
                    k: v for k, v in r._mapping.items() if k != "id"
                }
                for r in conn.execute(
                    database_module.select(database_module.UsageHourly)
                )
            }

    # The live write path's rollup is the expected backfill, but for the
    # latency the migration takes tool time out of. Its rows stay in place:
    # ingest can write usage_hourly before migrate runs.
    expected = hourly_rows()
    hour_10, hour_11 = TS_2026_04_17_10 // 1_000_000, TS_2026_04_17_11 // 1_000_000
    expected[("u0", hour_10, "opencode")]["latency_sum_ms"] = 300 + 200
    expected[("u0", hour_11, "kilo")]["latency_sum_ms"] = 0
    with engine.begin() as conn:
        conn.execute(database_module.text("CREATE TABLE usage_daily (id INTEGER)"))

    assert "usage_hourly.backfill" in schema_migrations.migrate_database(db_path)

    from sqlalchemy import inspect

    assert "usage_daily" not in inspect(engine).get_table_names()
    assert hourly_rows() == expected
    with engine.connect() as conn:
        latency = dict(
            conn.execute(database_module.text("SELECT id, latency_ms FROM usage")).all()
        )
    assert {name: latency[usage_id] for name, usage_id in ids.items()} == {
        "opencode": 1000,
        "opencode-2": 200,
        "kilo": 500,
        "claude": 1000,
        "none": None,
    }

    assert schema_migrations.migrate_database(db_path) == []
    assert hourly_rows() == expected


def test_migrate_database_creates_missing_model_indexes(fresh_db):
    schema_migrations = fresh_db.schema_migrations_module
    database_module = fresh_db.database_module
    engine = database_module.get_engine(fresh_db.db_path)
    with engine.begin() as conn:
        conn.execute(database_module.text("DROP INDEX ix_sessions_started_desc"))
        conn.execute(database_module.text("DROP INDEX ix_usage_session_id"))

    changes = schema_migrations.migrate_database(fresh_db.db_path)

    assert {"sessions.ix_sessions_started_desc", "usage.ix_usage_session_id"} <= set(
        changes
    )


def test_migrate_database_creates_missing_user_model_prices(fresh_db):
    """An install from before price overrides gets the table on upgrade."""
    from sqlalchemy import inspect

    schema_migrations = fresh_db.schema_migrations_module
    database_module = fresh_db.database_module
    engine = database_module.get_engine(fresh_db.db_path)
    with engine.begin() as conn:
        conn.execute(database_module.text("DROP TABLE user_model_prices"))
    assert "user_model_prices" not in inspect(engine).get_table_names()

    schema_migrations.migrate_database(fresh_db.db_path)

    assert "user_model_prices" in inspect(engine).get_table_names()


def test_sqlite_pragmas_enable_wal_and_busy_timeout(tmp_path):
    import sqlite3

    from src.database.engine import SQLITE_BUSY_TIMEOUT_MS, _sqlite_pragmas

    conn = sqlite3.connect(tmp_path / "wal.db")
    _sqlite_pragmas(conn, None)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
    conn.close()


def test_log_usage_rolls_back_row_when_hourly_rollup_fails(fresh_db, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    import src.database.usage as usage_module
    from src.database import get_engine, log_usage
    from src.database.models import ToolCall, Usage, UsageHourly

    def boom(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(usage_module, "upsert_hourly_aggregate", boom)
    usage = Usage(
        device_id="test-device",
        ts=TS_2026_05_19_00,
        provider="openai",
        model="gpt-4.1",
        client_source="opencode",
        session_id="atomic-session",
        endpoint="otlp",
        user_id="test-user",
    )
    with pytest.raises(RuntimeError):
        ingest_write(
            log_usage,
            usage,
            db_path=fresh_db.db_path,
            tool_calls=[
                {
                    "tool_use_id": "toolu_1",
                    "user_id": "test-user",
                    "tool_name": "bash",
                    "ts": 1,
                }
            ],
        )

    with Session(get_engine(fresh_db.db_path)) as session:
        assert session.scalars(select(Usage)).all() == []
        assert session.scalars(select(UsageHourly)).all() == []
        assert session.scalars(select(ToolCall)).all() == []


def _upsert_hourly(usage, db_path=None):
    from sqlalchemy.orm import Session

    from src.database import get_engine
    from src.database.usage import upsert_hourly_aggregate

    with Session(get_engine(db_path)) as session:
        upsert_hourly_aggregate(session, usage)
        session.commit()


def test_reprice_batch_rounds_like_per_row_updates(fresh_db, monkeypatch):
    """Sub-cent-scale costs: each row's change is rounded to the columns'
    8 decimals before grouping, so the daily and session totals equal the sum
    of the stored rows whatever the chunk size."""
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database import usage as usage_module
    from src.database.models import SessionRecord, UsageHourly
    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    # One input token at $0.006/M costs 0.000000006: stored as 0.00000001.
    prices = {"m-tiny": ModelCost(input=0.006, output=0.0, cache_read=0.0)}
    for user_id, chunk in (("tiny-row", 1), ("tiny-chunk", 500)):
        _batch_user(database_module, db_path, user_id)
        for i in range(3):
            ingest_write(
                database_module.log_usage,
                database_module.Usage(
                    id=f"{user_id}-{i}",
                    device_id=f"{user_id}-device",
                    ts=TS_2026_05_05_10 + i,
                    provider="p",
                    model="m-tiny",
                    client_source="codex",
                    session_id=f"{user_id}-s",
                    endpoint="otlp",
                    prompt_tokens=1,
                    completion_tokens=0,
                    cached_tokens=0,
                    total_tokens=1,
                    status=200,
                    user_id=user_id,
                ),
                db_path=db_path,
            )
        monkeypatch.setattr(usage_module, "_REPRICE_CHUNK", chunk)
        database_module.reprice_estimated_rows(
            user_id=user_id, model_costs=prices, model_cost_sources={}, db_path=db_path
        )
        with Session(database_module.get_engine(db_path)) as session:
            rows = sum(
                Decimal(str(u.total_cost_usd))
                for u in session.scalars(
                    select(database_module.Usage).where(
                        database_module.Usage.user_id == user_id
                    )
                )
            )
            daily = session.scalar(
                select(UsageHourly.total_cost_usd).where(UsageHourly.user_id == user_id)
            )
            total = session.scalar(
                select(SessionRecord.total_cost_usd).where(
                    SessionRecord.user_id == user_id
                )
            )
        assert rows == Decimal("0.00000003")
        assert Decimal(str(daily)) == rows
        assert Decimal(str(total)) == rows


def test_reprice_statement_count_does_not_grow_per_row(fresh_db, monkeypatch):
    """Batched: the statement count depends on chunks and rollup keys, not on
    how many rows a chunk reprices (no per-row reloads after commit)."""
    from sqlalchemy import event

    from src.database import usage as usage_module
    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    prices = {"m-flat": ModelCost(input=1.0, output=1.0, cache_read=0.0)}
    monkeypatch.setattr(usage_module, "_REPRICE_CHUNK", 500)
    engine = database_module.get_engine(db_path)
    counts = {}
    for user_id, n in (("few", 5), ("many", 60)):
        _batch_user(database_module, db_path, user_id)
        for i in range(n):
            ingest_write(
                database_module.log_usage,
                database_module.Usage(
                    id=f"{user_id}-{i}",
                    device_id=f"{user_id}-device",
                    ts=TS_2026_05_05_10 + i,
                    provider="p",
                    model="m-flat",
                    client_source="codex",
                    session_id=f"{user_id}-s",
                    endpoint="otlp",
                    prompt_tokens=100,
                    completion_tokens=10,
                    cached_tokens=0,
                    total_tokens=110,
                    status=200,
                    user_id=user_id,
                ),
                db_path=db_path,
            )
        statements = []

        def count(*args, **kwargs):
            statements.append(1)

        event.listen(engine, "before_cursor_execute", count)
        try:
            summary = database_module.reprice_estimated_rows(
                user_id=user_id,
                model_costs=prices,
                model_cost_sources={},
                db_path=db_path,
            )
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert summary["repriced"] == n
        counts[user_id] = len(statements)
    # 12x the rows: a per-row reload would add ~55 statements. Small
    # differences come from the first run creating the shared snapshot.
    assert counts["many"] - counts["few"] < 10, counts


def test_hourly_upsert_binds_fresh_values_per_row(fresh_db):
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database.models import Usage, UsageHourly
    from src.database.usage import (
        _HOURLY_COUNTER_COLUMNS,
        _HOURLY_KEY_COLUMNS,
        hourly_values,
        upsert_hourly_aggregate,
    )

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)

    def usage(model, tokens, cost):
        return Usage(
            user_id="test-user",
            device_id="test-device",
            ts=TS_2026_04_17_10,
            provider="anthropic",
            model=model,
            client_source=None,
            prompt_tokens=tokens,
            completion_tokens=3,
            prompt_length=0,
            input_cost_usd=Decimal(cost),
            output_cost_usd=Decimal("0.00000003"),
            total_cost_usd=Decimal(cost) + Decimal("0.00000003"),
        )

    # The cached statement binds exactly what hourly_values produces.
    assert set(hourly_values(usage("first", 1, "0"))) == {
        *_HOURLY_KEY_COLUMNS,
        *_HOURLY_COUNTER_COLUMNS,
    }
    with Session(engine) as session:
        upsert_hourly_aggregate(session, usage("first", 7, "0.00000001"))
        session.commit()
        upsert_hourly_aggregate(session, usage("first", 999, "12"))
        session.rollback()
        upsert_hourly_aggregate(session, usage("first", 11, "0.00000002"))
        upsert_hourly_aggregate(session, usage("second", 23, "0.00000007"))
        session.commit()
        rows = {r.model: r for r in session.scalars(select(UsageHourly))}
        assert rows["first"].request_count == 2
        assert rows["first"].prompt_tokens == 18
        assert rows["first"].total_cost_usd == Decimal("0.00000009")
        assert rows["second"].request_count == 1
        assert rows["second"].prompt_tokens == 23
        assert rows["second"].total_cost_usd == Decimal("0.00000010")
        assert all(r.client_source == "" for r in rows.values())
