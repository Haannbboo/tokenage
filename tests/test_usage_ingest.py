"""POST /ingest/v1/usage: ingest-token-only usage reports from the client proxy."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _report(**overrides) -> dict:
    report = {
        "provider": "anthropic",
        "model": "test-model",
        "endpoint": "/v1/messages",
        "client_source": "claude-code",
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_length": 42,
        "latency_ms": 900,
        "ttft_ms": 120,
        "status": 200,
        "base_url": "https://api.anthropic.com",
        "tool_calls": [{"tool_use_id": "toolu_1", "tool_name": "Bash"}],
    }
    report.update(overrides)
    return report


def _token(fresh_db, kind="ingest", email="a@example.com"):
    from src.auth.tokens import mint_token

    token, user = mint_token(email, kind=kind, db_path=fresh_db.db_path)
    return token, user


def _rows(fresh_db, _unused=None):
    from src.database import get_engine
    from src.database.models import ToolCall, Usage

    with Session(get_engine(fresh_db.db_path)) as s:
        return (
            s.execute(select(Usage)).scalars().all(),
            s.execute(select(ToolCall)).scalars().all(),
        )


def test_valid_report_is_recorded_priced_and_attributed(
    api_module, fresh_db, monkeypatch
):
    from src.pricing import costs
    from src.pricing.models import ModelCost

    monkeypatch.setitem(
        costs.MODEL_COSTS,
        "test-model",
        ModelCost(input=1, output=2, cache_read=0.25, cache_write=0.5),
    )
    monkeypatch.setitem(costs.MODEL_COST_SOURCES, "test-model", "test")
    token, user = _token(fresh_db)
    r = TestClient(api_module.app).post(
        "/ingest/v1/usage", json=_report(), headers={"x-tokenage-token": token}
    )
    assert r.status_code == 204
    usage, tools = _rows(fresh_db, None)
    assert len(usage) == 1
    row = usage[0]
    assert row.user_id == user.id
    assert row.prompt_tokens == 1000 and row.endpoint == "/v1/messages"
    assert row.total_cost_usd > 0  # server-priced
    assert row.ttft_ms == 120
    assert [(t.tool_use_id, t.tool_name, t.usage_id) for t in tools] == [
        ("toolu_1", "bash", row.id)
    ]


def test_missing_or_bad_token_is_rejected(api_module, fresh_db):
    client = TestClient(api_module.app)
    # loopback owner without a token must not be accepted
    assert client.post("/ingest/v1/usage", json=_report()).status_code == 401
    bad = {"x-tokenage-token": "tokenage_ingest_nope"}
    assert (
        client.post("/ingest/v1/usage", json=_report(), headers=bad).status_code == 401
    )
    assert _rows(fresh_db, None)[0] == []


def test_non_ingest_tokens_and_cookies_are_rejected(api_module, fresh_db):
    client = TestClient(api_module.app)
    web, _ = _token(fresh_db, kind="web")
    for kwargs in (
        {"headers": {"x-tokenage-token": web}},
        {"headers": {"Authorization": f"Bearer {web}"}},
        {"cookies": {"tokenage_session": web}},
    ):
        r = client.post("/ingest/v1/usage", json=_report(), **kwargs)
        assert r.status_code == 401
    assert _rows(fresh_db, None)[0] == []


def test_malformed_payload_is_422(api_module, fresh_db):
    token, _ = _token(fresh_db)
    client = TestClient(api_module.app)
    h = {"x-tokenage-token": token}
    assert (
        client.post("/ingest/v1/usage", json={"model": "x"}, headers=h).status_code
        == 422
    )
    r = client.post("/ingest/v1/usage", json=_report(prompt_tokens=-1), headers=h)
    assert r.status_code == 422


@pytest.mark.parametrize("provider", ["local", "google"])
def test_ingest_works_under_every_auth_provider(
    api_module, fresh_db, monkeypatch, provider
):
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG, "auth", {"provider": provider, "allowlist": []}
    )
    token, _ = _token(fresh_db)
    client = TestClient(api_module.app)
    h = {"x-tokenage-token": token}
    assert client.post("/ingest/v1/usage", json=_report(), headers=h).status_code == 204
    assert client.post("/ingest/v1/usage", json=_report()).status_code == 401


def test_usage_ingest_is_rate_limited_per_token(api_module, fresh_db, monkeypatch):
    from src.config.app import CONFIG

    monkeypatch.setitem(CONFIG["rate_limits"], "ingest_per_minute", 1)
    token1, _ = _token(fresh_db)
    token2, _ = _token(fresh_db, email="b@example.com")
    client = TestClient(api_module.app)
    post = lambda t: client.post(  # noqa: E731
        "/ingest/v1/usage", json=_report(), headers={"x-tokenage-token": t}
    )

    assert post(token1).status_code == 204
    limited = post(token1)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert post(token2).status_code == 204  # other tokens unaffected


def test_redelivered_report_does_not_double_count_tool_calls(api_module, fresh_db):
    token, _ = _token(fresh_db)
    client = TestClient(api_module.app)
    report = _report(tool_calls=[{"tool_use_id": "toolu_dup", "tool_name": "Bash"}])
    for _ in range(2):
        r = client.post(
            "/ingest/v1/usage", json=report, headers={"x-tokenage-token": token}
        )
        assert r.status_code == 204
    usage, tools = _rows(fresh_db, None)
    assert len(usage) == 2  # the report itself is not deduplicated
    assert [t.tool_use_id for t in tools] == ["toolu_dup"]
    assert tools[0].usage_id in {u.id for u in usage}
