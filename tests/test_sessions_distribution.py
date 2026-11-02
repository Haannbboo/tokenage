"""Shared chart/list predicates, boundary behavior, and session timeline reads."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import literal, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors

TS = 1776420000000000


@pytest.fixture
def sessions_api(api_module, fresh_db, monkeypatch):
    from src.database.models import SessionRecord, Usage

    # Include both sides of every exact edge, including subsecond duration.
    durations = [
        0,
        59.999999,
        60,
        599.999999,
        600,
        3599.999999,
        3600,
        21599.999999,
        21600,
        30000,
    ]
    costs = [
        "0",
        "0.09999999",
        "0.10",
        "0.99999999",
        "1",
        "4.99999999",
        "5",
        "19.99999999",
        "20",
        "21",
    ]
    with Session(fresh_db.database_module.get_engine()) as session:
        for i, (duration, cost) in enumerate(zip(durations, costs)):
            session.add(
                SessionRecord(
                    session_id=f"session-{i:02}",
                    user_id="test-user",
                    client_source="codex" if i % 2 else "opencode",
                    started=TS + i * 86400_000000,
                    ended=TS + i * 86400_000000 + round(duration * 1_000_000),
                    total_cost_usd=Decimal(cost),
                    updated_at="now",
                    request_count=2,
                )
            )
            for j in range(2):
                session.add(
                    Usage(
                        id=f"usage-{i}-{j}",
                        user_id="test-user",
                        session_id=f"session-{i:02}",
                        device_id="device-a" if j == 0 else None,
                        model="test-model" if j == 0 else "other-model",
                        provider="test-provider",
                        endpoint="otlp",
                        ts=TS + i * 86400_000000 + j,
                    )
                )
        session.commit()
    monkeypatch.setattr(api_module, "_request_user_id", lambda _: "test-user")
    return TestClient(api_module.app), fresh_db


def test_distribution_is_not_page_limited_and_clicks_match(sessions_api):
    client, _ = sessions_api
    charts = client.get("/sessions/distribution").json()
    assert len(client.get("/sessions", params={"limit": 1}).json()["sessions"]) == 1
    for key, param in [
        ("by_duration", "duration_bucket"),
        ("by_cost", "cost_bucket"),
        ("by_agent", "client_source"),
        ("over_time", "since"),
    ]:
        assert sum(row["session_count"] for row in charts[key]) == 10
        for row in charts[key]:
            params = {param: row["key"], "limit": 1, "offset": 1}
            if key == "over_time":
                params.update(since=row["start"], until=row["end"])
            page = client.get("/sessions", params=params).json()
            assert page["total"] == row["session_count"]
            assert len(page["sessions"]) == min(1, max(0, row["session_count"] - 1))
    assert [row["session_count"] for row in charts["by_duration"]] == [2] * 5
    assert [row["session_count"] for row in charts["by_cost"]] == [2] * 5
    assert sum(row["cost_share"] for row in charts["by_agent"]) == pytest.approx(1)
    assert sum(row["total_cost_usd"] for row in charts["by_agent"]) == pytest.approx(
        73.19999996
    )
    for row in charts["over_time"]:
        assert row["period"].startswith("2026-04-")


@pytest.mark.parametrize("granularity", ["day", "hour", "15m"])
@pytest.mark.parametrize("tz_offset", ["+00:00", "-07:00", "+05:30"])
def test_time_bucket_click_timezone_and_combined_filters(
    sessions_api, granularity, tz_offset
):
    client, _ = sessions_api
    params = {
        "granularity": granularity,
        "tz_offset": tz_offset,
        "client_source": "codex",
        "since": "2026-04-18",
        "until": "2026-04-26",
    }
    charts = client.get("/sessions/distribution", params=params).json()
    assert sum(row["session_count"] for row in charts["over_time"]) == 4
    for row in charts["over_time"]:
        page = client.get(
            "/sessions",
            params={
                "client_source": "codex",
                "since": row["start"],
                "until": row["end"],
            },
        ).json()
        assert page["total"] == row["session_count"]


def test_model_device_match_same_usage_and_unknown(sessions_api):
    client, _ = sessions_api
    for params, expected in [
        ({"model": "test-model"}, 10),
        ({"model": ["test-model", "other-model"]}, 10),
        ({"device_id": "unknown"}, 10),
        ({"device_id": "device-a"}, 10),
        ({"device_id": "device-a", "model": "other-model"}, 0),
    ]:
        page = client.get("/sessions", params=params).json()
        assert page["total"] == expected
        charts = client.get("/sessions/distribution", params=params).json()
        assert sum(row["session_count"] for row in charts["by_cost"]) == expected
        assert (
            client.get("/sessions/summary", params=params).json()["session_count"]
            == expected
        )
        assert (
            len(
                client.get("/sessions", params=params | {"view": "selector"}).json()[
                    "sessions"
                ]
            )
            == expected
        )


def test_provider_filter_selects_sessions_and_scopes_charts(sessions_api):
    client, _ = sessions_api
    assert client.get("/sessions?provider=no-such").json()["total"] == 0
    charts = client.get("/sessions/distribution?provider=no-such").json()
    assert sum(row["session_count"] for row in charts["by_cost"]) == 0
    assert client.get("/sessions?provider=test-provider").json()["total"] > 0


def test_missing_end_and_negative_duration_are_under_one_minute(fresh_db):
    from src.database.sessions import (
        DURATION_BUCKETS,
        _duration_s_expr,
        _fixed_bucket_expr,
    )

    # Current schema requires ended; legacy/live nullable values still have a
    # deterministic SQL bucket. Execute the actual expression with NULL inputs.
    for end in (None, TS - 1):
        expr = _fixed_bucket_expr(_duration_s_expr(), DURATION_BUCKETS)
        expr = visitors.replacement_traverse(
            expr,
            {},
            lambda node: (
                literal(TS)
                if getattr(node, "name", None) == "started"
                else literal(end)
                if getattr(node, "name", None) == "ended"
                else None
            ),
        )
        with fresh_db.database_module.get_engine().connect() as conn:
            assert conn.scalar(select(expr)) == "<1m"


@pytest.mark.parametrize("params", [{"since": "bad"}, {"until": "inf"}])
def test_bad_dates_are_json_errors(api_module, params):
    client = TestClient(api_module.app)
    for path in ("/sessions", "/sessions/distribution", "/sessions/summary"):
        assert client.get(path, params=params).status_code == 400


@pytest.mark.parametrize("params", [{"duration_bucket": "bad"}, {"cost_bucket": "bad"}])
def test_bad_list_buckets_are_json_errors(api_module, params):
    assert TestClient(api_module.app).get("/sessions", params=params).status_code == 400


@pytest.mark.parametrize("params", [{"granularity": "week"}, {"tz_offset": "bad"}])
def test_bad_chart_options_are_json_errors(api_module, params):
    assert (
        TestClient(api_module.app)
        .get("/sessions/distribution", params=params)
        .status_code
        == 400
    )


def test_charts_and_summary_ignore_list_bucket_filters(sessions_api):
    client, _ = sessions_api
    for path in ("/sessions/distribution", "/sessions/summary"):
        assert (
            client.get(path).json()
            == client.get(
                path, params={"duration_bucket": "<1m", "cost_bucket": "<0.10"}
            ).json()
        )


def test_time_filter_start_inclusive_end_exclusive_across_bucket_edge(sessions_api):
    from datetime import UTC, datetime

    from src.database.models import SessionRecord

    client, fresh_db = sessions_api
    edge = 1776470400000000  # 2026-04-18 UTC midnight
    with Session(fresh_db.database_module.get_engine()) as session:
        for name, start, end in (
            ("before", edge - 1, edge + 86400_000000),
            ("at", edge, edge + 1),
            ("after", edge + 1, edge + 2),
        ):
            session.add(
                SessionRecord(
                    session_id=name,
                    user_id="test-user",
                    client_source="edge-agent",
                    started=start,
                    ended=end,
                    updated_at="now",
                )
            )
        session.commit()
    chart = client.get(
        "/sessions/distribution", params={"client_source": "edge-agent"}
    ).json()
    assert [r["session_count"] for r in chart["over_time"]] == [1, 2]
    for row in chart["over_time"]:
        page = client.get(
            "/sessions",
            params={
                "client_source": "edge-agent",
                "since": row["start"],
                "until": row["end"],
            },
        ).json()
        assert page["total"] == row["session_count"]
        assert (
            int(datetime.fromisoformat(row["start"]).timestamp() * 1_000_000)
            == row["key"]
        )
    iso_edge = datetime.fromtimestamp(edge / 1_000_000, UTC).isoformat()
    assert (
        client.get("/sessions", params={"since": iso_edge, "until": iso_edge}).json()[
            "total"
        ]
        == 0
    )


def test_partial_bucket_boundaries_round_trip_chart_count(sessions_api):
    from datetime import UTC, datetime

    from src.database.models import SessionRecord

    client, fresh_db = sessions_api
    # All three starts share a bucket; each date boundary removes one of them.
    with Session(fresh_db.database_module.get_engine()) as session:
        for i in range(3):
            session.add(
                SessionRecord(
                    session_id=f"partial-{i}",
                    user_id="test-user",
                    client_source="partial-agent",
                    started=TS + i,
                    ended=TS + 86400_000000,
                    updated_at="now",
                )
            )
        session.commit()
    params = {
        "client_source": "partial-agent",
        "since": datetime.fromtimestamp((TS + 1) / 1_000_000, UTC).isoformat(),
        "until": datetime.fromtimestamp((TS + 2) / 1_000_000, UTC).isoformat(),
    }
    chart = client.get("/sessions/distribution", params=params).json()
    assert len(chart["over_time"]) == 1
    row = chart["over_time"][0]
    assert row["session_count"] == 1
    assert row["start"] == params["since"]
    assert row["end"] == params["until"]
    page = client.get(
        "/sessions",
        params={
            "client_source": "partial-agent",
            "since": row["start"],
            "until": row["end"],
        },
    ).json()
    assert page["total"] == row["session_count"]
    assert page["sessions"][0]["session_id"] == "partial-1"


def test_uniform_rows_and_empty_client_source_means_no_filter(sessions_api):
    from src.database.models import SessionRecord

    client, fresh_db = sessions_api
    with Session(fresh_db.database_module.get_engine()) as session:
        session.add(
            SessionRecord(
                session_id="no-agent",
                user_id="test-user",
                started=TS,
                ended=TS,
                updated_at="now",
            )
        )
        session.commit()
    chart = client.get("/sessions/distribution").json()
    assert any(row["key"] == "" for row in chart["by_agent"])
    for rows in chart.values():
        assert all(
            {"key", "session_count", "total_cost_usd"} <= row.keys() for row in rows
        )
    assert client.get("/sessions", params={"client_source": ""}).json()["total"] == 11
    assert (
        client.get("/sessions/distribution", params={"client_source": ""}).json()
        == chart
    )


def test_timeline_latest_30_cursor_tools_and_empty(sessions_api):
    from src.database.models import ToolCall, Usage

    client, fresh_db = sessions_api
    with Session(fresh_db.database_module.get_engine()) as session:
        session.add_all(
            [
                Usage(
                    id=f"timeline-{i:04}",
                    user_id="test-user",
                    session_id="timeline",
                    ts=TS + i // 3,
                    provider="test-provider",
                    model="test-model",
                    endpoint="otlp",
                    status=500 if i == 1001 else 200,
                )
                for i in range(1002)
            ]
        )
        session.add(
            ToolCall(
                tool_use_id="linked",
                usage_id="timeline-1001",
                session_id="timeline",
                user_id="test-user",
                tool_name="read",
                ts=TS // 1000,
                duration_ms=15,
            )
        )
        session.add(
            ToolCall(
                tool_use_id="unlinked",
                session_id="timeline",
                user_id="test-user",
                tool_name="write",
                ts=TS // 1000,
            )
        )
        session.commit()
    first = client.get("/usage", params={"session_id": "timeline", "limit": 30}).json()
    assert len(first) == 30
    assert [r["id"] for r in first] == [
        f"timeline-{i:04}" for i in range(1001, 971, -1)
    ]
    assert first[0]["status"] == 500
    last = first[-1]
    second = client.get(
        "/usage",
        params={
            "session_id": "timeline",
            "limit": 30,
            "before": f"{last['ts']}:{last['id']}",
        },
    ).json()
    assert [r["id"] for r in second] == [
        f"timeline-{i:04}" for i in range(971, 941, -1)
    ]
    assert (
        client.get("/usage", params={"session_id": "empty", "limit": 30}).json() == []
    )
    tools = client.get("/usage/timeline-1001/tool-calls").json()
    assert [r["tool_name"] for r in tools] == ["read"]
    assert tools[0]["duration_ms"] == 15
    assert all(
        "prompt" not in key or key in {"prompt_tokens", "prompt_length"}
        for row in first
        for key in row
    )
