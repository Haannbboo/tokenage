import asyncio
import json
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient


def test_usage_daily_endpoint_exists(api_module):
    # This just verifies the endpoint function is defined
    assert hasattr(api_module, "usage_daily")
    assert callable(api_module.usage_daily)


def test_reprice_estimated_usage_endpoint_passes_filters(api_module, monkeypatch):
    async def fake_live_cost_maps():
        return {}, {}, {}

    captured = {}

    def fake_reprice(**kwargs):
        captured.update(kwargs)
        return {"candidates": 2, "repriced": 1, "skipped": 1}

    monkeypatch.setattr(api_module, "_resolve_live_cost_maps", fake_live_cost_maps)
    monkeypatch.setattr(api_module, "reprice_estimated_rows", fake_reprice)

    result = asyncio.run(
        api_module.reprice_estimated_usage(
            provider="prov", model="mod", since=None, until=None, limit=10
        )
    )

    assert result == {"candidates": 2, "repriced": 1, "skipped": 1}
    assert captured["provider"] == "prov"
    assert captured["model"] == "mod"
    assert captured["limit"] == 10


def test_pricing_entry_includes_tiers_and_time_rates(api_module):
    from src.pricing.models import ModelCost, ModelTier, ResolvedCost, TimeRate

    cost = ModelCost(
        input=1.0,
        output=2.0,
        cache_read=0.1,
        tiers=(
            ModelTier(
                min_tokens=0, max_tokens=1000, input=1.0, output=2.0, cache_read=0.1
            ),
        ),
        time_rates=(
            TimeRate(
                days=frozenset({0, 6}),
                start_minute=0,
                end_minute=60,
                cost=ModelCost(input=0.5, output=1.0, cache_read=0.05),
            ),
        ),
    )

    entry = api_module._pricing_entry(ResolvedCost(cost=cost, source="openrouter"))

    assert entry["tiers"][0]["max_tokens"] == 1000
    assert entry["time_rates"][0]["days"] == [0, 6]
    assert entry["time_rates"][0]["start_minute"] == 0
    assert entry["time_rates"][0]["input"] == 0.5


def test_usage_run_summary_endpoint_passes_filters(api_module, monkeypatch):
    captured = {}

    def fake_summary(**kwargs):
        captured.update(kwargs)
        return {
            "window": {
                "after_ts": 1718000000000000,
                "until_ts": 1718100000000000,
                "row_count": 1,
            },
            "summary": {"requests": 1},
            "sessions": [],
            "client_sources": [],
            "models": [],
        }

    monkeypatch.setattr(api_module, "summarize_usage_window", fake_summary)

    response = TestClient(api_module.app).get(
        "/usage/run-summary",
        params={
            "after_ts": 1718000000000000,
            "until_ts": 1718100000000000,
            "since": "2026-04-17T00:00:00+00:00",
            "until": "2026-04-18T00:00:00+00:00",
            "client_source": "codex",
            "session_id": "conv-1",
            "provider": "openai",
            "model": "gpt-test",
            "include_rows": True,
        },
    )

    assert captured == {
        "user_id": ANY,
        "after_ts": 1718000000000000,
        "until_ts": 1718100000000000,
        "since": "2026-04-17T00:00:00+00:00",
        "until": "2026-04-18T00:00:00+00:00",
        "client_source": "codex",
        "device_id": None,
        "session_id": "conv-1",
        "provider": "openai",
        "model": "gpt-test",
        "include_rows": True,
    }
    assert response.status_code == 200
    assert response.json()["summary"]["requests"] == 1


def test_usage_high_watermark_route(api_module, monkeypatch):
    monkeypatch.setattr(
        api_module, "get_usage_high_watermark_ts", lambda **kwargs: 1718000000000000
    )

    response = TestClient(api_module.app).get("/usage/high-watermark")

    assert response.status_code == 200
    assert response.json() == {"ts": 1718000000000000}


def test_usage_run_summary_route_parses_query_filters(api_module, monkeypatch):
    captured = {}

    def fake_summary(**kwargs):
        captured.update(kwargs)
        return {
            "window": {
                "after_ts": 1718000000000000,
                "until_ts": 1718100000000000,
                "row_count": 1,
            },
            "summary": {"requests": 1},
            "sessions": [],
            "client_sources": [],
            "models": [],
        }

    monkeypatch.setattr(api_module, "summarize_usage_window", fake_summary)

    response = TestClient(api_module.app).get(
        "/usage/run-summary",
        params={
            "after_ts": "1718000000000000",
            "until_ts": "1718100000000000",
            "since": "2026-04-17T00:00:00+00:00",
            "until": "2026-04-18T00:00:00+00:00",
            "client_source": "codex",
            "session_id": "conv-1",
            "provider": "openai",
            "model": "gpt-test",
            "include_rows": "true",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "user_id": ANY,
        "after_ts": 1718000000000000,
        "until_ts": 1718100000000000,
        "since": "2026-04-17T00:00:00+00:00",
        "until": "2026-04-18T00:00:00+00:00",
        "client_source": "codex",
        "device_id": None,
        "session_id": "conv-1",
        "provider": "openai",
        "model": "gpt-test",
        "include_rows": True,
    }
    assert response.json()["summary"]["requests"] == 1


def test_usage_ingest_route_is_not_available(api_module):
    response = TestClient(api_module.app).post(
        "/usage",
        json={
            "ts": "2026-05-03T17:00:00+00:00",
            "provider": "codesonline",
            "model": "gpt-5.5",
            "client_source": "codex",
            "session_id": "codex-session-1",
            "endpoint": "otlp",
            "prompt_tokens": 21742,
            "completion_tokens": 6,
            "cached_tokens": 6528,
            "reasoning_tokens": 0,
            "total_tokens": 21748,
            "latency_ms": 12338,
            "ttft_ms": 8263,
            "base_url": "https://free.codesonline.dev",
            "base_url_source": "codex_config",
        },
    )

    assert response.status_code == 405


def test_usage_endpoint_passes_client_source(api_module, monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get(
        "/usage", params={"client_source": "claude-code", "limit": "10"}
    )
    assert response.status_code == 200
    assert captured["client_source"] == "claude-code"


def test_usage_endpoint_rejects_negative_offset(api_module, monkeypatch):
    fetch_called = False

    def fake_fetch(**kwargs):
        nonlocal fetch_called
        fetch_called = True
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get("/usage", params={"offset": "-1"})

    assert response.status_code == 422
    assert fetch_called is False


def test_usage_endpoint_rejects_unbounded_limit(api_module, monkeypatch):
    fetch_called = False

    def fake_fetch(**kwargs):
        nonlocal fetch_called
        fetch_called = True
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get("/usage", params={"limit": "1001"})

    assert response.status_code == 422
    assert fetch_called is False


def test_usage_endpoint_allows_zero_limit(api_module, monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get("/usage", params={"limit": "0"})

    assert response.status_code == 200
    assert captured["limit"] == 0


def test_usage_endpoint_includes_cost_split_components(api_module, monkeypatch):
    import src.database as database_module

    database_module.init_db()
    monkeypatch.setattr(
        api_module,
        "fetch_recent_usage",
        lambda **kwargs: [
            {
                "id": "row-1",
                "ts": 1779148800000000,
                "provider": "test-provider",
                "model": "test-model",
                "client_source": "codex",
                "session_id": None,
                "endpoint": "otlp",
                "prompt_tokens": 1000,
                "prompt_length": 0,
                "completion_tokens": 500,
                "reasoning_tokens": None,
                "cached_tokens": 200,
                "cache_creation_tokens": 0,
                "total_tokens": 1500,
                "latency_ms": 100,
                "ttft_ms": None,
                "tool_tokens": None,
                "tool_names": None,
                "input_cost_usd": 0.0,
                "output_cost_usd": 0.0,
                "total_cost_usd": 0.0,
                "status": 200,
                "base_url_id": None,
                "base_url": None,
            }
        ],
    )

    response = TestClient(api_module.app).get("/usage")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    # test-model: input 2.0, cacheRead 0.5.
    # normal: 800*2/1e6 = 0.0016; cache_read: 200*0.5/1e6 = 0.0001
    assert rows[0]["normal_input_cost_usd"] == pytest.approx(0.0016)
    assert rows[0]["cache_read_cost_usd"] == pytest.approx(0.0001)
    assert rows[0]["cache_write_cost_usd"] == 0


def test_usage_endpoint_includes_cors_for_localhost_origin(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "fetch_recent_usage", lambda **kwargs: [])

    response = TestClient(api_module.app).get(
        "/usage",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_usage_endpoint_preflight_allows_localhost_origin(api_module):
    response = TestClient(api_module.app).options(
        "/usage",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-methods"] == "GET"


def test_usage_endpoint_does_not_include_cors_for_untrusted_origin(
    api_module, monkeypatch
):
    monkeypatch.setattr(api_module, "fetch_recent_usage", lambda **kwargs: [])

    response = TestClient(api_module.app).get(
        "/usage",
        headers={"Origin": "https://example.com"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_usage_count_endpoint_passes_client_source(api_module, monkeypatch):
    captured = {}

    def fake_count(**kwargs):
        captured.update(kwargs)
        return 5

    monkeypatch.setattr(api_module, "count_usage", fake_count)

    response = TestClient(api_module.app).get(
        "/usage/count", params={"client_source": "codex"}
    )
    assert response.status_code == 200
    assert captured["client_source"] == "codex"


def test_usage_summary_endpoint_passes_client_source(api_module, monkeypatch):
    captured = {}

    def fake_summary(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "summarize_usage_daily", fake_summary)

    response = TestClient(api_module.app).get(
        "/usage/summary", params={"client_source": "opencode"}
    )
    assert response.status_code == 200
    assert captured["client_source"] == "opencode"


def test_usage_daily_endpoint_passes_params(api_module, monkeypatch):
    captured = {}

    def fake_aggregate(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "aggregate_usage_by_period", fake_aggregate)

    response = TestClient(api_module.app).get(
        "/usage/daily", params={"client_source": "claude-code", "tz_offset": "+08:00"}
    )
    assert response.status_code == 200
    assert captured["client_source"] == "claude-code"
    assert captured["tz_offset"] == "+08:00"
    assert captured["granularity"] == "day"


def test_usage_by_source_endpoint(api_module, monkeypatch):
    captured = {}

    def fake_by_source(**kwargs):
        captured.update(kwargs)
        return [
            {
                "client_source": "claude-code",
                "requests": 10,
                "prompt_tokens": 5000,
                "completion_tokens": 3000,
                "reasoning_tokens": 0,
                "cached_tokens": 1000,
                "total_tokens": 8000,
                "avg_latency_ms": 250.0,
                "input_cost_usd": 0.01,
                "output_cost_usd": 0.02,
                "total_cost_usd": 0.03,
                "successful_requests": 9,
                "failed_requests": 1,
            },
            {
                "client_source": "codex",
                "requests": 5,
                "prompt_tokens": 2000,
                "completion_tokens": 1000,
                "reasoning_tokens": 0,
                "cached_tokens": 500,
                "total_tokens": 3000,
                "avg_latency_ms": 180.0,
                "input_cost_usd": 0.005,
                "output_cost_usd": 0.01,
                "total_cost_usd": 0.015,
                "successful_requests": 5,
                "failed_requests": 0,
            },
        ]

    monkeypatch.setattr(api_module, "summarize_usage_by_source", fake_by_source)

    response = TestClient(api_module.app).get(
        "/usage/by-source",
        params={
            "since": "2026-01-01",
            "until": "2026-12-31",
            "client_source": "claude-code",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["client_source"] == "claude-code"
    assert data[0]["requests"] == 10
    assert captured["since"] == "2026-01-01"
    assert captured["client_source"] == "claude-code"


def test_usage_by_source_endpoint_passes_all_filters(api_module, monkeypatch):
    captured = {}

    def fake_by_source(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "summarize_usage_by_source", fake_by_source)

    response = TestClient(api_module.app).get(
        "/usage/by-source",
        params={"provider": "openai", "model": "gpt-4o"},
    )
    assert response.status_code == 200
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-4o"


def test_usage_by_tool_endpoint(api_module, monkeypatch):
    captured = {}

    def fake_by_tool(**kwargs):
        captured.update(kwargs)
        return [{"tool_name": "bash", "count": 2}, {"tool_name": "read", "count": 1}]

    monkeypatch.setattr(api_module, "summarize_tool_calls", fake_by_tool)

    response = TestClient(api_module.app).get(
        "/usage/by-tool",
        params={
            "since": "2026-01-01",
            "client_source": "claude-code",
            "status_429": "true",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data == [
        {"tool_name": "bash", "count": 2},
        {"tool_name": "read", "count": 1},
    ]
    assert captured["since"] == "2026-01-01"
    assert captured["client_source"] == "claude-code"
    assert captured["status_429"] is True
    assert captured["status_4xx"] is False
    assert captured["status_5xx"] is False


def test_usage_by_provider_endpoint_includes_avg_effective_price_per_million(
    api_module, monkeypatch
):
    captured = {}

    def fake_by_provider(**kwargs):
        captured.update(kwargs)
        return [
            {
                "provider": "openai",
                "requests": 3,
                "prompt_tokens": 600,
                "completion_tokens": 300,
                "reasoning_tokens": 0,
                "cached_tokens": 100,
                "total_tokens": 900,
                "avg_latency_ms": 210.0,
                "input_cost_usd": 0.003,
                "output_cost_usd": 0.006,
                "total_cost_usd": 0.009,
                "avg_effective_price_usd": 0.00001,
                "avg_effective_price_per_million_usd": 10.0,
                "successful_requests": 3,
                "failed_requests": 0,
            }
        ]

    monkeypatch.setattr(api_module, "summarize_usage_by_provider", fake_by_provider)

    response = TestClient(api_module.app).get(
        "/usage/by-provider",
        params={"provider": "openai", "model": "gpt-4o", "client_source": "codex"},
    )

    assert response.status_code == 200
    assert response.json()[0]["avg_effective_price_per_million_usd"] == 10.0
    assert captured == {
        "user_id": ANY,
        "since": None,
        "until": None,
        "provider": "openai",
        "model": "gpt-4o",
        "client_source": "codex",
        "device_id": None,
    }


@pytest.mark.parametrize("provider", ("local", "google"))
def test_connectivity_endpoint_removed(api_module, monkeypatch, provider):
    monkeypatch.setitem(api_module.CONFIG, "auth", {"provider": provider})
    assert "/test-connectivity" not in api_module.app.openapi()["paths"]
    response = TestClient(api_module.app).post(
        "/test-connectivity",
        json={
            "base_url": "http://127.0.0.1",
            "api_key": "test-key",
            "format": "openai",
        },
    )
    assert response.status_code in (404, 405)


def test_daily_by_dimension_returns_per_model_data(api_module, monkeypatch):
    """GET /usage/daily-by-dimension returns daily data grouped by model."""
    captured = {}

    def fake_daily_by_dimension(**kwargs):
        captured.update(kwargs)
        return [
            {
                "dimension": "claude-sonnet-4-6",
                "period": "2026-05-07",
                "total_tokens": 1000,
                "total_cost_usd": 0.01,
                "requests": 1,
                "completion_tokens": 400,
                "latency_sum_ms": 500,
                "successful_requests": 1,
                "failed_requests": 0,
            }
        ]

    monkeypatch.setattr(
        api_module, "aggregate_daily_by_dimension", fake_daily_by_dimension
    )

    response = TestClient(api_module.app).get(
        "/usage/daily-by-dimension",
        params={
            "dimension": "model",
            "since": "2026-05-07T00:00:00Z",
            "until": "2026-05-08T00:00:00Z",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["dimension"] == "claude-sonnet-4-6"
    assert "period" in data[0]
    assert "total_tokens" in data[0]


def test_daily_by_dimension_endpoint_passes_all_filters(api_module, monkeypatch):
    """GET /usage/daily-by-dimension passes all filter params to the database function."""
    captured = {}

    def fake_daily_by_dimension(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        api_module, "aggregate_daily_by_dimension", fake_daily_by_dimension
    )

    response = TestClient(api_module.app).get(
        "/usage/daily-by-dimension",
        params={
            "dimension": "provider",
            "since": "2026-05-01T00:00:00Z",
            "until": "2026-05-08T00:00:00Z",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "client_source": "claude-code",
        },
    )
    assert response.status_code == 200
    assert captured == {
        "user_id": ANY,
        "dimension": "provider",
        "since": "2026-05-01T00:00:00Z",
        "until": "2026-05-08T00:00:00Z",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "client_source": "claude-code",
        "device_id": None,
    }


def test_sessions_endpoint_exists(api_module):
    assert hasattr(api_module, "get_sessions")
    assert callable(api_module.get_sessions)


def test_sessions_endpoint_passes_filters(api_module, monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return []

    def fake_count(**kwargs):
        return 0

    monkeypatch.setattr(api_module, "fetch_sessions", fake_fetch)
    monkeypatch.setattr(api_module, "count_sessions", fake_count)

    response = TestClient(api_module.app).get(
        "/sessions",
        params={
            "client_source": "claude-code",
            "since": "2026-05-01T00:00:00Z",
            "until": "2026-05-10T00:00:00Z",
            "sort_by": "total_cost_usd",
            "sort_order": "asc",
            "limit": "25",
            "offset": "10",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "total" in data
    assert captured["client_source"] == "claude-code"
    assert captured["since"] == "2026-05-01T00:00:00Z"
    assert captured["sort_by"] == "total_cost_usd"
    assert captured["sort_order"] == "asc"
    assert captured["limit"] == 25
    assert captured["offset"] == 10


def test_sessions_endpoint_selector_view_skips_count(api_module, monkeypatch):
    captured = {}
    count_called = False

    def fake_selector(**kwargs):
        captured.update(kwargs)
        return [
            {
                "session_id": "sess-1",
                "client_source": "codex",
                "request_count": 2,
                "started": "2026-05-09T10:00:00+00:00",
            }
        ]

    def fake_count(**kwargs):
        nonlocal count_called
        count_called = True
        return 999

    monkeypatch.setattr(api_module, "fetch_session_selector_rows", fake_selector)
    monkeypatch.setattr(api_module, "count_sessions", fake_count)

    response = TestClient(api_module.app).get(
        "/sessions",
        params={
            "view": "selector",
            "client_source": "codex",
            "since": "2026-05-01T00:00:00Z",
            "sort_by": "started",
            "sort_order": "desc",
            "limit": "50",
            "offset": "0",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "sessions": [
            {
                "session_id": "sess-1",
                "client_source": "codex",
                "request_count": 2,
                "started": "2026-05-09T10:00:00+00:00",
            }
        ],
        "total": None,
    }
    assert count_called is False
    assert captured["client_source"] == "codex"
    assert captured["since"] == "2026-05-01T00:00:00Z"
    assert captured["sort_by"] == "started"
    assert captured["sort_order"] == "desc"
    assert captured["limit"] == 50
    assert captured["offset"] == 0


def test_sessions_endpoint_rejects_unknown_view(api_module):
    response = TestClient(api_module.app).get("/sessions", params={"view": "compact"})

    assert response.status_code == 400
    assert "Invalid view" in response.json()["detail"]


def test_sessions_summary_endpoint_passes_filters(api_module, monkeypatch):
    captured = {}

    def fake_summary(**kwargs):
        captured.update(kwargs)
        return {
            "session_count": 3,
            "avg_duration_s": 120,
            "total_tokens": 5000,
            "total_cost_usd": 0.05,
            "avg_latency_ms": 250.0,
        }

    monkeypatch.setattr(api_module, "summarize_sessions", fake_summary)

    response = TestClient(api_module.app).get(
        "/sessions/summary",
        params={"client_source": "opencode"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session_count"] == 3
    assert data["total_tokens"] == 5000
    assert captured["client_source"] == "opencode"


def test_usage_endpoint_passes_session_id(api_module, monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get(
        "/usage", params={"session_id": "sess-123", "limit": "10"}
    )
    assert response.status_code == 200
    assert captured["session_id"] == "sess-123"


def test_usage_count_endpoint_passes_session_id(api_module, monkeypatch):
    captured = {}

    def fake_count(**kwargs):
        captured.update(kwargs)
        return 3

    monkeypatch.setattr(api_module, "count_usage", fake_count)

    response = TestClient(api_module.app).get(
        "/usage/count", params={"session_id": "sess-123"}
    )
    assert response.status_code == 200
    assert captured["session_id"] == "sess-123"


# ---------------------------------------------------------------------------
# Slice 1: Session Evaluation API
# ---------------------------------------------------------------------------


def test_put_session_evaluation_creates_evaluation(api_module, monkeypatch):
    captured = {}

    def fake_upsert(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_module, "upsert_session_evaluation", fake_upsert)

    response = TestClient(api_module.app).put(
        "/sessions/sess-1/evaluation",
        json={
            "outcome": "solved",
            "source": "manual",
            "evidence": ["User marked solved"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured["session_id"] == "sess-1"
    assert captured["outcome"] == "solved"
    assert captured["source"] == "manual"
    assert captured["evidence"] == ["User marked solved"]


def test_put_session_evaluation_invalid_outcome_returns_400(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "upsert_session_evaluation", lambda **kw: None)

    response = TestClient(api_module.app).put(
        "/sessions/sess-1/evaluation",
        json={"outcome": "invalid_outcome"},
    )

    assert response.status_code == 400


def test_put_session_evaluation_invalid_source_returns_400(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "upsert_session_evaluation", lambda **kw: None)

    response = TestClient(api_module.app).put(
        "/sessions/sess-1/evaluation",
        json={"outcome": "solved", "source": "bogus_source"},
    )

    assert response.status_code == 400


def test_put_session_evaluation_rejects_llm_source(api_module, monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("manual evaluation endpoint should not write LLM results")

    monkeypatch.setattr(api_module, "upsert_session_evaluation", fail_if_called)

    response = TestClient(api_module.app).put(
        "/sessions/sess-1/evaluation",
        json={
            "outcome": "solved",
            "source": "llm",
            "task_title": "Spoofed task title",
            "task_title_zh": "伪造任务标题",
            "evidence": ["caller supplied an LLM source"],
        },
    )

    assert response.status_code == 400
    assert "manual" in response.json()["detail"].lower()


def test_put_session_evaluation_returns_404_when_session_not_found(
    api_module, monkeypatch
):
    def raise_not_found(**kwargs):
        raise ValueError("Session not found: nonexistent")

    monkeypatch.setattr(api_module, "upsert_session_evaluation", raise_not_found)

    response = TestClient(api_module.app).put(
        "/sessions/nonexistent/evaluation",
        json={"outcome": "solved"},
    )

    assert response.status_code == 404


def test_get_session_evaluation_returns_evaluation(api_module, monkeypatch):
    fake_eval = {
        "session_id": "sess-1",
        "outcome": "solved",
        "source": "manual",
        "confidence": None,
        "task_title": "Fixed bug",
        "task_title_zh": "修复错误",
        "summary": None,
        "evidence": ["User marked solved"],
        "failure_reason": None,
        "evaluated_at": "2026-05-11T00:00:00+00:00",
    }
    monkeypatch.setattr(
        api_module, "get_session_evaluation", lambda sid, **kw: fake_eval
    )

    response = TestClient(api_module.app).get("/sessions/sess-1/evaluation")

    assert response.status_code == 200
    data = response.json()
    assert data["evaluation"]["outcome"] == "solved"
    assert data["evaluation"]["task_title"] == "Fixed bug"
    assert data["evaluation"]["task_title_zh"] == "修复错误"


def test_get_session_evaluation_returns_null_when_absent(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "get_session_evaluation", lambda sid, **kw: None)

    response = TestClient(api_module.app).get("/sessions/sess-1/evaluation")

    assert response.status_code == 200
    assert response.json()["evaluation"] is None


def test_delete_session_evaluation_removes_evaluation(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "delete_session_evaluation", lambda sid, **kw: True)

    response = TestClient(api_module.app).delete("/sessions/sess-1/evaluation")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_delete_session_evaluation_returns_404_when_absent(api_module, monkeypatch):
    monkeypatch.setattr(
        api_module, "delete_session_evaluation", lambda sid, **kw: False
    )

    response = TestClient(api_module.app).delete("/sessions/sess-1/evaluation")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Slice 4: Model Effectiveness API
# ---------------------------------------------------------------------------


def test_model_effectiveness_endpoint_passes_filters(api_module, monkeypatch):
    captured = {}

    def fake_aggregate(**kwargs):
        captured.update(kwargs)
        return {
            "groups": [
                {
                    "key": "gpt-5.5",
                    "session_count": 2,
                    "evaluated_count": 1,
                    "solved_count": 1,
                    "partial_count": 0,
                    "failed_count": 0,
                    "stuck_count": 0,
                    "unknown_count": 1,
                    "no_op_count": 0,
                    "solve_rate": 1.0,
                    "total_cost_usd": 0.5,
                    "cost_per_solved": 0.5,
                    "avg_duration_s": 120.0,
                }
            ]
        }

    monkeypatch.setattr(api_module, "aggregate_model_effectiveness", fake_aggregate)

    response = TestClient(api_module.app).get(
        "/model-effectiveness",
        params={
            "group_by": "model",
            "since": "2026-05-01T00:00:00Z",
            "until": "2026-05-11T23:59:59Z",
            "client_source": "codex",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["groups"][0]["key"] == "gpt-5.5"
    assert captured == {
        "user_id": ANY,
        "group_by": "model",
        "since": "2026-05-01T00:00:00Z",
        "until": "2026-05-11T23:59:59Z",
        "client_source": "codex",
        "hide_noop": False,
    }


def test_model_effectiveness_endpoint_rejects_invalid_group_by(api_module, monkeypatch):
    monkeypatch.setattr(
        api_module, "aggregate_model_effectiveness", lambda **kwargs: {"groups": []}
    )

    response = TestClient(api_module.app).get(
        "/model-effectiveness",
        params={"group_by": "evaluation_source"},
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Slice 8: Daily Effectiveness Report API
# ---------------------------------------------------------------------------


def test_daily_effectiveness_endpoint_passes_date(api_module, monkeypatch):
    captured = {}

    def fake_report(**kwargs):
        captured.update(kwargs)
        return {
            "date": kwargs["date"],
            "summary": "You ran 2 AI sessions. 1 was evaluated. Total cost was $0.42.",
            "session_count": 2,
            "evaluated_count": 1,
            "classified_count": 1,
            "solved_count": 1,
            "partial_count": 0,
            "failed_count": 0,
            "stuck_count": 0,
            "no_op_count": 0,
            "unknown_count": 1,
            "total_cost_usd": 0.42,
            "highlights": ["codex / gpt-5.5 solved 1/1 evaluated sessions"],
            "needs_attention": [],
            "model_takeaways": ["codex / gpt-5.5 solved 1/1 evaluated sessions"],
            "groups": [],
        }

    monkeypatch.setattr(api_module, "daily_session_effectiveness_report", fake_report)

    response = TestClient(api_module.app).get(
        "/sessions/daily-effectiveness",
        params={"date": "2026-05-10"},
    )

    assert response.status_code == 200
    assert captured == {"date": "2026-05-10", "user_id": ANY}
    assert response.json()["date"] == "2026-05-10"


def test_daily_effectiveness_endpoint_rejects_invalid_date(api_module, monkeypatch):
    def fake_report(**kwargs):
        raise ValueError("Invalid date: not-a-date. Expected YYYY-MM-DD")

    monkeypatch.setattr(api_module, "daily_session_effectiveness_report", fake_report)

    response = TestClient(api_module.app).get(
        "/sessions/daily-effectiveness",
        params={"date": "not-a-date"},
    )

    assert response.status_code == 400
    assert "Invalid date" in response.json()["detail"]


# ---------------------------------------------------------------------------
# SPA catch-all routing
# ---------------------------------------------------------------------------


def test_spa_deep_links_serve_index_html(api_module):
    """GET /dashboard, /logs, /settings return 200 with HTML, not 404."""
    from pathlib import Path

    frontend_dist = (
        Path(api_module.__file__).resolve().parent.parent / "frontend" / "dist"
    )
    if not (frontend_dist / "index.html").is_file():
        import pytest

        pytest.skip("frontend/dist/index.html not built")

    client = TestClient(api_module.app)

    for path in ("/dashboard", "/logs", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
        assert "text/html" in response.headers.get("content-type", "")


def test_spa_deep_links_do_not_hijack_api_routes(api_module, monkeypatch):
    """SPA middleware must not rewrite API routes to index.html."""
    monkeypatch.setattr(api_module, "fetch_recent_usage", lambda **kwargs: [])

    client = TestClient(api_module.app)
    response = client.get("/usage", params={"limit": "0"})

    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")


def test_spa_does_not_hijack_evaluation_jobs_routes(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "list_active_evaluation_jobs", lambda **kwargs: [])
    response = TestClient(api_module.app).get("/evaluation-jobs/active")

    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    assert response.json() == {"jobs": {}}


def test_spa_missing_asset_does_not_return_html(api_module):
    """Missing asset-like paths (with extensions) must not be rewritten to index.html."""
    from pathlib import Path

    frontend_dist = (
        Path(api_module.__file__).resolve().parent.parent / "frontend" / "dist"
    )
    if not (frontend_dist / "index.html").is_file():
        import pytest

        pytest.skip("frontend/dist/index.html not built")

    client = TestClient(api_module.app)
    response = client.get("/assets/missing.js")

    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


# ─── /usage/tools endpoint ────────────────────────────────────────────────────


def test_usage_tools_endpoint_exists(api_module, monkeypatch):
    monkeypatch.setattr(
        api_module, "distinct_tool_names", lambda **kw: ["bash", "read"]
    )
    response = TestClient(api_module.app).get("/usage/tools")
    assert response.status_code == 200
    assert response.json() == ["bash", "read"]


def test_usage_tools_passes_time_filters(api_module, monkeypatch):
    captured = {}

    def fake_distinct(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "distinct_tool_names", fake_distinct)

    TestClient(api_module.app).get(
        "/usage/tools",
        params={
            "since": "2026-04-17T00:00:00+00:00",
            "until": "2026-04-18T00:00:00+00:00",
        },
    )
    assert captured["since"] == "2026-04-17T00:00:00+00:00"
    assert captured["until"] == "2026-04-18T00:00:00+00:00"


def test_usage_endpoint_passes_tool_name(api_module, monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api_module, "fetch_recent_usage", fake_fetch)

    response = TestClient(api_module.app).get(
        "/usage", params={"tool_name": "bash", "limit": "10"}
    )
    assert response.status_code == 200
    assert captured["tool_name"] == "bash"


def test_usage_count_passes_tool_name(api_module, monkeypatch):
    captured = {}

    def fake_count(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(api_module, "count_usage", fake_count)

    response = TestClient(api_module.app).get(
        "/usage/count", params={"tool_name": "edit"}
    )
    assert response.status_code == 200
    assert captured["tool_name"] == "edit"


# ---------------------------------------------------------------------------
# PR 22: local/hosted/admin route classification
# (docs/design/specs/pr22-route-classification.md)
# ---------------------------------------------------------------------------

ADMIN_ONLY_ROUTES = [
    ("PATCH", "/config"),
]

# Evaluation routes the server used to own. Evaluation runs on the client now;
# these paths must never resolve to a handler again.
REMOVED_EVALUATION_ROUTES = [
    ("PUT", "/local/sessions/sess-1/evaluation"),
    ("GET", "/local/sessions/sess-1/evaluation"),
    ("DELETE", "/local/sessions/sess-1/evaluation"),
    ("POST", "/local/sessions/sess-1/evaluate-with-llm"),
    ("GET", "/local/poll/job-1"),
    ("GET", "/local/evaluation-jobs/active"),
    ("GET", "/local/sessions/sess-1/evaluation-jobs"),
    ("PATCH", "/local/evaluation-jobs/job-1"),
    ("POST", "/sessions/sess-1/evaluate-with-llm"),
    ("GET", "/poll/job-1"),
    ("PATCH", "/evaluation-jobs/job-1"),
    ("PATCH", "/config/evaluation"),
]


def _authenticated_client(api_module, fresh_db, monkeypatch):
    import src.config.app
    from src.auth.tokens import mint_token

    monkeypatch.setitem(
        src.config.app.CONFIG, "auth", {"provider": "google", "allowlist": []}
    )
    token, _ = mint_token("a@example.com", kind="cli", db_path=fresh_db.db_path)
    client = TestClient(api_module.app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.mark.parametrize("method,path", ADMIN_ONLY_ROUTES)
def test_admin_routes_404_for_authenticated_user_under_google_provider(
    api_module, fresh_db, monkeypatch, method, path
):
    client = _authenticated_client(api_module, fresh_db, monkeypatch)
    response = client.request(method, path)
    assert response.status_code == 404


@pytest.mark.parametrize("method,path", ADMIN_ONLY_ROUTES)
def test_admin_routes_unaffected_under_local_provider(api_module, method, path):
    response = TestClient(api_module.app).request(method, path)
    assert response.status_code != 404


def _assert_route_removed(response):
    """Assert an old pre-rename path no longer resolves to its real handler.

    Not a strict 404: depending on the installed Starlette version, an
    unmatched path either hits Starlette's own 404 or falls through to this
    app's SPA `StaticFiles(html=True)` mount (200 text/html for GET/HEAD,
    405 for other methods since StaticFiles only serves GET/HEAD) — a
    pre-existing gap unrelated to this rename (`requirements.txt` doesn't
    pin fastapi/starlette, so the SPA fallback's exact behavior on an
    unmatched path drifts with whatever version gets installed). Every one
    of those outcomes proves the old route's handler never ran, which is
    what actually matters here.
    """
    content_type = response.headers.get("content-type", "")
    assert (
        response.status_code == 404
        or response.status_code == 405
        or (response.status_code == 200 and "text/html" in content_type)
    ), f"expected old route to be gone, got {response.status_code} {content_type}"


@pytest.mark.parametrize("method,path", REMOVED_EVALUATION_ROUTES)
def test_removed_evaluation_routes_are_gone_under_local_provider(
    api_module, method, path
):
    response = TestClient(api_module.app).request(method, path)
    _assert_route_removed(response)


@pytest.mark.parametrize("method,path", REMOVED_EVALUATION_ROUTES)
def test_removed_evaluation_routes_are_gone_under_google_provider(
    api_module, fresh_db, monkeypatch, method, path
):
    client = _authenticated_client(api_module, fresh_db, monkeypatch)
    response = client.request(method, path)
    _assert_route_removed(response)


REMOVED_LOCAL_INSPECTION_PATHS = [
    ("GET", "/local/agents"),
    ("GET", "/local/setup-health"),
]


@pytest.mark.parametrize("method,path", REMOVED_LOCAL_INSPECTION_PATHS)
def test_local_inspection_routes_are_gone_under_local_provider(
    api_module, method, path
):
    """Machine inspection belongs to the client service now, not the server."""
    response = TestClient(api_module.app).request(method, path)
    _assert_route_removed(response)


@pytest.mark.parametrize("method,path", REMOVED_LOCAL_INSPECTION_PATHS)
def test_local_inspection_routes_are_gone_under_google_provider(
    api_module, fresh_db, monkeypatch, method, path
):
    client = _authenticated_client(api_module, fresh_db, monkeypatch)
    response = client.request(method, path)
    _assert_route_removed(response)


def test_slow_fetch_does_not_block_event_loop(api_module, monkeypatch):
    """A slow upstream fetch must not stall the event loop."""
    import time

    def slow_fetch(*args, **kwargs):
        time.sleep(0.2)
        return []

    monkeypatch.setattr(api_module, "_fetch_live_sources", slow_fetch)

    async def main():
        task = asyncio.create_task(api_module._resolve_live_cost_maps())
        await asyncio.sleep(0.05)
        assert not task.done(), "event loop was blocked by pricing fetch"
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(main())


def test_version_publishes_the_collector_for_clients(api_module, monkeypatch):
    """Clients need to know where to point agents, and only the server knows."""
    monkeypatch.setattr(
        api_module,
        "resolve_server_urls",
        lambda _config: {
            "api_url": "https://tracker.example.test:4004",
            "otlp_url": "https://tracker.example.test:4005",
        },
    )
    client = TestClient(api_module.app)
    payload = client.get("/version").json()

    assert payload["otlp_logs_endpoint"] == "https://tracker.example.test:4005/v1/logs"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://localhost:9205/v1/logs", "http://localhost:9205/v1/logs"),
        ("https://[::1]:9205/v1/logs", "https://[::1]:9205/v1/logs"),
        ("http://0.0.0.0:9205", "http://localhost:9205/v1/logs"),
    ],
)
def test_version_collector_hint_honors_runtime_endpoint_override(
    api_module, monkeypatch, endpoint, expected
):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", endpoint)
    monkeypatch.setattr(api_module, "CONFIG", {"server": {"otlp_port": 4002}})
    assert (
        TestClient(api_module.app).get("/version").json()["otlp_logs_endpoint"]
        == expected
    )


@pytest.mark.parametrize(
    "override",
    [
        "not-a-url",
        "http://localhost:bad/v1/logs",
        "https://user:secret@localhost:9205/v1/logs",
        "https://localhost:9205/v1/logs?token=secret",
    ],
)
def test_version_collector_hint_does_not_publish_unsafe_overrides(
    api_module, monkeypatch, override
):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", override)
    payload = TestClient(api_module.app).get("/version").json()
    assert "secret" not in json.dumps(payload)
    if "secret" in override:
        assert "otlp_logs_endpoint" not in payload
        assert payload["collector_bind"] == {"host": "localhost", "port": 9205}
    else:
        assert payload["otlp_logs_endpoint"].endswith("/v1/logs")


def test_version_preserves_safe_custom_collector_path(api_module, monkeypatch):
    endpoint = "https://collector.example:9443/custom/v1/logs"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", endpoint)
    assert (
        TestClient(api_module.app).get("/version").json()["otlp_logs_endpoint"]
        == endpoint
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "[::]"])
def test_version_wildcard_collector_uses_configured_public_origin(
    api_module, monkeypatch, host
):
    monkeypatch.setattr(
        api_module,
        "CONFIG",
        {
            "server": {
                "host": "0.0.0.0",
                "base_url": "https://tracker.example",
                "otlp_port": 4002,
            }
        },
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", f"http://{host}:9205/v1/logs"
    )
    payload = TestClient(api_module.app).get("/version").json()
    assert payload["otlp_logs_endpoint"] == "https://tracker.example:9205/v1/logs"
    assert payload["collector_bind"] == {"host": host.strip("[]"), "port": 9205}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_version_does_not_publish_server_loopback_to_remote_clients(
    api_module, monkeypatch, host
):
    monkeypatch.setattr(
        api_module, "CONFIG", {"server": {"base_url": "https://tracker.example"}}
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", f"http://{host}:9205/v1/logs"
    )
    assert "otlp_logs_endpoint" not in TestClient(api_module.app).get("/version").json()


def test_version_publishes_supported_protocol(api_module):
    from protocol import MAX_SUPPORTED_GENERATION, MIN_SUPPORTED_GENERATION

    payload = TestClient(api_module.app).get("/version").json()
    assert payload["protocol_min"] == MIN_SUPPORTED_GENERATION
    assert payload["protocol_max"] == MAX_SUPPORTED_GENERATION


def test_client_accepts_real_api_version_contract(api_module, monkeypatch, capsys):
    from client import auth

    api_client = TestClient(api_module.app)
    monkeypatch.setitem(api_module.CONFIG, "auth", {"provider": "google"})
    monkeypatch.setattr(auth.httpx, "get", api_client.get)

    assert auth.check_server("http://testserver") == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "server_url",
    ["https://tracker.example.test:4004", "http://localhost:4001", "http://[::1]:4001"],
)
def test_hosted_installer_uses_configured_origin_and_is_public(
    api_module, monkeypatch, server_url
):
    import shlex
    import subprocess

    monkeypatch.setattr(
        api_module, "resolve_server_urls", lambda _config: {"api_url": server_url}
    )
    monkeypatch.setitem(api_module.CONFIG, "auth", {"provider": "google"})
    response = TestClient(api_module.app).get(
        "/install.sh", headers={"host": "attacker.example"}
    )
    assert response.status_code == 200
    assert "text/x-shellscript" in response.headers["content-type"]
    assert f"TOKENAGE_INSTALL_SERVER={shlex.quote(server_url)}" in response.text
    assert "TOKENAGE_INSTALL_COMMIT=''" in response.text
    assert "__TOKENAGE_SERVER_URL__" not in response.text
    assert "__TOKENAGE_INSTALL_COMMIT__" not in response.text
    assert (
        subprocess.run(
            ["sh", "-n"], input=response.text, text=True, capture_output=True
        ).returncode
        == 0
    )


@pytest.mark.parametrize(
    "server_url",
    [
        "http://public.example.test:4004",
        "https://tracker.example.test:bad",
        "https://tracker.example.test/path",
        "https://name:pass@tracker.example.test",
        "https://tracker.example.test?foo=bar",
        "https://tracker.example.test\n",
    ],
)
def test_hosted_installer_rejects_invalid_api_origins(
    api_module, monkeypatch, server_url
):
    monkeypatch.setattr(
        api_module, "resolve_server_urls", lambda _config: {"api_url": server_url}
    )
    response = TestClient(api_module.app).get("/install.sh")
    assert response.status_code == 503
