import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import drain
from tests.test_otlp import _attrs, _authed_client


def agy_record(event_name="agy.invocation_completed", *, seconds=0, **attributes):
    return {
        "timeUnixNano": str(1_800_000_000_000_000_000 + seconds * 1_000_000_000),
        "attributes": _attrs({"event.name": event_name, **attributes}),
    }


def post_logs(client, *records):
    response = client.post(
        "/v1/logs",
        json={
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": _attrs(
                            {"service.name": "agy", "session.id": "resource-session"}
                        )
                    },
                    "scopeLogs": [{"logRecords": records}],
                }
            ]
        },
    )
    assert response.status_code == 200
    drain()


def test_agy_usage_and_cache_pricing(otlp_module, fresh_db, monkeypatch):
    from src.database.models import Usage
    from src.pricing.maps import MODEL_COSTS
    from src.pricing.models import ModelCost

    # Fixed rates keep the accounting test independent of remote pricing data.
    monkeypatch.setitem(
        MODEL_COSTS,
        "gemini-2.5-pro",
        ModelCost(input=2, output=8, cache_read=0.5, cache_write=4),
    )
    client = _authed_client(otlp_module, fresh_db)
    post_logs(
        client,
        agy_record(
            model="gemini-2.5-pro-high",
            input_tokens=1000,
            cache_read_tokens=600,
            output_tokens=200,
            cache_creation_tokens=50,
            duration_ms=810,
            **{"session.id": "invocation-session", "http.response.status_code": 200},
        ),
        agy_record(
            model="claude-opus-5-5-medium",
            seconds=1,
            provider="evil-provider",
            input_tokens="700",
            cache_read_tokens="500",
            output_tokens="80",
            cache_creation_tokens="90",
            duration_ms="90",
        ),
    )

    with Session(fresh_db.database_module.get_engine()) as db:
        gemini, claude = db.scalars(select(Usage).order_by(Usage.ts)).all()

    for row in (gemini, claude):
        assert (row.client_source, row.provider, row.endpoint) == (
            "agy",
            "google",
            "otlp",
        )
        assert row.device_id

    assert (gemini.model, gemini.effort) == ("gemini-2.5-pro", "high")
    assert gemini.session_id == "invocation-session"
    assert gemini.ts == 1_800_000_000_000_000
    assert (gemini.latency_ms, gemini.status) == (810, 200)
    assert (gemini.prompt_tokens, gemini.cached_tokens) == (1600, 600)
    assert (gemini.completion_tokens, gemini.cache_creation_tokens) == (200, 50)
    assert gemini.total_tokens == 1850
    assert gemini.input_cost_usd == Decimal("0.0025")  # .002 + .0003 + .0002
    assert gemini.output_cost_usd == Decimal("0.0016")
    assert gemini.total_cost_usd == Decimal("0.0041")
    assert gemini.price_snapshot_id is not None

    assert (claude.model, claude.effort) == ("claude-opus-5-5", "medium")
    assert claude.session_id == "resource-session"
    assert (claude.latency_ms, claude.status) == (90, None)
    assert (claude.prompt_tokens, claude.cached_tokens) == (1200, 500)
    assert (claude.completion_tokens, claude.cache_creation_tokens) == (80, 90)
    assert claude.total_tokens == 1370
    assert claude.total_cost_usd == 0
    assert claude.price_snapshot_id is None


@pytest.mark.parametrize(
    ("model", "expected_model", "effort"),
    [
        ("gemini-3.8-flash-high", "gemini-3.8-flash", "high"),
        ("claude-opus-5-5-medium", "claude-opus-5-5", "medium"),
        ("gpt-oss-120b-MeDiuM", "gpt-oss-120b", "medium"),
        ("gemini-4-neon-xhigh", "gemini-4-neon", "xhigh"),
        ("gemini-pro-max", "gemini-pro", "max"),
        ("gemini-pro-low", "gemini-pro", "low"),
        ("gemini-pro-orchestrator-neon", "gemini-pro-orchestrator-neon", None),
        ("gpt-5.1-codex-max", "gpt-5.1-codex-max", None),
        ("Qwen-MAX", "Qwen-MAX", None),
        ("-high", "-high", None),
    ],
)
def test_split_agy_model_effort(otlp_module, model, expected_model, effort):
    assert otlp_module._split_agy_model_effort(model) == (expected_model, effort)


def test_agy_tokenless_completion_is_skipped(otlp_module, fresh_db):
    from src.database.models import Usage

    client = _authed_client(otlp_module, fresh_db)
    post_logs(
        client,
        agy_record(
            model="gemini-pro",
            input_token_count=100,
            cached_token_count=20,
            output_token_count=30,
            cache_creation_token_count=10,
            effort="max",
            variant="low",
        ),
    )

    with Session(fresh_db.database_module.get_engine()) as db:
        assert db.scalars(select(Usage)).all() == []

    # Explicit zero is a reported token count.
    post_logs(client, agy_record(input_tokens=0))
    with Session(fresh_db.database_module.get_engine()) as db:
        assert db.scalars(select(Usage)).one().prompt_tokens == 0


@pytest.mark.parametrize("invalid", ["some/path", "with space", "x" * 129, 1])
def test_agy_rejects_invalid_model_and_tool_metadata(otlp_module, fresh_db, invalid):
    from src.database.models import ToolCall, Usage

    client = _authed_client(otlp_module, fresh_db)
    message = {"message.id": "message"}
    decision = agy_record(
        "agy.tool_decision", tool_name=invalid, call_id="id", **message
    )
    completion = agy_record(model=invalid, input_tokens=7, **message)
    assert (
        otlp_module._attr(
            otlp_module._queue_record(decision, "agy")["attributes"], "tool_name"
        )
        is None
    )
    assert (
        otlp_module._attr(
            otlp_module._queue_record(completion, "agy")["attributes"], "model"
        )
        is None
    )
    post_logs(client, decision, completion)
    with Session(fresh_db.database_module.get_engine()) as db:
        assert db.scalars(select(Usage)).one().model == "agy-unknown"
        assert db.scalars(select(ToolCall)).all() == []


def test_agy_tool_ids_and_buffer_are_scoped_by_device_and_session(
    otlp_module, fresh_db
):
    from src.database.models import ToolCall, Usage

    first = _authed_client(otlp_module, fresh_db)
    second = _authed_client(otlp_module, fresh_db)
    streams = [
        (first, "shared", "Read"),
        (second, "shared", "Bash"),
        (first, "other", "Grep"),
    ]
    for client, session, name in streams:
        post_logs(
            client,
            agy_record(
                "agy.tool_decision",
                tool_name=name,
                call_id="same-call",
                **{"message.id": "same-message", "session.id": session},
            ),
        )
    for client, session, _ in streams:
        post_logs(
            client,
            agy_record(
                input_tokens=7, **{"message.id": "same-message", "session.id": session}
            ),
        )
    with Session(fresh_db.database_module.get_engine()) as db:
        pairs = db.execute(select(ToolCall, Usage).join(Usage)).all()
    assert len({usage.user_id for _, usage in pairs}) == 1
    assert len({usage.device_id for _, usage in pairs}) == 2
    assert {(tool.tool_name, usage.session_id) for tool, usage in pairs} == {
        (name.lower(), session) for _, session, name in streams
    }
    assert len({tool.tool_use_id for tool, _ in pairs}) == 3


def test_agy_tools_keep_usage_and_session_on_redelivery(otlp_module, fresh_db):
    from src.database.models import SessionRecord, ToolCall, Usage

    client = _authed_client(otlp_module, fresh_db)
    message = {"message.id": "message-1"}
    decision = agy_record(
        "agy.tool_decision",
        tool_name="Read",
        call_id="call-1",
        duration_ms=8,
        **message,
    )
    other_decision = agy_record(
        "agy.tool_decision",
        tool_name="Bash",
        call_id="call-2",
        duration_ms=11,
        **message,
    )
    invocation = agy_record(model="gemini-pro", input_tokens=7, seconds=1, **message)
    post_logs(client, decision, other_decision, invocation)
    post_logs(client, decision, other_decision, invocation)

    with Session(fresh_db.database_module.get_engine()) as db:
        usage = db.scalars(select(Usage)).one()
        session = db.scalars(select(SessionRecord)).one()
        tools = db.scalars(select(ToolCall).order_by(ToolCall.tool_name)).all()
    assert session.request_count == 1
    assert json.loads(session.tool_calls_json) == {"bash": 1, "read": 1}
    assert [(tool.tool_name, tool.duration_ms) for tool in tools] == [
        ("bash", 11),
        ("read", 8),
    ]
    for tool in tools:
        assert tool.usage_id == usage.id
        assert tool.session_id == session.session_id == usage.session_id
        assert tool.client_source == session.client_source == "agy"
        assert tool.user_id == usage.user_id
        assert tool.ts == 1_800_000_000_000


def test_agy_collector_attaches_every_tool_in_a_capped_spool_file(
    otlp_module, fresh_db, monkeypatch, tmp_path
):
    from client import agy_hooks
    from src.database.models import ToolCall, Usage

    spool_payloads = []

    def spool(_directory, body, _limit, *, bound):
        assert bound == "binding"
        spool_payloads.append(json.loads(body))
        return True

    monkeypatch.setattr(agy_hooks, "_spool", spool)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "step_index": 1,
                "input_tokens": 7,
                "tool_calls": [{"name": f"Read_{i}"} for i in range(125)],
            }
        )
        + "\n"
    )
    payload = {"transcriptPath": str(transcript), "initialNumSteps": 0}
    state = agy_hooks._fresh_state()
    agy_hooks._pre_invocation(state, {"initialNumSteps": 0}, 0)
    agy_hooks._flush(state, "session", payload, 1, True, "binding")

    (body,) = spool_payloads
    client = _authed_client(otlp_module, fresh_db)
    response = client.post("/v1/logs", json=body)
    assert response.status_code == 200 and response.json() == {}
    drain()
    with Session(fresh_db.database_module.get_engine()) as db:
        usage = db.scalars(select(Usage)).one()
        tools = db.scalars(select(ToolCall)).all()
    assert len(tools) == 100
    assert {tool.tool_name for tool in tools} == {f"read_{i}" for i in range(100)}
    assert all(tool.usage_id == usage.id for tool in tools)


def test_agy_tool_buffer_caps_entries_per_message(otlp_module, monkeypatch):
    buffer = {}
    monkeypatch.setattr(otlp_module, "_agy_tool_buffer", buffer)

    def parse_decision(message_id, call_id, duration_ms):
        otlp_module._parse_log_record(
            agy_record(
                "agy.tool_decision",
                tool_name="Read",
                call_id=call_id,
                duration_ms=duration_ms,
                **{"message.id": message_id},
            ),
            "agy",
            "test-session",
            user_id="user",
            device_id="device",
            session=None,
        )

    for i in range(101):
        parse_decision("message", f"call-{i}", 1)
    parse_decision("message", "call-0", 2)
    parse_decision("other-message", "call-100", 3)

    entries = buffer[json.dumps(["user", "device", "test-session", "message"])]
    assert [entry["tool_use_id"] for entry in entries] == [
        f"call-{i}" for i in range(100)
    ]
    assert entries[0]["duration_ms"] == 2
    assert (
        len(buffer[json.dumps(["user", "device", "test-session", "other-message"])])
        == 1
    )


@pytest.mark.parametrize("field", ["message.id", "tool_name", "call_id"])
@pytest.mark.parametrize("value", ["x" * 257, 123])
def test_agy_tool_buffer_rejects_invalid_identifiers(
    otlp_module, monkeypatch, field, value
):
    buffer = {}
    monkeypatch.setattr(otlp_module, "_agy_tool_buffer", buffer)
    attrs = {"message.id": "message", "tool_name": "Read", "call_id": "call"}
    attrs[field] = value
    otlp_module._parse_log_record(
        agy_record("agy.tool_decision", **attrs),
        "agy",
        "test-session",
        user_id="user",
        device_id="device",
        session=None,
    )
    assert buffer == {}


@pytest.mark.parametrize("message_id", ["x" * 257, 123])
def test_agy_invocation_skips_invalid_message_correlation(
    otlp_module, monkeypatch, message_id
):
    key = json.dumps(["user", "device", "test-session", message_id])
    buffer = {key: []}
    monkeypatch.setattr(otlp_module, "_agy_tool_buffer", buffer)
    monkeypatch.setattr(otlp_module, "record_usage", lambda **kwargs: None)
    otlp_module._parse_log_record(
        agy_record(model="gemini-pro", input_tokens=3, **{"message.id": message_id}),
        "agy",
        "test-session",
        user_id="user",
        device_id="device",
        session=None,
    )
    assert key in buffer


def test_agy_session_project(api_module, otlp_module, fresh_db):
    from src.auth.tokens import mint_token
    from src.database.models import SessionRecord

    token, _ = mint_token("agy@example.com", kind="ingest", db_path=fresh_db.db_path)
    client = TestClient(api_module.app, headers={"x-tokenage-token": token})
    response = client.post(
        "/ingest/v1/events",
        json={
            "events": [
                {
                    "type": "session.project",
                    "agent": "agy",
                    "session_id": "project-session",
                    "ts": 1_800_000_000_000_000,
                    "data": {"project": "orchard"},
                }
            ]
        },
    )
    assert response.status_code == 204
    drain()
    post_logs(
        TestClient(otlp_module.app, headers=client.headers),
        agy_record(
            model="gemini-pro", input_tokens=7, **{"session.id": "project-session"}
        ),
    )
    with Session(fresh_db.database_module.get_engine()) as db:
        session = db.scalars(select(SessionRecord)).one()
    assert (session.session_id, session.client_source, session.project_name) == (
        "project-session",
        "agy",
        "orchard",
    )


def test_agy_queue_keeps_only_usage_metadata(otlp_module):
    metadata = agy_record(
        model="gemini-pro",
        provider="google",
        input_tokens=5,
        output_tokens=2,
        cache_read_tokens=1,
        cache_creation_tokens=3,
        call_id="call",
        tool_name="Read",
        duration_ms=4,
        **{
            "message.id": "message",
            "session.id": "session",
            "http.response.status_code": 200,
        },
    )
    private_attrs = _attrs(
        {key: "private" for key in ("prompt", "response", "content")}
    )
    queued = otlp_module._queue_record(
        {
            **metadata,
            "body": {"stringValue": "private"},
            "attributes": metadata["attributes"] + private_attrs,
        },
        "agy",
    )
    assert "body" not in queued
    assert queued["attributes"] == metadata["attributes"]
