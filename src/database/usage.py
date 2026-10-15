"""Usage persistence, aggregation, and reporting helpers.

Extracted from database/__init__.py during Phase 5 refactoring.
"""

from __future__ import annotations

import calendar
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    and_,
    case,
    func,
    literal_column,
    or_,
    select,
    text,
    types,
    update,
)
from sqlalchemy.orm import Session

from ..pricing.costs import calculate_costs, compute_input_split, resolve_pricing
from ..utils import (
    micros_to_secs,
    normalize_model_name,
    normalize_provider_name,
    normalize_tool_name,
    secs_to_micros,
)
from .engine import get_engine
from .model_prices import load_model_price_overrides
from .models import BaseUrl, Device, ToolCall, Usage, UsageDaily

logger = logging.getLogger(__name__)

# Client sources whose recorded latency is the full assistant-message lifetime
# and therefore already includes tool execution time. Tool-call duration is only
# netted out of throughput for these. Model-call sources (claude-code, codex)
# time a single request, so their latency already excludes tool time and
# subtracting it would overstate throughput.
TOOL_DURATION_IN_LATENCY_SOURCES = ("opencode", "kilo")


def _iso_to_micros(value: str) -> int:
    """Convert an ISO-8601 datetime string to integer microseconds since epoch.

    Uses integer arithmetic to avoid float precision loss from .timestamp().
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    else:
        dt = dt.replace(tzinfo=UTC)
    epoch_s = calendar.timegm(dt.timetuple())
    return secs_to_micros(epoch_s) + dt.microsecond


def _iso_to_millis(value: str) -> int:
    """Convert an ISO-8601 datetime string to integer milliseconds since epoch."""
    return _iso_to_micros(value) // 1000


# === Price helpers ===


def _avg_effective_price_expr(total_cost_col: Any, total_tokens_col: Any) -> Any:
    return case(
        (
            func.sum(total_tokens_col) > 0,
            func.sum(total_cost_col) / func.sum(total_tokens_col),
        ),
        else_=0,
    )


def _avg_effective_price_per_million_expr(
    total_cost_col: Any, total_tokens_col: Any
) -> Any:
    return _avg_effective_price_expr(total_cost_col, total_tokens_col) * 1_000_000


# === Entity / Persistence Helpers ===

# ponytail: proxy and OTLP share no request id and neither reliably lands
# first (observed empirically: OpenCode's proxy write beat its OTLP write),
# so dedup checks both directions and matches on token counts within this
# window instead of an exact key. Widen the window, or add a real shared id,
# if collisions or missed matches become a problem. This also can't close the
# race where both writers' lookups run before either has committed its own
# insert (they're separate requests in separate transactions) — if that
# happens, both still insert. Accepted gap: fixing it needs a DB-level
# uniqueness constraint on a real shared key, which doesn't exist yet.
DEDUP_WINDOW_MICROS = 15_000_000

# `provider` is not a reliable match field: OTLP's opencode/kilo parser
# reports the agent's own configured provider alias (e.g. "tokenage
# proxy" when opencode points at the local proxy), while the proxy reports
# the real resolved upstream (e.g. "Volce") for the same request. So dedup
# matches on client_source family + model + tokens only.
#
# Kilo is a fork that reuses OpenCode's config schema and OTLP field
# extraction (both parsed by _extract_opencode_fields in otlp.py), but the
# proxy's user-agent sniffing (parse_client_source in client/proxy/usage.py) has no
# separate Kilo case — if Kilo's outbound requests don't rebrand their UA,
# the proxy would label them "opencode" while OTLP labels them "kilo".
# Folding them into one family keeps dedup working either way.
_CLIENT_SOURCE_FAMILIES: dict[str, str] = {
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "opencode": "opencode",
    "kilo": "opencode",
}
_CLIENT_SOURCES_BY_FAMILY: dict[str, list[str]] = {}
for _label, _family in _CLIENT_SOURCE_FAMILIES.items():
    _CLIENT_SOURCES_BY_FAMILY.setdefault(_family, []).append(_label)


def merge_duplicate_usage(
    *,
    client_source: str | None,
    is_otlp: bool,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cached_tokens: int | None,
    total_tokens: int | None,
    cache_creation_tokens: int | None,
    ts: int,
    session_id: str | None,
    user_id: str,
    device_id: str,
    db_path: str | None = None,
) -> Usage | None:
    """Find a same-shaped usage row already recorded by the other collection path
    and enrich it instead of inserting a duplicate.

    `is_otlp` is whether the call currently being recorded is OTLP-sourced;
    the lookup searches the opposite path (OTLP looks for a proxy row and
    vice versa) so a request captured by both never double-counts. The match
    includes the device: identical events from two machines never merge.

    Only `session_id` is merged onto the existing row. OTLP
    is treated as authoritative for session_id (it carries the agent's real
    session, while the proxy never has one): an incoming OTLP call can
    overwrite these fields, an incoming proxy call only fills them in if the
    existing row doesn't already have them. Other fields (tokens, cost,
    latency, status, provider, ...) are left untouched even on a match:
    they're already folded into `usage_daily`/`sessions` running sums the
    moment the existing row was inserted, so changing them here would desync
    those aggregates without a matching correction.
    """
    family = _CLIENT_SOURCE_FAMILIES.get(client_source or "")
    if family is None:
        return None
    other_path_sources = _CLIENT_SOURCES_BY_FAMILY[family]
    endpoint_filter = Usage.endpoint != "otlp" if is_otlp else Usage.endpoint == "otlp"

    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        existing = session.scalar(
            select(Usage)
            .where(
                and_(
                    endpoint_filter,
                    Usage.user_id == user_id,
                    Usage.device_id == device_id,
                    Usage.client_source.in_(other_path_sources),
                    Usage.model == normalize_model_name(model),
                    Usage.prompt_tokens == prompt_tokens,
                    Usage.completion_tokens == completion_tokens,
                    Usage.cached_tokens == cached_tokens,
                    Usage.total_tokens == total_tokens,
                    # Proxy normalizes a missing cache-write to 0 while the OTLP
                    # extractors leave it NULL; coalesce so the two paths still
                    # match on the same logical request.
                    func.coalesce(Usage.cache_creation_tokens, 0)
                    == func.coalesce(cache_creation_tokens, 0),
                    Usage.ts >= ts - DEDUP_WINDOW_MICROS,
                    Usage.ts <= ts + DEDUP_WINDOW_MICROS,
                )
            )
            .order_by(func.abs(Usage.ts - ts))
            # Row lock is PostgreSQL-only; the SQLite dialect ignores FOR UPDATE
            # (SQLite serializes writes via its whole-file write lock instead).
            .with_for_update()
            .limit(1)
        )
        if existing is None:
            return None

        session_id_was_empty = not existing.session_id
        if session_id is not None and (is_otlp or existing.session_id is None):
            existing.session_id = session_id
        session.commit()

    logger.info(
        "merged duplicate usage from %s into %s ts=%s model=%s prompt=%s "
        "completion=%s cached=%s total=%s",
        "otlp" if is_otlp else "proxy",
        "otlp" if not is_otlp else "proxy",
        ts,
        model,
        prompt_tokens,
        completion_tokens,
        cached_tokens,
        total_tokens,
    )

    if session_id_was_empty and existing.session_id:
        try:
            from .sessions import upsert_session_from_usage

            upsert_session_from_usage(existing, db_path=db_path)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to update session record for merged usage ts=%s", existing.ts
            )

    return existing


def log_usage(
    usage: Usage,
    db_path: str | None = None,
    tool_calls: Sequence[dict[str, Any]] = (),
) -> None:
    """Persist a single usage row and its rollups in one transaction.

    *tool_calls* are ``tool_calls`` rows (without ``usage_id``) reported with
    this usage; they join the same transaction and a tool_use_id that is
    already recorded is skipped, so a redelivered report cannot double count.

    The row and its usage_daily increment land together or not at all: the
    error propagates and the transaction rolls back. Callers must not assume a
    retry (the proxy client drops the report on anything but 429).

    The sessions rollup stays best-effort inside a SAVEPOINT: a failure or a
    lock timeout there must not discard a valid usage row.
    """
    from .sessions import _upsert_session_from_usage

    _require_device_id(usage)
    usage.provider = normalize_provider_name(usage.provider)
    usage.model = normalize_model_name(usage.model)
    with Session(get_engine(db_path), expire_on_commit=False) as session:
        session.add(usage)
        # Flush so usage.id exists for the session rollup. The insert also
        # takes SQLite's write lock up front, so the later reads in this
        # transaction never need a lock upgrade (which fails without waiting).
        session.flush()
        if tool_calls:
            _insert_tool_calls(session, usage.id, tool_calls)
        upsert_daily_aggregate(session, usage)
        try:
            with session.begin_nested():
                _upsert_session_from_usage(session, usage)
        except Exception:
            logger.warning(
                "Failed to update session record for usage ts=%s",
                usage.ts,
                exc_info=True,
            )
        session.commit()


def _insert_tool_calls(
    session: Session, usage_id: str, tool_calls: Sequence[dict[str, Any]]
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert_fn = (
        pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    )
    session.execute(
        insert_fn(ToolCall)
        .values([{**tc, "usage_id": usage_id} for tc in tool_calls])
        .on_conflict_do_nothing(index_elements=["tool_use_id"])
    )


def _require_device_id(usage: Usage) -> None:
    # A NULL in the daily unique key never conflicts, so a device-less live row
    # would silently split its daily bucket instead of incrementing it.
    if usage.device_id is None:
        raise ValueError("usage.device_id is required on the live write path")


def _usage_date(usage: Usage) -> str:
    """UTC date bucket for a usage row's daily aggregate."""
    return datetime.fromtimestamp(micros_to_secs(usage.ts), tz=UTC).strftime("%Y-%m-%d")


def _usage_daily_match(usage: Usage, date: str) -> Any:
    """Filter selecting the usage_daily row that aggregates *usage*'s group.

    Shared by every incremental usage_daily update so the match fields
    can't drift between callers.
    """
    return and_(
        UsageDaily.date == date,
        UsageDaily.provider == usage.provider,
        UsageDaily.model == usage.model,
        UsageDaily.client_source == (usage.client_source or ""),
        UsageDaily.user_id == usage.user_id,
        UsageDaily.device_id == usage.device_id,
    )


def daily_values(usage: Usage) -> dict[str, Any]:
    """The usage_daily contribution (key columns + additive counters) of one row.

    Shared by the live upsert and the one-off daily rebuild so both always
    derive the same date bucket and counters.
    """
    date = _usage_date(usage)
    client_source = usage.client_source or ""
    is_success = usage.status is None or usage.status < 400
    latency = usage.latency_ms or 0
    input_cost = Decimal(str(usage.input_cost_usd))
    output_cost = Decimal(str(usage.output_cost_usd))
    total_cost = Decimal(str(usage.total_cost_usd))

    status_429 = status_4xx = status_5xx = status_unknown = 0
    if not is_success:
        status = usage.status
        if status == 429:
            status_429 = 1
        elif status is not None and 400 <= status < 500:
            status_4xx = 1
        elif status is not None and status >= 500:
            status_5xx = 1
        else:
            status_unknown = 1

    values: dict[str, Any] = {
        "date": date,
        "provider": usage.provider,
        "model": usage.model,
        "client_source": client_source,
        "user_id": usage.user_id,
        "device_id": usage.device_id,
        "request_count": 1,
        "prompt_tokens": usage.prompt_tokens or 0,
        "completion_tokens": usage.completion_tokens or 0,
        "reasoning_tokens": usage.reasoning_tokens or 0,
        "cached_tokens": usage.cached_tokens or 0,
        "total_tokens": usage.total_tokens or 0,
        "tool_tokens": usage.tool_tokens or 0,
        "cache_creation_tokens": usage.cache_creation_tokens or 0,
        "prompt_length": usage.prompt_length,
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
        "successful_requests": 1 if is_success else 0,
        "failed_requests": 0 if is_success else 1,
        "latency_sum_ms": latency,
        "status_429": status_429,
        "status_4xx": status_4xx,
        "status_5xx": status_5xx,
        "status_unknown": status_unknown,
    }
    return values


def upsert_daily_aggregate(session: Session, usage: Usage) -> None:
    """Add *usage* to usage_daily on *session*; the caller owns the commit."""
    _require_device_id(usage)
    values = daily_values(usage)

    engine = session.get_bind()
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert

    statement = insert_fn(UsageDaily).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[
            "date",
            "provider",
            "model",
            "client_source",
            "user_id",
            "device_id",
        ],
        set_={
            "request_count": UsageDaily.request_count
            + statement.excluded.request_count,
            "prompt_tokens": UsageDaily.prompt_tokens
            + statement.excluded.prompt_tokens,
            "completion_tokens": UsageDaily.completion_tokens
            + statement.excluded.completion_tokens,
            "reasoning_tokens": UsageDaily.reasoning_tokens
            + statement.excluded.reasoning_tokens,
            "cached_tokens": UsageDaily.cached_tokens
            + statement.excluded.cached_tokens,
            "total_tokens": UsageDaily.total_tokens + statement.excluded.total_tokens,
            "tool_tokens": UsageDaily.tool_tokens + statement.excluded.tool_tokens,
            "cache_creation_tokens": UsageDaily.cache_creation_tokens
            + statement.excluded.cache_creation_tokens,
            "prompt_length": UsageDaily.prompt_length
            + statement.excluded.prompt_length,
            "input_cost_usd": UsageDaily.input_cost_usd
            + statement.excluded.input_cost_usd,
            "output_cost_usd": UsageDaily.output_cost_usd
            + statement.excluded.output_cost_usd,
            "total_cost_usd": UsageDaily.total_cost_usd
            + statement.excluded.total_cost_usd,
            "successful_requests": UsageDaily.successful_requests
            + statement.excluded.successful_requests,
            "failed_requests": UsageDaily.failed_requests
            + statement.excluded.failed_requests,
            "latency_sum_ms": UsageDaily.latency_sum_ms
            + statement.excluded.latency_sum_ms,
            "status_429": UsageDaily.status_429 + statement.excluded.status_429,
            "status_4xx": UsageDaily.status_4xx + statement.excluded.status_4xx,
            "status_5xx": UsageDaily.status_5xx + statement.excluded.status_5xx,
            "status_unknown": UsageDaily.status_unknown
            + statement.excluded.status_unknown,
        },
    )

    session.execute(statement)


@dataclass(frozen=True)
class CostRecalcResult:
    skipped: bool
    reason: str | None = None
    old_costs: dict[str, Decimal] | None = None
    new_costs: dict[str, Decimal] | None = None
    price_snapshot_id: int | None = None
    pricing: dict[str, Any] | None = None


@dataclass(frozen=True)
class _RowReprice:
    """One examined row: ``resolved`` is None when its model has no price
    match (row untouched); otherwise its costs were rewritten."""

    usage: Usage
    resolved: Any = None
    old_costs: dict[str, Decimal] | None = None
    new_costs: dict[str, Decimal] | None = None


# Rows per transaction in reprice_estimated_rows.
_REPRICE_CHUNK = 500
# Scale of the cost columns (Numeric(18, 8)).
_COST_SCALE = Decimal("0.00000001")


def _reprice_usage_rows(
    session: Session,
    usages: Sequence[Usage],
    *,
    model_costs: dict[str, Any] | None,
    model_cost_sources: dict[str, str] | None,
    overrides: dict[str, Any] | None,
    db_path: str | None,
) -> list[_RowReprice]:
    """Recompute *usages*' costs against current pricing, rebind their price
    snapshots and delta-adjust the usage_daily and sessions rollups, all on
    *session* (the caller loads the rows and owns the commit).

    Returns one entry per row.
    """
    from ..pricing.snapshots import ensure_price_snapshot, serialize_rates
    from .sessions import (
        _add_usage_to_cost_maps,
        _load_cost_map,
        _primary_by_cost,
        get_session_record,
    )

    # Price everything and bind snapshots before the first write: the
    # snapshot insert uses its own connection, which on SQLite would block
    # behind this session's write lock.
    examined: list[_RowReprice] = []
    snapshot_ids: dict[tuple[str, str, str, str], int | None] = {}
    for usage in usages:
        resolved = resolve_pricing(
            usage.model, usage.ts, model_costs, model_cost_sources, overrides
        )
        if resolved.match is None:
            examined.append(_RowReprice(usage))
            continue
        old_costs = {
            "input_cost_usd": Decimal(str(usage.input_cost_usd)),
            "output_cost_usd": Decimal(str(usage.output_cost_usd)),
            "total_cost_usd": Decimal(str(usage.total_cost_usd)),
        }
        new_costs = calculate_costs(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            model_cost=resolved.cost,
        )
        # Rebind the row to the exact rates just used so the displayed split
        # matches the recalculated totals. Best-effort: a snapshot failure
        # clears the binding (reads then mark the row estimated) but the
        # costs are still recalculated.
        date = _usage_date(usage)
        source = resolved.source or "unknown"
        key = (date, usage.model, source, serialize_rates(resolved.cost))
        if key not in snapshot_ids:
            try:
                snapshot_ids[key] = ensure_price_snapshot(
                    date=date,
                    model=usage.model,
                    source=source,
                    cost=resolved.cost,
                    db_path=db_path,
                )
            except Exception:
                snapshot_ids[key] = None
                logger.warning(
                    "reprice: failed to record price snapshot for model=%s",
                    usage.model,
                )
        usage.price_snapshot_id = snapshot_ids[key]
        examined.append(_RowReprice(usage, resolved, old_costs, new_costs))

    daily: dict[tuple[Any, ...], tuple[Usage, str, dict[str, Decimal]]] = {}
    session_rows: dict[tuple[str, str], list[tuple[Usage, Decimal]]] = {}
    for row in examined:
        if row.resolved is None:
            continue
        usage = row.usage
        assert row.old_costs is not None and row.new_costs is not None
        # Rounded to the columns' NUMERIC(18, 8) scale per row: a per-row
        # `col + delta` UPDATE rounds each step, so summing unrounded deltas
        # would make the totals depend on how rows are grouped.
        deltas = {
            f: (row.new_costs[f] - row.old_costs[f]).quantize(
                _COST_SCALE, ROUND_HALF_UP
            )
            for f in row.old_costs
        }
        date = _usage_date(usage)
        daily_key = (
            date,
            usage.provider,
            usage.model,
            usage.client_source or "",
            usage.user_id,
            usage.device_id,
        )
        if daily_key in daily:
            sums = daily[daily_key][2]
            for field, delta in deltas.items():
                sums[field] += delta
        else:
            daily[daily_key] = (usage, date, dict(deltas))
        if usage.session_id and deltas["total_cost_usd"] != 0:
            session_rows.setdefault((usage.session_id, usage.user_id), []).append(
                (usage, deltas["total_cost_usd"])
            )
        usage.input_cost_usd = row.new_costs["input_cost_usd"]
        usage.output_cost_usd = row.new_costs["output_cost_usd"]
        usage.total_cost_usd = row.new_costs["total_cost_usd"]

    # Atomic `col = col + delta` UPDATEs rather than a select-mutate-commit
    # round trip: correct even under concurrent recalculations on SQLite,
    # where with_for_update() is a silent no-op (same caveat as
    # merge_duplicate_usage's row lock, elsewhere in this module). Keys are
    # sorted so concurrent reprices lock rollup rows in the same order.
    for daily_key in sorted(daily, key=repr):
        usage, date, sums = daily[daily_key]
        daily_result = session.execute(
            update(UsageDaily)
            .where(_usage_daily_match(usage, date))
            .values(
                input_cost_usd=UsageDaily.input_cost_usd + sums["input_cost_usd"],
                output_cost_usd=UsageDaily.output_cost_usd + sums["output_cost_usd"],
                total_cost_usd=UsageDaily.total_cost_usd + sums["total_cost_usd"],
            )
        )
        if daily_result.rowcount == 0:  # type: ignore[attr-defined]
            logger.warning(
                "reprice: no usage_daily row for date=%s "
                "provider=%s model=%s client_source=%s, rollup left as-is",
                date,
                usage.provider,
                usage.model,
                usage.client_source or "",
            )

    # sessions.total_cost_usd/models_json/providers_json are a JSON blob
    # that can only be read-modify-written (no SQL-level atomic increment
    # for a JSON map), so this part keeps the same SQLite race caveat as
    # above; harmless in practice since recalculation is a rare manual
    # action, not a hot ingest path. Deltas are applied row by row, in
    # order, so the float cost maps match a per-row recalculation exactly.
    for session_id, user_id in sorted(session_rows):
        session_row = get_session_record(
            session, session_id, user_id=user_id, with_for_update=True
        )
        if session_row is None:
            logger.warning(
                "reprice: no session row for session_id=%s, "
                "session cost rollup left as-is",
                session_id,
            )
            continue
        models_costs = _load_cost_map(session_row.models_json)
        providers_costs = _load_cost_map(session_row.providers_json)
        for usage, delta in session_rows[(session_id, user_id)]:
            session_row.total_cost_usd += delta
            _add_usage_to_cost_maps(
                usage,
                total_cost=delta,
                models_costs=models_costs,
                providers_costs=providers_costs,
            )
        session_row.models_json = json.dumps(models_costs)
        session_row.providers_json = json.dumps(providers_costs)
        session_row.primary_model = _primary_by_cost(models_costs)
        session_row.primary_provider = _primary_by_cost(providers_costs)
        session_row.updated_at = datetime.now(UTC).isoformat()
    return examined


def recalculate_usage_cost(
    usage_id: str,
    *,
    user_id: str,
    model_costs: dict[str, Any] | None = None,
    model_cost_sources: dict[str, str] | None = None,
    db_path: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> CostRecalcResult | None:
    """Recompute one Usage row's cost against current pricing and keep its
    usage_daily and sessions rollups in sync, all in one transaction. Returns
    None if the row doesn't exist or belongs to another user when scoped.

    If the row's (provider, model) doesn't resolve against current pricing
    (e.g. a deprecated/renamed model), the row is left untouched rather than
    overwritten with a zeroed fallback cost — callers should surface
    `result.skipped` to the user instead of treating it as success.
    """
    if overrides is None:
        overrides = load_model_price_overrides(user_id=user_id, db_path=db_path)
    engine = get_engine(db_path)
    with Session(engine) as session:
        query = select(Usage).where(Usage.id == usage_id, Usage.user_id == user_id)
        usage = session.scalar(query.with_for_update())
        if usage is None:
            return None

        [row] = _reprice_usage_rows(
            session,
            [usage],
            model_costs=model_costs,
            model_cost_sources=model_cost_sources,
            overrides=overrides,
            db_path=db_path,
        )
        if row.resolved is None:
            return CostRecalcResult(skipped=True, reason="no pricing match for model")
        assert row.new_costs is not None
        session.commit()

        components = compute_input_split(
            prompt_tokens=usage.prompt_tokens,
            cached_tokens=usage.cached_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cost=row.resolved.cost,
        )
        from ..pricing.snapshots import build_pricing_detail

        pricing = build_pricing_detail(
            row.resolved.cost,
            row.resolved.source,
            {
                "prompt_tokens": usage.prompt_tokens,
                "cached_tokens": usage.cached_tokens,
                "cache_creation_tokens": usage.cache_creation_tokens,
            },
            snapshot_id=usage.price_snapshot_id,
        )
        return CostRecalcResult(
            skipped=False,
            old_costs=row.old_costs,
            new_costs={**row.new_costs, **components},
            price_snapshot_id=usage.price_snapshot_id,
            pricing=pricing,
        )


def fetch_estimated_usage_rows(
    *,
    user_id: str,
    provider: str | None = None,
    model: str | None = None,
    since: str | None = None,
    until: str | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Ids of the user's usage rows not bound to a price snapshot (estimated).

    ``model`` selects the rows a price keyed ``model`` would match: the full
    name or its last ``/`` segment (exact then segment resolution).
    """
    filters = _usage_filters(
        user_id=user_id, provider=provider, since=since, until=until
    )
    filters.append(Usage.price_snapshot_id.is_(None))
    if model:
        name = normalize_model_name(model)
        filters.append(Usage.model.in_({name, name.rsplit("/", maxsplit=1)[-1]}))
    query = select(Usage.id).where(and_(*filters)).order_by(Usage.ts.asc())
    with get_engine(db_path).connect() as connection:
        return [row[0] for row in connection.execute(query)]


def reprice_estimated_rows(
    *,
    user_id: str,
    provider: str | None = None,
    model: str | None = None,
    since: str | None = None,
    until: str | None = None,
    model_costs: dict[str, Any] | None = None,
    model_cost_sources: dict[str, str] | None = None,
    db_path: str | None = None,
) -> dict[str, int]:
    """Rebind and recompute the user's estimated (unbound) rows to current
    pricing, honoring the user's own price overrides.

    Only rows with ``price_snapshot_id IS NULL`` are touched. Rows are
    repriced in chunks of ``_REPRICE_CHUNK``, one transaction per chunk, by
    the same ``_reprice_usage_rows`` core as ``recalculate_usage_cost`` so
    stored costs, session/daily rollups, and the snapshot binding stay
    consistent. Rows whose provider/model no longer resolves are left
    untouched and counted as skipped. Pass explicit ``model_costs`` / source
    maps to price every row from one pricing generation; otherwise the
    runtime maps are used.
    """
    ids = fetch_estimated_usage_rows(
        user_id=user_id,
        provider=provider,
        model=model,
        since=since,
        until=until,
        db_path=db_path,
    )
    overrides = load_model_price_overrides(user_id=user_id, db_path=db_path)
    engine = get_engine(db_path)
    repriced = 0
    for start in range(0, len(ids), _REPRICE_CHUNK):
        chunk = ids[start : start + _REPRICE_CHUNK]
        with Session(engine) as session:
            # skip_locked: a concurrent reprice can't take the same row (a
            # no-op on SQLite, same caveat as the rollup updates). Rows gone
            # or already bound since the scan are counted as skipped.
            loaded = {
                usage.id: usage
                for usage in session.scalars(
                    select(Usage)
                    .where(
                        Usage.id.in_(chunk),
                        Usage.user_id == user_id,
                        Usage.price_snapshot_id.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            }
            results = _reprice_usage_rows(
                session,
                [loaded[i] for i in chunk if i in loaded],
                model_costs=model_costs,
                model_cost_sources=model_cost_sources,
                overrides=overrides,
                db_path=db_path,
            )
            # Counted before commit: commit expires the ORM rows, and reading
            # them afterwards would reload each one with its own SELECT. A
            # snapshot failure leaves the row unbound, so don't report it as
            # repriced; it stays eligible for a later run.
            bound = sum(
                1
                for r in results
                if r.resolved is not None and r.usage.price_snapshot_id is not None
            )
            session.commit()
            repriced += bound
    return {
        "candidates": len(ids),
        "repriced": repriced,
        "skipped": len(ids) - repriced,
    }


# === Reporting / Query Helpers ===


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: _normalize_value(value) for key, value in row._mapping.items()}


SUMMARY_SUM_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "tool_tokens",
    "cache_creation_tokens",
    "total_tokens",
    "input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
)


def _empty_usage_summary() -> dict[str, Any]:
    return {
        "requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "status_429": 0,
        "status_4xx": 0,
        "status_5xx": 0,
        "status_unknown": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "tool_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
        "input_cost_usd": 0.0,
        "output_cost_usd": 0.0,
        "total_cost_usd": 0.0,
        "avg_latency_ms": None,
        "avg_ttft_ms": None,
        "cache_hit_rate": 0.0,
    }


def _add_row_to_summary(summary: dict[str, Any], row: dict[str, Any]) -> None:
    summary["requests"] += 1
    status = row.get("status")
    if status is not None and status >= 400:
        summary["failed_requests"] += 1
        if status == 429:
            summary["status_429"] += 1
        elif 400 <= status < 500:
            summary["status_4xx"] += 1
        elif status >= 500:
            summary["status_5xx"] += 1
        else:
            summary["status_unknown"] += 1
    else:
        summary["successful_requests"] += 1

    for field in SUMMARY_SUM_FIELDS:
        summary[field] += row.get(field) or 0


def _finalize_usage_summary(
    summary: dict[str, Any],
    *,
    latency_values: list[int],
    ttft_values: list[int],
) -> dict[str, Any]:
    if latency_values:
        summary["avg_latency_ms"] = sum(latency_values) / len(latency_values)
    if ttft_values:
        summary["avg_ttft_ms"] = sum(ttft_values) / len(ttft_values)

    # prompt_tokens already includes cached_tokens as a subset (see the client
    # proxy's extract_usage); cache_creation_tokens is a disjoint bucket on top of that,
    # so it must be added to the denominator to get true cache efficiency.
    total_input_tokens = summary["prompt_tokens"] + summary["cache_creation_tokens"]
    summary["cache_hit_rate"] = (
        summary["cached_tokens"] / total_input_tokens if total_input_tokens else 0.0
    )

    return summary


UNKNOWN_DEVICE = "unknown"


def _device_filter(column: Any, device_id: str) -> Any:
    """`unknown` selects rows with no device (historical, unattributed)."""
    return column.is_(None) if device_id == UNKNOWN_DEVICE else column == device_id


def _usage_filters(
    *,
    user_id: str,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    session_id: str | None = None,
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    only_failed: bool = False,
    status_429: bool = False,
    status_4xx: bool = False,
    status_5xx: bool = False,
    device_id: str | None = None,
) -> list[Any]:
    filters: list[Any] = [Usage.user_id == user_id]
    if provider:
        filters.append(Usage.provider == normalize_provider_name(provider))
    if model:
        filters.append(Usage.model == normalize_model_name(model))
    if client_source:
        filters.append(Usage.client_source == client_source)
    if device_id:
        filters.append(_device_filter(Usage.device_id, device_id))
    if session_id:
        filters.append(Usage.session_id == session_id)
    if tool_name is not None:
        tool_query = select(ToolCall.tool_use_id).where(
            ToolCall.usage_id == Usage.id,
            ToolCall.tool_name == normalize_tool_name(tool_name),
        )
        tool_query = tool_query.where(ToolCall.user_id == Usage.user_id)
        filters.append(tool_query.exists())
    if since:
        filters.append(Usage.ts >= _iso_to_micros(since))
    if until:
        filters.append(Usage.ts <= _iso_to_micros(until))
    if only_failed:
        filters.append(Usage.status >= 400)
    if status_429:
        filters.append(Usage.status == 429)
    if status_4xx:
        filters.append(
            and_(Usage.status >= 400, Usage.status < 500, Usage.status != 429)
        )
    if status_5xx:
        filters.append(Usage.status >= 500)
    return filters


def _tool_duration_sum_by(
    *group_cols: Any,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> dict[tuple[Any, ...], int]:
    """Tool-call duration sums grouped like a usage query.

    One pass over tool_calls joined to usage (usage_id is indexed). Null
    durations are ignored. Only sources whose latency already includes tool
    execution time contribute (see TOOL_DURATION_IN_LATENCY_SOURCES); for any
    group, ``sum(latency) - sum(tool_ms)`` is then the true generation time even
    when sources are mixed. Group keys normalize None to "" so they match
    usage_daily's client_source convention.
    """
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        since=since,
        until=until,
    )
    filters.append(Usage.client_source.in_(TOOL_DURATION_IN_LATENCY_SOURCES))
    filters.append(ToolCall.user_id == user_id)
    query = (
        select(
            *group_cols,
            func.coalesce(func.sum(ToolCall.duration_ms), 0).label("tool_ms"),
        )
        .select_from(ToolCall)
        .join(Usage, Usage.id == ToolCall.usage_id)
        .where(and_(*filters))
    )
    query = query.group_by(*group_cols)
    n = len(group_cols)
    with get_engine(db_path).connect() as connection:
        return {
            tuple("" if row[i] is None else row[i] for i in range(n)): row[n]
            for row in connection.execute(query)
        }


def _tool_duration_get(tool_map: dict[tuple[Any, ...], int], *keys: Any) -> int:
    return tool_map.get(tuple("" if k is None else k for k in keys), 0)


def _tool_net_throughput(
    completion_tokens: Any, latency_sum_ms: Any, tool_duration_sum_ms: Any
) -> float | None:
    """Completion tokens per second, excluding tool execution time."""
    net = max((latency_sum_ms or 0) - (tool_duration_sum_ms or 0), 0)
    if net <= 0:
        return None
    return (completion_tokens or 0) * 1000.0 / net


def _attach_tool_throughput(
    rows: list[dict[str, Any]],
    tool_map: dict[tuple[Any, ...], int],
    key_fn: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    """Attach tool-duration sums and net throughput to summary rows in place."""
    for row in rows:
        tool_ms = _tool_duration_get(tool_map, *key_fn(row))
        row["tool_duration_sum_ms"] = tool_ms
        row["avg_throughput"] = _tool_net_throughput(
            row.get("completion_tokens"), row.get("latency_sum_ms"), tool_ms
        )
    return rows


def _daily_usage_filters(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
) -> list[Any]:
    filters: list[Any] = [UsageDaily.user_id == user_id]
    if since:
        filters.append(UsageDaily.date >= since[:10])
    _until = until or datetime.now(UTC).isoformat()
    if _until:
        filters.append(UsageDaily.date <= _until[:10])
    if provider:
        filters.append(UsageDaily.provider == normalize_provider_name(provider))
    if model:
        filters.append(UsageDaily.model == normalize_model_name(model))
    if client_source:
        filters.append(UsageDaily.client_source == client_source)
    if device_id:
        filters.append(_device_filter(UsageDaily.device_id, device_id))
    return filters


def _should_use_daily_table(since: str | None, until: str | None) -> bool:
    """Return True when the time range is wide enough for daily aggregation.

    For sub-2-day ranges, the daily aggregate table loses precision because it
    includes full calendar days.  Fall back to the raw usage table for accuracy.
    """
    if since is None:
        return True
    try:
        since_dt = datetime.fromisoformat(since)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=UTC)
        if until is None:
            until_dt = datetime.now(UTC)
        else:
            until_dt = datetime.fromisoformat(until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=UTC)
        return (until_dt - since_dt).total_seconds() >= 2 * 86400
    except (ValueError, TypeError):
        return True


def _daily_usage_token_columns(*, include_reasoning: bool = True) -> tuple[Any, ...]:
    columns: list[Any] = [
        func.sum(UsageDaily.prompt_tokens).label("prompt_tokens"),
        func.sum(UsageDaily.completion_tokens).label("completion_tokens"),
    ]
    if include_reasoning:
        columns.append(func.sum(UsageDaily.reasoning_tokens).label("reasoning_tokens"))
    columns.extend(
        [
            func.sum(UsageDaily.cached_tokens).label("cached_tokens"),
            func.sum(UsageDaily.cache_creation_tokens).label("cache_creation_tokens"),
            func.sum(UsageDaily.total_tokens).label("total_tokens"),
        ]
    )
    return tuple(columns)


def _daily_usage_latency_columns(
    *, include_avg_latency: bool = True
) -> tuple[Any, ...]:
    columns: list[Any] = []
    if include_avg_latency:
        # Cast to REAL to avoid integer division truncation on SQLite
        columns.append(
            (
                func.cast(func.sum(UsageDaily.latency_sum_ms), types.REAL)
                / func.sum(UsageDaily.request_count)
            ).label("avg_latency_ms")
        )
    columns.append(func.sum(UsageDaily.latency_sum_ms).label("latency_sum_ms"))
    return tuple(columns)


def _daily_usage_cost_columns(*, include_input_output: bool = True) -> tuple[Any, ...]:
    columns: list[Any] = []
    if include_input_output:
        columns.extend(
            [
                func.sum(UsageDaily.input_cost_usd).label("input_cost_usd"),
                func.sum(UsageDaily.output_cost_usd).label("output_cost_usd"),
            ]
        )
    columns.append(func.sum(UsageDaily.total_cost_usd).label("total_cost_usd"))
    return tuple(columns)


def _daily_usage_effective_price_columns() -> tuple[Any, ...]:
    return (
        _avg_effective_price_expr(
            UsageDaily.total_cost_usd, UsageDaily.total_tokens
        ).label("avg_effective_price_usd"),
        _avg_effective_price_per_million_expr(
            UsageDaily.total_cost_usd, UsageDaily.total_tokens
        ).label("avg_effective_price_per_million_usd"),
    )


def _daily_usage_status_columns() -> tuple[Any, ...]:
    return (
        func.sum(UsageDaily.successful_requests).label("successful_requests"),
        func.sum(UsageDaily.failed_requests).label("failed_requests"),
        func.sum(UsageDaily.status_429).label("status_429"),
        func.sum(UsageDaily.status_4xx).label("status_4xx"),
        func.sum(UsageDaily.status_5xx).label("status_5xx"),
        func.sum(UsageDaily.status_unknown).label("status_unknown"),
    )


def fetch_recent_usage(
    *,
    user_id: str,
    limit: int,
    offset: int = 0,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    only_failed: bool = False,
    status_429: bool = False,
    status_4xx: bool = False,
    status_5xx: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent usage rows plus the resolved base URL when available."""
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        session_id=session_id,
        tool_name=tool_name,
        since=since,
        until=until,
        only_failed=only_failed,
        status_429=status_429,
        status_4xx=status_4xx,
        status_5xx=status_5xx,
    )
    # Subquery: aggregate tool names per usage_id into a comma-separated string.
    dialect = get_engine(db_path).dialect.name
    if dialect == "postgresql":
        tool_names_expr = func.string_agg(ToolCall.tool_name, ",").label("tool_names")
    else:
        tool_names_expr = func.group_concat(ToolCall.tool_name, ",").label("tool_names")
    tool_query = select(
        ToolCall.usage_id,
        tool_names_expr,
        func.sum(ToolCall.duration_ms).label("tool_duration_ms"),
    ).group_by(ToolCall.usage_id)
    tool_query = tool_query.where(ToolCall.user_id == user_id)
    tool_agg = tool_query.subquery()

    query = (
        select(
            Usage.id,
            Usage.ts,
            Usage.provider,
            Usage.model,
            Usage.client_source,
            Usage.session_id,
            Usage.endpoint,
            Usage.prompt_tokens,
            Usage.prompt_length,
            Usage.completion_tokens,
            Usage.reasoning_tokens,
            Usage.cached_tokens,
            Usage.total_tokens,
            Usage.latency_ms,
            Usage.ttft_ms,
            Usage.tool_tokens,
            Usage.cache_creation_tokens,
            Usage.input_cost_usd,
            Usage.output_cost_usd,
            Usage.total_cost_usd,
            Usage.status,
            Usage.device_id,
            Device.device_name.label("device_name"),
            Usage.base_url_id,
            Usage.price_snapshot_id,
            BaseUrl.base_url.label("base_url"),
            tool_agg.c.tool_names.label("tool_names"),
            tool_agg.c.tool_duration_ms.label("tool_duration_ms"),
        )
        .select_from(Usage)
        .outerjoin(BaseUrl, Usage.base_url_id == BaseUrl.id)
        .outerjoin(Device, Usage.device_id == Device.id)
        .outerjoin(tool_agg, tool_agg.c.usage_id == Usage.id)
        .order_by(Usage.ts.desc())
        .limit(limit)
        .offset(offset)
    )
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return [_row_to_dict(row) for row in connection.execute(query)]


def distinct_client_sources(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Return distinct client_source values from usage_daily."""
    filters = _daily_usage_filters(user_id=user_id, since=since, until=until)
    filters.append(UsageDaily.client_source != "")
    query = (
        select(UsageDaily.client_source)
        .distinct()
        .where(and_(*filters))
        .order_by(UsageDaily.client_source)
    )
    with get_engine(db_path).connect() as connection:
        return [row[0] for row in connection.execute(query)]


def distinct_tool_names(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Return distinct tool_name values from tool_calls within the time range.

    ``ToolCall.ts`` is stored in milliseconds (unlike ``Usage.ts`` which uses
    microseconds), so this function uses ``_iso_to_millis``.
    """
    filters: list[Any] = [ToolCall.user_id == user_id]
    if since:
        filters.append(ToolCall.ts >= _iso_to_millis(since))
    if until:
        filters.append(ToolCall.ts <= _iso_to_millis(until))
    query = select(ToolCall.tool_name).distinct().order_by(ToolCall.tool_name).limit(20)
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return [row[0] for row in connection.execute(query)]


TOOL_CALLS_QUERY_LIMIT = 500


def fetch_tool_calls(
    *,
    usage_id: str | None = None,
    session_id: str | None = None,
    user_id: str,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return tool calls for a single usage row or session, ordered by ts.

    Per-usage results include a row-relative ``start_ms`` when the source
    reports tool duration, so the timeline can place parallel tools correctly.
    """
    column = ToolCall.usage_id if usage_id is not None else ToolCall.session_id
    value = usage_id if usage_id is not None else session_id
    filters = [column == value]
    filters.append(ToolCall.user_id == user_id)
    query = (
        select(
            ToolCall.tool_use_id,
            ToolCall.usage_id,
            ToolCall.session_id,
            ToolCall.tool_name,
            ToolCall.client_source,
            ToolCall.duration_ms,
            ToolCall.ts,
        )
        .where(and_(*filters))
        .order_by(ToolCall.ts)
        .limit(TOOL_CALLS_QUERY_LIMIT)
    )
    with get_engine(db_path).connect() as connection:
        rows = [_row_to_dict(row) for row in connection.execute(query)]
        if usage_id is None or not rows:
            return rows
        usage_query = select(Usage.ts, Usage.latency_ms, Usage.client_source).where(
            Usage.id == usage_id
        )
        usage_query = usage_query.where(Usage.user_id == user_id)
        usage_row = connection.execute(usage_query).first()
        if usage_row is None:
            return rows
        # OpenCode/Kilo tool events carry the completion time and true execution
        # duration, and their latency includes tool time, so the row-relative
        # start is completion - duration. Other sources report a decision time
        # with latency excluding tool time, so they get no explicit start.
        usage_ts_micros, latency_ms, client_source = usage_row
        row_start_ms = (
            usage_ts_micros // 1000 - latency_ms
            if client_source in TOOL_DURATION_IN_LATENCY_SOURCES and latency_ms
            else None
        )
        for row in rows:
            duration_ms = row["duration_ms"]
            if row_start_ms is None or duration_ms is None or duration_ms < 0:
                row["start_ms"] = None
            else:
                row["start_ms"] = max(0, row["ts"] - duration_ms - row_start_ms)
        return rows


def count_usage(
    *,
    user_id: str,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    db_path: str | None = None,
) -> int:
    """Sum request_count from usage_daily matching the optional filters.

    When *session_id* is set the daily-aggregate table cannot help (it has no
    session_id column), so we fall back to counting rows in the raw ``usage``
    table instead.
    """
    if (
        session_id is not None
        or tool_name is not None
        or not _should_use_daily_table(since, until)
    ):
        filters = _usage_filters(
            user_id=user_id,
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
            session_id=session_id,
            tool_name=tool_name,
            since=since,
            until=until,
        )
        query = select(func.count()).select_from(Usage)
        if filters:
            query = query.where(and_(*filters))
        with get_engine(db_path).connect() as connection:
            result = connection.execute(query).scalar_one()
            return int(result or 0)

    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )
    query = select(func.sum(UsageDaily.request_count))
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        result = connection.execute(query).scalar_one()
        return int(result or 0)


def get_usage_high_watermark_ts(*, user_id: str, db_path: str | None = None) -> int:
    query = select(func.max(Usage.ts)).where(Usage.user_id == user_id)
    with get_engine(db_path).connect() as connection:
        value = connection.execute(query).scalar_one()
    return int(value or 0)


def summarize_usage_window(
    *,
    user_id: str,
    after_ts: int = 0,
    until_ts: int | None = None,
    since: str | None = None,
    until: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    include_rows: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        session_id=session_id,
        since=since,
        until=until,
    )
    filters.append(Usage.ts > after_ts)
    if until_ts is not None:
        filters.append(Usage.ts <= until_ts)

    query = (
        select(
            Usage.id,
            Usage.ts,
            Usage.provider,
            Usage.model,
            Usage.client_source,
            Usage.session_id,
            Usage.endpoint,
            Usage.prompt_tokens,
            Usage.completion_tokens,
            Usage.reasoning_tokens,
            Usage.cached_tokens,
            Usage.tool_tokens,
            Usage.cache_creation_tokens,
            Usage.total_tokens,
            Usage.latency_ms,
            Usage.ttft_ms,
            Usage.input_cost_usd,
            Usage.output_cost_usd,
            Usage.total_cost_usd,
            Usage.status,
        )
        .where(and_(*filters))
        .order_by(Usage.ts.asc())
    )

    with get_engine(db_path).connect() as connection:
        rows = [_row_to_dict(row) for row in connection.execute(query)]

    return _build_usage_window_summary(
        rows,
        after_ts=after_ts,
        until_ts=until_ts,
        include_rows=include_rows,
    )


def _build_usage_window_summary(
    rows: list[dict[str, Any]],
    *,
    after_ts: int,
    until_ts: int | None,
    include_rows: bool,
) -> dict[str, Any]:
    overall = _empty_usage_summary()
    overall_latencies: list[int] = []
    overall_ttfts: list[int] = []
    grouped: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
        "sessions": {},
        "client_sources": {},
        "models": {},
    }
    group_latencies: dict[tuple[str, tuple[Any, ...]], list[int]] = {}
    group_ttfts: dict[tuple[str, tuple[Any, ...]], list[int]] = {}

    def ensure_group(
        group_name: str,
        key: tuple[Any, ...],
        labels: dict[str, Any],
    ) -> dict[str, Any]:
        groups = grouped[group_name]
        if key not in groups:
            groups[key] = labels | _empty_usage_summary()
            group_latencies[(group_name, key)] = []
            group_ttfts[(group_name, key)] = []
        return groups[key]

    for row in rows:
        _add_row_to_summary(overall, row)
        if row.get("latency_ms") is not None:
            overall_latencies.append(int(row["latency_ms"]))
        if row.get("ttft_ms") is not None:
            overall_ttfts.append(int(row["ttft_ms"]))

        group_specs = [
            (
                "sessions",
                (row.get("session_id"),),
                {"session_id": row.get("session_id")},
            ),
            (
                "client_sources",
                (row.get("client_source"),),
                {"client_source": row.get("client_source")},
            ),
            (
                "models",
                (row.get("provider"), row.get("model")),
                {"provider": row.get("provider"), "model": row.get("model")},
            ),
        ]
        for group_name, key, labels in group_specs:
            group = ensure_group(group_name, key, labels)
            _add_row_to_summary(group, row)
            if row.get("latency_ms") is not None:
                group_latencies[(group_name, key)].append(int(row["latency_ms"]))
            if row.get("ttft_ms") is not None:
                group_ttfts[(group_name, key)].append(int(row["ttft_ms"]))

    _finalize_usage_summary(
        overall,
        latency_values=overall_latencies,
        ttft_values=overall_ttfts,
    )
    window_until_ts = until_ts
    if window_until_ts is None:
        window_until_ts = rows[-1]["ts"] if rows else after_ts

    result: dict[str, Any] = {
        "window": {
            "after_ts": after_ts,
            "until_ts": window_until_ts,
            "row_count": len(rows),
        },
        "summary": overall,
        "sessions": [],
        "client_sources": [],
        "models": [],
    }

    for group_name in ("sessions", "client_sources", "models"):
        values = []
        for key, summary in grouped[group_name].items():
            values.append(
                _finalize_usage_summary(
                    summary,
                    latency_values=group_latencies[(group_name, key)],
                    ttft_values=group_ttfts[(group_name, key)],
                )
            )
        result[group_name] = sorted(
            values,
            key=lambda item: (
                str(
                    item.get("session_id")
                    or item.get("client_source")
                    or item.get("provider")
                    or ""
                ),
                str(item.get("model") or ""),
            ),
        )

    if include_rows:
        result["rows"] = rows

    return result


def _summarize_usage_raw(
    *,
    user_id: str,
    select_cols: tuple[Any, ...],
    group_cols: tuple[Any, ...],
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate raw usage table for sub-day ranges."""
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        since=since,
        until=until,
    )

    latency_sum = func.coalesce(
        func.sum(case((Usage.latency_ms.isnot(None), Usage.latency_ms), else_=0)), 0
    )

    query = (
        select(
            *select_cols,
            func.count().label("requests"),
            func.coalesce(func.sum(Usage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(Usage.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(Usage.reasoning_tokens), 0).label(
                "reasoning_tokens"
            ),
            func.coalesce(func.sum(Usage.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(Usage.cache_creation_tokens), 0).label(
                "cache_creation_tokens"
            ),
            func.coalesce(func.sum(Usage.total_tokens), 0).label("total_tokens"),
            func.avg(Usage.latency_ms).label("avg_latency_ms"),
            latency_sum.label("latency_sum_ms"),
            func.coalesce(func.sum(Usage.input_cost_usd), 0).label("input_cost_usd"),
            func.coalesce(func.sum(Usage.output_cost_usd), 0).label("output_cost_usd"),
            func.coalesce(func.sum(Usage.total_cost_usd), 0).label("total_cost_usd"),
            _avg_effective_price_expr(Usage.total_cost_usd, Usage.total_tokens).label(
                "avg_effective_price_usd"
            ),
            _avg_effective_price_per_million_expr(
                Usage.total_cost_usd, Usage.total_tokens
            ).label("avg_effective_price_per_million_usd"),
            func.count(
                case((or_(Usage.status.is_(None), Usage.status < 400), 1))
            ).label("successful_requests"),
            func.count(
                case((and_(Usage.status.isnot(None), Usage.status >= 400), 1))
            ).label("failed_requests"),
            func.count(case((Usage.status == 429, 1))).label("status_429"),
            func.count(
                case(
                    (
                        and_(
                            Usage.status >= 400, Usage.status < 500, Usage.status != 429
                        ),
                        1,
                    )
                )
            ).label("status_4xx"),
            func.count(case((Usage.status >= 500, 1))).label("status_5xx"),
            literal_column("0").label("status_unknown"),
        )
        .group_by(*group_cols)
        .order_by(func.coalesce(func.sum(Usage.total_tokens), 0).desc())
    )
    if filters:
        query = query.where(and_(*filters))
    tool_map = _tool_duration_sum_by(
        *group_cols,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )
    with get_engine(db_path).connect() as connection:
        result = [_row_to_dict(row) for row in connection.execute(query)]
    return _attach_tool_throughput(
        result, tool_map, lambda d: tuple(d.get(col.name) for col in group_cols)
    )


def summarize_tool_calls(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    only_failed: bool = False,
    status_429: bool = False,
    status_4xx: bool = False,
    status_5xx: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate tool call counts and durations by tool_name, filtered like the usage log.

    duration_ms is sparsely populated (only sources that emit tool timing),
    so total/avg are None when no timed calls exist for a tool.
    """
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        since=since,
        until=until,
        only_failed=only_failed,
        status_429=status_429,
        status_4xx=status_4xx,
        status_5xx=status_5xx,
    )
    filters.append(ToolCall.user_id == user_id)
    query = (
        select(
            ToolCall.tool_name,
            func.count(ToolCall.tool_use_id).label("count"),
            func.sum(ToolCall.duration_ms).label("total_duration_ms"),
            func.avg(ToolCall.duration_ms).label("avg_duration_ms"),
        )
        .select_from(ToolCall)
        .join(Usage, Usage.id == ToolCall.usage_id)
        .group_by(ToolCall.tool_name)
        .order_by(func.count(ToolCall.tool_use_id).desc())
        .limit(20)
    )
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return [_row_to_dict(row) for row in connection.execute(query)]


def summarize_usage_by_source(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate usage totals by client_source for dashboard source chart."""
    if not _should_use_daily_table(since, until):
        return _summarize_usage_raw(
            user_id=user_id,
            select_cols=(Usage.client_source,),
            group_cols=(Usage.client_source,),
            since=since,
            until=until,
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
            db_path=db_path,
        )

    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    query = (
        select(
            UsageDaily.client_source,
            func.sum(UsageDaily.request_count).label("requests"),
            *_daily_usage_token_columns(),
            *_daily_usage_latency_columns(),
            *_daily_usage_cost_columns(),
            *_daily_usage_status_columns(),
        )
        .group_by(UsageDaily.client_source)
        .order_by(func.sum(UsageDaily.total_tokens).desc())
    )
    if filters:
        query = query.where(and_(*filters))
    tool_map = _tool_duration_sum_by(
        Usage.client_source,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )
    with get_engine(db_path).connect() as connection:
        result = [_row_to_dict(row) for row in connection.execute(query)]
    return _attach_tool_throughput(
        result, tool_map, lambda d: (d.get("client_source"),)
    )


def summarize_usage_by_provider(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate usage totals by provider for dashboard provider chart."""
    if not _should_use_daily_table(since, until):
        return _summarize_usage_raw(
            user_id=user_id,
            select_cols=(Usage.provider,),
            group_cols=(Usage.provider,),
            since=since,
            until=until,
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
            db_path=db_path,
        )

    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    query = (
        select(
            UsageDaily.provider,
            func.sum(UsageDaily.request_count).label("requests"),
            *_daily_usage_token_columns(),
            *_daily_usage_latency_columns(),
            *_daily_usage_cost_columns(),
            *_daily_usage_effective_price_columns(),
            *_daily_usage_status_columns(),
        )
        .group_by(UsageDaily.provider)
        .order_by(func.sum(UsageDaily.total_tokens).desc())
    )
    if filters:
        query = query.where(and_(*filters))
    tool_map = _tool_duration_sum_by(
        Usage.provider,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )
    with get_engine(db_path).connect() as connection:
        result = [_row_to_dict(row) for row in connection.execute(query)]
    return _attach_tool_throughput(result, tool_map, lambda d: (d.get("provider"),))


def summarize_usage_daily(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate usage totals by provider and model for dashboard summaries.

    For sub-2-day time ranges, queries the raw usage table for precision.
    """
    if not _should_use_daily_table(since, until):
        return _summarize_usage_raw(
            user_id=user_id,
            select_cols=(Usage.provider, Usage.model),
            group_cols=(Usage.provider, Usage.model),
            since=since,
            until=until,
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
            db_path=db_path,
        )

    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    query = (
        select(
            UsageDaily.provider,
            UsageDaily.model,
            func.sum(UsageDaily.request_count).label("requests"),
            *_daily_usage_token_columns(),
            *_daily_usage_latency_columns(),
            *_daily_usage_cost_columns(),
            *_daily_usage_effective_price_columns(),
            *_daily_usage_status_columns(),
        )
        .group_by(UsageDaily.provider, UsageDaily.model)
        .order_by(func.sum(UsageDaily.total_tokens).desc())
    )
    if filters:
        query = query.where(and_(*filters))
    tool_map = _tool_duration_sum_by(
        Usage.provider,
        Usage.model,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )
    with get_engine(db_path).connect() as connection:
        result = [_row_to_dict(row) for row in connection.execute(query)]
    return _attach_tool_throughput(
        result, tool_map, lambda d: (d.get("provider"), d.get("model"))
    )


# === Period aggregation ===


def _parse_tz_offset(tz_offset: str) -> tuple[int, int]:
    """Parse '+05:30' into (hours, minutes) with sign."""
    sign = 1 if tz_offset.startswith("+") else -1
    hours_str, minutes_str = tz_offset[1:].split(":", 1)
    return sign * int(hours_str), sign * int(minutes_str)


def _period_expression(granularity: str, tz_offset: str) -> Any:
    """Return a dialect-aware SQL expression that buckets Usage.ts into period strings.

    Usage.ts is stored as epoch microseconds (int). This function converts to a
    datetime with timezone offset, then formats to the requested granularity.
    """
    dialect = get_engine().dialect.name
    offset_hours, offset_minutes = _parse_tz_offset(tz_offset)

    if dialect == "sqlite":
        fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
        modifiers: list[str] = ["unixepoch"]
        if offset_hours:
            modifiers.append(f"{offset_hours:+d} hours")
        if offset_minutes:
            modifiers.append(f"{offset_minutes:+d} minutes")
        return func.strftime(fmt, func.datetime(Usage.ts / 1_000_000, *modifiers))

    if dialect == "postgresql":
        pg_fmt = "YYYY-MM-DD HH24:00" if granularity == "hour" else "YYYY-MM-DD"
        ts_cast = func.to_timestamp(Usage.ts / 1_000_000.0)
        parts: list[str] = []
        if offset_hours:
            parts.append(f"{offset_hours} hours")
        if offset_minutes:
            parts.append(f"{offset_minutes} minutes")
        ts_adjusted = (
            ts_cast + text(f"interval '{' '.join(parts)}'") if parts else ts_cast
        )
        # to_char() on a timestamptz implicitly converts to the session's
        # TimeZone setting first; anchor to UTC so bucketing doesn't depend
        # on the server/role's configured default timezone. self_group()
        # parenthesizes ts_adjusted so AT TIME ZONE binds to the whole sum,
        # not just the trailing interval.
        return func.to_char(ts_adjusted.self_group().op("AT TIME ZONE")("UTC"), pg_fmt)

    if dialect == "mysql":
        fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
        ts_parsed = func.from_unixtime(Usage.ts / 1_000_000.0)
        mysql_parts: list[str] = []
        if offset_hours:
            mysql_parts.append(f"INTERVAL {offset_hours} HOUR")
        if offset_minutes:
            mysql_parts.append(f"INTERVAL {offset_minutes} MINUTE")
        ts_adjusted = ts_parsed
        for part in mysql_parts:
            ts_adjusted = func.date_add(ts_adjusted, text(part))
        return func.date_format(ts_adjusted, fmt)

    raise ValueError(f"Unsupported database dialect: {dialect}")


def aggregate_usage_by_period(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    granularity: str = "day",
    tz_offset: str = "+00:00",
) -> list[dict[str, Any]]:
    """Bucket usage into local-time hourly or daily periods via SQL GROUP BY."""
    filters = _usage_filters(
        user_id=user_id,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        since=since,
        until=until,
    )

    period_expr = _period_expression(granularity, tz_offset)

    latency_count = func.count(case((Usage.latency_ms.isnot(None), 1)))
    latency_sum = func.coalesce(
        func.sum(case((Usage.latency_ms.isnot(None), Usage.latency_ms), else_=0)),
        0,
    )

    query = (
        select(
            period_expr.label("period"),
            func.count().label("requests"),
            func.coalesce(func.sum(Usage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(Usage.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(Usage.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(func.sum(Usage.cache_creation_tokens), 0).label(
                "cache_creation_tokens"
            ),
            func.coalesce(func.sum(Usage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(Usage.input_cost_usd), 0).label("input_cost_usd"),
            func.coalesce(func.sum(Usage.output_cost_usd), 0).label("output_cost_usd"),
            func.coalesce(func.sum(Usage.total_cost_usd), 0).label("total_cost_usd"),
            latency_sum.label("latency_sum"),
            latency_count.label("latency_count"),
            func.count(
                case((and_(Usage.status.isnot(None), Usage.status >= 400), 1))
            ).label("failed_requests"),
            func.count(
                case((or_(Usage.status.is_(None), Usage.status < 400), 1))
            ).label("successful_requests"),
        )
        .group_by(period_expr)
        .order_by(period_expr)
    )
    if filters:
        query = query.where(and_(*filters))

    tool_map = _tool_duration_sum_by(
        period_expr,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    result = []
    with get_engine().connect() as connection:
        for row in connection.execute(query):
            count = row.latency_count
            avg_latency = row.latency_sum / count if count > 0 else 0
            result.append(
                {
                    "period": row.period,
                    "requests": row.requests,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "cached_tokens": row.cached_tokens,
                    "cache_creation_tokens": row.cache_creation_tokens,
                    "total_tokens": row.total_tokens,
                    "input_cost_usd": _normalize_value(row.input_cost_usd),
                    "output_cost_usd": _normalize_value(row.output_cost_usd),
                    "total_cost_usd": _normalize_value(row.total_cost_usd),
                    "latency_sum_ms": _normalize_value(row.latency_sum),
                    "avg_latency_ms": _normalize_value(avg_latency),
                    "successful_requests": row.successful_requests,
                    "failed_requests": row.failed_requests,
                }
            )
    return _attach_tool_throughput(result, tool_map, lambda d: (d["period"],))


def aggregate_daily_by_period(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read daily-bucketed usage from the pre-aggregated usage_daily table."""
    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    query = (
        select(
            UsageDaily.date.label("period"),
            func.sum(UsageDaily.request_count).label("requests"),
            *_daily_usage_token_columns(include_reasoning=False),
            *_daily_usage_cost_columns(),
            *_daily_usage_status_columns(),
            *_daily_usage_latency_columns(),
        )
        .group_by(UsageDaily.date)
        .order_by(UsageDaily.date)
    )
    if filters:
        query = query.where(and_(*filters))

    # usage_daily.date is the UTC date of Usage.ts, so bucket tool sums the same.
    tool_map = _tool_duration_sum_by(
        _period_expression("day", "+00:00"),
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )
    with get_engine(db_path).connect() as connection:
        result = [_row_to_dict(row) for row in connection.execute(query)]
    return _attach_tool_throughput(result, tool_map, lambda d: (d.get("period"),))


def aggregate_daily_by_dimension(
    *,
    user_id: str,
    dimension: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read daily-bucketed usage grouped by a dimension (model/provider/client_source/device)."""
    filters = _daily_usage_filters(
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )

    dim_col: Any
    name_cols: list[Any] = []
    if dimension == "provider":
        dim_col = UsageDaily.provider
    elif dimension == "client_source":
        dim_col = UsageDaily.client_source
    elif dimension == "device":
        dim_col = UsageDaily.device_id  # NULL = Unknown
        name_cols = [Device.device_name.label("dimension_name")]
    else:
        dim_col = UsageDaily.model
    group_cols: list[Any] = [dim_col, *name_cols, UsageDaily.date]

    query = (
        select(
            dim_col.label("dimension"),
            *name_cols,
            UsageDaily.date.label("period"),
            func.sum(UsageDaily.request_count).label("requests"),
            func.sum(UsageDaily.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageDaily.cached_tokens).label("cached_tokens"),
            func.sum(UsageDaily.cache_creation_tokens).label("cache_creation_tokens"),
            func.sum(UsageDaily.total_tokens).label("total_tokens"),
            func.sum(UsageDaily.total_cost_usd).label("total_cost_usd"),
            func.sum(UsageDaily.completion_tokens).label("completion_tokens"),
            func.sum(UsageDaily.latency_sum_ms).label("latency_sum_ms"),
            *_daily_usage_status_columns(),
        )
        .select_from(UsageDaily)
        .group_by(*group_cols)
        .order_by(UsageDaily.date)
    )
    if dimension == "device":
        query = query.outerjoin(Device, UsageDaily.device_id == Device.id)
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return [_row_to_dict(row) for row in connection.execute(query)]
