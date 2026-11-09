"""Reasoning effort per usage row: normalizer, OTLP/proxy sources, merge, reads."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from protocol.usage_report import EFFORT_LEVELS, UsageReport, normalize_effort
from tests.conftest import drain
from tests.test_otlp import _attrs

TS = 1_800_000_000_000_000  # microseconds


@pytest.mark.parametrize("level", sorted(EFFORT_LEVELS))
def test_normalizer_keeps_known_levels(level):
    assert normalize_effort(level) == level
    assert normalize_effort(f"  {level.upper()}\n") == level


@pytest.mark.parametrize(
    "value", [None, "", "  ", "turbo", "my-fast-variant", "x" * 10_000, 3, ["high"]]
)
def test_normalizer_drops_unknown_values(value):
    assert normalize_effort(value) is None
    report = UsageReport(provider="p", model="m", endpoint="e", effort=value)
    assert report.effort is None


def test_extractors_read_effort(otlp_module):
    claude = otlp_module._extract_claude_fields(
        {}, _attrs({"effort": "XHigh", "model": "m"}), "", user_id="u"
    )
    assert claude["effort"] == "xhigh"
    assert (
        otlp_module._extract_claude_fields({}, _attrs({}), "", user_id="u")["effort"]
        is None
    )

    codex_attrs = {
        "event.kind": "response.completed",
        "input_token_count": 1,
        "output_token_count": 1,
    }
    codex = otlp_module._extract_codex_fields(
        {},
        _attrs({**codex_attrs, "model_reasoning_effort": "minimal"}),
        "codex_cli_rs",
        user_id="u",
    )
    assert codex["effort"] == "minimal"
    # The startup prewarm response carries tokens but no effort.
    prewarm = otlp_module._extract_codex_fields(
        {}, _attrs(codex_attrs), "codex_cli_rs", user_id="u"
    )
    assert prewarm["effort"] is None


@pytest.mark.parametrize("client_source", ["opencode", "kilo"])
@pytest.mark.parametrize(
    ("variant", "expected"),
    [("max", "max"), ("default", "default"), ("my-custom", None), (None, None)],
)
def test_opencode_variant_becomes_effort(otlp_module, client_source, variant, expected):
    values = {"input_token_count": 1, "output_token_count": 1}
    if variant is not None:
        values["variant"] = variant
    fields = otlp_module._extract_opencode_fields(
        {}, _attrs(values), "s", client_source=client_source, user_id="u"
    )
    assert fields["effort"] == expected


def test_effort_attributes_cross_the_queue(otlp_module):
    keys = ("effort", "model_reasoning_effort", "variant")
    record = otlp_module._queue_record(
        {"attributes": _attrs({key: "high" for key in (*keys, "speed")})}
    )
    # Speed/tier are deliberately not kept.
    assert [a["key"] for a in record["attributes"]] == list(keys)


def _usage_rows(fresh_db):
    from src.database import get_engine
    from src.database.models import Usage

    with Session(get_engine(fresh_db.db_path)) as db:
        return db.execute(select(Usage)).scalars().all()


def _plugin_body(variant: str | None) -> dict:
    values = {
        "event.name": "opencode.message_completed",
        "session.id": "oc-session",
        "message.id": "msg-1",
        "model": "test-model",
        "provider": "tokenage proxy",
        "input_token_count": 100,
        "output_token_count": 50,
    }
    if variant is not None:
        values["variant"] = variant
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": _attrs(
                        {"service.name": "opencode", "session.id": "oc-session"}
                    )
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": str(TS * 1000),
                                "attributes": _attrs(values),
                            }
                        ]
                    }
                ],
            }
        ]
    }


def _proxy_report(effort: str | None) -> dict:
    return {
        "ts": TS + 1_000_000,
        "provider": "upstream",
        "model": "test-model",
        "endpoint": "/v1/chat/completions",
        "client_source": "opencode",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "status": 200,
        "effort": effort,
    }


@pytest.mark.parametrize("proxy_first", [True, False])
@pytest.mark.parametrize(
    ("variant", "proxy_effort", "expected"),
    [
        ("max", "low", "low"),  # proxy reflects the request sent
        ("default", "high", "high"),  # proxy replaces an unselected variant
        (None, "low", "low"),  # proxy fills an empty effort
        ("max", None, "max"),  # missing proxy effort preserves the plugin value
        ("my-custom", "high", "high"),  # a custom variant counts as empty
        ("default", None, "default"),
    ],
)
def test_opencode_through_proxy_keeps_one_row_and_request_effort(
    api_module, fresh_db, monkeypatch, proxy_first, variant, proxy_effort, expected
):
    from src.auth.tokens import mint_token

    token, user = mint_token(
        "effort@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app, headers={"x-tokenage-token": token})

    def send_proxy():
        response = client.post("/ingest/v1/usage", json=_proxy_report(proxy_effort))
        assert response.status_code == 204
        drain()

    def send_plugin():
        assert (
            client.post("/otlp/v1/logs", json=_plugin_body(variant)).status_code == 200
        )
        drain()

    for send in (send_proxy, send_plugin) if proxy_first else (send_plugin, send_proxy):
        send()

    (row,) = _usage_rows(fresh_db)
    assert (row.effort, row.session_id) == (expected, "oc-session")
    monkeypatch.setattr(api_module, "_request_user_id", lambda _: user.id)
    (logged,) = TestClient(api_module.app).get("/usage").json()
    assert logged["effort"] == expected


def test_migration_adds_effort_column_once(fresh_db):
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE usage DROP COLUMN effort"))
    migrate = fresh_db.schema_migrations_module.migrate_database
    assert "usage.effort" in migrate(fresh_db.db_path)
    column = next(
        c for c in inspect(engine).get_columns("usage") if c["name"] == "effort"
    )
    assert column["nullable"]
    assert migrate(fresh_db.db_path) == []


@pytest.fixture
def effort_rows(fresh_db):
    from src.database import get_engine
    from src.database.models import SessionRecord, Usage

    rows = [
        # (session, owner, project, efforts)
        ("s1", "me", "orchard", ["high", "high", None]),
        ("s2", "me", "orchard", ["low", "high"]),
        ("s3", "me", "grove", ["max"]),
        ("s1", "other", "orchard", ["ultra", "ultra"]),
    ]
    with Session(get_engine(fresh_db.db_path)) as db:
        for session_id, owner, project, efforts in rows:
            db.add(
                SessionRecord(
                    session_id=session_id if owner == "me" else "foreign",
                    user_id=owner,
                    project_name=project,
                    client_source="codex",
                    started=TS,
                    ended=TS,
                    updated_at="now",
                )
            )
            for i, effort in enumerate(efforts):
                db.add(
                    Usage(
                        id=f"{owner}-{session_id}-{i}",
                        user_id=owner,
                        # "other" reuses session id s1: the project join must
                        # still exclude it (sessions.session_id is the PK).
                        session_id=session_id,
                        model="selected" if session_id == "s1" and i == 0 else "m",
                        provider="p",
                        endpoint="otlp",
                        ts=TS + i,
                        effort=effort,
                    )
                )
        db.commit()
    return fresh_db


def test_session_effort_mix_counts_nulls_and_is_user_scoped(effort_rows):
    from src.database.sessions import aggregate_session_context

    mix = aggregate_session_context("s1", "me", db_path=effort_rows.db_path)
    assert mix["effort_mix"] == [
        {"effort": "high", "requests": 2},
        {"effort": None, "requests": 1},
    ]
    # Another user's usage under the same session id never mixes in.
    other = aggregate_session_context("s1", "other", db_path=effort_rows.db_path)
    assert other["effort_mix"] == [{"effort": "ultra", "requests": 2}]
    empty = aggregate_session_context("nope", "me", db_path=effort_rows.db_path)
    assert empty["effort_mix"] == []


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, [("high", 3), (None, 1), ("low", 1)]),
        ({"model": "absent"}, None),
    ],
)
def test_project_effort_mix_joins_sessions_and_is_user_scoped(
    effort_rows, filters, expected
):
    from src.database.sessions import SessionFilters, session_project_detail

    detail = session_project_detail(
        name="orchard",
        filters=SessionFilters(user_id="me", **filters),
        db_path=effort_rows.db_path,
    )
    if expected is None:
        assert detail is None
        return
    assert [(m["effort"], m["requests"]) for m in detail["effort_mix"]] == expected
    # Match s1 by one model, but include its other model's requests as well.
    # Correlating the EXISTS to each outer usage row would lose those requests.
    model_filtered = session_project_detail(
        name="orchard",
        filters=SessionFilters(user_id="me", model="selected"),
        db_path=effort_rows.db_path,
    )
    assert model_filtered["effort_mix"] == [
        {"effort": "high", "requests": 2},
        {"effort": None, "requests": 1},
    ]
