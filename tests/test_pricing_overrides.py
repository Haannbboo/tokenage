"""Per-user model price overrides: resolution, record time, reprice, routes."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.pricing.models import ModelCost

TS = 1779148800000000  # 2026-05-19 00:00 UTC, microseconds
SHARED = {"shared-model": ModelCost(input=1.0, output=2.0, cache_read=0.1)}


@pytest.fixture
def users(fresh_db):
    from src.auth.tokens import mint_token

    tokens, ids = {}, {}
    for name in ("a", "b"):
        token, row = mint_token(
            f"{name}@example.com", kind="cli", db_path=fresh_db.db_path
        )
        tokens[name], ids[name] = token, row.id
    return tokens, ids


@pytest.fixture
def shared_prices(monkeypatch):
    from src.pricing import costs

    for key, cost in SHARED.items():
        monkeypatch.setitem(costs.MODEL_COSTS, key, cost)
        monkeypatch.setitem(costs.MODEL_COST_SOURCES, key, "server")
        monkeypatch.setitem(costs.MODEL_SEGMENT_COSTS, key, (key, cost))


def _record(user_id, model, db_path, **extra):
    from src.recorder import record_usage

    return record_usage(
        device_id="test-device",
        ts=TS,
        provider="p",
        model=model,
        endpoint="otlp",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        total_tokens=2_000_000,
        status=200,
        db_path=db_path,
        user_id=user_id,
        **extra,
    )


def _snapshot_source(db, snapshot_id):
    from src.database.models import PriceSnapshot

    with Session(db.get_engine()) as session:
        return session.scalar(
            select(PriceSnapshot.source).where(PriceSnapshot.id == snapshot_id)
        )


def test_resolve_pricing_prefers_override_with_segment_match():
    from src.pricing.costs import resolve_pricing

    overrides = {"vendor/shared-model": ModelCost(input=9.0, output=9.0, cache_read=0)}
    resolved = resolve_pricing("shared-model", TS, SHARED, {}, overrides)
    assert resolved.source == "user"
    assert resolved.cost.input == 9.0
    no_override = resolve_pricing(
        "shared-model", TS, SHARED, {"shared-model": "server"}, {}
    )
    assert no_override.source == "server"
    assert resolve_pricing("other", TS, SHARED, {}, overrides).match is None


def test_record_usage_override_beats_shared_and_is_per_user(
    fresh_db, users, shared_prices
):
    db = fresh_db.database_module
    _, ids = users
    db.upsert_model_price(
        user_id=ids["a"], model="Shared-Model", input=5.0, output=7.0, cache_read=None
    )

    a = _record(ids["a"], "shared-model", fresh_db.db_path)
    b = _record(ids["b"], "shared-model", fresh_db.db_path)

    assert float(a.total_cost_usd) == pytest.approx(12.0)
    assert _snapshot_source(db, a.price_snapshot_id) == "user"
    assert float(b.total_cost_usd) == pytest.approx(3.0)
    assert _snapshot_source(db, b.price_snapshot_id) == "server"


def test_record_usage_override_prices_unknown_model(fresh_db, users):
    db = fresh_db.database_module
    _, ids = users
    db.upsert_model_price(
        user_id=ids["a"], model="my/private-model", input=1.0, output=1.0
    )

    usage = _record(ids["a"], "private-model", fresh_db.db_path)

    assert float(usage.total_cost_usd) == pytest.approx(2.0)
    assert _snapshot_source(db, usage.price_snapshot_id) == "user"


def test_model_price_helpers_upsert_and_delete(fresh_db, users):
    db = fresh_db.database_module
    _, ids = users
    db.upsert_model_price(user_id=ids["a"], model="m", input=1.0, output=2.0)
    db.upsert_model_price(
        user_id=ids["a"], model=" M ", input=3.0, output=4.0, cache_write=0.5
    )

    rows = db.list_model_prices(user_id=ids["a"])
    assert [(r["model"], r["input"], r["cache_write"]) for r in rows] == [
        ("m", 3.0, 0.5)
    ]
    (cost,) = db.load_model_price_overrides(user_id=ids["a"]).values()
    assert (cost.input, cost.output, cost.cache_read, cost.cache_write) == (
        3.0,
        4.0,
        0.0,
        0.5,
    )
    assert db.list_model_prices(user_id=ids["b"]) == []
    assert db.delete_model_price(user_id=ids["b"], model="m") is False
    assert db.delete_model_price(user_id=ids["a"], model="M") is True
    assert db.list_model_prices(user_id=ids["a"]) == []


def _unpriced(db, user_id, row_id, model):
    db.log_usage(
        db.Usage(
            id=row_id,
            device_id="test-device",
            ts=TS,
            provider="p",
            model=model,
            endpoint="otlp",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            total_tokens=1_000_000,
            input_cost_usd=0.0,
            output_cost_usd=0.0,
            total_cost_usd=0.0,
            status=200,
            user_id=user_id,
        )
    )


def test_reprice_is_per_user_unpriced_only_and_matches_segment(
    fresh_db, users, shared_prices
):
    db = fresh_db.database_module
    _, ids = users
    db.upsert_model_price(
        user_id=ids["a"], model="vendor/private", input=4.0, output=0.0
    )
    _unpriced(db, ids["a"], "full", "vendor/private")
    _unpriced(db, ids["a"], "segment", "private")
    _unpriced(db, ids["a"], "other", "elsewhere")
    _unpriced(db, ids["b"], "b-row", "private")
    priced = _record(ids["a"], "private", fresh_db.db_path)

    summary = db.reprice_estimated_rows(
        user_id=ids["a"], model="Vendor/Private", db_path=fresh_db.db_path
    )

    assert summary == {"candidates": 2, "repriced": 2, "skipped": 0}
    rows = {
        r["id"]: r
        for r in db.fetch_recent_usage(limit=10, user_id=ids["a"])
        + db.fetch_recent_usage(limit=10, user_id=ids["b"])
    }
    assert rows["full"]["total_cost_usd"] == pytest.approx(4.0)
    assert rows["segment"]["total_cost_usd"] == pytest.approx(4.0)
    assert rows["other"]["price_snapshot_id"] is None
    assert rows["b-row"]["price_snapshot_id"] is None
    assert rows[priced.id]["price_snapshot_id"] == priced.price_snapshot_id


def test_recalculate_usage_cost_honors_override(fresh_db, users, shared_prices):
    db = fresh_db.database_module
    _, ids = users
    usage = _record(ids["a"], "shared-model", fresh_db.db_path)
    db.upsert_model_price(user_id=ids["a"], model="shared-model", input=0, output=1)

    result = db.recalculate_usage_cost(usage.id, user_id=ids["a"])

    assert result.new_costs["total_cost_usd"] == pytest.approx(1.0)
    assert result.pricing["source"] == "user"


@pytest.fixture
def client(api_module, users, monkeypatch):
    import src.config.app

    async def live_maps():
        from src.pricing.models import ResolvedCost

        resolved = {k: ResolvedCost(cost=c, source="server") for k, c in SHARED.items()}
        return resolved, dict(SHARED), dict.fromkeys(SHARED, "server")

    monkeypatch.setattr(api_module, "_resolve_live_cost_maps", live_maps)
    monkeypatch.setitem(
        src.config.app.CONFIG, "auth", {"provider": "google", "allowlist": []}
    )
    tokens, _ = users

    def as_user(name):
        c = TestClient(api_module.app)
        c.headers["Authorization"] = f"Bearer {tokens[name]}"
        return c

    return as_user


def test_override_routes_crud_validation_and_isolation(client):
    a, b = client("a"), client("b")

    saved = a.put("/pricing/overrides/Vendor/X", json={"input": 1, "output": 2})
    assert saved.status_code == 200, saved.text
    assert saved.json()["model"] == "vendor/x"
    assert saved.json()["cache_read"] is None

    listed = a.get("/pricing/overrides").json()
    assert [row["model"] for row in listed] == ["vendor/x"]
    assert set(listed[0]) == {
        "model",
        "input",
        "output",
        "cache_read",
        "cache_write",
        "updated_at",
    }
    assert b.get("/pricing/overrides").json() == []

    for body in (
        {"input": -1, "output": 1},
        {"input": 1},
        {"input": 1, "output": 1, "cache_read": -0.1},
        {"input": "1", "output": 1},
    ):
        assert a.put("/pricing/overrides/m", json=body).status_code == 422, body
    assert (
        a.put("/pricing/overrides/%20", json={"input": 1, "output": 1}).status_code
        == 422
    )

    assert b.delete("/pricing/overrides/vendor/x").status_code == 404
    assert a.delete("/pricing/overrides/vendor/x").status_code == 204
    assert a.delete("/pricing/overrides/vendor/x").status_code == 404


def test_override_body_rejects_non_finite(api_module):
    from pydantic import ValidationError

    # JSON can carry 1e999 (parsed as inf); the route's 422 body can't echo it,
    # so check the model directly.
    with pytest.raises(ValidationError):
        api_module.ModelPriceUpdate(input=float("inf"), output=1.0)


def _pricing(c, **params) -> dict:
    return {m["name"]: m for m in c.get("/pricing", params=params).json()["models"]}


def test_get_pricing_merges_caller_overrides(client):
    a, b = client("a"), client("b")
    a.put("/pricing/overrides/shared-model", json={"input": 9, "output": 9})
    a.put("/pricing/overrides/vendor/unknown", json={"input": 3, "output": 4})

    entries = _pricing(a)
    assert entries["shared-model"]["source"] == "user"
    assert entries["shared-model"]["input"] == 9
    assert entries["vendor/unknown"]["tiers"] == []
    assert entries["vendor/unknown"]["time_rates"] == []
    assert _pricing(b)["shared-model"]["source"] == "server"
    assert "vendor/unknown" not in _pricing(b)

    single = a.get("/pricing/unknown").json()
    assert single["model"] == "vendor/unknown"
    assert single["source"] == "user"
    assert b.get("/pricing/unknown").json()["resolved"] is False
    assert b.get("/pricing/shared-model").json()["source"] == "server"


def test_reprice_route_is_scoped_to_caller(client, fresh_db, users):
    db = fresh_db.database_module
    _, ids = users
    _unpriced(db, ids["a"], "a-row", "shared-model")
    _unpriced(db, ids["b"], "b-row", "shared-model")

    response = client("a").post("/usage/reprice")

    assert response.json() == {"candidates": 1, "repriced": 1, "skipped": 0}
    assert (
        db.fetch_recent_usage(limit=1, user_id=ids["b"])[0]["price_snapshot_id"] is None
    )


def test_unbound_rows_never_borrow_another_users_override_snapshot(
    fresh_db, users, shared_prices
):
    from src.pricing.snapshots import enrich_rows, get_price_snapshot

    db = fresh_db.database_module
    _, ids = users
    db.upsert_model_price(user_id=ids["a"], model="shared-model", input=50, output=70)
    a = _record(ids["a"], "shared-model", fresh_db.db_path)
    assert _snapshot_source(db, a.price_snapshot_id) == "user"
    _unpriced(db, ids["b"], "b-row", "shared-model")

    (row,) = enrich_rows(db.fetch_recent_usage(limit=10, user_id=ids["b"]))
    assert row["pricing"]["source"] != "user"
    assert row["pricing"]["input"] == pytest.approx(1.0)
    assert get_price_snapshot(date="2026-05-19", model="shared-model") is None


def test_reprice_loads_overrides_once(fresh_db, users, shared_prices, monkeypatch):
    from src.database import usage as usage_module

    db = fresh_db.database_module
    _, ids = users
    for i in range(3):
        _unpriced(db, ids["a"], f"row-{i}", "shared-model")
    calls = []
    real = usage_module.load_model_price_overrides
    monkeypatch.setattr(
        usage_module,
        "load_model_price_overrides",
        lambda **kw: calls.append(kw) or real(**kw),
    )

    summary = db.reprice_estimated_rows(user_id=ids["a"], db_path=fresh_db.db_path)

    assert summary["repriced"] == 3
    assert len(calls) == 1


def test_get_pricing_overlays_segment_matched_overrides(client):
    a = client("a")
    a.put("/pricing/overrides/proxy/zen/shared-model", json={"input": 6, "output": 8})

    entries = _pricing(a)
    assert entries["shared-model"]["source"] == "user"
    assert entries["shared-model"]["input"] == 6
    assert entries["proxy/zen/shared-model"]["source"] == "user"
    assert _pricing(client("b"))["shared-model"]["source"] == "server"


def test_get_pricing_filters_and_pages(client):
    a = client("a")
    a.put("/pricing/overrides/shared-model", json={"input": 9, "output": 9})
    a.put("/pricing/overrides/vendor/zzz", json={"input": 1, "output": 1})

    body = a.get("/pricing", params={"source": "user"}).json()
    assert [m["name"] for m in body["models"]] == ["shared-model", "vendor/zzz"]
    assert body["total"] == 2
    assert body["counts"]["user"] == 2

    page = a.get("/pricing", params={"source": "user", "limit": 1, "offset": 1}).json()
    assert [m["name"] for m in page["models"]] == ["vendor/zzz"]
    assert page["total"] == 2

    found = a.get("/pricing", params={"q": "ZZZ"}).json()
    assert [m["name"] for m in found["models"]] == ["vendor/zzz"]
    assert found["counts"] == {"user": 1}

    # Custom rows sort first; unfiltered lists every source.
    everything = a.get("/pricing").json()["models"]
    assert everything[0]["source"] == "user"
    assert _pricing(client("b"), source="user") == {}
