"""Tests for the record_usage pipeline (Candidate 1 deepening)."""

import pytest

from src.database.usage import fetch_recent_usage


@pytest.fixture
def test_db(fresh_db):
    # Schema already initialized by fresh_db; truncation between tests.
    return fresh_db.db_path


def test_record_usage_inserts_row(test_db):
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        ts=1779148800000000,
        provider="openai",
        model="gpt-4",
        client_source="test",
        session_id="sess-1",
        endpoint="/v1/chat/completions",
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        total_tokens=150,
        reasoning_tokens=0,
        latency_ms=500,
        ttft_ms=None,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "openai"
    assert row["model"] == "gpt-4"
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50
    assert row["status"] == 200
    assert row["total_cost_usd"] is not None


def test_record_usage_normalizes_models_and_rollups(test_db, monkeypatch):
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database import count_usage, get_engine, summarize_usage_window
    from src.database.models import PriceSnapshot, SessionRecord, UsageHourly
    from src.pricing import costs
    from src.pricing.models import ModelCost
    from src.recorder import record_usage

    canonical = "stealth/space-bunny-alpha"
    monkeypatch.setitem(
        costs.MODEL_COSTS, canonical, ModelCost(input=0, output=0, cache_read=0)
    )
    usages = []
    for model in (" Space-Bunny-Free ", "STEALTH/SPACE-BUNNY-ALPHA"):
        usage = record_usage(
            device_id="test-device",
            ts=1779148800000000,
            provider="normalization-test",
            model=model,
            client_source="opencode",
            session_id="normalized-session",
            endpoint="otlp",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            status=200,
            db_path=test_db,
            user_id="test-user",
        )
        assert usage is not None
        assert usage.model == canonical
        assert usage.price_snapshot_id is not None
        usages.append(usage)
    assert usages[0].price_snapshot_id == usages[1].price_snapshot_id

    with Session(get_engine(test_db)) as session:
        daily = session.scalars(select(UsageHourly)).one()
        assert (daily.model, daily.request_count, daily.total_tokens) == (
            canonical,
            2,
            300,
        )
        record = session.get(SessionRecord, "normalized-session")
        assert record is not None
        assert record.primary_model == canonical
        assert json.loads(record.models_json) == {canonical: 0}
        assert session.scalars(select(PriceSnapshot)).one().model == canonical

    for model in (" Space-Bunny-Free ", canonical):
        assert (
            len(
                fetch_recent_usage(
                    limit=10, model=model, db_path=test_db, user_id="test-user"
                )
            )
            == 2
        )
        assert count_usage(model=model, db_path=test_db, user_id="test-user") == 2
        assert (
            count_usage(
                model=model,
                session_id="normalized-session",
                db_path=test_db,
                user_id="test-user",
            )
            == 2
        )
        summary = summarize_usage_window(
            model=model, db_path=test_db, user_id="test-user"
        )
        assert summary["window"]["row_count"] == 2
        assert summary["models"][0]["model"] == canonical


def test_record_usage_normalizes_provider_without_losing_pricing(test_db, monkeypatch):
    import json
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from src.database import count_usage, get_engine, summarize_usage_window
    from src.database.models import BaseUrl, PriceSnapshot, SessionRecord, UsageHourly
    from src.pricing import costs
    from src.pricing.models import ModelCost
    from src.recorder import record_usage

    provider = "custom-provider"
    monkeypatch.setitem(
        costs.MODEL_COSTS,
        "test-model",
        ModelCost(input=1, output=2, cache_read=0.25, cache_write=0.5),
    )
    monkeypatch.setitem(costs.MODEL_COST_SOURCES, "test-model", "test-source")
    assert costs.resolve_cost_match("test-model").source == "test-source"

    for name in ("CUSTOM-PROVIDER", "Custom-Provider"):
        usage = record_usage(
            device_id="test-device",
            ts=1779148800000000,
            provider=name,
            model="test-model",
            session_id="provider-normalization",
            endpoint="otlp",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=200,
            cache_creation_tokens=100,
            total_tokens=1600,
            base_url="https://api.example.com/v1",
            base_url_provider=name,
            db_path=test_db,
            user_id="test-user",
        )
        assert usage is not None
        assert usage.provider == provider
        assert usage.total_cost_usd == Decimal("0.0019")
        assert usage.price_snapshot_id is not None

    with Session(get_engine(test_db)) as session:
        daily = session.scalars(select(UsageHourly)).one()
        assert daily.provider == provider
        assert daily.request_count == 2
        record = session.get(SessionRecord, "provider-normalization")
        assert record.primary_provider == provider
        assert json.loads(record.providers_json) == {provider: 0.0038}
        assert session.scalars(select(PriceSnapshot)).one().model == "test-model"
        assert session.scalars(select(BaseUrl)).one().provider_name == provider

    assert (
        len(
            fetch_recent_usage(
                limit=10,
                provider="CUSTOM-PROVIDER",
                db_path=test_db,
                user_id="test-user",
            )
        )
        == 2
    )
    assert (
        count_usage(provider="CUSTOM-PROVIDER", db_path=test_db, user_id="test-user")
        == 2
    )
    assert (
        count_usage(
            provider="CUSTOM-PROVIDER",
            session_id="provider-normalization",
            db_path=test_db,
            user_id="test-user",
        )
        == 2
    )
    assert (
        summarize_usage_window(
            provider="CUSTOM-PROVIDER", db_path=test_db, user_id="test-user"
        )["window"]["row_count"]
        == 2
    )


def test_record_usage_computes_costs(test_db):
    from src.pricing import costs as costs_module
    from src.pricing.costs import ModelCost
    from src.recorder import record_usage

    original_model_costs = costs_module.MODEL_COSTS.copy()
    costs_module.MODEL_COSTS.clear()
    costs_module.MODEL_COSTS["test-model"] = ModelCost(
        input=1.0, output=2.0, cache_read=0.5
    )

    try:
        record_usage(
            device_id="test-device",
            provider="test-provider-without-override",
            model="test-model",
            client_source="test",
            session_id="sess-2",
            endpoint="/v1/chat/completions",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=200,
            total_tokens=1500,
            reasoning_tokens=0,
            latency_ms=200,
            status=200,
            db_path=test_db,
            user_id="test-user",
        )

        rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
        assert len(rows) == 1
        row = rows[0]
        assert float(row["total_cost_usd"]) == pytest.approx(
            0.0008 + 0.0001 + 0.001, rel=1e-3
        )
    finally:
        costs_module.MODEL_COSTS.clear()
        costs_module.MODEL_COSTS.update(original_model_costs)


def test_record_usage_writes_price_snapshot(test_db):
    from src.pricing import costs as costs_module
    from src.pricing.costs import ModelCost
    from src.pricing.snapshots import get_price_snapshot
    from src.recorder import record_usage

    original_model_costs = costs_module.MODEL_COSTS.copy()
    original_model_cost_sources = costs_module.MODEL_COST_SOURCES.copy()
    costs_module.MODEL_COSTS.clear()
    costs_module.MODEL_COST_SOURCES.clear()
    costs_module.MODEL_COSTS["snap-model"] = ModelCost(
        input=1.0, output=2.0, cache_read=0.5, cache_write=3.0
    )
    costs_module.MODEL_COST_SOURCES["snap-model"] = "litellm"

    try:
        record_usage(
            device_id="test-device",
            ts=1779148800000000,
            provider="snap-provider",
            model="snap-model",
            client_source="test",
            session_id="sess-snap",
            endpoint="/v1/chat/completions",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_tokens=200,
            cache_creation_tokens=100,
            total_tokens=1500,
            status=200,
            db_path=test_db,
            user_id="test-user",
        )

        snapshot = get_price_snapshot(
            date="2026-05-19",
            model="snap-model",
            db_path=test_db,
        )
        assert snapshot is not None
        cost, _source = snapshot
        from dataclasses import asdict

        assert asdict(cost) == asdict(
            ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=3.0)
        )

        from src.pricing.snapshots import enrich_rows

        enriched = enrich_rows(
            [
                {
                    "ts": 1779148800000000,
                    "provider": "snap-provider",
                    "model": "snap-model",
                    "prompt_tokens": 1000,
                    "cached_tokens": 200,
                    "cache_creation_tokens": 100,
                }
            ],
            db_path=test_db,
        )[0]
        assert enriched["normal_input_cost_usd"] == 0.0008
        assert enriched["cache_read_cost_usd"] == 0.0001
        assert enriched["cache_write_cost_usd"] == 0.0003
    finally:
        costs_module.MODEL_COSTS.clear()
        costs_module.MODEL_COSTS.update(original_model_costs)
        costs_module.MODEL_COST_SOURCES.clear()
        costs_module.MODEL_COST_SOURCES.update(original_model_cost_sources)


def test_record_usage_skips_zero_tokens_on_success(test_db):
    """Status 200 with all token fields zero/None should not be recorded."""
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        provider="openrouter",
        model="test-model",
        endpoint="/v1/chat/completions",
        prompt_tokens=0,
        completion_tokens=0,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 0


def test_record_usage_skips_none_tokens_on_success(test_db):
    """Status 200 with all token fields None should not be recorded."""
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        provider="openrouter",
        model="test-model",
        endpoint="/v1/chat/completions",
        status=200,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 0


def test_record_usage_records_prompt_only_tokens(test_db):
    """Status 200 with prompt_tokens only should be recorded."""
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=100,
        completion_tokens=0,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 100


def test_record_usage_records_zero_tokens_on_error(test_db):
    """Error status with zero tokens should still be recorded."""
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=0,
        completion_tokens=0,
        status=400,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["status"] == 400


def test_record_usage_records_zero_tokens_on_unknown_status(test_db):
    """None status with zero tokens should still be recorded."""
    from src.recorder import record_usage

    record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=0,
        completion_tokens=0,
        status=None,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["status"] is None


def test_record_tool_call_links_to_usage(test_db):
    """record_tool_call creates a tool_calls row linked to the usage row."""
    from src.recorder import record_tool_call, record_usage

    usage = record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=10,
        completion_tokens=5,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    assert usage is not None

    record_tool_call(
        tool_use_id="call_abc123",
        usage_id=usage.id,
        tool_name="get_weather",
        client_source="test",
        ts=usage.ts,
        db_path=test_db,
        user_id="test-user",
    )

    # Verify via fetch_recent_usage which already joins tool_names
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["tool_names"] == "get_weather"


def test_record_tool_call_persists_and_returns_duration(test_db):
    from src.database.usage import fetch_tool_calls
    from src.recorder import record_tool_call, record_usage

    usage = record_usage(
        device_id="test-device",
        provider="anthropic",
        model="claude-sonnet-4-5",
        endpoint="/v1/messages",
        prompt_tokens=10,
        completion_tokens=5,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    assert usage is not None

    record_tool_call(
        tool_use_id="toolu_dur",
        usage_id=usage.id,
        tool_name="Bash",
        duration_ms=1234,
        ts=usage.ts,
        db_path=test_db,
        user_id="test-user",
    )
    record_tool_call(
        tool_use_id="toolu_nodur",
        usage_id=usage.id,
        tool_name="Read",
        ts=usage.ts,
        db_path=test_db,
        user_id="test-user",
    )

    calls = fetch_tool_calls(usage_id=usage.id, db_path=test_db, user_id="test-user")
    by_id = {call["tool_use_id"]: call for call in calls}
    assert by_id["toolu_dur"]["duration_ms"] == 1234
    assert by_id["toolu_nodur"]["duration_ms"] is None


def test_record_tool_call_multiple_per_usage(test_db):
    """Multiple tool calls from one usage row all get linked."""
    from src.recorder import record_tool_call, record_usage

    usage = record_usage(
        device_id="test-device",
        provider="anthropic",
        model="claude-sonnet-4-5",
        endpoint="/v1/messages",
        prompt_tokens=20,
        completion_tokens=10,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    assert usage is not None

    for tool_id, tool_name in [
        ("toolu_1", "bash"),
        ("toolu_2", "file_read"),
    ]:
        record_tool_call(
            tool_use_id=tool_id,
            usage_id=usage.id,
            tool_name=tool_name,
            ts=usage.ts,
            db_path=test_db,
            user_id="test-user",
        )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    # tool_names are comma-separated (order depends on DB query)
    tool_names = set(rows[0]["tool_names"].split(","))
    assert tool_names == {"bash", "file_read"}


def test_record_tool_call_duplicate_id_is_noop(test_db):
    """Redelivery of the same tool_use_id must not raise or double-count."""
    from src.recorder import record_tool_call, record_usage

    usage = record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=1,
        completion_tokens=1,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    assert usage is not None

    for _ in range(2):
        record_tool_call(
            tool_use_id="dup_1",
            usage_id=usage.id,
            tool_name="bash",
            ts=usage.ts,
            db_path=test_db,
            user_id="test-user",
        )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["tool_names"] == "bash"


def test_record_tool_call_scoped_collision_keeps_new_call(test_db):
    """A raw ID collision from another session must not be deduplicated."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as OrmSession

    from src.database import get_engine
    from src.database.models import ToolCall
    from src.recorder import record_tool_call

    record_tool_call(
        tool_use_id="call_abc",
        session_id="legacy-session",
        client_source="claude-code",
        tool_name="bash",
        ts=1,
        db_path=test_db,
        user_id="test-user",
    )
    record_tool_call(
        tool_use_id="user-1:call_abc",
        user_id="user-1",
        session_id="user-1:new-session",
        client_source="claude-code",
        tool_name="bash",
        ts=2,
        db_path=test_db,
    )

    with OrmSession(get_engine(test_db)) as session:
        rows = session.scalars(select(ToolCall)).all()
    assert {row.tool_use_id for row in rows} == {"call_abc", "user-1:call_abc"}


def test_record_tool_call_integrity_error_is_noop(test_db, monkeypatch):
    """A concurrent redelivery that races past the pre-check must still no-op
    instead of letting IntegrityError propagate out of the OTLP handler."""
    from sqlalchemy.orm import Session as SASession

    from src.recorder import record_tool_call, record_usage

    usage = record_usage(
        device_id="test-device",
        provider="openai",
        model="gpt-4",
        endpoint="/v1/chat/completions",
        prompt_tokens=1,
        completion_tokens=1,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    assert usage is not None

    record_tool_call(
        tool_use_id="race_1",
        usage_id=usage.id,
        tool_name="bash",
        ts=usage.ts,
        db_path=test_db,
        user_id="test-user",
    )

    # Simulate a second request racing past the "already recorded" pre-check.
    monkeypatch.setattr(SASession, "get", lambda self, *a, **k: None)

    record_tool_call(
        tool_use_id="race_1",
        usage_id=usage.id,
        tool_name="bash",
        ts=usage.ts,
        db_path=test_db,
        user_id="test-user",
    )

    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    assert rows[0]["tool_names"] == "bash"


def _record_otlp_usage(test_db, **overrides):
    from src.recorder import record_usage

    fields = dict(
        ts=1779148800000000,
        provider="anthropic",
        model="claude-3",
        client_source="claude-code",
        session_id="otlp-session-1",
        endpoint="otlp",
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        total_tokens=150,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    fields.update(overrides)
    return record_usage(device_id="test-device", **fields)


def _record_proxy_usage(test_db, **overrides):
    from src.recorder import record_usage

    fields = dict(
        ts=1779148800000000 + 2_000_000,  # 2s later, within the dedup window
        provider="anthropic",
        model="claude-3",
        client_source="claude",
        endpoint="/v1/messages",
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        total_tokens=150,
        status=200,
        db_path=test_db,
        user_id="test-user",
    )
    fields.update(overrides)
    return record_usage(device_id="test-device", **fields)


@pytest.mark.parametrize("otlp_first", [True, False])
def test_record_usage_deduplicates_model_aliases(test_db, otlp_first):
    def otlp():
        return _record_otlp_usage(
            test_db, model=" Space-Bunny-Free ", client_source="opencode"
        )

    def proxy():
        return _record_proxy_usage(
            test_db, model="STEALTH/SPACE-BUNNY-ALPHA", client_source="opencode"
        )

    first, second = (otlp, proxy) if otlp_first else (proxy, otlp)
    initial = first()
    duplicate = second()
    assert initial is not None and duplicate is not None
    assert initial.id == duplicate.id
    assert duplicate.model == "stealth/space-bunny-alpha"
    assert len(fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")) == 1


def test_record_usage_keeps_unmapped_model_variants_separate(test_db):
    otlp = _record_otlp_usage(test_db, model="deepseek-v4.1-flash-free")
    proxy = _record_proxy_usage(test_db, model="deepseek-v4.1-flash")
    assert otlp is not None and proxy is not None
    assert otlp.model == "deepseek-v4.1-flash-free"
    assert otlp.id != proxy.id
    assert otlp.price_snapshot_id is None
    assert otlp.total_cost_usd == 0


def test_record_usage_merges_proxy_duplicate_into_existing_otlp_row(test_db):
    """OTLP first, proxy second: proxy fills gaps but never overwrites OTLP's session_id."""
    otlp_usage = _record_otlp_usage(test_db)
    assert otlp_usage is not None

    proxy_usage = _record_proxy_usage(test_db, session_id="proxy-would-be-session")

    assert proxy_usage is not None
    assert proxy_usage.id == otlp_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint"] == "otlp"
    assert row["session_id"] == "otlp-session-1"  # OTLP's session_id wins


def test_record_usage_merges_otlp_duplicate_into_existing_proxy_row(test_db):
    """Proxy first, OTLP second (the observed OpenCode ordering): OTLP can
    overwrite, backfilling the session_id the proxy never has."""
    from sqlalchemy.orm import Session as SASession

    from src.database.engine import get_engine
    from src.database.models import SessionRecord

    proxy_usage = _record_proxy_usage(test_db, session_id=None)
    assert proxy_usage is not None

    otlp_usage = _record_otlp_usage(
        test_db,
        ts=1779148800000000 + 4_000_000,
        provider="volce",  # deliberately mismatched, like the real OpenCode case
        session_id="otlp-session-1",
    )

    assert otlp_usage is not None
    assert otlp_usage.id == proxy_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint"] == "/v1/messages"  # surviving row is the proxy's
    assert row["provider"] == "anthropic"  # proxy's provider is untouched
    assert row["session_id"] == "otlp-session-1"  # backfilled from OTLP

    # Backfilling session_id for the first time should also populate the
    # sessions table, using the proxy row's own (accurate) tokens/cost.
    with SASession(get_engine(test_db)) as session:
        record = session.get(SessionRecord, "otlp-session-1")
        assert record is not None
        assert record.request_count == 1
        assert record.total_tokens == 150


def test_record_usage_keeps_proxy_row_when_tokens_differ(test_db):
    assert _record_otlp_usage(test_db) is not None

    proxy_usage = _record_proxy_usage(test_db, prompt_tokens=999)

    assert proxy_usage is not None
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 2


def test_record_usage_keeps_proxy_row_when_outside_dedup_window(test_db):
    assert _record_otlp_usage(test_db) is not None

    proxy_usage = _record_proxy_usage(test_db, ts=1779148800000000 + 60_000_000)

    assert proxy_usage is not None
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 2


def test_record_usage_keeps_proxy_row_for_unrecognized_client_source(test_db):
    """No known OTLP-tracked family for this client_source, so no dedup lookup."""
    assert _record_otlp_usage(test_db) is not None

    proxy_usage = _record_proxy_usage(test_db, client_source="some-other-cli")

    assert proxy_usage is not None
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 2


def test_record_usage_otlp_rows_are_never_deduped_against_each_other(test_db):
    """The dedup check only matches the opposite collection path."""
    first = _record_otlp_usage(test_db)
    second = _record_otlp_usage(test_db, ts=1779148800000000 + 1_000_000)

    assert first is not None
    assert second is not None
    assert first.id != second.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 2


def test_record_usage_merges_kilo_and_opencode_across_the_family(test_db):
    """Kilo is a fork sharing OpenCode's OTLP extraction; the proxy may still
    label its traffic "opencode" if Kilo doesn't rebrand its user agent."""
    otlp_usage = _record_otlp_usage(test_db, client_source="kilo")
    assert otlp_usage is not None

    proxy_usage = _record_proxy_usage(test_db, client_source="opencode")

    assert proxy_usage is not None
    assert proxy_usage.id == otlp_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1


def test_record_usage_dedup_handles_none_token_fields(test_db):
    """A malformed/partial response with None tokens shouldn't crash the
    dedup lookup or falsely match a row with different (non-None) tokens."""
    otlp_usage = _record_otlp_usage(
        test_db,
        prompt_tokens=None,
        completion_tokens=None,
        cached_tokens=5,
        total_tokens=None,
    )
    assert otlp_usage is not None

    proxy_usage = _record_proxy_usage(
        test_db,
        prompt_tokens=None,
        completion_tokens=None,
        cached_tokens=5,
        total_tokens=None,
    )

    assert proxy_usage is not None
    assert proxy_usage.id == otlp_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1


def test_record_usage_merges_when_proxy_zero_vs_otlp_none_cache_creation(test_db):
    """Proxy normalizes a missing cache-write to 0 while OTLP records NULL; the
    dedup predicate coalesces both sides so the two paths still merge."""
    otlp_usage = _record_otlp_usage(test_db)
    assert otlp_usage is not None

    proxy_usage = _record_proxy_usage(test_db, cache_creation_tokens=0)

    assert proxy_usage is not None
    assert proxy_usage.id == otlp_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1


def test_record_usage_merges_when_proxy_zero_then_otlp_none_cache_creation(test_db):
    """Same 0-vs-NULL asymmetry, proxy row landing first (the OpenCode ordering)."""
    proxy_usage = _record_proxy_usage(test_db, cache_creation_tokens=0)
    assert proxy_usage is not None

    otlp_usage = _record_otlp_usage(test_db, ts=1779148800000000 + 4_000_000)

    assert otlp_usage is not None
    assert otlp_usage.id == proxy_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 1


def test_record_usage_does_not_merge_different_cache_creation(test_db):
    """Coalescing must not collapse genuinely different cache-write amounts."""
    otlp_usage = _record_otlp_usage(test_db, cache_creation_tokens=3, total_tokens=153)
    assert otlp_usage is not None

    proxy_usage = _record_proxy_usage(
        test_db, cache_creation_tokens=5, total_tokens=153
    )

    assert proxy_usage is not None
    assert proxy_usage.id != otlp_usage.id
    rows = fetch_recent_usage(limit=10, db_path=test_db, user_id="test-user")
    assert len(rows) == 2


def test_record_usage_does_not_merge_across_user_ids(test_db):
    """AC 7: dedup never crosses tenants."""
    otlp_usage = _record_otlp_usage(test_db)
    assert otlp_usage is not None

    def total() -> int:
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session as OrmSession

        from src.database import get_engine
        from src.database.models import Usage

        with OrmSession(get_engine(test_db)) as session:
            return session.scalar(select(func.count()).select_from(Usage))

    proxy_usage = _record_proxy_usage(test_db, user_id="u1")
    assert proxy_usage is not None
    assert proxy_usage.id != otlp_usage.id
    assert total() == 2

    other_otlp = _record_otlp_usage(
        test_db, ts=1779148800000000 + 100_000_000, user_id="u2"
    )
    other_proxy = _record_proxy_usage(
        test_db, ts=1779148800000000 + 100_000_000, user_id="u3"
    )
    assert other_otlp is not None
    assert other_proxy is not None
    assert other_proxy.id != other_otlp.id
    assert total() == 4

    same_user_otlp = _record_otlp_usage(
        test_db, ts=1779148800000000 + 200_000_000, user_id="u4"
    )
    same_user_proxy = _record_proxy_usage(
        test_db, ts=1779148800000000 + 200_000_000, user_id="u4"
    )
    assert same_user_otlp is not None
    assert same_user_proxy is not None
    assert same_user_proxy.id == same_user_otlp.id
    assert total() == 5
