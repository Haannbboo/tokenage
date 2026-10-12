"""One-off device attribution backfill: dry-run vs apply, totals, safety."""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

TS = 1776384000000000  # 2026-04-17T00:00:00Z
DAY = 86_400_000_000


@pytest.fixture
def env(fresh_db):
    from src.auth.tokens import get_or_create_user
    from src.database import get_engine
    from src.database.models import Device

    script = importlib.import_module("src.scripts.backfill_device_attribution")
    with get_engine(fresh_db.db_path).begin() as conn:  # session-shared DB
        conn.execute(text(f"DROP TABLE IF EXISTS {script.BACKUP_TABLE}"))
    user = get_or_create_user("a@example.com", fresh_db.db_path)
    engine = get_engine(fresh_db.db_path)
    ids = {}
    with Session(engine) as s:
        for name in ("unraid-vm", "Hanbos-MacBook-Air.local"):
            device = Device(
                user_id=user.id,
                installation_hash=f"hash-{name}",
                device_name=name,
                created_at=1,
            )
            s.add(device)
            s.flush()
            ids[name] = device.id
        s.commit()
    # Historical rows go through the live path with a placeholder device, then
    # are reset to Unknown with the IP they would have had.
    db = fresh_db.database_module
    rows = [
        ("100.71.211.51", TS, 0.1),
        ("100.71.211.51", TS + 1, 0.2),
        ("127.0.0.1", TS + DAY, 0.3),
        ("100.88.94.9", TS + DAY + 1, 0.4),
        ("9.9.9.9", TS + DAY + 2, 0.5),
        (None, TS + 2 * DAY, 0.6),
    ]
    for ip, ts, cost in rows:
        db.log_usage(
            db.Usage(
                ts=ts,
                provider="p",
                model="m",
                client_source="claude-code",
                endpoint="/v1/messages",
                prompt_tokens=10,
                total_tokens=15,
                total_cost_usd=cost,
                status=200,
                user_id=user.id,
                device_id="placeholder",
                client_ip=ip,
            ),
            db_path=fresh_db.db_path,
        )
    with engine.begin() as conn:
        conn.execute(text("UPDATE usage SET device_id = NULL"))
        conn.execute(text("UPDATE usage_daily SET device_id = NULL"))
    return script, engine, ids, user


def _state(engine):
    with engine.connect() as conn:
        usage = dict(
            conn.execute(
                text("SELECT client_ip, device_id FROM usage ORDER BY ts")
            ).all()
        )
        daily = conn.execute(
            text(
                "SELECT date, device_id, request_count FROM usage_daily "
                "ORDER BY date, device_id"
            )
        ).all()
    return usage, daily


def test_dry_run_changes_nothing(env):
    script, engine, _ids, _user = env
    before = _state(engine)
    out: list[str] = []
    assert script.run(engine, apply=False, out=out.append) == 0
    assert _state(engine) == before
    assert script.BACKUP_TABLE not in inspect(engine).get_table_names()
    assert any("DRY RUN" in line for line in out)


def test_apply_attributes_rebuilds_and_preserves_totals(env):
    script, engine, ids, _user = env
    out: list[str] = []
    script.run(engine, apply=True, out=out.append)

    usage, daily = _state(engine)
    assert usage["100.71.211.51"] == ids["unraid-vm"]
    assert usage["127.0.0.1"] == ids["unraid-vm"]
    assert usage["100.88.94.9"] == ids["Hanbos-MacBook-Air.local"]
    assert usage["9.9.9.9"] is None  # unmatched IP untouched
    assert usage[None] is None  # no IP stays Unknown

    by_day = {(d, dev): n for d, dev, n in daily}
    assert by_day[("2026-04-17", ids["unraid-vm"])] == 2
    assert by_day[("2026-04-18", ids["unraid-vm"])] == 1
    assert by_day[("2026-04-18", ids["Hanbos-MacBook-Air.local"])] == 1
    assert by_day[("2026-04-18", None)] == 1  # 9.9.9.9 on the same day
    assert by_day[("2026-04-19", None)] == 1

    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT SUM(request_count) FROM usage_daily")).scalar()
            == 6
        )
        backup = conn.execute(
            text(f"SELECT SUM(request_count), COUNT(*) FROM {script.BACKUP_TABLE}")
        ).one()
    assert tuple(backup) == (6, 3)  # original: one row per day, pre-split
    assert any("100.71.211.51" in line and "2" in line for line in out)


def test_second_apply_refuses_when_backup_exists(env):
    script, engine, _ids, _user = env
    script.run(engine, apply=True, out=lambda _l: None)
    with pytest.raises(script.BackfillError, match="already exists"):
        script.run(engine, apply=True, out=lambda _l: None)


def test_aborts_and_rolls_back_when_daily_totals_disagree(env):
    script, engine, _ids, _user = env
    with engine.begin() as conn:
        conn.execute(text("UPDATE usage_daily SET request_count = request_count + 1"))
    before = _state(engine)
    with pytest.raises(script.BackfillError, match="day totals changed"):
        script.run(engine, apply=True, out=lambda _l: None)
    assert _state(engine) == before
    assert script.BACKUP_TABLE not in inspect(engine).get_table_names()


def test_accept_drift_takes_usage_as_source_of_truth(env):
    script, engine, _ids, _user = env
    with engine.begin() as conn:
        conn.execute(text("UPDATE usage_daily SET request_count = request_count + 1"))
    script.run(engine, apply=True, accept_drift=True, out=lambda _l: None)
    with engine.connect() as conn:
        daily = conn.execute(
            text("SELECT SUM(request_count) FROM usage_daily")
        ).scalar()
        raw = conn.execute(text("SELECT COUNT(*) FROM usage")).scalar()
    assert daily == raw


def test_refuses_missing_device(env):
    script, engine, ids, _user = env
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE devices SET device_name = 'x' WHERE id = :i"),
            {"i": ids["Hanbos-MacBook-Air.local"]},
        )
    with pytest.raises(script.BackfillError, match="no device named"):
        script.run(engine, apply=True, out=lambda _l: None)


def test_refuses_ambiguous_device(env):
    script, engine, _ids, user = env
    from src.database.models import Device

    with Session(engine) as s:
        s.add(
            Device(
                user_id=user.id,
                installation_hash="dup",
                device_name="unraid-vm",
                created_at=1,
            )
        )
        s.commit()
    with pytest.raises(script.BackfillError, match="ambiguous"):
        script.run(engine, apply=True, out=lambda _l: None)
