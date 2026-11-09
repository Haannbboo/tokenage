"""Project API totals, portable activity buckets, filters and ownership."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

DAY = 86400_000000
START = int(datetime(2026, 4, 20, tzinfo=UTC).timestamp() * 1_000_000)
WINDOW = {"since": str(START / 1_000_000), "until": str((START + 3 * DAY) / 1_000_000)}


@pytest.fixture
def projects_api(api_module, fresh_db, monkeypatch):
    from src.database.models import SessionRecord, Usage

    with Session(fresh_db.database_module.get_engine()) as db:
        for sid, project, owner, cost, agent, start, end in [
            ("a", "orchard", "test-user", "1.25", "codex", START - DAY, START),
            (
                "b",
                "orchard",
                "test-user",
                "2.75",
                "opencode",
                START + DAY,
                START + 2 * DAY,
            ),
            ("c", "grove", "test-user", "2", "codex", START, START + DAY - 1),
            ("d", None, "test-user", "0.5", "codex", START, START),
            ("foreign", "orchard", "other-user", "999", "codex", START, START),
            ("private", "secret", "other-user", "999", "codex", START, START),
        ]:
            db.add(
                SessionRecord(
                    session_id=sid,
                    user_id=owner,
                    project_name=project,
                    client_source=agent,
                    started=start,
                    ended=end,
                    updated_at="now",
                    total_cost_usd=Decimal(cost),
                    request_count=4,
                    failed_requests=1,
                    prompt_tokens=100,
                    cached_tokens=50,
                    cache_creation_tokens=25,
                    latency_sum_ms=1200,
                    models_json='{"model-a": 1, "model-b": 2}',
                    tool_calls_json='{"Read": 3, "Bash": 2}',
                )
            )
            db.add(
                Usage(
                    id=f"usage-{sid}",
                    user_id=owner,
                    session_id=sid,
                    device_id="laptop" if sid == "b" else None,
                    model="model-a" if sid == "b" else "model-b",
                    provider="test-provider",
                    endpoint="otlp",
                    ts=start,
                )
            )
        db.commit()
    monkeypatch.setattr(api_module, "_request_user_id", lambda _: "test-user")
    return TestClient(api_module.app), fresh_db


def test_project_totals_and_user_isolation(projects_api):
    client, _ = projects_api
    data = client.get("/sessions/projects", params=WINDOW).json()
    assert [p["key"] for p in data["projects"]] == ["orchard", "grove"]
    row = data["projects"][0]
    assert row == dict(
        key="orchard",
        session_count=2,
        total_cost_usd=4,
        request_count=8,
        prompt_tokens=200,
        cached_tokens=100,
        cache_creation_tokens=50,
        last_active=START + 2 * DAY,
        agents={"codex": 1, "opencode": 1},
    )
    assert "unassigned" not in data
    assert "failed_requests" not in row
    assert (
        "by_project" not in client.get("/sessions/distribution", params=WINDOW).json()
    )


def test_activity_uses_overlap_and_shared_window(projects_api):
    client, _ = projects_api
    buckets = client.get("/sessions/projects", params=WINDOW).json()["over_time"]
    assert len(buckets) == 3
    assert [b["by_project"] for b in buckets] == [
        {"orchard": 1, "grove": 1},
        {"orchard": 1},
        {"orchard": 1},
    ]
    assert buckets[0]["period"] == "2026-04-20"
    hourly = client.get(
        "/sessions/projects",
        params={**WINDOW, "granularity": "hour", "tz_offset": "+05:30"},
    ).json()
    assert len(hourly["over_time"]) == 73
    # Every stack equals the sum of the two project-specific distribution reads.
    for name in ["orchard", "grove"]:
        distribution = client.get(
            "/sessions/distribution",
            params={
                **WINDOW,
                "project": name,
                "granularity": "hour",
                "tz_offset": "+05:30",
            },
        ).json()
        assert [b["by_project"].get(name, 0) for b in hourly["over_time"]] == [
            b["session_count"] for b in distribution["over_time"]
        ]


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"client_source": "opencode"}, 1),
        ({"device_id": "laptop"}, 1),
        ({"provider": "test-provider", "model": "model-a"}, 1),
        ({"model": ["model-a", "absent"]}, 1),
        ({"provider": "absent"}, 0),
        ({"since": str((START + DAY) / 1_000_000)}, 1),
        ({"until": str((START + DAY) / 1_000_000)}, 1),
    ],
)
def test_project_filters_match_session_filters(projects_api, filters, expected):
    client, _ = projects_api
    params = {**WINDOW, **filters}
    data = client.get("/sessions/projects", params=params).json()
    orchard = next((p for p in data["projects"] if p["key"] == "orchard"), None)
    assert (orchard["session_count"] if orchard else 0) == expected
    response = client.get(
        "/sessions/projects/detail", params={**params, "name": "orchard"}
    )
    assert response.status_code == (200 if expected else 404)
    if expected:
        assert len(response.json()["recent_sessions"]) == expected


def test_detail_totals_and_recent_session_shape(projects_api):
    client, _ = projects_api
    data = client.get(
        "/sessions/projects/detail", params={**WINDOW, "name": "orchard"}
    ).json()
    assert data["failed_requests"] == 2
    assert data["latency_sum_ms"] == 2400
    assert data["first_started"] == START - DAY
    assert set(data) == {
        "key",
        "failed_requests",
        "latency_sum_ms",
        "first_started",
        "models",
        "tools",
        "recent_sessions",
    }
    assert data["models"] == {"model-b": 4, "model-a": 2}
    assert data["tools"] == {"Read": 6, "Bash": 4}
    assert (
        data["recent_sessions"]
        == client.get(
            "/sessions", params={**WINDOW, "project": "orchard", "limit": 10}
        ).json()["sessions"]
    )
    assert [s["session_id"] for s in data["recent_sessions"]] == ["b", "a"]


def test_bad_json_and_top_five_and_ten(projects_api):
    from src.database.models import SessionRecord

    client, fresh_db = projects_api
    with Session(fresh_db.database_module.get_engine()) as db:
        row = db.get(SessionRecord, "a")
        row.models_json = "{broken"
        row.tool_calls_json = "[1,2]"
        row = db.get(SessionRecord, "b")
        row.models_json = '{"valid": 2, "bad": "oops", "infinite": NaN}'
        row.tool_calls_json = "invalid"
        for i in range(12):
            db.add(
                SessionRecord(
                    session_id=f"recent-{i:02}",
                    user_id="test-user",
                    project_name="orchard",
                    started=START,
                    ended=START + 2 * DAY + i,
                    updated_at="now",
                    models_json=str({})
                    if i
                    else '{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}',
                )
            )
        db.commit()
    response = client.get(
        "/sessions/projects/detail", params={**WINDOW, "name": "orchard"}
    )
    assert response.status_code == 200
    data = response.json()
    assert list(data["models"]) == ["f", "e", "d", "c", "b"]
    assert data["tools"] == {}
    assert len(data["recent_sessions"]) == 10
    assert data["recent_sessions"][0]["session_id"] == "recent-11"


def test_numeric_json_map_skips_huge_ints(projects_api):
    import sys

    from src.database.models import SessionRecord
    from src.database.sessions import _numeric_json_map

    top = int(sys.float_info.max)
    assert _numeric_json_map(f'{{"a": 1{"0" * 400}, "b": 2, "c": {top}}}') == {
        "b": 2,
        "c": top,
    }
    # Detail sums maps across sessions; a huge int plus a float used to overflow.
    client, fresh_db = projects_api
    with Session(fresh_db.database_module.get_engine()) as db:
        db.get(
            SessionRecord, "a"
        ).models_json = f'{{"model-a": 1{"0" * 400}, "model-b": 1.5}}'
        db.get(SessionRecord, "b").models_json = '{"model-a": 0.5, "model-b": 2}'
        db.commit()
    response = client.get(
        "/sessions/projects/detail", params={**WINDOW, "name": "orchard"}
    )
    assert response.status_code == 200
    assert response.json()["models"] == {"model-b": 3.5, "model-a": 0.5}


@pytest.mark.parametrize(
    "params,status",
    [
        ({}, 422),
        ({"name": ""}, 422),
        ({"name": "x" * 101}, 422),
        ({"name": "missing"}, 404),
        ({"name": "secret"}, 404),
    ],
)
def test_detail_name_validation_and_404(projects_api, params, status):
    client, _ = projects_api
    assert (
        client.get("/sessions/projects/detail", params={**WINDOW, **params}).status_code
        == status
    )


@pytest.mark.parametrize("path", ["/sessions/projects", "/sessions/projects/detail"])
@pytest.mark.parametrize(
    "invalid",
    [
        {"granularity": "week"},
        {"tz_offset": "bad"},
        {"since": "bad"},
        {"granularity": "15m", "since": "1", "until": str(START / 1_000_000)},
    ],
)
def test_project_bucketing_validation(projects_api, path, invalid):
    client, _ = projects_api
    assert client.get(
        path, params={**WINDOW, "name": "orchard", **invalid}
    ).status_code == (
        200 if path.endswith("/detail") and invalid.get("since") == "1" else 400
    )


def test_agent_mix_coalesces_null_and_empty(projects_api):
    from src.database.models import SessionRecord

    client, fresh_db = projects_api
    with Session(fresh_db.database_module.get_engine()) as db:
        db.get(SessionRecord, "a").client_source = None
        db.get(SessionRecord, "b").client_source = ""
        db.commit()
    project = client.get("/sessions/projects", params=WINDOW).json()["projects"][0]
    assert project["agents"] == {"": 2}
    assert sum(project["agents"].values()) == project["session_count"]


@pytest.mark.parametrize("granularity", ["day", "hour", "15m"])
@pytest.mark.parametrize("offset", ["+00:00", "+05:30", "-07:00"])
def test_sparse_single_sweep_matches_per_project(granularity, offset, monkeypatch):
    from collections import namedtuple
    from random import Random

    import src.database.sessions as module

    Timestamp = namedtuple("Timestamp", "project_name started ended")
    Pair = namedtuple("Pair", "started ended")
    rng = Random(12)
    rows = []
    for i in range(60):
        start = START + rng.randrange(-DAY, 3 * DAY)
        rows.append(Timestamp(f"p{i % 11}", start, start + rng.randrange(0, DAY)))
    # Exact lower/upper edges and a spanning session exercise inclusive ends.
    rows += [
        Timestamp("edge", START - DAY, START),
        Timestamp("edge", START + 3 * DAY, START + 4 * DAY),
        Timestamp("span", START - DAY, START + 4 * DAY),
    ]
    filters = module.SessionFilters(user_id="u", **WINDOW)
    expected = {
        name: module._active_session_buckets(
            [Pair(row.started, row.ended) for row in rows if row.project_name == name],
            filters=filters,
            granularity=granularity,
            tz_offset=offset,
        )
        for name in {row.project_name for row in rows}
    }
    calls = []
    original = module._granularity
    monkeypatch.setattr(
        module, "_granularity", lambda value: (calls.append(value), original(value))[1]
    )
    monkeypatch.setattr(
        module,
        "_active_session_buckets",
        lambda *args, **kwargs: pytest.fail("per-project sweep must not run"),
    )
    buckets = module._project_time_buckets(
        rows, filters=filters, granularity=granularity, tz_offset=offset
    )
    assert calls == [granularity]
    assert all(
        value > 0 for bucket in buckets for value in bucket["by_project"].values()
    )
    for name, old in expected.items():
        assert [bucket["by_project"].get(name, 0) for bucket in buckets] == [
            bucket["session_count"] for bucket in old
        ]
        assert [(b["key"], b["start"], b["end"], b["period"]) for b in buckets] == [
            (b["key"], b["start"], b["end"], b["period"]) for b in old
        ]


def test_detail_reads_only_extras_without_list_aggregate(projects_api, monkeypatch):
    from sqlalchemy import event

    import src.database.sessions as module

    client, fresh_db = projects_api
    monkeypatch.setattr(
        module,
        "aggregate_session_projects",
        lambda **kwargs: pytest.fail("duplicate list aggregate"),
    )
    statements = []
    engine = fresh_db.database_module.get_engine()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(
            "/sessions/projects/detail", params={**WINDOW, "name": "orchard"}
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    # One SQL aggregate, one two-column JSON read, and the limited recent list.
    reads = [sql for sql in statements if "FROM sessions" in sql]
    assert len(reads) == 3
    assert "sum(sessions.failed_requests)" in reads[0]
    assert "sum(sessions.latency_sum_ms)" in reads[0]
    assert "min(sessions.started)" in reads[0]
    assert (
        reads[1].split("FROM")[0].strip()
        == "SELECT sessions.models_json, sessions.tool_calls_json"
    )
    assert "LIMIT" in reads[2]
