"""Read isolation through real auth tokens, SQL queries, and aggregate tables."""

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import ingest_write

DAY = "2026-04-17"
START_TS = 1776420000000000  # 2026-04-17 10:00 UTC, in microseconds.
READ_PATHS = (
    "/usage",
    "/usage/count",
    "/usage/high-watermark",
    "/usage/sources",
    "/usage/tools",
    "/usage/run-summary",
    "/usage/summary",
    "/usage/by-source",
    "/usage/by-tool",
    "/usage/by-provider",
    "/usage/daily",
    "/usage/daily-by-dimension",
    "/sessions",
    "/sessions/summary",
    "/model-effectiveness",
    "/sessions/daily-effectiveness",
)


@pytest.fixture
def tenant_reads(api_module, fresh_db):
    from src.auth.tokens import mint_token
    from src.database.models import ToolCall, Usage
    from src.database.sessions import upsert_session_from_tool_call

    db = fresh_db.database_module
    tokens = {}
    user_ids = {}
    for name in ("a", "b", "empty", "local"):
        token, row = mint_token(
            f"{name}@example.com", kind="cli", db_path=fresh_db.db_path
        )
        tokens[name] = token
        user_ids[name] = row.id
    cookie, _ = mint_token("a@example.com", kind="web", db_path=fresh_db.db_path)
    tokens["cookie"] = cookie

    for owner_index, (name, weight, outcome) in enumerate(
        (("a", 1, "solved"), ("b", 10, "failed"), ("local", 100, "stuck"))
    ):
        for index, source in enumerate(("opencode", f"{name}-source")):
            usage = Usage(
                device_id="test-device",
                id=f"{name}-{index}",
                user_id=user_ids.get(name),
                ts=START_TS + (owner_index * 100 + index * 10) * 1_000_000,
                provider="test-provider",
                model="test-model",
                client_source=source,
                session_id=f"{name}-session-{index}",
                endpoint="/v1/chat/completions",
                prompt_tokens=50 * weight,
                completion_tokens=100 * weight,
                total_tokens=150 * weight,
                latency_ms=1000,
                total_cost_usd=weight,
                status=200,
            )
            with Session(db.get_engine()) as session:
                session.add(usage)
                session.commit()
                session.refresh(usage)
                session.expunge(usage)
            _upsert_hourly(usage)
            db.upsert_session_from_usage(usage)
            tool = ToolCall(
                tool_use_id=f"{name}-tool-{index}",
                user_id=usage.user_id,
                usage_id=usage.id,
                session_id=usage.session_id,
                tool_name="shared-tool" if index == 0 else f"{name}-tool",
                client_source=source,
                duration_ms=100 * weight,
                ts=usage.ts // 1000,
            )
            with Session(db.get_engine()) as session:
                session.add(tool)
                session.commit()
                session.refresh(tool)
                session.expunge(tool)
            ingest_write(upsert_session_from_tool_call, tool)
            db.upsert_session_evaluation(
                usage.session_id,
                outcome,
                summary=f"{name}-private-evaluation",
                user_id=usage.user_id,
            )
    return tokens, user_ids


@pytest.mark.parametrize("auth_mode", ("cookie", "bearer", "empty", "loopback"))
@pytest.mark.parametrize("range_mode", ("days", "hours"))
def test_all_read_routes_isolate_data(
    api_module, tenant_reads, monkeypatch, auth_mode, range_mode
):
    from src.config.app import CONFIG

    tokens, _ = tenant_reads
    # "loopback": the local provider's owner (no token) owns none of the seeded
    # rows, so it reads nothing.
    monkeypatch.setitem(
        CONFIG, "auth", {"provider": "local" if auth_mode == "loopback" else "google"}
    )
    client = TestClient(api_module.app)
    if auth_mode == "cookie":
        client.cookies.set("tokenage_session", tokens["cookie"])
    elif auth_mode != "loopback":
        client.headers["Authorization"] = f"Bearer {tokens.get(auth_mode, tokens['a'])}"
    owners = () if auth_mode in ("empty", "loopback") else ("a",)
    count = 2 * len(owners)
    weight = int(bool(owners))
    params = {
        "since": "2026-04-16T00:00:00+00:00"
        if range_mode == "days"
        else "2026-04-17T09:00:00+00:00",
        "until": "2026-04-19T00:00:00+00:00"
        if range_mode == "days"
        else "2026-04-17T11:00:00+00:00",
    }

    def read(path, **extra):
        response = client.get(path, params=params | extra)
        assert response.status_code == 200, response.text
        return response.json()

    expected_ids = {f"{owner}-{index}" for owner in owners for index in range(2)}
    rows = read("/usage")
    assert {row["id"] for row in rows} == expected_ids
    assert sum(row["total_cost_usd"] for row in rows) == 2 * weight
    assert read("/usage/count") == {"total": count}
    latest = START_TS + 10 * 1_000_000
    assert read("/usage/high-watermark") == {"ts": latest if owners else 0}
    assert set(read("/usage/sources")) == (
        {"opencode", *(f"{owner}-source" for owner in owners)} if owners else set()
    )
    assert set(read("/usage/tools")) == (
        {"shared-tool", *(f"{owner}-tool" for owner in owners)} if owners else set()
    )
    run = read("/usage/run-summary", include_rows=True)
    assert run["window"]["row_count"] == count
    assert run["summary"]["requests"] == count
    assert run["summary"]["total_cost_usd"] == 2 * weight
    assert {row["id"] for row in run["rows"]} == expected_ids
    assert {row["session_id"] for row in run["sessions"]} == {
        f"{owner}-session-{index}" for owner in owners for index in range(2)
    }

    for path in (
        "/usage/summary",
        "/usage/by-source",
        "/usage/by-provider",
        "/usage/daily",
    ):
        groups = read(path)
        assert sum(row["requests"] for row in groups) == count, path
        assert sum(row["total_tokens"] for row in groups) == 300 * weight, path
        assert sum(row["total_cost_usd"] for row in groups) == 2 * weight, path
        if auth_mode in ("cookie", "bearer"):
            assert groups[0]["avg_throughput"] == pytest.approx(100.0)

    tools = read("/usage/by-tool")
    assert sum(row["count"] for row in tools) == count
    assert sum(row["total_duration_ms"] for row in tools) == 200 * weight
    for dimension in ("model", "provider", "client_source"):
        groups = read("/usage/daily-by-dimension", dimension=dimension)
        assert sum(row["requests"] for row in groups) == count
        assert sum(row["total_cost_usd"] for row in groups) == 2 * weight

    expected_sessions = {
        f"{owner}-session-{index}" for owner in owners for index in range(2)
    }
    sessions = read("/sessions")
    assert sessions["total"] == count
    assert {row["session_id"] for row in sessions["sessions"]} == expected_sessions
    assert {row["evaluation"]["summary"] for row in sessions["sessions"]} == {
        f"{owner}-private-evaluation" for owner in owners
    }
    selector = read("/sessions", view="selector")
    assert selector["total"] is None
    assert {row["session_id"] for row in selector["sessions"]} == expected_sessions
    summary = read("/sessions/summary")
    assert summary["session_count"] == count
    assert summary["total_cost_usd"] == 2 * weight
    for group_by in ("model", "provider", "source"):
        groups = read("/model-effectiveness", group_by=group_by)["groups"]
        assert sum(row["session_count"] for row in groups) == count
        assert sum(row["total_cost_usd"] for row in groups) == 2 * weight
        assert sum(row["solved_count"] for row in groups) == (2 if owners else 0)
        assert sum(row["failed_count"] for row in groups) == (2 if "b" in owners else 0)
    report = read("/sessions/daily-effectiveness", date=DAY)
    assert report["session_count"] == count
    assert report["total_cost_usd"] == 2 * weight
    assert sum(row["session_count"] for row in report["groups"]) == count
    assert sum(row["total_cost_usd"] for row in report["groups"]) == 2 * weight
    if auth_mode in ("cookie", "bearer"):
        assert report["needs_attention"] == []
        assert read("/usage", limit=1)[0]["id"] == "a-1"
        assert read("/usage", limit=1, offset=1)[0]["id"] == "a-0"
        page = read("/sessions", limit=1, offset=1)
        assert page["total"] == 2
        assert page["sessions"][0]["session_id"] == "a-session-0"


def test_foreign_ids_and_filters_cannot_expand_scope(
    api_module, tenant_reads, monkeypatch
):
    from src.config.app import CONFIG

    tokens, user_ids = tenant_reads
    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    client = TestClient(
        api_module.app, headers={"Authorization": f"Bearer {tokens['a']}"}
    )
    for owner in ("b", "local"):
        params = {"session_id": f"{owner}-session-0", "user_id": user_ids["b"]}
        assert client.get("/usage", params=params).json() == []
        assert client.get("/usage/count", params=params).json() == {"total": 0}
        run = client.get(
            "/usage/run-summary", params=params | {"include_rows": True}
        ).json()
        assert run["summary"]["requests"] == 0
        assert run["rows"] == []
        assert client.get(f"/usage/{owner}-0/tool-calls").json() == []
        assert client.get(f"/sessions/{owner}-session-0/tool-calls").json() == []
        assert (
            client.get(f"/sessions/{owner}-session-0/tool-calls/summary").status_code
            == 404
        )
        assert client.get("/usage", params={"tool_name": f"{owner}-tool"}).json() == []
        assert client.get(
            "/usage/count", params={"tool_name": f"{owner}-tool"}
        ).json() == {"total": 0}
        assert client.get(
            "/sessions", params={"client_source": f"{owner}-source"}
        ).json() == {
            "sessions": [],
            "total": 0,
        }
    assert len(client.get("/usage", params={"user_id": user_ids["b"]}).json()) == 2
    assert len(client.get("/usage/a-0/tool-calls").json()) == 1
    assert client.get("/sessions/a-session-0/tool-calls/summary").json() == [
        {"tool_name": "shared-tool", "count": 1}
    ]


def test_tool_queries_require_matching_owner(
    api_module, tenant_reads, monkeypatch, fresh_db
):
    from src.config.app import CONFIG
    from src.database.models import ToolCall

    tokens, user_ids = tenant_reads
    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    with Session(fresh_db.database_module.get_engine()) as session:
        session.add(
            ToolCall(
                tool_use_id="foreign-link",
                user_id=user_ids["b"],
                usage_id="a-0",
                session_id="a-session-0",
                tool_name="foreign-tool",
                duration_ms=999,
                ts=START_TS // 1000,
            )
        )
        session.commit()
    client = TestClient(
        api_module.app, headers={"Authorization": f"Bearer {tokens['a']}"}
    )
    rows = client.get("/usage").json()
    row = next(row for row in rows if row["id"] == "a-0")
    assert row["tool_names"] == "shared-tool"
    assert client.get("/usage", params={"tool_name": "foreign-tool"}).json() == []
    assert client.get("/usage/count", params={"tool_name": "foreign-tool"}).json() == {
        "total": 0
    }
    assert {row["tool_name"] for row in client.get("/usage/by-tool").json()} == {
        "shared-tool",
        "a-tool",
    }


def test_reads_reject_missing_invalid_and_ingest_tokens(
    api_module, tenant_reads, monkeypatch
):
    from src.auth.tokens import mint_token
    from src.config.app import CONFIG

    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    ingest, _ = mint_token("a@example.com", kind="ingest")
    client = TestClient(api_module.app)
    for token in (None, "invalid", ingest):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for path in READ_PATHS:
            assert (
                client.get(path, params={"date": DAY}, headers=headers).status_code
                == 401
            )


def test_request_user_id_fails_closed_without_middleware(api_module, monkeypatch):
    from src.config.app import CONFIG

    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as exc:
        api_module._request_user_id(request)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("auth_mode", ("cookie", "bearer", "b"))
def test_cost_recalculation_requires_row_ownership(
    api_module, tenant_reads, monkeypatch, fresh_db, auth_mode
):
    from sqlalchemy import select

    from src.config.app import CONFIG
    from src.database.models import PriceSnapshot, SessionRecord, Usage, UsageHourly

    tokens, _ = tenant_reads
    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    client = TestClient(api_module.app)
    if auth_mode == "cookie":
        client.cookies.set("tokenage_session", tokens["cookie"])
    else:
        client.headers["Authorization"] = f"Bearer {tokens.get(auth_mode, tokens['a'])}"
    owner = "b" if auth_mode == "b" else "a"

    def stored_costs():
        with Session(fresh_db.database_module.get_engine()) as session:
            return (
                session.execute(
                    select(
                        Usage.id, Usage.total_cost_usd, Usage.price_snapshot_id
                    ).order_by(Usage.id)
                ).all(),
                session.execute(
                    select(UsageHourly.id, UsageHourly.total_cost_usd).order_by(
                        UsageHourly.id
                    )
                ).all(),
                session.execute(
                    select(
                        SessionRecord.session_id,
                        SessionRecord.total_cost_usd,
                        SessionRecord.models_json,
                        SessionRecord.providers_json,
                    ).order_by(SessionRecord.session_id)
                ).all(),
                session.execute(
                    select(PriceSnapshot.id).order_by(PriceSnapshot.id)
                ).all(),
            )

    before = stored_costs()
    rejected = ["missing"]
    rejected.extend(f"{other}-0" for other in ("a", "b", "local") if other != owner)
    for usage_id in rejected:
        response = client.post(f"/usage/{usage_id}/recalculate-cost")
        assert response.status_code == 404
        assert response.json() == {"detail": "usage row not found"}
    assert stored_costs() == before

    response = client.post(
        f"/usage/{owner}-0/recalculate-cost", params={"user_id": "another-user"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["skipped"] is False
    new_cost = result["new_costs"]["total_cost_usd"]
    old_cost = result["old_costs"]["total_cost_usd"]
    assert new_cost != old_cost
    after = stored_costs()
    for index in (0, 1, 2):
        changed = [new for old, new in zip(before[index], after[index]) if old != new]
        assert len(changed) == 1, (index, changed)
        if index != 1:
            assert changed[0][0] == f"{owner}-{'0' if index == 0 else 'session-0'}"
        assert float(changed[0][1]) == pytest.approx(new_cost)
    assert len(after[3]) == len(before[3]) + 1


def test_loopback_owner_cannot_recalculate_other_users_rows(
    api_module, tenant_reads, monkeypatch
):
    from src.config.app import CONFIG

    monkeypatch.setitem(CONFIG, "auth", {"provider": "local"})
    client = TestClient(api_module.app)
    for usage_id in ("a-0", "b-0", "local-0"):
        assert client.post(f"/usage/{usage_id}/recalculate-cost").status_code == 404


def test_cost_recalculation_rejects_invalid_and_revoked_tokens(
    api_module, tenant_reads, monkeypatch
):
    from src.auth.tokens import mint_token, resolve_token, revoke_token
    from src.config.app import CONFIG

    tokens, user_ids = tenant_reads
    ingest, _ = mint_token("a@example.com", kind="ingest")
    _, token_row = resolve_token(tokens["a"])
    assert revoke_token(token_row.id, user_id=user_ids["a"])
    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    client = TestClient(api_module.app)
    for token in (None, "invalid", ingest, tokens["a"]):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        assert (
            client.post("/usage/a-0/recalculate-cost", headers=headers).status_code
            == 401
        )


def _upsert_hourly(usage, db_path=None):
    from sqlalchemy.orm import Session

    from src.database import get_engine
    from src.database.usage import upsert_hourly_aggregate

    with Session(get_engine(db_path)) as session:
        upsert_hourly_aggregate(session, usage)
        session.commit()
