"""Tests for per-day price snapshots and read-time cost splits."""

from dataclasses import asdict

from src.pricing.models import ModelCost, ModelTier

# 2026-05-19T00:00:00Z in microseconds (matches repo ts convention).
TS_2026_05_18 = 1779148800000000

COST = ModelCost(input=2.0, output=6.0, cache_read=0.5, cache_write=3.0)


def _snapshots_module():
    from src.pricing import snapshots

    return snapshots


def _insert_snapshot(
    db_path,
    *,
    model="test-model",
    cost=COST,
    date="2026-05-19",
    source="yaml",
):
    _snapshots_module().ensure_price_snapshot(
        date=date,
        model=model,
        source=source,
        cost=cost,
        db_path=db_path,
    )


def _split(row, db_path):
    """Input-cost split for one row, via the production enrich_rows path."""
    enriched = _snapshots_module().enrich_rows([row], db_path=db_path)[0]
    return {
        key: enriched[key]
        for key in (
            "normal_input_cost_usd",
            "cache_read_cost_usd",
            "cache_write_cost_usd",
        )
    }


def test_serialize_parse_roundtrip_with_tiers():
    snapshots = _snapshots_module()
    cost = ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        cache_write=3.0,
        tiers=(
            ModelTier(
                min_tokens=0,
                max_tokens=256000,
                input=0.4,
                output=1.6,
                cache_read=0.08,
                cache_write=3.0,
            ),
            ModelTier(
                min_tokens=256000,
                max_tokens=None,
                input=1.2,
                output=4.8,
                cache_read=0.24,
                cache_write=7.5,
            ),
        ),
    )
    payload = snapshots.serialize_rates(cost)
    parsed_cost = snapshots.parse_rates(payload)

    # asdict comparison: isolated_home tests reload src.pricing modules, so a
    # top-level ModelCost import can be a different class instance than the
    # one parse_rates constructs.
    assert asdict(parsed_cost) == asdict(cost)


def test_serialize_parse_handles_missing_cache_write():
    snapshots = _snapshots_module()
    payload = snapshots.serialize_rates(
        ModelCost(input=2.0, output=6.0, cache_read=0.5)
    )
    assert snapshots.parse_rates(payload).cache_write is None


def test_ensure_snapshot_is_idempotent(fresh_db):
    snapshots = _snapshots_module()
    db_path = fresh_db.db_path

    _insert_snapshot(db_path)
    _insert_snapshot(db_path)

    stored = snapshots.get_price_snapshot(
        date="2026-05-19",
        model="test-model",
        db_path=db_path,
    )
    assert stored is not None
    cost, _source = stored
    assert asdict(cost) == asdict(COST)


def test_get_snapshot_returns_none_when_missing(fresh_db):
    snapshots = _snapshots_module()
    assert (
        snapshots.get_price_snapshot(
            date="2026-05-19",
            model="test-model",
            db_path=fresh_db.db_path,
        )
        is None
    )


def test_get_snapshot_prefers_latest_when_sources_change_mid_day(fresh_db):
    snapshots = _snapshots_module()
    db_path = fresh_db.db_path

    _insert_snapshot(db_path, source="litellm")
    _insert_snapshot(
        db_path,
        source="yaml",
        cost=ModelCost(input=5.0, output=10.0, cache_read=1.0, cache_write=6.0),
    )

    snapshot = snapshots.get_price_snapshot(
        date="2026-05-19",
        model="test-model",
        db_path=db_path,
    )
    assert snapshot is not None
    cost, _source = snapshot
    assert asdict(cost) == asdict(
        ModelCost(input=5.0, output=10.0, cache_read=1.0, cache_write=6.0)
    )


def test_split_row_uses_snapshot_prices(fresh_db):
    db_path = fresh_db.db_path
    _insert_snapshot(db_path)

    row = {
        "ts": TS_2026_05_18,
        "provider": "test-provider",
        "model": "test-model",
        "prompt_tokens": 1000,
        "cached_tokens": 200,
        "cache_creation_tokens": 100,
        "completion_tokens": 500,
    }

    # normal: 800*2/1e6, cache_read: 200*0.5/1e6, cache_write: 100*3/1e6
    assert _split(row, db_path) == {
        "normal_input_cost_usd": 0.0016,
        "cache_read_cost_usd": 0.0001,
        "cache_write_cost_usd": 0.0003,
    }


def test_split_row_selects_tier_from_snapshot(fresh_db):
    db_path = fresh_db.db_path
    tiered = ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        tiers=(
            ModelTier(
                min_tokens=0,
                max_tokens=256000,
                input=0.4,
                output=1.6,
                cache_read=0.08,
            ),
            ModelTier(
                min_tokens=256000,
                max_tokens=1000000,
                input=1.2,
                output=4.8,
                cache_read=0.24,
            ),
        ),
    )
    _insert_snapshot(db_path, cost=tiered)

    row = {
        "ts": TS_2026_05_18,
        "provider": "test-provider",
        "model": "test-model",
        "prompt_tokens": 300000,
        "cached_tokens": 100000,
        "cache_creation_tokens": 0,
    }

    # second tier: normal 200000*1.2/1e6, cache_read 100000*0.24/1e6
    split = _split(row, db_path)
    assert split["normal_input_cost_usd"] == 0.24
    assert split["cache_read_cost_usd"] == 0.024


def test_split_row_falls_back_to_current_config(costs_module, config_module, fresh_db):
    db_path = fresh_db.db_path

    original_model_costs = costs_module.MODEL_COSTS.copy()
    costs_module.MODEL_COSTS.clear()
    costs_module.MODEL_COSTS["fallback-model"] = ModelCost(
        input=2.0, output=6.0, cache_read=0.5, cache_write=3.0
    )
    try:
        row = {
            "ts": TS_2026_05_18,
            "provider": "test-provider-without-override",
            "model": "fallback-model",
            "prompt_tokens": 1000,
            "cached_tokens": 200,
            "cache_creation_tokens": 100,
        }
        split = _split(row, db_path)
        assert split == {
            "normal_input_cost_usd": 0.0016,
            "cache_read_cost_usd": 0.0001,
            "cache_write_cost_usd": 0.0003,
        }
    finally:
        costs_module.MODEL_COSTS.clear()
        costs_module.MODEL_COSTS.update(original_model_costs)


def test_split_row_returns_zeros_without_resolution(
    costs_module, config_module, fresh_db
):
    db_path = fresh_db.db_path

    original_model_costs = costs_module.MODEL_COSTS.copy()
    costs_module.MODEL_COSTS.clear()
    try:
        row = {
            "ts": TS_2026_05_18,
            "provider": "no-such-provider",
            "model": "no-such-model",
            "prompt_tokens": 1000,
            "cached_tokens": 200,
        }
        assert _split(row, db_path) == {
            "normal_input_cost_usd": 0.0,
            "cache_read_cost_usd": 0.0,
            "cache_write_cost_usd": 0.0,
        }
    finally:
        costs_module.MODEL_COSTS.clear()
        costs_module.MODEL_COSTS.update(original_model_costs)


def test_enrich_rows_merges_split(fresh_db):
    snapshots = _snapshots_module()
    db_path = fresh_db.db_path
    _insert_snapshot(db_path)

    rows = [
        {
            "ts": TS_2026_05_18,
            "provider": "test-provider",
            "model": "test-model",
            "prompt_tokens": 1000,
            "cached_tokens": 200,
            "cache_creation_tokens": 100,
        }
    ]
    enriched = snapshots.enrich_rows(rows, db_path=db_path)
    assert enriched[0]["normal_input_cost_usd"] == 0.0016
    assert enriched[0]["cache_read_cost_usd"] == 0.0001
    assert enriched[0]["cache_write_cost_usd"] == 0.0003


def test_enrich_rows_includes_pricing_detail(fresh_db):
    snapshots = _snapshots_module()
    db_path = fresh_db.db_path
    tiered = ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        tiers=(
            ModelTier(0, 256000, 0.4, 1.6, 0.08),
            ModelTier(256000, None, 1.2, 4.8, 0.24),
        ),
    )
    _insert_snapshot(db_path, cost=tiered, source="litellm")

    row = {
        "ts": TS_2026_05_18,
        "provider": "test-provider",
        "model": "test-model",
        "prompt_tokens": 300000,
        "cached_tokens": 100000,
        "cache_creation_tokens": 0,
        "price_snapshot_id": None,
    }
    pricing = snapshots.enrich_rows([row], db_path=db_path)[0]["pricing"]

    assert pricing["source"] == "litellm"
    # Second tier selected (300k input tokens).
    assert pricing["input"] == 1.2
    assert pricing["output"] == 4.8
    assert pricing["cache_read"] == 0.24
    assert pricing["tier"] == {"min_tokens": 256000, "max_tokens": None}
    assert pricing["estimated"] is True


def test_enrich_rows_pricing_is_none_when_unpriceable(fresh_db):
    snapshots = _snapshots_module()
    row = {
        "ts": TS_2026_05_18,
        "provider": "nope",
        "model": "unknown-model",
        "prompt_tokens": 10,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "price_snapshot_id": None,
    }
    assert snapshots.enrich_rows([row], db_path=fresh_db.db_path)[0]["pricing"] is None


def test_ensure_price_snapshot_caches_ids_per_engine(fresh_db):
    from sqlalchemy import event

    from src.database import get_engine

    snapshots = _snapshots_module()
    engine = get_engine(fresh_db.db_path)
    statements = []
    event.listen(
        engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    kwargs = dict(
        date="2026-05-19",
        model="test-model",
        source="yaml",
        db_path=fresh_db.db_path,
    )

    first = snapshots.ensure_price_snapshot(cost=COST, **kwargs)
    issued = len(statements)
    assert issued > 0
    assert snapshots.ensure_price_snapshot(cost=COST, **kwargs) == first
    assert len(statements) == issued  # second call is served from the cache

    other = ModelCost(input=9.0, output=9.0, cache_read=9.0)
    assert snapshots.ensure_price_snapshot(cost=other, **kwargs) != first
