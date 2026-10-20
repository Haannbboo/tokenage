"""Usage persistence, aggregation, and reporting helpers.

Extracted from database/__init__.py during Phase 5 refactoring.
"""

from __future__ import annotations

import calendar
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    and_,
    func,
    literal_column,
    select,
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
from .models import BaseUrl, Device, ToolCall, Usage, UsageHourly

logger = logging.getLogger(__name__)


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
    they're already folded into `usage_hourly`/`sessions` running sums the
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

    The row and its usage_hourly increment land together or not at all: the
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
        upsert_hourly_aggregate(session, usage)
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
    # A NULL in the hourly unique key never conflicts, so a device-less live row
    # would silently split its hourly bucket instead of incrementing it.
    if usage.device_id is None:
        raise ValueError("usage.device_id is required on the live write path")


def _usage_date(usage: Usage) -> str:
    """UTC date of a usage row (its price snapshot's date)."""
    return datetime.fromtimestamp(micros_to_secs(usage.ts), tz=UTC).strftime("%Y-%m-%d")


def _usage_hour(usage: Usage) -> int:
    """UTC hour start, in epoch seconds, of a usage row's hourly aggregate."""
    return usage.ts // 3_600_000_000 * 3600


def _usage_hourly_match(usage: Usage, hour: int) -> Any:
    """Filter selecting the usage_hourly row that aggregates *usage*'s group.

    Shared by every incremental usage_hourly update so the match fields
    can't drift between callers.
    """
    return and_(
        UsageHourly.hour == hour,
        UsageHourly.provider == usage.provider,
        UsageHourly.model == usage.model,
        UsageHourly.client_source == (usage.client_source or ""),
        UsageHourly.user_id == usage.user_id,
        UsageHourly.device_id == usage.device_id,
    )


def hourly_values(usage: Usage) -> dict[str, Any]:
    """The usage_hourly contribution (key columns + additive counters) of one row."""
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
        "hour": _usage_hour(usage),
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


def upsert_hourly_aggregate(session: Session, usage: Usage) -> None:
    """Add *usage* to usage_hourly on *session*; the caller owns the commit."""
    _require_device_id(usage)
    values = hourly_values(usage)

    engine = session.get_bind()
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert

    statement = insert_fn(UsageHourly).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[
            "user_id",
            "hour",
            "provider",
            "model",
            "client_source",
            "device_id",
        ],
        set_={
            "request_count": UsageHourly.request_count
            + statement.excluded.request_count,
            "prompt_tokens": UsageHourly.prompt_tokens
            + statement.excluded.prompt_tokens,
            "completion_tokens": UsageHourly.completion_tokens
            + statement.excluded.completion_tokens,
            "reasoning_tokens": UsageHourly.reasoning_tokens
            + statement.excluded.reasoning_tokens,
            "cached_tokens": UsageHourly.cached_tokens
            + statement.excluded.cached_tokens,
            "total_tokens": UsageHourly.total_tokens + statement.excluded.total_tokens,
            "tool_tokens": UsageHourly.tool_tokens + statement.excluded.tool_tokens,
            "cache_creation_tokens": UsageHourly.cache_creation_tokens
            + statement.excluded.cache_creation_tokens,
            "prompt_length": UsageHourly.prompt_length
            + statement.excluded.prompt_length,
            "input_cost_usd": UsageHourly.input_cost_usd
            + statement.excluded.input_cost_usd,
            "output_cost_usd": UsageHourly.output_cost_usd
            + statement.excluded.output_cost_usd,
            "total_cost_usd": UsageHourly.total_cost_usd
            + statement.excluded.total_cost_usd,
            "successful_requests": UsageHourly.successful_requests
            + statement.excluded.successful_requests,
            "failed_requests": UsageHourly.failed_requests
            + statement.excluded.failed_requests,
            "latency_sum_ms": UsageHourly.latency_sum_ms
            + statement.excluded.latency_sum_ms,
            "status_429": UsageHourly.status_429 + statement.excluded.status_429,
            "status_4xx": UsageHourly.status_4xx + statement.excluded.status_4xx,
            "status_5xx": UsageHourly.status_5xx + statement.excluded.status_5xx,
            "status_unknown": UsageHourly.status_unknown
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
    snapshots and delta-adjust the usage_hourly and sessions rollups, all on
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

    hourly: dict[tuple[Any, ...], tuple[Usage, int, dict[str, Decimal]]] = {}
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
        hour = _usage_hour(usage)
        hourly_key = (
            hour,
            usage.provider,
            usage.model,
            usage.client_source or "",
            usage.user_id,
            usage.device_id,
        )
        if hourly_key in hourly:
            sums = hourly[hourly_key][2]
            for field, delta in deltas.items():
                sums[field] += delta
        else:
            hourly[hourly_key] = (usage, hour, dict(deltas))
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
    for hourly_key in sorted(hourly, key=repr):
        usage, hour, sums = hourly[hourly_key]
        hourly_result = session.execute(
            update(UsageHourly)
            .where(_usage_hourly_match(usage, hour))
            .values(
                input_cost_usd=UsageHourly.input_cost_usd + sums["input_cost_usd"],
                output_cost_usd=UsageHourly.output_cost_usd + sums["output_cost_usd"],
                total_cost_usd=UsageHourly.total_cost_usd + sums["total_cost_usd"],
            )
        )
        if hourly_result.rowcount == 0:  # type: ignore[attr-defined]
            logger.warning(
                "reprice: no usage_hourly row for hour=%s "
                "provider=%s model=%s client_source=%s, rollup left as-is",
                hour,
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
    usage_hourly and sessions rollups in sync, all in one transaction. Returns
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
    stored costs, session/hourly rollups, and the snapshot binding stay
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


def _tz_seconds(tz_offset: str) -> int:
    """Parse '+05:30' into signed seconds."""
    sign = 1 if tz_offset.startswith("+") else -1
    hours_str, minutes_str = tz_offset[1:].split(":", 1)
    return sign * (int(hours_str) * 3600 + int(minutes_str) * 60)


def _hour_bounds(since: str | None, until: str | None) -> tuple[int | None, int | None]:
    """Inclusive usage_hourly.hour bounds of a range, rounded to the UTC hour.

    A bound without a timezone is UTC, as in ``_iso_to_micros``; a date-only
    *until* covers that whole day.
    """

    def hour_of(value: str) -> int:
        return _iso_to_micros(value) // 3_600_000_000 * 3600

    since_hour = hour_of(since) if since else None
    until_hour = None
    if until:
        until_hour = hour_of(until) + (86400 - 3600 if len(until) == 10 else 0)
    return since_hour, until_hour


def _hourly_filters(
    *,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
) -> list[Any]:
    filters: list[Any] = [UsageHourly.user_id == user_id]
    since_hour, until_hour = _hour_bounds(since, until)
    if since_hour is not None:
        filters.append(UsageHourly.hour >= since_hour)
    if until_hour is not None:
        filters.append(UsageHourly.hour <= until_hour)
    if provider:
        filters.append(UsageHourly.provider == normalize_provider_name(provider))
    if model:
        filters.append(UsageHourly.model == normalize_model_name(model))
    if client_source:
        filters.append(UsageHourly.client_source == client_source)
    if device_id:
        filters.append(_device_filter(UsageHourly.device_id, device_id))
    return filters


ROLLUP_SUM_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "cache_creation_tokens",
    "total_tokens",
    "input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
    "latency_sum_ms",
    "successful_requests",
    "failed_requests",
    "status_429",
    "status_4xx",
    "status_5xx",
    "status_unknown",
)


def _rollup(
    *group_cols: Any,
    user_id: str,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    order_by: Any = None,
    join_device: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Sum usage_hourly over a range and filters, grouped by *group_cols*.

    The one query behind every dashboard summary and chart; rows get the
    derived averages and throughput. Default order is total tokens, desc.
    """
    total_tokens = func.sum(UsageHourly.total_tokens)
    query = (
        select(
            *group_cols,
            func.sum(UsageHourly.request_count).label("requests"),
            *(
                func.sum(getattr(UsageHourly, field)).label(field)
                for field in ROLLUP_SUM_FIELDS
            ),
        )
        .select_from(UsageHourly)
        .where(
            *_hourly_filters(
                user_id=user_id,
                since=since,
                until=until,
                provider=provider,
                model=model,
                client_source=client_source,
                device_id=device_id,
            )
        )
        .group_by(*group_cols)
        .order_by(total_tokens.desc() if order_by is None else order_by)
    )
    if join_device:
        query = query.outerjoin(Device, UsageHourly.device_id == Device.id)
    with get_engine(db_path).connect() as connection:
        rows = [_row_to_dict(row) for row in connection.execute(query)]
    for row in rows:
        latency_sum = row["latency_sum_ms"] or 0
        row["avg_latency_ms"] = latency_sum / row["requests"]
        row["avg_throughput"] = (
            row["completion_tokens"] * 1000.0 / latency_sum if latency_sum > 0 else None
        )
        row["avg_effective_price_usd"] = (
            row["total_cost_usd"] / row["total_tokens"] if row["total_tokens"] else 0
        )
        row["avg_effective_price_per_million_usd"] = (
            row["avg_effective_price_usd"] * 1_000_000
        )
    return rows


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
    tool_query = select(ToolCall.usage_id, tool_names_expr).group_by(ToolCall.usage_id)
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
    """Return distinct client_source values from usage_hourly."""
    filters = _hourly_filters(user_id=user_id, since=since, until=until)
    filters.append(UsageHourly.client_source != "")
    query = (
        select(UsageHourly.client_source)
        .distinct()
        .where(and_(*filters))
        .order_by(UsageHourly.client_source)
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
    """Return tool calls for a single usage row or session, ordered by ts."""
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
        return [_row_to_dict(row) for row in connection.execute(query)]


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
    """Count usage rows matching the optional filters.

    Unbounded counts (the Overview total) read usage_hourly; a time range,
    session or tool counts raw ``usage`` rows exactly, matching the Logs rows.
    """
    if since or until or session_id is not None or tool_name is not None:
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
        query = select(func.count()).select_from(Usage).where(and_(*filters))
    else:
        filters = _hourly_filters(
            user_id=user_id,
            since=since,
            until=until,
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
        )
        query = select(func.sum(UsageHourly.request_count)).where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return int(connection.execute(query).scalar_one() or 0)


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
    return _rollup(
        UsageHourly.client_source,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
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
    return _rollup(
        UsageHourly.provider,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )


def summarize_usage_by_model(
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
    """Aggregate usage totals by provider and model for dashboard summaries."""
    return _rollup(
        UsageHourly.provider,
        UsageHourly.model,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        db_path=db_path,
    )


# === Period aggregation ===


def _local_period(granularity: str, tz_offset: str) -> tuple[Any, str]:
    """Local period key (the period start as local-shifted epoch seconds) of a
    usage_hourly row and its display format.

    ponytail: a half-hour offset shifts whole UTC hours, so a request within
    30 min of local midnight can land on the neighbouring day; 15-min rows
    would make it exact.
    """
    size, fmt = (
        (3600, "%Y-%m-%d %H:00") if granularity == "hour" else (86400, "%Y-%m-%d")
    )
    # Inline integers: Postgres only matches the GROUP BY to the SELECT
    # expression when both render identically, which bind params don't.
    offset = literal_column(str(_tz_seconds(tz_offset)), BigInteger)
    # Modulo, not division: `/` is float on MySQL, so this stays an integer.
    local = UsageHourly.hour + offset
    period = local - local % literal_column(str(size), BigInteger)
    return period.label("period"), fmt


def _format_periods(rows: list[dict[str, Any]], fmt: str) -> None:
    for row in rows:
        row["period"] = datetime.fromtimestamp(row["period"], UTC).strftime(fmt)


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
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Bucket usage into local-time hourly or daily periods."""
    period, fmt = _local_period(granularity, tz_offset)
    rows = _rollup(
        period,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        order_by=period,
        db_path=db_path,
    )
    _format_periods(rows, fmt)
    return rows


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
    tz_offset: str = "+00:00",
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Local-day usage grouped by a dimension (model/provider/client_source/device)."""
    name_cols: list[Any] = []
    dim_col: Any
    if dimension == "provider":
        dim_col = UsageHourly.provider
    elif dimension == "client_source":
        dim_col = UsageHourly.client_source
    elif dimension == "device":
        dim_col = UsageHourly.device_id  # NULL = Unknown
        name_cols = [Device.device_name.label("dimension_name")]
    else:
        dim_col = UsageHourly.model
    period, fmt = _local_period("day", tz_offset)
    rows = _rollup(
        dim_col.label("dimension"),
        *name_cols,
        period,
        user_id=user_id,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        order_by=period,
        join_device=dimension == "device",
        db_path=db_path,
    )
    _format_periods(rows, fmt)
    return rows
