"""Per-device usage attribution: write path, daily split, filters, log rows."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

TS = 1776384000000000  # 2026-04-17T00:00:00+00:00


def _device_token(fresh_db, email="a@example.com", name="laptop"):
    from src.auth.tokens import mint_token

    token, user = mint_token(
        email, kind="ingest", device_name=name, db_path=fresh_db.db_path
    )
    from src.database import get_engine
    from src.database.models import AuthToken

    with Session(get_engine(fresh_db.db_path)) as s:
        device_id = (
            s.execute(select(AuthToken.device_id).where(AuthToken.user_id == user.id))
            .scalars()
            .all()[-1]
        )
    return token, user, device_id


def _usage(db, user_id, device_id, **kw):
    fields = dict(
        ts=TS,
        provider="p",
        model="m",
        client_source="claude-code",
        endpoint="/v1/messages",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        total_cost_usd=0.5,
        status=200,
        user_id=user_id,
        device_id=device_id,
    )
    fields.update(kw)
    return db.Usage(**fields)


def _daily(fresh_db):
    from src.database import get_engine
    from src.database.models import UsageDaily

    with Session(get_engine(fresh_db.db_path)) as s:
        return s.execute(select(UsageDaily)).scalars().all()


def test_ingest_report_attaches_token_device(api_module, fresh_db):
    token, user, device_id = _device_token(fresh_db)
    r = TestClient(api_module.app).post(
        "/ingest/v1/usage",
        json={
            "provider": "anthropic",
            "model": "test-model",
            "endpoint": "/v1/messages",
            "client_source": "claude-code",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "status": 200,
        },
        headers={"x-tokenage-token": token},
    )
    assert r.status_code == 204
    from src.database import get_engine
    from src.database.models import Usage

    with Session(get_engine(fresh_db.db_path)) as s:
        assert s.execute(select(Usage.device_id)).scalars().all() == [device_id]
    assert [d.device_id for d in _daily(fresh_db)] == [device_id]


def test_otlp_record_attaches_token_device(otlp_module, fresh_db):
    token, _user, device_id = _device_token(fresh_db)
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "claude-code"},
                        }
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "attributes": [
                                    {
                                        "key": "event.name",
                                        "value": {
                                            "stringValue": "claude_code.api_request"
                                        },
                                    },
                                    {
                                        "key": "input_tokens",
                                        "value": {"intValue": "10"},
                                    },
                                    {
                                        "key": "output_tokens",
                                        "value": {"intValue": "5"},
                                    },
                                    {
                                        "key": "model",
                                        "value": {"stringValue": "test-model"},
                                    },
                                ],
                                "timeUnixNano": "1800000000000000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }
    r = TestClient(otlp_module.app).post(
        "/v1/logs", json=body, headers={"x-tokenage-token": token}
    )
    assert r.status_code == 200
    assert [d.device_id for d in _daily(fresh_db)] == [device_id]


def test_upsert_requires_device_id(fresh_db):
    db = fresh_db.database_module
    usage = _usage(db, "u1", None)
    with pytest.raises(ValueError, match="device_id"):
        db.usage.upsert_daily_aggregate(usage, db_path=fresh_db.db_path)
    with pytest.raises(ValueError, match="device_id"):
        db.log_usage(usage, db_path=fresh_db.db_path)
    assert _daily(fresh_db) == []


def test_record_usage_requires_device_id(fresh_db):
    from src.recorder import record_usage

    with pytest.raises(TypeError):
        record_usage(  # type: ignore[call-arg]
            provider="p", model="m", user_id="u1", endpoint="/x", prompt_tokens=1
        )


def test_daily_rows_split_per_device_and_increment(fresh_db):
    db = fresh_db.database_module
    for device in ("d1", "d2", "d1"):
        db.log_usage(_usage(db, "u1", device), db_path=fresh_db.db_path)
    rows = {d.device_id: d.request_count for d in _daily(fresh_db)}
    assert rows == {"d1": 2, "d2": 1}


def test_device_filter_and_unknown_bucket(fresh_db):
    db = fresh_db.database_module
    path = fresh_db.db_path
    db.log_usage(_usage(db, "u1", "d1"), db_path=path)
    db.log_usage(_usage(db, "u1", "d2"), db_path=path)
    # Historical row: no device (written below the live path's guard).
    from src.database import get_engine

    with Session(get_engine(path)) as s:
        s.add(_usage(db, "u1", None))
        s.add(
            db.UsageDaily(
                user_id="u1",
                date="2026-04-17",
                provider="p",
                model="m",
                client_source="claude-code",
                device_id=None,
                request_count=4,
            )
        )
        s.commit()

    span = dict(since="2026-04-10T00:00:00Z", until="2026-04-20T00:00:00Z")
    assert db.count_usage(user_id="u1", db_path=path, **span) == 6
    assert db.count_usage(user_id="u1", device_id="d1", db_path=path, **span) == 1
    assert db.count_usage(user_id="u1", device_id="unknown", db_path=path, **span) == 4
    # sub-2-day range reads the raw usage table (one NULL-device row there)
    day = dict(since="2026-04-17T00:00:00Z", until="2026-04-17T12:00:00Z")
    assert db.count_usage(user_id="u1", device_id="unknown", db_path=path, **day) == 1
    by_device = {
        r["dimension"]: r["requests"]
        for r in db.aggregate_daily_by_dimension(
            user_id="u1", dimension="device", db_path=path, **span
        )
    }
    assert by_device == {"d1": 1, "d2": 1, None: 4}
    summary = db.summarize_usage_daily(
        user_id="u1", device_id="d2", db_path=path, **span
    )
    assert sum(r["requests"] for r in summary) == 1


def test_request_log_rows_return_device_name(fresh_db):
    db = fresh_db.database_module
    path = fresh_db.db_path
    _token, user, device_id = _device_token(fresh_db, name="unraid-vm")
    db.log_usage(_usage(db, user.id, device_id, ts=TS), db_path=path)
    from src.database import get_engine

    with Session(get_engine(path)) as s:
        s.add(_usage(db, user.id, None, ts=TS + 1))
        s.commit()
    rows = db.fetch_recent_usage(user_id=user.id, limit=10, db_path=path)
    by_ts = {r["ts"]: r for r in rows}
    assert by_ts[TS]["device_id"] == device_id
    assert by_ts[TS]["device_name"] == "unraid-vm"
    assert by_ts[TS + 1]["device_id"] is None
    assert by_ts[TS + 1]["device_name"] is None
    only = db.fetch_recent_usage(
        user_id=user.id, limit=10, device_id="unknown", db_path=path
    )
    assert [r["ts"] for r in only] == [TS + 1]


def test_auth_devices_include_revoked(api_module, fresh_db):
    from src.auth.tokens import revoke_device

    token, user, device_id = _device_token(fresh_db)
    revoke_device(device_id, user.id, db_path=fresh_db.db_path)
    from src.auth.tokens import mint_token

    cli, _ = mint_token(user.email, kind="cli", db_path=fresh_db.db_path)
    client = TestClient(api_module.app, headers={"authorization": f"Bearer {cli}"})
    ids = [d["id"] for d in client.get("/auth/devices").json()["devices"]]
    assert device_id not in ids
    listed = client.get("/auth/devices", params={"include_revoked": "true"}).json()
    revoked = [d for d in listed["devices"] if d["id"] == device_id]
    assert revoked and revoked[0]["revoked_at"] is not None


def test_migrate_adds_device_id_and_swaps_daily_index(fresh_db):
    sm = fresh_db.schema_migrations_module
    from src.database import get_engine, text

    engine = get_engine(fresh_db.db_path)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX uq_usage_daily"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_usage_daily ON usage_daily "
                "(date, provider, model, client_source, user_id)"
            )
        )
    changes = sm.migrate_database(fresh_db.db_path)
    assert "usage_daily.drop_uq_usage_daily_without_device" in changes
    assert "usage_daily.uq_usage_daily" in changes
    from sqlalchemy import inspect

    cols = next(
        i["column_names"]
        for i in inspect(engine).get_indexes("usage_daily")
        if i["name"] == "uq_usage_daily"
    )
    assert "device_id" in cols


def test_dimension_device_returns_names(fresh_db):
    db = fresh_db.database_module
    path = fresh_db.db_path
    _token, user, device_id = _device_token(fresh_db, name="unraid-vm")
    db.log_usage(_usage(db, user.id, device_id), db_path=path)
    rows = db.aggregate_daily_by_dimension(
        user_id=user.id, dimension="device", db_path=path
    )
    assert [(r["dimension"], r["dimension_name"]) for r in rows] == [
        (device_id, "unraid-vm")
    ]


def _record(fresh_db, device_id, endpoint, user_id="u1"):
    from src.recorder import record_usage

    return record_usage(
        ts=TS,
        provider="anthropic",
        model="dedup-model",
        user_id=user_id,
        device_id=device_id,
        client_source="claude-code",
        endpoint=endpoint,
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        status=200,
        db_path=fresh_db.db_path,
    )


def _usage_rows(fresh_db):
    from src.database import get_engine
    from src.database.models import Usage

    with Session(get_engine(fresh_db.db_path)) as s:
        return s.execute(select(Usage.device_id)).scalars().all()


def test_duplicate_merge_requires_same_device(fresh_db):
    # Same shape, same window, different devices: both rows are kept.
    _record(fresh_db, "d1", "/v1/messages")
    _record(fresh_db, "d2", "otlp")
    assert sorted(_usage_rows(fresh_db)) == ["d1", "d2"]


def test_duplicate_merge_still_happens_on_same_device(fresh_db):
    first = _record(fresh_db, "d1", "/v1/messages")
    second = _record(fresh_db, "d1", "otlp")
    assert second.id == first.id
    assert _usage_rows(fresh_db) == ["d1"]
