import pytest

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

    base_url_id = database_module.get_or_create_base_url(
        "https://api.example.com/v1",
        db_path=db_path,
        provider_name="test-provider",
        source="proxy_config",
    )

    database_module.log_usage(
        database_module.Usage(
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
        "tool_duration_ms": None,
        "cache_creation_tokens": None,
        "input_cost_usd": 2e-05,
        "output_cost_usd": 3e-05,
        "total_cost_usd": 5e-05,
        "status": 200,
        "client_ip": None,
        "base_url_id": base_url_id,
        "price_snapshot_id": None,
        "base_url": "https://api.example.com/v1",
    }


def test_log_usage_normalizes_model_before_rollups(fresh_db):
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database import get_engine, log_usage
    from src.database.models import SessionRecord, Usage, UsageDaily

    usage = Usage(
        ts=TS_2026_05_19_00,
        provider="CuStOm",
        model=" Space-Bunny-Free ",
        client_source="opencode",
        session_id="normalized-session",
        endpoint="otlp",
        user_id="test-user",
    )
    log_usage(usage, db_path=fresh_db.db_path)

    with Session(get_engine(fresh_db.db_path)) as session:
        assert session.get(Usage, usage.id).provider == "custom"
        assert session.get(Usage, usage.id).model == "stealth/space-bunny-alpha"
        assert session.scalars(select(UsageDaily)).one().provider == "custom"
        assert (
            session.scalars(select(UsageDaily)).one().model
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

    database_module.log_usage(
        database_module.Usage(
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
        "tool_duration_ms",
        "cache_creation_tokens",
        "input_cost_usd",
        "output_cost_usd",
        "total_cost_usd",
        "status",
        "client_ip",
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
    database_module.log_usage(usage_obj, db_path=db_path)

    # Insert multiple tool calls for this usage
    for i, tool_name in enumerate(["read_file", "edit_file", "bash"]):
        record_tool_call(
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

    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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


def test_summarize_usage_daily_includes_avg_effective_price(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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

    rows = database_module.summarize_usage_daily(user_id="test-user")
    assert rows[0]["avg_effective_price_usd"] == pytest.approx(0.0015 / 450, abs=1e-8)


def test_summarize_usage_by_provider_includes_avg_effective_price(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for model, total_tokens, total_cost in [
        ("test-model", 150, 0.0005),
        ("gpt-4.1", 300, 0.0010),
    ]:
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
    }


def test_reprice_estimated_rows_rebinds_and_updates_costs(
    database_module, isolated_home
):
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice.db")
    database_module.init_db(db_path)
    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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


def test_reprice_limit_scans_past_unresolvable_rows(database_module, isolated_home):
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice-limit.db")
    database_module.init_db(db_path)
    # Earlier row has no pricing match; the later row must still be repriced
    # even though `limit=1` only allows one successful row.
    for ts, model in (
        (TS_2026_05_03_18, "missing-model"),
        (TS_2026_05_03_18 + 1, "m1"),
    ):
        database_module.log_usage(
            database_module.Usage(
                ts=ts,
                provider="p1",
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

    summary = database_module.reprice_estimated_rows(
        limit=1,
        model_costs={"m1": ModelCost(input=2.0, output=4.0, cache_read=0.5)},
        model_cost_sources={},
        db_path=db_path,
    )

    assert summary == {"candidates": 2, "repriced": 1, "skipped": 1}
    rows = database_module.fetch_recent_usage(
        limit=10, db_path=db_path, user_id="test-user"
    )
    by_model = {row["model"]: row for row in rows}
    assert by_model["m1"]["price_snapshot_id"] is not None
    assert by_model["missing-model"]["price_snapshot_id"] is None


def test_reprice_counts_snapshot_failure_as_skipped(
    database_module, isolated_home, monkeypatch
):
    from src.pricing import snapshots
    from src.pricing.models import ModelCost

    db_path = str(isolated_home / "reprice-snapshot-fail.db")
    database_module.init_db(db_path)
    database_module.log_usage(
        database_module.Usage(
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


def test_summarize_usage_window_groups_by_session_source_and_model(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    rows = [
        database_module.Usage(
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
        database_module.log_usage(row, db_path=db_path)

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


def test_summarize_usage_window_filters_after_and_until_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for index in range(3):
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(row, db_path=db_path)

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

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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

    assert daily == [
        {
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
            "tool_duration_sum_ms": 0,
            "avg_latency_ms": 150.0,
        }
    ]


def test_throughput_excludes_tool_execution_time(fresh_db):
    """Dashboard throughput nets tool-call duration out of the latency sum.

    Only sources whose latency is the full assistant-message lifetime (so it
    already contains tool execution) are netted; opencode is one of them.
    """
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
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
    database_module.log_usage(usage, db_path=db_path)
    record_tool_call(
        tool_use_id="tool-throughput-1",
        usage_id=usage.id,
        tool_name="bash",
        duration_ms=500,
        ts=TS_2026_04_17_10 // 1000,
        db_path=db_path,
        user_id="test-user",
    )

    # Net latency 1000 - 500 = 500ms → 100 tokens / 0.5s = 200 tok/s
    daily = database_module.aggregate_usage_by_period(
        granularity="day", user_id="test-user"
    )
    assert daily[0]["avg_throughput"] == 200.0

    # Rollup summary exposes the tool sum and nets throughput
    summary = database_module.summarize_usage_daily(user_id="test-user")
    assert summary[0]["tool_duration_sum_ms"] == 500
    assert summary[0]["avg_throughput"] == 200.0

    # Raw summary path (short range) too
    raw = database_module.summarize_usage_daily(
        since="2026-04-17T00:00:00", until="2026-04-17T23:59:59", user_id="test-user"
    )
    assert raw[0]["tool_duration_sum_ms"] == 500
    assert raw[0]["avg_throughput"] == 200.0

    # by-source too (source-dimension Speed bar reads avg_throughput)
    by_source = database_module.summarize_usage_by_source(user_id="test-user")
    assert by_source[0]["tool_duration_sum_ms"] == 500
    assert by_source[0]["avg_throughput"] == 200.0

    # by-provider API path nets in the rollup branch
    by_provider = database_module.summarize_usage_by_provider(user_id="test-user")
    assert by_provider[0]["tool_duration_sum_ms"] == 500
    assert by_provider[0]["avg_throughput"] == 200.0

    # daily rollup reader (TrendChart) nets too
    daily_rollup = database_module.aggregate_daily_by_period(user_id="test-user")
    assert daily_rollup[0]["tool_duration_sum_ms"] == 500
    assert daily_rollup[0]["avg_throughput"] == 200.0


def test_throughput_keeps_tool_time_for_api_request_sources(fresh_db):
    """claude-code/codex latency is a single model call and excludes tool time.

    Netted tool duration would overstate throughput, so their tool time stays
    out of the throughput subtraction.
    """
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
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
    database_module.log_usage(usage, db_path=db_path)
    record_tool_call(
        tool_use_id="tool-claude-1",
        usage_id=usage.id,
        tool_name="bash",
        duration_ms=500,
        ts=TS_2026_04_17_10 // 1000,
        db_path=db_path,
        user_id="test-user",
    )

    # Latency already excludes the tool, so throughput stays 100 tok/s.
    daily = database_module.aggregate_usage_by_period(
        granularity="day", user_id="test-user"
    )
    assert daily[0]["tool_duration_sum_ms"] == 0
    assert daily[0]["avg_throughput"] == 100.0


def test_fetch_tool_calls_derives_row_relative_start(fresh_db):
    """Tool-inclusive sources get exact start offsets from completion - duration.

    Parallel tools that finish together must still start at different offsets
    when their durations differ.
    """
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
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
    database_module.log_usage(usage, db_path=db_path)

    completion_ms = usage.ts // 1000 - 100
    for tool_use_id, duration_ms, expected_start in [
        ("tool-short", 300, 600),
        ("tool-long", 800, 100),
        # A merged proxy row can have a shorter latency than the tool lifetime;
        # the start may exceed it and the timeline expands its axis.
        ("tool-late", 1000, 4900),
    ]:
        record_tool_call(
            tool_use_id=tool_use_id,
            usage_id=usage.id,
            tool_name="read",
            duration_ms=duration_ms,
            ts=completion_ms + (5000 if tool_use_id == "tool-late" else 0),
            db_path=db_path,
            user_id="test-user",
        )

    calls = database_module.fetch_tool_calls(
        usage_id=usage.id, db_path=db_path, user_id="test-user"
    )
    starts = {call["tool_use_id"]: call["start_ms"] for call in calls}
    assert starts == {"tool-short": 600, "tool-long": 100, "tool-late": 4900}

    claude_usage = database_module.Usage(
        ts=TS_2026_04_17_10,
        provider="anthropic",
        model="claude-sonnet-4-6",
        client_source="claude-code",
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
    database_module.log_usage(claude_usage, db_path=db_path)
    record_tool_call(
        tool_use_id="tool-claude-start",
        usage_id=claude_usage.id,
        tool_name="read",
        duration_ms=300,
        ts=completion_ms,
        db_path=db_path,
        user_id="test-user",
    )

    claude_calls = database_module.fetch_tool_calls(
        usage_id=claude_usage.id, db_path=db_path, user_id="test-user"
    )
    assert claude_calls[0]["start_ms"] is None


def test_get_or_create_base_url_reuses_exact_url_and_updates_metadata(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    base_url_id = database_module.get_or_create_base_url(
        "https://gateway.example/v1",
        db_path=db_path,
    )
    same_id = database_module.get_or_create_base_url(
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


def test_get_or_create_base_url_retries_in_fresh_transaction_after_duplicate_insert(
    database_module, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    import src.database.base_url as base_url_mod

    class FakeRow:
        id = 7
        provider_name = None
        source = None

    state = {"attempt": -1, "updated": False, "rolled_back": False}

    class FakeSession:
        def __init__(self, engine):
            state["attempt"] += 1
            self.attempt = state["attempt"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def scalar(self, statement):
            if self.attempt == 0:
                return None
            return FakeRow()

        def add(self, row):
            self.row = row

        def commit(self):
            if self.attempt == 0:
                raise IntegrityError("INSERT", None, Exception("dup"))
            state["updated"] = True

        def rollback(self):
            state["rolled_back"] = True

    monkeypatch.setattr(base_url_mod, "get_engine", lambda db_path=None: object())
    monkeypatch.setattr(base_url_mod, "Session", FakeSession)

    base_url_id = database_module.get_or_create_base_url(
        "https://race.example/v1",
        provider_name="OpenAI",
        source="proxy_config",
    )

    assert base_url_id == 7
    assert state["updated"] is True
    assert state["rolled_back"] is True


def test_upsert_daily_aggregate_inserts_and_updates(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage1 = database_module.Usage(
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
    database_module.log_usage(usage1, db_path=db_path)

    # Verify insert
    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageDaily)))
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert rows[0].prompt_tokens == 100
    assert rows[0].latency_sum_ms == 200
    assert rows[0].successful_requests == 1
    assert rows[0].failed_requests == 0

    # Same date, provider, model, client_source -> upsert
    usage2 = database_module.Usage(
        ts=TS_2026_04_17_11,
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
    database_module.log_usage(usage2, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageDaily)))
    assert len(rows) == 1
    assert rows[0].request_count == 2
    assert rows[0].prompt_tokens == 300
    assert rows[0].completion_tokens == 150
    assert rows[0].latency_sum_ms == 500
    assert rows[0].successful_requests == 1
    assert rows[0].failed_requests == 1
    assert float(rows[0].total_cost_usd) == 0.009


def test_upsert_daily_aggregate_handles_null_client_source(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
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
    database_module.log_usage(usage, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageDaily)))
    assert len(rows) == 1
    assert rows[0].client_source == ""
    assert rows[0].request_count == 1


def test_upsert_daily_aggregate_separates_by_date(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts in [TS_2026_04_17_10, TS_2026_04_18_10]:
        usage = database_module.Usage(
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
        database_module.log_usage(usage, db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(conn.execute(database_module.select(database_module.UsageDaily)))
    assert len(rows) == 2
    dates = {r.date for r in rows}
    assert dates == {"2026-04-17", "2026-04-18"}


def test_log_usage_automaticaly_upserts_daily_aggregate(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
        rows = list(conn.execute(database_module.select(database_module.UsageDaily)))
    assert len(rows) == 1
    assert rows[0].request_count == 1
    assert rows[0].prompt_tokens == 100


def test_summarize_usage_daily_reads_from_aggregate_table(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Insert two rows on same day, same provider/model
    for prompt, comp, latency, status in [
        (100, 50, 200, 200),
        (200, 100, 300, 500),
    ]:
        usage = database_module.Usage(
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
        database_module.log_usage(usage, db_path=db_path)

    summary = database_module.summarize_usage_daily(user_id="test-user")

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


def test_summarize_usage_daily_filters_by_provider(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for provider, model, ts in [
        ("anthropic", "claude-sonnet-4-6", TS_2026_04_17_10),
        ("openai", "gpt-4", TS_2026_04_17_11),
    ]:
        usage = database_module.Usage(
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
        database_module.log_usage(usage, db_path=db_path)

    summary = database_module.summarize_usage_daily(
        provider="anthropic", user_id="test-user"
    )
    assert len(summary) == 1
    assert summary[0]["provider"] == "anthropic"


def test_summarize_usage_daily_filters_by_since(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts in [TS_2026_04_17_10, TS_2026_04_20_10]:
        usage = database_module.Usage(
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
        database_module.log_usage(usage, db_path=db_path)

    summary = database_module.summarize_usage_daily(
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
    database_module.log_usage(
        database_module.Usage(
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


def test_summarize_usage_daily_sub_day_range(fresh_db):
    """Sub-day time range must use raw usage table, not daily aggregates."""
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
    summary = database_module.summarize_usage_daily(
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
    """by-source with sub-day range must use raw usage table."""
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
    """by-provider with sub-day range must use raw usage table."""
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


def test_aggregate_daily_by_period_reads_from_aggregate_table(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, tokens in [
        (TS_2026_04_17_10, 100),
        (TS_2026_04_17_11, 200),
        (TS_2026_04_18_10, 300),
    ]:
        usage = database_module.Usage(
            ts=ts,
            provider="anthropic",
            model="claude-sonnet-4-6",
            client_source=None,
            session_id=None,
            endpoint="/v1/messages",
            prompt_tokens=tokens,
            prompt_length=500,
            completion_tokens=tokens // 2,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=tokens + tokens // 2,
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
        database_module.log_usage(usage, db_path=db_path)

    result = database_module.aggregate_daily_by_period(user_id="test-user")

    assert len(result) == 2
    assert result[0]["period"] == "2026-04-17"
    assert result[0]["requests"] == 2
    assert result[0]["prompt_tokens"] == 300
    assert result[0]["total_tokens"] == 450
    assert result[1]["period"] == "2026-04-18"
    assert result[1]["requests"] == 1
    assert result[1]["prompt_tokens"] == 300


def test_aggregate_daily_by_period_filters_by_provider(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for provider, model in [("anthropic", "claude-sonnet-4-6"), ("openai", "gpt-4")]:
        usage = database_module.Usage(
            ts=TS_2026_04_17_10,
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
        database_module.log_usage(usage, db_path=db_path)

    result = database_module.aggregate_daily_by_period(
        provider="anthropic", user_id="test-user"
    )
    assert len(result) == 1
    assert result[0]["requests"] == 1


def test_aggregate_usage_by_period_hourly_with_tz_offset(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Two rows 1 hour apart in UTC
    for ts, tokens in [
        (TS_2026_05_05_10_30, 100),
        (TS_2026_05_05_11_45, 200),
    ]:
        database_module.log_usage(
            database_module.Usage(
                ts=ts,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                client_source=None,
                session_id=None,
                endpoint="/v1/messages",
                prompt_tokens=50,
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

    # With +05:30 offset, 10:30 UTC -> 16:00 IST, 11:45 UTC -> 17:15 IST
    result = database_module.aggregate_usage_by_period(
        granularity="hour", tz_offset="+05:30", user_id="test-user"
    )
    assert len(result) == 2
    assert result[0]["period"] == "2026-05-05 16:00"
    assert result[0]["requests"] == 1
    assert result[0]["total_tokens"] == 100
    assert result[1]["period"] == "2026-05-05 17:00"
    assert result[1]["requests"] == 1
    assert result[1]["total_tokens"] == 200


def test_aggregate_usage_by_period_hourly_merges_same_hour(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Two rows in the same UTC hour
    for ts, prompt, completion in [
        (TS_2026_05_05_10_15, 50, 50),
        (TS_2026_05_05_10_45, 30, 70),
    ]:
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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


def test_aggregate_daily_by_dimension_groups_by_provider(fresh_db):
    """aggregate_daily_by_dimension returns daily data grouped by provider."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    for ts, provider, model, tokens, cost in [
        (TS_2026_05_07_10, "anthropic", "claude-sonnet-4-6", 1000, 0.01),
        (TS_2026_05_07_10, "openai", "gpt-4o", 500, 0.005),
    ]:
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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


def test_fetch_recent_usage_only_failed_filter(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.Usage(
                ts=TS_2024_01_01_00,
                provider="p1",
                model="m1",
                endpoint="/chat",
                status=200,
                user_id="test-user",
            )
        )
        session.add(
            database_module.Usage(
                ts=TS_2024_01_01_01,
                provider="p1",
                model="m1",
                endpoint="/chat",
                status=500,
                user_id="test-user",
            )
        )
        session.commit()

    # When: fetch all
    all_rows = database_module.fetch_recent_usage(
        limit=10, db_path=db_path, user_id="test-user"
    )
    assert len(all_rows) == 2

    # When: fetch only failed
    failed_rows = database_module.fetch_recent_usage(
        limit=10, only_failed=True, db_path=db_path, user_id="test-user"
    )
    assert len(failed_rows) == 1
    assert failed_rows[0]["status"] == 500


def test_usage_daily_status_breakdown(database_module, isolated_home):
    db_path = str(isolated_home / "usage_status.db")
    database_module.init_db(db_path)

    # Record 4 requests with different status codes
    codes = [200, 429, 403, 500]
    for i, code in enumerate(codes):
        usage = database_module.Usage(
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
        database_module.upsert_daily_aggregate(usage, db_path=db_path)

    # Check aggregation
    summary = database_module.summarize_usage_daily(
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

    database_module.log_usage(
        database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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
        database_module.log_usage(
            database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(usage, db_path=db_path)

    for tool_id, tool_name in [("t1", "bash"), ("t2", "bash"), ("t3", "read")]:
        record_tool_call(
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


# ---------------------------------------------------------------------------
# Slice 7: Session Evaluation Jobs
# ---------------------------------------------------------------------------


def test_create_session_evaluation_job_persists_queued_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module,
        db_path,
        "sess-llm",
        client_source="codex",
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    assert job["job_id"]
    assert job["kind"] == "session_evaluation"
    assert job["session_id"] == "sess-llm"
    assert job["status"] == "queued"
    assert job["error"] is None

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled == job


def test_create_session_evaluation_job_records_trigger(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module, db_path, "sess-manual", client_source="codex"
    )

    manual = database_module.create_session_evaluation_job(
        session_id="sess-manual",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )

    assert manual["trigger"] == "manual"


def test_create_session_evaluation_job_defaults_trigger_to_manual(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module, db_path, "sess-default", client_source="codex"
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-default",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    assert job["trigger"] == "manual"


def test_find_active_session_evaluation_job_reuses_non_stale_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "sess-llm", client_source="codex")

    job = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    active = database_module.find_active_session_evaluation_job(
        session_id="sess-llm",
        db_path=db_path,
        user_id="test-user",
    )

    assert active is not None
    assert active["job_id"] == job["job_id"]


def test_create_session_evaluation_job_reuses_active_job_after_unique_conflict(
    fresh_db,
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "sess-llm", client_source="codex")

    first = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )
    second = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    assert second["job_id"] == first["job_id"]


def test_create_session_evaluation_job_persists_evaluator_type(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module, db_path, "sess-claude", client_source="codex"
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-claude",
        client_source="codex",
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    assert job["evaluator_type"] == "claude"
    assert (
        database_module.get_evaluation_job(
            job["job_id"], db_path=db_path, user_id="test-user"
        )["evaluator_type"]
        == "claude"
    )


def test_update_queued_evaluation_job_evaluator_preserves_queue_position(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "first", client_source="codex")
    _insert_session_record(database_module, db_path, "second", client_source="codex")
    database_module.create_session_evaluation_job(
        session_id="first",
        client_source="codex",
        evaluator_type="codex",
        db_path=db_path,
        user_id="test-user",
    )
    job = database_module.create_session_evaluation_job(
        session_id="second",
        client_source="codex",
        evaluator_type="codex",
        db_path=db_path,
        user_id="test-user",
    )
    before = database_module.get_evaluation_job_progress(
        job["job_id"], db_path=db_path, user_id="test-user"
    )

    updated = database_module.update_queued_evaluation_job_evaluator(
        job["job_id"],
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )
    after = database_module.get_evaluation_job_progress(
        job["job_id"], db_path=db_path, user_id="test-user"
    )

    assert updated is not None
    assert updated["job_id"] == job["job_id"]
    assert updated["evaluator_type"] == "claude"
    assert after["queue_position"] == before["queue_position"]


def test_update_queued_evaluation_job_evaluator_rejects_running_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "running", client_source="codex")
    job = database_module.create_session_evaluation_job(
        session_id="running",
        client_source="codex",
        evaluator_type="codex",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)

    updated = database_module.update_queued_evaluation_job_evaluator(
        job["job_id"],
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    assert updated is None
    assert (
        database_module.get_evaluation_job(
            job["job_id"], db_path=db_path, user_id="test-user"
        )["evaluator_type"]
        == "codex"
    )


def test_list_session_evaluation_jobs_with_progress_includes_history(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module, db_path, "sess-history", client_source="codex"
    )

    failed = database_module.create_session_evaluation_job(
        session_id="sess-history",
        client_source="codex",
        evaluator_type="codex",
        created_at="2026-05-14T09:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(
        now="2026-05-14T09:01:00+00:00",
        db_path=db_path,
    )
    database_module.mark_evaluation_job_failed(
        failed["job_id"],
        "Evaluator exited with code 1\nfull stderr details",
        db_path=db_path,
    )
    queued = database_module.create_session_evaluation_job(
        session_id="sess-history",
        client_source="codex",
        evaluator_type="claude",
        created_at="2026-05-14T09:02:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )

    jobs = database_module.list_session_evaluation_jobs_with_progress(
        "sess-history",
        db_path=db_path,
        user_id="test-user",
    )

    assert [job["job_id"] for job in jobs] == [queued["job_id"], failed["job_id"]]
    assert jobs[0]["evaluator_type"] == "claude"
    assert jobs[0]["queue_position"] == 1
    assert jobs[1]["status"] == "failed"
    assert jobs[1]["error"] == "Evaluator exited with code 1\nfull stderr details"


def test_stale_session_evaluation_job_is_failed_before_new_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "sess-llm", client_source="codex")

    old_job = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        created_at="2026-05-11T10:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )

    active = database_module.find_active_session_evaluation_job(
        session_id="sess-llm",
        now="2026-05-11T10:06:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )

    assert active is None
    stale = database_module.get_evaluation_job(
        old_job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert stale is not None
    assert stale["status"] == "failed"
    assert stale["error"] == "Evaluation job timed out"


def test_running_session_evaluation_job_uses_started_at_for_stale_check(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module, db_path, "sess-running", client_source="codex"
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-running",
        client_source="codex",
        created_at="2026-05-11T10:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    claimed = database_module.claim_next_evaluation_job(
        now="2026-05-11T10:05:00+00:00",
        db_path=db_path,
    )

    active = database_module.find_active_session_evaluation_job(
        session_id="sess-running",
        now="2026-05-11T10:06:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )

    assert claimed["job_id"] == job["job_id"]
    assert active is not None
    assert active["job_id"] == job["job_id"]
    assert active["status"] == "running"


def test_claim_next_evaluation_job_orders_manual_before_auto_then_newest_session(
    fresh_db,
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module,
        db_path,
        "old-auto",
        client_source="codex",
        updated_at="2026-05-14T10:00:00+00:00",
    )
    _insert_session_record(
        database_module,
        db_path,
        "new-auto",
        client_source="codex",
        updated_at="2026-05-14T11:00:00+00:00",
    )
    _insert_session_record(
        database_module,
        db_path,
        "manual",
        client_source="codex",
        updated_at="2026-05-14T09:00:00+00:00",
    )
    database_module.create_session_evaluation_job(
        session_id="old-auto",
        client_source="codex",
        trigger="auto",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.create_session_evaluation_job(
        session_id="new-auto",
        client_source="codex",
        trigger="auto",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.create_session_evaluation_job(
        session_id="manual",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )

    first = database_module.claim_next_evaluation_job(db_path=db_path)
    second = database_module.claim_next_evaluation_job(db_path=db_path)
    third = database_module.claim_next_evaluation_job(db_path=db_path)

    assert first["session_id"] == "manual"
    assert first["status"] == "running"
    assert second["session_id"] == "new-auto"
    assert third["session_id"] == "old-auto"


def test_claim_next_evaluation_job_skips_candidate_claimed_by_competing_worker(
    fresh_db, monkeypatch
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(
        database_module,
        db_path,
        "first",
        client_source="codex",
        updated_at="2026-05-14T11:00:00+00:00",
    )
    _insert_session_record(
        database_module,
        db_path,
        "second",
        client_source="codex",
        updated_at="2026-05-14T10:00:00+00:00",
    )
    first = database_module.create_session_evaluation_job(
        session_id="first",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )
    second = database_module.create_session_evaluation_job(
        session_id="second",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )

    original_next_queued_job_id = database_module.evaluation_jobs._next_queued_job_id
    returned_stale_candidate = False

    def stale_candidate_once(session):
        nonlocal returned_stale_candidate
        if returned_stale_candidate:
            return original_next_queued_job_id(session)
        returned_stale_candidate = True
        with database_module.Session(database_module.get_engine(db_path)) as session:
            job = session.get(database_module.EvaluationJob, first["job_id"])
            job.status = "running"
            job.started_at = "2026-05-14T11:01:00+00:00"
            session.commit()
        return first["job_id"]

    monkeypatch.setattr(
        database_module.evaluation_jobs,
        "_next_queued_job_id",
        stale_candidate_once,
    )

    claimed = database_module.claim_next_evaluation_job(
        now="2026-05-14T11:02:00+00:00",
        db_path=db_path,
    )

    assert claimed["job_id"] == second["job_id"]
    assert (
        database_module.get_evaluation_job(
            first["job_id"], db_path=db_path, user_id="test-user"
        )["started_at"]
        == "2026-05-14T11:01:00+00:00"
    )


def test_get_evaluation_job_progress_reports_queue_position(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "running", client_source="codex")
    _insert_session_record(database_module, db_path, "queued-1", client_source="codex")
    _insert_session_record(database_module, db_path, "queued-2", client_source="codex")

    running = database_module.create_session_evaluation_job(
        session_id="running",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )
    queued_1 = database_module.create_session_evaluation_job(
        session_id="queued-1",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )
    queued_2 = database_module.create_session_evaluation_job(
        session_id="queued-2",
        client_source="codex",
        trigger="auto",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)

    assert (
        database_module.get_evaluation_job_progress(
            running["job_id"], db_path=db_path, user_id="test-user"
        )["queue_position"]
        == 1
    )
    queued_progress = database_module.get_evaluation_job_progress(
        queued_1["job_id"], db_path=db_path, user_id="test-user"
    )
    assert queued_progress["ahead_count"] == 1
    assert queued_progress["queue_position"] == 2
    assert (
        database_module.get_evaluation_job_progress(
            queued_2["job_id"], db_path=db_path, user_id="test-user"
        )["queue_position"]
        == 3
    )


def test_evaluation_job_active_helpers_count_list_and_fail_stale_running_jobs(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "stale", client_source="codex")
    _insert_session_record(database_module, db_path, "fresh", client_source="codex")

    stale = database_module.create_session_evaluation_job(
        session_id="stale",
        client_source="codex",
        trigger="manual",
        created_at="2026-05-14T09:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    fresh = database_module.create_session_evaluation_job(
        session_id="fresh",
        client_source="codex",
        trigger="auto",
        created_at="2026-05-14T09:03:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    first = database_module.claim_next_evaluation_job(
        now="2026-05-14T09:00:00+00:00",
        db_path=db_path,
    )
    second = database_module.claim_next_evaluation_job(
        now="2026-05-14T09:03:00+00:00",
        db_path=db_path,
    )

    assert [first["job_id"], second["job_id"]] == [stale["job_id"], fresh["job_id"]]
    assert database_module.count_running_evaluation_jobs(db_path=db_path) == 2
    active = database_module.list_active_evaluation_jobs(
        session_ids=["fresh"],
        db_path=db_path,
        user_id="test-user",
    )
    assert [job["job_id"] for job in active] == [fresh["job_id"]]

    failed_count = database_module.fail_stale_running_evaluation_jobs(
        now="2026-05-14T09:06:00+00:00",
        db_path=db_path,
    )

    assert failed_count == 1
    assert database_module.count_running_evaluation_jobs(db_path=db_path) == 1
    assert (
        database_module.get_evaluation_job(
            stale["job_id"],
            db_path=db_path,
            user_id="test-user",
        )["status"]
        == "failed"
    )
    assert (
        database_module.get_evaluation_job(
            fresh["job_id"],
            db_path=db_path,
            user_id="test-user",
        )["status"]
        == "running"
    )


def test_fail_stale_running_evaluation_jobs_does_not_overwrite_terminal_update(
    fresh_db, monkeypatch
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "stale", client_source="codex")

    job = database_module.create_session_evaluation_job(
        session_id="stale",
        client_source="codex",
        trigger="manual",
        created_at="2026-05-14T09:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(
        now="2026-05-14T09:00:00+00:00",
        db_path=db_path,
    )
    original_fail_stale_job = getattr(
        database_module.evaluation_jobs,
        "_fail_stale_evaluation_job",
        None,
    )

    def terminal_update_before_stale_fail(session, stale_job, finished_at):
        session.execute(
            database_module.evaluation_jobs.update(database_module.EvaluationJob)
            .where(database_module.EvaluationJob.job_id == stale_job.job_id)
            .values(
                status="succeeded",
                finished_at="2026-05-14T09:05:30+00:00",
                error=None,
            )
            .execution_options(synchronize_session=False)
        )
        return original_fail_stale_job(session, stale_job, finished_at)

    monkeypatch.setattr(
        database_module.evaluation_jobs,
        "_fail_stale_evaluation_job",
        terminal_update_before_stale_fail,
        raising=False,
    )

    failed_count = database_module.fail_stale_running_evaluation_jobs(
        now="2026-05-14T09:06:00+00:00",
        db_path=db_path,
    )

    current = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert failed_count == 0
    assert current["status"] == "succeeded"
    assert current["finished_at"] == "2026-05-14T09:05:30+00:00"


def test_find_active_session_evaluation_job_does_not_overwrite_terminal_update(
    fresh_db, monkeypatch
):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "stale", client_source="codex")

    job = database_module.create_session_evaluation_job(
        session_id="stale",
        client_source="codex",
        trigger="manual",
        created_at="2026-05-14T09:00:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )
    original_fail_stale_job = getattr(
        database_module.evaluation_jobs,
        "_fail_stale_evaluation_job",
        None,
    )

    def terminal_update_before_stale_fail(session, stale_job, finished_at):
        session.execute(
            database_module.evaluation_jobs.update(database_module.EvaluationJob)
            .where(database_module.EvaluationJob.job_id == stale_job.job_id)
            .values(
                status="succeeded",
                finished_at="2026-05-14T09:05:30+00:00",
                error=None,
            )
            .execution_options(synchronize_session=False)
        )
        return original_fail_stale_job(session, stale_job, finished_at)

    monkeypatch.setattr(
        database_module.evaluation_jobs,
        "_fail_stale_evaluation_job",
        terminal_update_before_stale_fail,
        raising=False,
    )

    active = database_module.find_active_session_evaluation_job(
        session_id="stale",
        now="2026-05-14T09:06:00+00:00",
        db_path=db_path,
        user_id="test-user",
    )

    current = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert active is None
    assert current["status"] == "succeeded"
    assert current["finished_at"] == "2026-05-14T09:05:30+00:00"


def test_mark_evaluation_job_succeeded_does_not_overwrite_failed_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "failed", client_source="codex")

    job = database_module.create_session_evaluation_job(
        session_id="failed",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)
    database_module.mark_evaluation_job_failed(
        job["job_id"],
        "first failure",
        db_path=db_path,
    )

    database_module.mark_evaluation_job_succeeded(job["job_id"], db_path=db_path)

    current = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert current["status"] == "failed"
    assert current["error"] == "first failure"


def test_mark_evaluation_job_running_does_not_revive_failed_job(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)
    _insert_session_record(database_module, db_path, "failed", client_source="codex")

    job = database_module.create_session_evaluation_job(
        session_id="failed",
        client_source="codex",
        trigger="manual",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)
    database_module.mark_evaluation_job_failed(
        job["job_id"],
        "first failure",
        db_path=db_path,
    )

    changed = database_module.mark_evaluation_job_running(
        job["job_id"], db_path=db_path
    )

    current = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert changed is False
    assert current["status"] == "failed"
    assert current["error"] == "first failure"


def test_upsert_daily_aggregate_with_epoch_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
    from src.database.models import UsageDaily

    with Session(get_engine(db_path)) as session:
        daily = session.query(UsageDaily).first()
        assert daily is not None
        assert daily.date == "2026-04-17"


def test_fetch_recent_usage_filters_by_since_until_with_epoch_ts(fresh_db):
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    # Insert two rows at different times
    for ts_val in [1776384000000000, 1776470400000000]:  # Apr 17, Apr 18 (micros)
        database_module.log_usage(
            database_module.Usage(
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
    database_module.log_usage(usage_obj, db_path=db_path)
    ts_ms = ts // 1000
    for i, tool_name in enumerate(tools):
        record_tool_call(
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


def test_recalculate_usage_cost_updates_row_and_daily_rollup(fresh_db):
    """Recalculating a row must update its cost columns and delta-adjust (not
    replace) the matching usage_daily rollup, since other rows share it."""
    from decimal import Decimal

    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
    # A second row sharing the same usage_daily bucket (same date/provider/
    # model/client_source), to prove the rollup is delta-adjusted rather than
    # clobbered by the single recalculated row.
    database_module.log_usage(
        database_module.Usage(
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
    assert result.pricing["source"] == "yaml"
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

    daily = database_module.summarize_usage_daily(db_path=db_path, user_id="test-user")
    bucket = next(d for d in daily if d["model"] == "recalc-model")
    # Old combined total was 2.0 + 0.2 = 2.2; the recalculated row's total
    # went 2.0 -> 6.0, so the bucket should read 6.0 + 0.2 = 6.2, not 6.0.
    assert bucket["total_cost_usd"] == pytest.approx(6.2)


def test_recalculate_usage_cost_adjusts_session_rollup(fresh_db):
    """Recalculating a session-linked row must delta-adjust sessions.total_cost_usd
    and its per-model cost map (and re-derive primary_model), not just usage_daily —
    upsert_session_from_usage maintains this rollup at record time, so recalculation
    must keep it in sync too instead of letting it silently go stale."""
    import json
    from decimal import Decimal

    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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

    database_module.log_usage(
        database_module.Usage(
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

OLD_SHAPE_USAGE_DDL = """
CREATE TABLE usage (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    client_source TEXT,
    session_id TEXT,
    endpoint TEXT NOT NULL,
    prompt_tokens INTEGER,
    prompt_length INTEGER NOT NULL DEFAULT 0,
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
    status INTEGER,
    client_ip TEXT
);
"""

OLD_SHAPE_USAGE_DAILY_DDL = """
CREATE TABLE usage_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    client_source TEXT NOT NULL DEFAULT '',
    request_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    tool_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_length INTEGER NOT NULL DEFAULT 0,
    input_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
    output_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
    total_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
    successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    status_429 INTEGER NOT NULL DEFAULT 0,
    status_4xx INTEGER NOT NULL DEFAULT 0,
    status_5xx INTEGER NOT NULL DEFAULT 0,
    status_unknown INTEGER NOT NULL DEFAULT 0,
    latency_sum_ms INTEGER NOT NULL DEFAULT 0,
    UNIQUE(date, provider, model, client_source)
);
"""

OLD_SHAPE_SESSIONS_DDL = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    client_source TEXT,
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
    latency_sum_ms INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms NUMERIC(18, 4),
    avg_ttft_ms NUMERIC(18, 4),
    primary_provider TEXT,
    primary_model TEXT,
    providers_json TEXT,
    models_json TEXT,
    tool_calls_json TEXT DEFAULT '{}',
    last_usage_id TEXT,
    updated_at TEXT NOT NULL
);
"""

OLD_SHAPE_EVALUATION_JOBS_DDL = """
CREATE TABLE evaluation_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    session_id TEXT NOT NULL,
    client_source TEXT,
    trigger TEXT NOT NULL DEFAULT 'manual',
    evaluator_type TEXT NOT NULL DEFAULT 'codex',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);
"""


CREATE_ALL_ERA_USAGE_DAILY_DDL = OLD_SHAPE_USAGE_DAILY_DDL.replace(
    ",\n    UNIQUE(date, provider, model, client_source)", ""
)


def _create_all_era_db(db_file) -> None:
    """Build today's shape as created by Base.metadata.create_all (no
    table-level UNIQUE on usage_daily) with duplicate daily rows."""
    import sqlite3

    connection = sqlite3.connect(db_file)
    connection.executescript(
        OLD_SHAPE_USAGE_DDL
        + CREATE_ALL_ERA_USAGE_DAILY_DDL
        + OLD_SHAPE_SESSIONS_DDL
        + OLD_SHAPE_EVALUATION_JOBS_DDL
    )
    connection.execute(
        """
        INSERT INTO usage (id, ts, provider, model, client_source, session_id,
            endpoint, prompt_tokens, completion_tokens, total_tokens,
            latency_ms, status, input_cost_usd, output_cost_usd, total_cost_usd)
        VALUES ('uuid-1', ?, 'anthropic', 'm1', 'claude-code', 'sess-1',
            '/v1/messages', 10, 5, 15, 100, 200, 0.001, 0.002, 0.003)
        """,
        (TS_2026_04_17_10,),
    )
    connection.execute(
        """
        INSERT INTO usage_daily (date, provider, model, client_source,
            request_count, prompt_tokens, completion_tokens, total_tokens,
            input_cost_usd, total_cost_usd, latency_sum_ms)
        VALUES ('2026-04-17', 'anthropic', 'm1', 'claude-code',
            1, 10, 5, 15, 0.001, 0.003, 100)
        """
    )
    connection.execute(
        """
        INSERT INTO usage_daily (date, provider, model, client_source,
            request_count, prompt_tokens, completion_tokens, total_tokens,
            input_cost_usd, total_cost_usd, latency_sum_ms)
        VALUES ('2026-04-17', 'anthropic', 'm1', 'claude-code',
            2, 20, 10, 30, 0.002, 0.006, 200)
        """
    )
    connection.execute(
        """
        INSERT INTO usage_daily (date, provider, model, client_source,
            request_count, prompt_tokens)
        VALUES ('2026-04-18', 'anthropic', 'm1', 'claude-code', 7, 70)
        """
    )
    connection.execute(
        """
        INSERT INTO sessions (session_id, started, ended, request_count, updated_at)
        VALUES ('sess-1', ?, ?, 1, '2026-04-17T00:00:00+00:00')
        """,
        (TS_2026_04_17_10, TS_2026_04_17_10),
    )
    connection.execute(
        """
        INSERT INTO evaluation_jobs (job_id, kind, session_id, status, created_at)
        VALUES ('job-1', 'session_evaluation', 'sess-1', 'queued',
            '2026-04-17T00:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()


def _old_shape_db(db_file) -> None:
    import sqlite3

    connection = sqlite3.connect(db_file)
    connection.executescript(
        OLD_SHAPE_USAGE_DDL
        + OLD_SHAPE_USAGE_DAILY_DDL
        + OLD_SHAPE_SESSIONS_DDL
        + OLD_SHAPE_EVALUATION_JOBS_DDL
    )
    connection.execute(
        """
        INSERT INTO usage (id, ts, provider, model, client_source, session_id,
            endpoint, prompt_tokens, completion_tokens, total_tokens,
            latency_ms, status, input_cost_usd, output_cost_usd, total_cost_usd)
        VALUES ('uuid-1', ?, 'anthropic', 'm1', 'claude-code', 'sess-1',
            '/v1/messages', 10, 5, 15, 100, 200, 0.001, 0.002, 0.003)
        """,
        (TS_2026_04_17_10,),
    )
    connection.execute(
        """
        INSERT INTO usage_daily (date, provider, model, client_source,
            request_count, prompt_tokens)
        VALUES ('2026-04-17', 'anthropic', 'm1', 'claude-code', 42, 4242)
        """
    )
    connection.execute(
        """
        INSERT INTO sessions (session_id, started, ended, request_count, updated_at)
        VALUES ('sess-1', ?, ?, 42, '2026-04-17T00:00:00+00:00')
        """,
        (TS_2026_04_17_10, TS_2026_04_17_10),
    )
    connection.execute(
        """
        INSERT INTO evaluation_jobs (job_id, kind, session_id, status, created_at)
        VALUES ('job-1', 'session_evaluation', 'sess-1', 'queued',
            '2026-04-17T00:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()


def test_init_db_creates_tenancy_shapes(fresh_db):
    """AC 2: init_db alone (no migrate_database) creates the new shapes."""
    from sqlalchemy import inspect

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path

    engine = database_module.get_engine(db_path)
    inspector = inspect(engine)
    for table in ("usage", "usage_daily", "sessions", "tool_calls", "evaluation_jobs"):
        assert "user_id" in {c["name"] for c in inspector.get_columns(table)}

    daily_indexes = {i["name"]: i for i in inspector.get_indexes("usage_daily")}
    assert daily_indexes["uq_usage_daily"]["unique"]
    assert "uq_usage_daily_local" not in daily_indexes

    for table, index_name in [
        ("usage", "ix_usage_user_id"),
        ("sessions", "ix_sessions_user_id"),
        ("tool_calls", "ix_tool_calls_user_id"),
    ]:
        assert index_name in {i["name"] for i in inspector.get_indexes(table)}


def test_upsert_daily_aggregate_separates_user_rows(fresh_db):
    """Same (date, provider, model, client_source) for users u0 and u1
    produces two rows; increments land in the right row (both ON CONFLICT
    branches)."""
    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    def make_usage(user_id):
        return database_module.Usage(
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

    database_module.upsert_daily_aggregate(make_usage("u0"), db_path=db_path)
    database_module.upsert_daily_aggregate(make_usage("u0"), db_path=db_path)
    database_module.upsert_daily_aggregate(make_usage("u1"), db_path=db_path)
    database_module.upsert_daily_aggregate(make_usage("u1"), db_path=db_path)

    with database_module.get_engine(db_path).connect() as conn:
        rows = list(
            conn.execute(
                database_module.select(database_module.UsageDaily).order_by(
                    database_module.UsageDaily.id
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
    session_id fails cleanly with IntegrityError instead of polluting the
    first user's row.
    """
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session as OrmSession

    from src.database.sessions import get_session_record

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    def make_usage(session_id, user_id, ts=TS_2026_04_17_10):
        return database_module.Usage(
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

    # Cross-tenant session_id collision: clean IntegrityError, no pollution.
    with pytest.raises(IntegrityError):
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


def test_rebuild_sessions_from_usage_stamps_user_id(fresh_db):
    """AC 8: rebuild stamps user_id from the usage rows and still recomputes
    tool_calls_json from the tool_calls table."""
    from src.recorder import record_tool_call

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    usage = database_module.Usage(
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
    database_module.log_usage(usage, db_path=db_path)

    record_tool_call(
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
    """The daily delta UPDATE lands only in the row matching the usage row's
    user_id when both a NULL-slice and a real-user-slice row exist."""
    from src.pricing.models import ModelCost

    database_module = fresh_db.database_module
    db_path = fresh_db.db_path
    database_module.init_db(db_path)

    database_module.log_usage(
        database_module.Usage(
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
    database_module.log_usage(
        database_module.Usage(
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
                    "SELECT user_id, total_cost_usd FROM usage_daily ORDER BY user_id"
                )
            )
        )
    assert len(rows) == 2
    by_user = {row.user_id: row for row in rows}
    # test-user slice: 0.2 -> 6.0; the u1 slice is untouched.
    assert float(by_user["test-user"].total_cost_usd) == pytest.approx(6.0)
    assert float(by_user["u1"].total_cost_usd) == pytest.approx(2.0)


def test_migrate_database_swaps_legacy_usage_daily_indexes(fresh_db):
    """A database from before user_id was required carried two partial unique
    indexes on usage_daily; migrating replaces them with one plain index and is
    then a no-op."""
    schema_migrations = fresh_db.schema_migrations_module
    database_module = fresh_db.database_module
    engine = database_module.get_engine(fresh_db.db_path)

    with engine.begin() as conn:
        conn.execute(database_module.text("DROP INDEX uq_usage_daily"))
        conn.execute(
            database_module.text(
                "CREATE UNIQUE INDEX uq_usage_daily_local ON usage_daily "
                "(date, provider, model, client_source) WHERE user_id IS NULL"
            )
        )
        conn.execute(
            database_module.text(
                "CREATE UNIQUE INDEX uq_usage_daily_user ON usage_daily "
                "(date, provider, model, client_source, user_id) "
                "WHERE user_id IS NOT NULL"
            )
        )

    changes = schema_migrations.migrate_database(fresh_db.db_path)

    assert "usage_daily.drop_uq_usage_daily_local" in changes
    assert "usage_daily.drop_uq_usage_daily_user" in changes
    assert "usage_daily.uq_usage_daily" in changes
    from sqlalchemy import inspect

    names = {i["name"] for i in inspect(engine).get_indexes("usage_daily")}
    assert "uq_usage_daily" in names
    assert not names & {"uq_usage_daily_local", "uq_usage_daily_user"}
    assert schema_migrations.migrate_database(fresh_db.db_path) == []


def test_migrate_database_rejects_sqlite_with_nullable_user_id(fresh_db):
    import pytest

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
        conn.execute(database_module.text("DROP INDEX uq_usage_daily"))

    try:
        with pytest.raises(RuntimeError, match="tool_calls"):
            schema_migrations.migrate_database(fresh_db.db_path)

        # Rejected before any DDL: the dropped index was not recreated.
        from sqlalchemy import inspect

        names = {i["name"] for i in inspect(engine).get_indexes("usage_daily")}
        assert "uq_usage_daily" not in names
    finally:  # the test schema is shared across the session
        from src.database.models import ToolCall

        with engine.begin() as conn:
            conn.execute(database_module.text("DROP TABLE tool_calls"))
        ToolCall.__table__.create(engine)
        schema_migrations.migrate_database(fresh_db.db_path)


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
