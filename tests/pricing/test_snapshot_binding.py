"""Snapshot versioning and usage-row binding (Option A)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.pricing.models import ModelCost

TS = int(datetime(2026, 5, 19, 12, 0, tzinfo=UTC).timestamp() * 1_000_000)


def _snapshots():
    from src.pricing import snapshots

    return snapshots


def _insert(db_path, *, cost, source="litellm", date="2026-05-19"):
    return _snapshots().ensure_price_snapshot(
        date=date,
        model="test-model",
        source=source,
        cost=cost,
        db_path=db_path,
    )


def test_ensure_is_content_addressed(fresh_db):
    db = fresh_db.db_path
    first = _insert(db, cost=ModelCost(2.0, 6.0, 0.5, 3.0))
    same = _insert(db, cost=ModelCost(2.0, 6.0, 0.5, 3.0))
    changed = _insert(db, cost=ModelCost(9.0, 9.0, 9.0))

    assert first == same  # identical rates dedup to one row
    assert changed != first  # a changed rate is a new version


def test_bound_row_uses_its_own_snapshot_not_the_newest(fresh_db):
    db = fresh_db.db_path
    bound = _insert(db, cost=ModelCost(2.0, 6.0, 0.5, 3.0), source="litellm")
    # A newer snapshot for the same day/model/source must not affect the bound row.
    _insert(db, cost=ModelCost(10.0, 20.0, 5.0), source="yaml")

    row = {
        "ts": TS,
        "provider": "test-provider",
        "model": "test-model",
        "prompt_tokens": 1000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "price_snapshot_id": bound,
    }
    out = _snapshots().enrich_rows([row], db_path=db)[0]

    assert out["cost_estimated"] is False
    # normal = 1000 * 2.0 / 1e6, from the bound rates not the newer 10.0.
    assert out["normal_input_cost_usd"] == 0.002
    assert out["pricing"]["snapshot_id"] == bound
    assert out["pricing"]["estimated"] is False
    assert out["pricing"]["source"] == "litellm"


def test_legacy_row_is_marked_estimated(fresh_db):
    db = fresh_db.db_path
    _insert(db, cost=ModelCost(2.0, 6.0, 0.5, 3.0))

    row = {
        "ts": TS,
        "provider": "test-provider",
        "model": "test-model",
        "prompt_tokens": 1000,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "price_snapshot_id": None,
    }
    out = _snapshots().enrich_rows([row], db_path=db)[0]

    assert out["cost_estimated"] is True
    assert out["normal_input_cost_usd"] == 0.002


def test_unpriceable_row_is_estimated_and_zero(fresh_db):
    row = {
        "ts": TS,
        "provider": "nope",
        "model": "unknown-model",
        "prompt_tokens": 10,
        "cached_tokens": 0,
        "cache_creation_tokens": 0,
        "price_snapshot_id": None,
    }
    out = _snapshots().enrich_rows([row], db_path=fresh_db.db_path)[0]

    assert out["cost_estimated"] is True
    assert out["normal_input_cost_usd"] == 0.0
