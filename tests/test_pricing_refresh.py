"""Live pricing refresh: fake source/clock, real usage and rollup updates."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import ingest_write


@pytest.fixture
def refresh_env(fresh_db, monkeypatch, tmp_path):
    from src.config.app import CONFIG
    from src.pricing import costs, refresh
    from src.pricing.maps import refresh_pricing_maps
    from src.pricing.models import ModelCost
    from src.pricing.sources import base
    from src.pricing.sources.base import FetchedSource, SourceEntry

    clock = SimpleNamespace(mono=0.0, wall=1780000000.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.mono, time=lambda: clock.wall)
    monkeypatch.setattr(refresh, "time", fake_time)
    monkeypatch.setattr(costs, "time", fake_time)
    monkeypatch.setattr(base, "time", fake_time)
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path))
    monkeypatch.setitem(CONFIG, "pricing", {"auto_fetch": True})
    monkeypatch.setitem(CONFIG, "models", {})
    monkeypatch.setattr(refresh, "_last_download_attempt", None)
    monkeypatch.setattr(refresh, "_last_manual_download_attempt", None)
    costs.MISS_TIMES.clear()
    refresh_pricing_maps(CONFIG, [])
    state = SimpleNamespace(fresh=True, generation=0, calls=[], prices={})
    original_cache_state = refresh._cache_state
    monkeypatch.setattr(
        refresh, "_cache_state", lambda _: (state.generation, not state.fresh)
    )

    def fetch(config, *, force=False):
        state.calls.append(force)
        state.fresh = True
        cache = base.cache_path("litellm")
        if cache.exists():
            os.utime(cache, (clock.wall, clock.wall))
        return [
            FetchedSource(
                "litellm", 0, tuple(SourceEntry(k, v) for k, v in state.prices.items())
            )
        ]

    monkeypatch.setattr(refresh, "fetch_sources", fetch)
    env = SimpleNamespace(
        refresh=refresh,
        costs=costs,
        clock=clock,
        state=state,
        db=fresh_db.database_module,
        path=fresh_db.db_path,
        config=CONFIG,
        price=ModelCost(input=2, output=3, cache_read=0),
        original_cache_state=original_cache_state,
    )
    yield env
    costs.MISS_TIMES.clear()
    refresh_pricing_maps(CONFIG, [])


def record(env, model="new-model", **extra):
    from src.recorder import record_usage

    return ingest_write(
        record_usage,
        user_id="u0",
        device_id="test-device",
        ts=int(env.clock.wall * 1_000_000),
        provider="p",
        model=model,
        session_id="s1",
        endpoint="otlp",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        total_tokens=2_000_000,
        status=200,
        db_path=env.path,
        **extra,
    )


def totals(env):
    from src.database.models import SessionRecord, Usage, UsageHourly

    with Session(env.db.get_engine()) as session:
        usage = session.scalars(select(Usage).order_by(Usage.ts.desc())).first()
        hourly = sum(
            float(x) for x in session.scalars(select(UsageHourly.total_cost_usd))
        )
        sessions = sum(
            float(x) for x in session.scalars(select(SessionRecord.total_cost_usd))
        )
        return usage.price_snapshot_id, float(usage.total_cost_usd), hourly, sessions


def test_ttl_expiry_fetches_once_and_swaps_maps(refresh_env, monkeypatch):
    e = refresh_env
    from src.pricing.sources.base import cache_path

    monkeypatch.setattr(e.refresh, "_cache_state", e.original_cache_state)
    e.config["pricing"]["sources"] = [
        {"name": "catalog", "type": "litellm", "ttl_seconds": 60}
    ]
    cache = cache_path("litellm")
    cache.parent.mkdir(parents=True)
    cache.write_text("{}")
    os.utime(cache, (e.clock.wall, e.clock.wall))
    worker = e.refresh.PricingRefresh()
    worker.tick()
    assert not e.state.calls
    e.state.prices["new-model"] = e.price
    e.clock.wall += 61
    worker.tick()
    worker.tick()
    assert e.state.calls == [False]
    assert e.costs.resolve_model_cost("new-model") == e.price


def test_stale_cache_outage_respects_force_interval(refresh_env, monkeypatch):
    e = refresh_env
    from src.pricing.maps import refresh_pricing_maps
    from src.pricing.sources import litellm, registry
    from src.pricing.sources.base import cache_path, save_cache_json

    raw = {
        "cached-model": {
            "input_cost_per_token": 0.000002,
            "output_cost_per_token": 0.000003,
        }
    }
    save_cache_json("litellm", raw)
    os.utime(cache_path("litellm"), (e.clock.wall, e.clock.wall))
    refresh_pricing_maps(e.config, registry.fetch_sources(e.config))
    e.clock.wall += 2 * 86400
    calls = []
    monkeypatch.setattr(litellm, "_fetch_litellm_json", lambda: calls.append(1))
    monkeypatch.setattr(e.refresh, "fetch_sources", registry.fetch_sources)
    monkeypatch.setattr(e.refresh, "_cache_state", e.original_cache_state)
    worker = e.refresh.PricingRefresh()
    for second in range(0, e.refresh.FORCE_INTERVAL, e.refresh.TICK_SECONDS):
        e.clock.mono = second
        worker.tick()
    assert len(calls) == 1
    assert not worker.pending_backlog
    e.clock.mono = e.refresh.FORCE_INTERVAL
    worker.tick()
    assert len(calls) == 2
    assert not worker.pending_backlog


def test_miss_entries_expire_after_24_hours(refresh_env):
    e = refresh_env
    record(e)
    e.clock.mono = e.refresh.MISS_RETENTION_SECONDS - 1
    e.refresh.PricingRefresh().tick()
    assert ("u0", "new-model") in e.costs.MISS_TIMES
    e.clock.mono += 1
    e.refresh.PricingRefresh().tick()
    assert not e.costs.MISS_TIMES
    assert not e.state.calls


@pytest.mark.parametrize("fail_once", [False, True])
def test_miss_rebound_rollups_once(refresh_env, monkeypatch, fail_once):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()
    row = record(e)
    assert row.price_snapshot_id is None
    assert ("u0", "new-model") in e.costs.MISS_TIMES
    e.state.prices["new-model"] = e.price
    if fail_once:
        original = e.db.reprice_estimated_rows

        def fail(**kwargs):
            raise RuntimeError("busy")

        monkeypatch.setattr(e.db, "reprice_estimated_rows", fail)
        worker.tick()
        assert totals(e)[0] is None
        monkeypatch.setattr(e.db, "reprice_estimated_rows", original)
    worker.tick()
    assert len(e.state.calls) == 1
    assert totals(e)[0] is not None
    assert totals(e)[1:] == (5, 5, 5)
    worker.tick()
    assert totals(e)[1:] == (5, 5, 5)
    assert not e.costs.MISS_TIMES


def test_manual_force_has_separate_floor_from_tick(refresh_env):
    e = refresh_env
    record(e)
    worker = e.refresh.PricingRefresh()
    worker.tick()
    e.clock.mono = 899
    record(e, "other")
    worker.tick()
    assert e.refresh.refresh_live_maps(e.config, force=True, manual=True)
    assert e.refresh.refresh_live_maps(e.config, force=True, manual=True)
    assert sum(e.state.calls) == 2
    e.clock.mono = 900
    worker.tick()
    assert sum(e.state.calls) == 3


def test_give_up_and_ttl_backlog_bounds(refresh_env):
    e = refresh_env
    record(e)
    e.clock.wall -= 8 * 86400
    record(e, "old-model")
    e.clock.wall += 8 * 86400
    worker = e.refresh.PricingRefresh()
    worker.tick()
    e.clock.mono = 3600
    worker.tick()
    assert e.costs.MISS_TIMES  # Retain first-seen times until the 24-hour bound.
    assert len(e.state.calls) == 1
    e.costs.resolve_pricing("new-model", int(e.clock.wall * 1_000_000), user_id="u0")
    worker.tick()
    assert len(e.state.calls) == 1
    e.state.prices.update({"new-model": e.price, "old-model": e.price})
    e.state.fresh = False
    worker.tick()
    assert totals(e)[1:] == (5, 5, 5)
    from src.database.models import Usage

    with Session(e.db.get_engine()) as session:
        old = session.scalar(select(Usage).where(Usage.model == "old-model"))
        assert old.price_snapshot_id is None


def test_auto_fetch_false_does_nothing(refresh_env, monkeypatch):
    e = refresh_env
    record(e)
    e.config["pricing"]["auto_fetch"] = False
    e.state.fresh = False
    monkeypatch.setattr(
        e.db, "reprice_estimated_rows", lambda **_: pytest.fail("reprice")
    )
    worker = e.refresh.PricingRefresh()
    worker.start()
    assert worker.worker is None
    e.costs.MISS_TIMES.clear()
    record(e, "disabled-miss")
    assert not e.costs.MISS_TIMES
    assert not e.state.calls


def test_lifespan_stops_thread(refresh_env):
    async def run():
        async with refresh_env.refresh.pricing_refresh_lifespan() as worker:
            assert worker.worker.daemon
            assert worker.worker.is_alive()
        assert worker.stop_event.is_set()
        assert not worker.worker.is_alive()

    asyncio.run(run())


@pytest.mark.parametrize("endpoint", ["bulk", "single"])
def test_manual_reprice_finds_fresh_cache_and_prices_later_ingest(
    refresh_env, monkeypatch, endpoint
):
    e = refresh_env
    import src.api as api

    row = record(e)
    worker = e.refresh.PricingRefresh()
    e.clock.wall += 3  # A distinct request beyond the OTLP dedup ceiling.
    record(e)
    e.state.prices["new-model"] = e.price
    monkeypatch.setattr(api, "_request_user_id", lambda _: "u0")
    if endpoint == "bulk":
        result = asyncio.run(api.reprice_estimated_usage(None, model="new-model"))
        assert result["repriced"] == 2
    else:
        result = asyncio.run(api.recalculate_usage_cost_route(None, row.id))
        assert not result["skipped"]
        # The forced-fetch floor must not defer repair of the other missed row.
        worker.tick()
        assert totals(e)[0] is not None
    assert totals(e)[2:] == (10, 10)
    assert e.state.calls[0] is True
    e.clock.wall += 3
    later = record(e)
    assert later.price_snapshot_id is not None
    assert float(later.total_cost_usd) == 5


def test_concurrent_postgres_reprice_once(pg_db, monkeypatch):
    import src.database as db
    from src.config.app import CONFIG
    from src.pricing.maps import refresh_pricing_maps
    from tests.test_tenancy_pg import _seed_users

    monkeypatch.setitem(CONFIG, "db", {"url": pg_db})
    _seed_users(db.get_engine(), "u0")
    e = SimpleNamespace(db=db, path=pg_db, clock=SimpleNamespace(wall=1780000000))
    refresh_pricing_maps({"models": {}}, [])
    record(e)
    refresh_pricing_maps(
        {"models": {"new-model": {"cost": {"input": 2, "output": 3}}}}, []
    )
    gate = threading.Barrier(2)

    def reprice():
        gate.wait(timeout=5)
        return db.reprice_estimated_rows(user_id="u0", model="new-model")

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: reprice(), range(2)))
    assert sum(r["repriced"] for r in results) == 1
    assert totals(e)[1:] == (5, 5, 5)


def test_startup_backlog_repairs_lost_miss(refresh_env):
    e = refresh_env
    from src.pricing.maps import refresh_pricing_maps

    record(e)
    e.costs.MISS_TIMES.clear()
    refresh_pricing_maps(
        {"models": {"new-model": {"cost": {"input": 2, "output": 3}}}}, []
    )
    e.refresh.PricingRefresh().tick()
    assert not e.state.calls
    assert totals(e)[0] is not None
    assert totals(e)[1:] == (5, 5, 5)


def test_forced_fetch_and_sibling_rewrite_do_not_arm_backlog(refresh_env, monkeypatch):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()  # Consume the startup pass.
    from src.database import usage

    monkeypatch.setattr(
        usage, "get_engine", lambda *_: pytest.fail("unexpected backlog")
    )
    e.costs.MISS_TIMES[("u0", "missing")] = e.clock.mono
    worker.tick()
    assert e.state.calls == [True]
    e.refresh.refresh_live_maps(e.config, force=True)
    e.state.generation += 1  # Another process rewrote the cache.
    e.costs.MISS_TIMES.clear()
    worker.tick()
    assert e.state.calls == [True, False, False]
    assert not worker.pending_backlog


def test_resolved_pair_dropped_after_successful_empty_reprice(refresh_env, monkeypatch):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()
    record(e)
    e.state.prices["new-model"] = e.price
    original = e.db.reprice_estimated_rows
    monkeypatch.setattr(
        e.db, "reprice_estimated_rows", lambda **_: {"candidates": 0, "repriced": 0}
    )
    worker.tick()
    assert not e.costs.MISS_TIMES
    monkeypatch.setattr(e.db, "reprice_estimated_rows", original)
    e.clock.mono = 900
    worker.tick()
    assert e.state.calls == [True]
    assert totals(e)[1:] == (0, 0, 0)
    e.state.fresh = False
    worker.tick()  # The TTL backlog recovers a row that was still in flight.
    assert totals(e)[1:] == (5, 5, 5)


def test_resolved_pair_retained_when_all_candidates_skipped(refresh_env, monkeypatch):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()
    record(e)
    e.state.prices["new-model"] = e.price
    original = e.db.reprice_estimated_rows
    monkeypatch.setattr(
        e.db, "reprice_estimated_rows", lambda **_: {"candidates": 1, "repriced": 0}
    )
    worker.tick()
    assert ("u0", "new-model") in e.costs.MISS_TIMES
    monkeypatch.setattr(e.db, "reprice_estimated_rows", original)
    worker.tick()
    assert not e.costs.MISS_TIMES
    assert totals(e)[1:] == (5, 5, 5)


def test_failed_backlog_retries_after_15_minutes(refresh_env, monkeypatch, caplog):
    e = refresh_env
    from src.database import usage

    attempts = []

    def fail(*_):
        attempts.append(1)
        raise RuntimeError("busy")

    monkeypatch.setattr(usage, "get_engine", fail)
    worker = e.refresh.PricingRefresh()
    worker.tick()
    e.clock.mono += 60
    worker.tick()
    assert attempts == [1]
    assert worker.pending_backlog
    assert "Pricing backlog reprice failed" in caplog.text
    e.clock.mono = 899
    worker.tick()
    assert attempts == [1]
    e.clock.mono = 900
    worker.tick()
    assert attempts == [1, 1]


def test_ttl_refresh_downloads_only_due_source(refresh_env, monkeypatch):
    e = refresh_env
    from src.pricing.sources import litellm, openrouter, registry
    from src.pricing.sources.base import cache_path, save_cache_json

    e.config["pricing"]["sources"] = [
        {"type": "litellm", "ttl_seconds": 86400},
        {"type": "openrouter", "ttl_seconds": 60},
    ]
    for name in ("litellm", "openrouter"):
        save_cache_json(
            name,
            {
                "cached-model": {
                    "input_cost_per_token": 0.000002,
                    "output_cost_per_token": 0.000003,
                }
            }
            if name == "litellm"
            else {"data": []},
        )
        os.utime(cache_path(name), (e.clock.wall, e.clock.wall))
    e.clock.wall += 61
    calls = []
    monkeypatch.setattr(
        litellm, "_fetch_litellm_json", lambda: pytest.fail("fresh source downloaded")
    )
    monkeypatch.setattr(
        openrouter, "fetch_json", lambda *a, **k: calls.append(1) or {"data": []}
    )
    monkeypatch.setattr(e.refresh, "fetch_sources", registry.fetch_sources)
    monkeypatch.setattr(e.refresh, "_cache_state", e.original_cache_state)
    e.refresh.PricingRefresh().tick()
    assert calls == [1]


@pytest.mark.parametrize("first_force", [False, True])
def test_download_clock_shared_between_ttl_and_force(refresh_env, first_force):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    e.state.fresh = first_force
    e.refresh.refresh_live_maps(e.config, force=first_force)
    e.state.fresh = False
    e.clock.mono = 60
    worker.tick()
    e.refresh.refresh_live_maps(e.config, force=True)
    assert e.state.calls == [first_force]
    e.clock.mono = 900
    worker.tick()
    assert e.state.calls == [first_force, False]


def test_already_resolved_pair_repairs_without_download(refresh_env):
    e = refresh_env
    from src.pricing.maps import refresh_pricing_maps

    record(e)
    refresh_pricing_maps(
        {"models": {"new-model": {"cost": {"input": 2, "output": 3}}}}, []
    )
    worker = e.refresh.PricingRefresh()
    worker.tick()
    for second in range(60, 3600, 60):
        e.clock.mono = second
        worker.tick()
    assert not e.state.calls
    assert not e.costs.MISS_TIMES
    assert totals(e)[1:] == (5, 5, 5)


def test_throttled_force_rebuilds_from_fresh_cache(refresh_env, monkeypatch):
    e = refresh_env
    from src.pricing.sources import litellm, registry
    from src.pricing.sources.base import cache_path, save_cache_json

    save_cache_json("litellm", {"cached-model": {"input_cost_per_token": 0.000002}})
    os.utime(cache_path("litellm"), (e.clock.wall, e.clock.wall))
    monkeypatch.setattr(e.refresh, "_cache_state", e.original_cache_state)
    monkeypatch.setattr(e.refresh, "fetch_sources", registry.fetch_sources)
    monkeypatch.setattr(litellm, "_fetch_litellm_json", lambda: pytest.fail("download"))
    e.refresh._last_download_attempt = e.clock.mono

    assert e.refresh.refresh_live_maps(e.config, force=True)
    assert e.costs.resolve_model_cost("cached-model").input == 2


def test_user_override_repairs_miss_without_forced_download(refresh_env, monkeypatch):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()
    record(e)
    record(e, "private-model")
    e.db.upsert_model_price(user_id="u0", model="vendor/new-model", input=2, output=3)
    e.db.upsert_model_price(user_id="u0", model="private-model", input=2, output=3)
    original = e.db.load_model_price_overrides
    calls = []

    def load(**kwargs):
        calls.append(kwargs["user_id"])
        return original(**kwargs)

    monkeypatch.setattr(e.db, "load_model_price_overrides", load)
    worker.tick()
    assert calls == ["u0"]
    assert not e.state.calls
    assert not e.costs.MISS_TIMES
    assert totals(e)[1:] == (5, 10, 10)


def test_override_load_failure_does_not_break_tick(refresh_env, monkeypatch, caplog):
    e = refresh_env
    worker = e.refresh.PricingRefresh()
    worker.tick()
    record(e)
    calls = []

    def fail(**_):
        calls.append(1)
        raise RuntimeError("private detail")

    monkeypatch.setattr(e.db, "load_model_price_overrides", fail)
    worker.tick()
    assert calls == [1]
    assert e.state.calls == [True]
    assert "Pricing overrides load failed: RuntimeError" in caplog.text
    assert "private detail" not in caplog.text


def test_concurrent_sqlite_reprice_once(refresh_env, monkeypatch):
    e = refresh_env
    from src.database import usage
    from src.pricing.maps import refresh_pricing_maps

    if e.db.get_engine().dialect.name != "sqlite":
        pytest.skip("SQLite-specific writer serialization")
    record(e)
    refresh_pricing_maps(
        {"models": {"new-model": {"cost": {"input": 2, "output": 3}}}}, []
    )
    scanned = threading.Barrier(2)
    original_scan = usage.fetch_estimated_usage_rows
    original_reprice = usage._reprice_usage_rows
    both_loaded = threading.Event()
    loaded_count = 0
    count_lock = threading.Lock()
    connections = set()

    def scan(**kwargs):
        ids = original_scan(**kwargs)
        scanned.wait(timeout=5)  # Both workers must scan the same unbound row.
        return ids

    def reprice(session, rows, **kwargs):
        nonlocal loaded_count
        with count_lock:
            connections.add(id(session.connection().connection.dbapi_connection))
            loaded_count += bool(rows)
            if loaded_count == 2:
                both_loaded.set()
        # Without the fix, both readers reach this point before either writes.
        # With BEGIN IMMEDIATE, the second SELECT waits for the first commit.
        if rows:
            both_loaded.wait(timeout=0.5)
        return original_reprice(session, rows, **kwargs)

    monkeypatch.setattr(usage, "fetch_estimated_usage_rows", scan)
    monkeypatch.setattr(usage, "_reprice_usage_rows", reprice)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: e.db.reprice_estimated_rows(user_id="u0"), range(2))
        )
    assert len(connections) == 2
    assert totals(e)[1:] == (5, 5, 5)
    assert sum(r["repriced"] for r in results) == 1


def test_zero_cost_reprice_rechecks_cost_after_scan(refresh_env, monkeypatch):
    e = refresh_env
    from src.database import usage
    from src.database.models import Usage
    from src.pricing.maps import refresh_pricing_maps

    row = record(e)
    refresh_pricing_maps(
        {"models": {"new-model": {"cost": {"input": 2, "output": 3}}}}, []
    )
    original_scan = usage.fetch_estimated_usage_rows

    def scan(**kwargs):
        ids = original_scan(**kwargs)
        with Session(e.db.get_engine()) as session:
            session.get(Usage, row.id).total_cost_usd = 7
            session.commit()
        return ids

    monkeypatch.setattr(usage, "fetch_estimated_usage_rows", scan)
    result = e.db.reprice_estimated_rows(user_id="u0", zero_cost_only=True)
    assert result == {"candidates": 1, "repriced": 0, "skipped": 1}
    assert totals(e)[0:2] == (None, 7)


def test_persistently_failed_user_does_not_block_backlog(refresh_env, monkeypatch):
    e = refresh_env
    from src.database import usage

    monkeypatch.setattr(usage, "users_with_unpriced_rows", lambda **_: ["u0", "u1"])
    attempted = []

    def reprice(*, user_id, **_):
        attempted.append(user_id)
        if user_id == "u0":
            raise RuntimeError("busy")
        return {"repriced": 0}

    monkeypatch.setattr(e.db, "reprice_estimated_rows", reprice)
    worker = e.refresh.PricingRefresh()
    worker.tick()
    assert worker.pending_backlog
    e.clock.mono = 899
    worker.tick()
    assert attempted == ["u0", "u1"]
    e.clock.mono = 900
    worker.tick()
    assert attempted == ["u0", "u1", "u0", "u1"]
    assert worker.pending_backlog
