"""Session-related database operations.

Extracted from database/__init__.py during Phase 5 refactoring.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..utils import secs_to_micros
from .engine import get_engine
from .models import (
    SessionRecord,
    ToolCall,
    Usage,
)
from .usage import (
    _format_periods,
    _granularity,
    _tz_seconds,
    _usage_filters,
)

logger = logging.getLogger(__name__)


def _project_buffer():
    from ..shared_state import SharedMapping

    return SharedMapping("hooks:session", ttl=86400, cap=100_000)


def apply_session_project(
    session: Session, *, user_id: str, session_id: str, project: str
) -> None:
    """First accepted name wins; never create metadata-only session rows."""
    from protocol.events import sanitize_project

    project = sanitize_project(project)
    # The UPDATE user_id predicate enforces ownership; do not remove it.
    owner = session.scalar(
        select(SessionRecord.user_id).where(SessionRecord.session_id == session_id)
    )
    if owner is not None:
        if owner == user_id:
            # Atomic even when usage or another hook holds the row lock.
            session.execute(
                update(SessionRecord)
                .where(
                    SessionRecord.session_id == session_id,
                    SessionRecord.user_id == user_id,
                    SessionRecord.project_name.is_(None),
                )
                .values(project_name=project)
            )
        return
    buffer = _project_buffer()
    key = f"{user_id}:{session_id}"
    buffer.setdefault(key, project)


def _take_session_project(user_id: str, session_id: str) -> str | None:
    return _project_buffer().pop(f"{user_id}:{session_id}", None)


def get_session_record(
    db_session: Session,
    session_id: str,
    user_id: str,
    *,
    with_for_update: bool = False,
) -> SessionRecord | None:
    """Return the session row matching (session_id, user_id)."""
    query = select(SessionRecord).where(
        and_(
            SessionRecord.session_id == session_id,
            SessionRecord.user_id == user_id,
        )
    )
    if with_for_update:
        query = query.with_for_update()
    return db_session.scalar(query)


def _successful_usage_count(usages: list[Usage]) -> int:
    return sum(1 for usage in usages if usage.status is None or usage.status < 400)


def _primary_by_cost(costs: dict[str, float]) -> str | None:
    if not costs:
        return None
    return max(costs, key=costs.get)  # type: ignore[arg-type]


MAX_TIME_BUCKETS = 5000


def _load_cost_map(payload: str | None) -> dict[str, float]:
    return json.loads(payload or "{}")


def _session_models(payload: str | None) -> list[str]:
    """Models used by a session, costliest first; bad JSON yields none."""
    try:
        costs = json.loads(payload or "{}")
    except (ValueError, TypeError):
        return []
    if not isinstance(costs, dict):
        return []
    numeric = {
        model: cost
        for model, cost in costs.items()
        if type(cost) is int or (type(cost) is float and math.isfinite(cost))
    }
    return sorted(numeric, key=lambda model: numeric[model], reverse=True)


def _add_usage_to_cost_maps(
    usage: Usage,
    *,
    total_cost: Decimal,
    models_costs: dict[str, float],
    providers_costs: dict[str, float],
) -> None:
    models_costs[usage.model] = models_costs.get(usage.model, 0) + float(total_cost)
    providers_costs[usage.provider] = providers_costs.get(usage.provider, 0) + float(
        total_cost
    )


def _cost_maps_from_usage(
    usages: list[Usage],
) -> tuple[dict[str, float], dict[str, float]]:
    models_costs: dict[str, float] = {}
    providers_costs: dict[str, float] = {}
    for usage in usages:
        _add_usage_to_cost_maps(
            usage,
            total_cost=Decimal(str(usage.total_cost_usd)),
            models_costs=models_costs,
            providers_costs=providers_costs,
        )
    return models_costs, providers_costs


def _session_record_kwargs(
    session_id: str,
    usages: list[Usage],
    *,
    now: str,
    missing_ttft_as_zero: bool = False,
) -> dict[str, Any]:
    request_count = len(usages)
    successful = _successful_usage_count(usages)
    total_cost = sum(Decimal(str(usage.total_cost_usd)) for usage in usages)
    latency_sum = sum(usage.latency_ms or 0 for usage in usages)
    if missing_ttft_as_zero:
        ttft_values = [usage.ttft_ms or 0 for usage in usages]
    else:
        ttft_values = [usage.ttft_ms for usage in usages if usage.ttft_ms is not None]
    models_costs, providers_costs = _cost_maps_from_usage(usages)

    return {
        "session_id": session_id,
        "user_id": usages[0].user_id,
        "client_source": usages[0].client_source,
        "started": usages[0].ts,
        "ended": usages[-1].ts,
        "request_count": request_count,
        "successful_requests": successful,
        "failed_requests": request_count - successful,
        "total_tokens": sum(usage.total_tokens or 0 for usage in usages),
        "prompt_tokens": sum(usage.prompt_tokens or 0 for usage in usages),
        "completion_tokens": sum(usage.completion_tokens or 0 for usage in usages),
        "cached_tokens": sum(usage.cached_tokens or 0 for usage in usages),
        "cache_creation_tokens": sum(
            usage.cache_creation_tokens or 0 for usage in usages
        ),
        "total_cost_usd": total_cost,
        "latency_sum_ms": latency_sum,
        "avg_latency_ms": latency_sum / request_count if request_count else None,
        "avg_ttft_ms": sum(ttft_values) / len(ttft_values) if ttft_values else None,
        "primary_provider": _primary_by_cost(providers_costs),
        "primary_model": _primary_by_cost(models_costs),
        "providers_json": json.dumps(providers_costs),
        "models_json": json.dumps(models_costs),
        "last_usage_id": usages[-1].id,
        "updated_at": now,
    }


def upsert_session_from_usage(usage: Usage, db_path: str | None = None) -> None:
    """Incrementally update the sessions table for a single usage row."""
    if not usage.session_id:
        return
    with Session(get_engine(db_path)) as session:
        _upsert_session_from_usage(session, usage)
        session.commit()


def _upsert_session_from_usage(session: Session, usage: Usage) -> None:
    """Add *usage* to its session record on *session*; the caller commits."""
    if not usage.session_id:
        return

    is_success = usage.status is None or usage.status < 400
    latency = usage.latency_ms or 0
    ttft = usage.ttft_ms or 0
    total_cost = Decimal(str(usage.total_cost_usd))
    now = datetime.now(UTC).isoformat()

    # Lock the row (PostgreSQL): the counters below are read-modify-write in
    # Python, so without it two concurrent usages of one session overwrite each
    # other's increment. SQLite ignores FOR UPDATE; there log_usage's earlier
    # insert already holds the write lock. The standalone wrapper above does
    # not, so concurrent calls to it can still lose an increment on SQLite.
    existing = get_session_record(
        session, usage.session_id, user_id=usage.user_id, with_for_update=True
    )
    if existing is None:
        # Two first usages can both find no row; the loser of this insert must
        # fall through and add to the winner's row instead of failing.
        insert_fn = (
            pg_insert
            if session.get_bind().dialect.name == "postgresql"
            else sqlite_insert
        )
        pending_project = _project_buffer().get(f"{usage.user_id}:{usage.session_id}")
        created = session.execute(
            insert_fn(SessionRecord)
            .values(
                **_session_record_kwargs(
                    usage.session_id, [usage], now=now, missing_ttft_as_zero=True
                ),
                project_name=pending_project,
            )
            .on_conflict_do_nothing(index_elements=["session_id"])
            # rowcount is -1 for this statement on psycopg; RETURNING is exact.
            .returning(SessionRecord.session_id)
        )
        if created.first() is not None:
            if pending_project is not None:
                _take_session_project(usage.user_id, usage.session_id)
            return
        existing = get_session_record(
            session, usage.session_id, user_id=usage.user_id, with_for_update=True
        )
    if existing is None:
        # The session_id primary key is global and belongs to another user.
        logger.warning(
            "Session rollup skipped: session_id %r is owned by another user",
            usage.session_id,
        )
        return
    if existing:
        if usage.ts < existing.started:
            existing.started = usage.ts
        if usage.ts > existing.ended:
            existing.ended = usage.ts

        existing.request_count += 1
        existing.prompt_tokens += usage.prompt_tokens or 0
        existing.completion_tokens += usage.completion_tokens or 0
        existing.cached_tokens += usage.cached_tokens or 0
        existing.cache_creation_tokens += usage.cache_creation_tokens or 0
        existing.total_tokens += usage.total_tokens or 0
        existing.total_cost_usd += total_cost
        existing.latency_sum_ms += latency
        existing.successful_requests += 1 if is_success else 0
        existing.failed_requests += 0 if is_success else 1

        existing.avg_latency_ms = existing.latency_sum_ms / existing.request_count
        old_n = existing.request_count - 1
        old_avg_ttft = float(existing.avg_ttft_ms or 0)
        existing.avg_ttft_ms = (old_avg_ttft * old_n + ttft) / existing.request_count

        models_costs = _load_cost_map(existing.models_json)
        providers_costs = _load_cost_map(existing.providers_json)
        _add_usage_to_cost_maps(
            usage,
            total_cost=total_cost,
            models_costs=models_costs,
            providers_costs=providers_costs,
        )

        existing.models_json = json.dumps(models_costs)
        existing.providers_json = json.dumps(providers_costs)
        existing.primary_model = _primary_by_cost(models_costs)
        existing.primary_provider = _primary_by_cost(providers_costs)

        existing.last_usage_id = usage.id
        existing.updated_at = now


def _load_tool_calls_json(raw: str | None) -> dict[str, int]:
    try:
        tools = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(tools, dict):
        return {}
    return {
        name: count
        for name, count in tools.items()
        if isinstance(name, str)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    }


def upsert_session_from_tool_call(tool_call: ToolCall, *, session: Session) -> None:
    """Incrementally update tool_calls_json on the session for a single tool call."""
    if not tool_call.session_id:
        return

    existing = get_session_record(
        session, tool_call.session_id, user_id=tool_call.user_id
    )
    if not existing:
        return

    tools = _load_tool_calls_json(existing.tool_calls_json)
    tools[tool_call.tool_name] = tools.get(tool_call.tool_name, 0) + 1
    existing.tool_calls_json = json.dumps(tools)
    existing.updated_at = datetime.now(UTC).isoformat()
    session.flush()


def rebuild_sessions_from_usage(db_path: str | None = None) -> int:
    """Clear and rebuild sessions table from usage rows. Returns count of sessions."""
    engine = get_engine(db_path)

    with engine.begin() as conn:
        names = {
            (row.session_id, row.user_id): row.project_name
            for row in conn.execute(
                select(
                    SessionRecord.session_id,
                    SessionRecord.user_id,
                    SessionRecord.project_name,
                )
            )
        }
        conn.execute(text("DELETE FROM sessions"))

    with Session(engine) as session:
        rows = (
            session.execute(
                select(Usage)
                .where(Usage.session_id.isnot(None), Usage.session_id != "")
                .order_by(Usage.session_id, Usage.ts)
            )
            .scalars()
            .all()
        )
        tool_call_rows = session.execute(
            select(ToolCall.session_id, ToolCall.tool_name).where(
                ToolCall.session_id.isnot(None), ToolCall.session_id != ""
            )
        ).all()

    grouped: dict[str, list[Usage]] = {}
    for row in rows:
        grouped.setdefault(row.session_id, []).append(row)  # type: ignore[arg-type]

    # Recompute tool_calls_json from the tool_calls table (not from Usage) so
    # calls recorded before their session existed aren't lost on rebuild.
    tool_calls_by_session: dict[str, dict[str, int]] = {}
    for session_id, tool_name in tool_call_rows:
        if session_id is None:
            continue
        counts = tool_calls_by_session.setdefault(session_id, {})
        counts[tool_name] = counts.get(tool_name, 0) + 1

    now = datetime.now(UTC).isoformat()
    count = 0

    with Session(engine) as session:
        for sid, usages in grouped.items():
            kwargs = _session_record_kwargs(sid, usages, now=now)
            kwargs["project_name"] = names.get((sid, usages[0].user_id))
            tool_counts = tool_calls_by_session.get(sid)
            if tool_counts:
                kwargs["tool_calls_json"] = json.dumps(tool_counts)
            session.add(SessionRecord(**kwargs))
            count += 1

        session.commit()

    return count


# Allowed sort columns for fetch_sessions (maps frontend names to SessionRecord columns)
_SESSION_SORT_COLUMNS = {
    "session_id": SessionRecord.session_id,
    "client_source": SessionRecord.client_source,
    "started": SessionRecord.started,
    "ended": SessionRecord.ended,
    "request_count": SessionRecord.request_count,
    "total_tokens": SessionRecord.total_tokens,
    "total_cost_usd": SessionRecord.total_cost_usd,
    "avg_latency_ms": SessionRecord.avg_latency_ms,
    "avg_ttft_ms": SessionRecord.avg_ttft_ms,
}


def _compute_duration_s(started: int | str | None, ended: int | str | None) -> int:
    """Compute duration in seconds from epoch microseconds (or legacy ISO strings)."""
    if started is None or ended is None:
        return 0
    try:
        if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
            return max(0, int((ended - started) // 1_000_000))
        fmt = "%Y-%m-%dT%H:%M:%S"
        start = datetime.strptime(str(started)[:19], fmt)
        end = datetime.strptime(str(ended)[:19], fmt)
        return max(0, int((end - start).total_seconds()))
    except (ValueError, TypeError):
        return 0


def _duration_s_expr() -> Any:
    """SQL expression for session duration in seconds from epoch microseconds."""
    duration = (
        func.coalesce(SessionRecord.ended, SessionRecord.started)
        - SessionRecord.started
    ) / 1_000_000
    return case((duration > 0, duration), else_=0)


# Stable API bucket keys. Boundaries are lower inclusive, upper exclusive.
DURATION_BUCKETS = (
    ("<1m", 60),
    ("1-10m", 600),
    ("10-60m", 3600),
    ("1-6h", 21600),
    (">6h", None),
)
COST_BUCKETS = (
    ("<0.10", Decimal("0.10")),
    ("0.10-1", Decimal("1")),
    ("1-5", Decimal("5")),
    ("5-20", Decimal("20")),
    (">20", None),
)


def _fixed_bucket_expr(value: Any, buckets: tuple) -> Any:
    return case(
        *((value < upper, key) for key, upper in buckets if upper is not None),
        else_=buckets[-1][0],
    )


def _normalize_timestamp_filter(value: str) -> int:
    """Convert an ISO timestamp string to epoch microseconds for column comparison."""
    try:
        normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return secs_to_micros(float(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    else:
        parsed = parsed.replace(tzinfo=UTC)
    epoch_s = calendar.timegm(parsed.timetuple())
    return secs_to_micros(epoch_s) + parsed.microsecond


@dataclass(frozen=True)
class SessionFilters:
    """Session predicates; date strings normalize once to epoch microseconds."""

    user_id: str
    client_source: str | None = None
    since: str | int | None = None
    until: str | int | None = None
    device_id: str | None = None
    provider: str | None = None
    model: str | list[str] | None = None
    duration_bucket: str | None = None
    cost_bucket: str | None = None
    project: str | None = None

    def __post_init__(self):
        for name, buckets in (
            ("duration_bucket", DURATION_BUCKETS),
            ("cost_bucket", COST_BUCKETS),
        ):
            value = getattr(self, name)
            if value is not None and value not in dict(buckets):
                raise ValueError(f"Invalid {name}")
        for name in ("since", "until"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, int):
                try:
                    parsed = _normalize_timestamp_filter(value)
                    if not -(2**63) <= parsed < 2**63:
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise ValueError("Invalid session date filter") from None
                object.__setattr__(self, name, parsed)


def _session_filters(criteria: SessionFilters) -> list[Any]:
    filters: list[Any] = [SessionRecord.user_id == criteria.user_id]
    if criteria.project is not None:
        filters.append(
            SessionRecord.project_name.is_(None)
            if criteria.project == ""
            else SessionRecord.project_name == criteria.project
        )
    if criteria.client_source:
        filters.append(SessionRecord.client_source == criteria.client_source)
    if criteria.since is not None:
        filters.append(SessionRecord.ended >= criteria.since)
    if criteria.until is not None:
        filters.append(SessionRecord.started < criteria.until)
    if criteria.device_id or criteria.provider or criteria.model:
        # Match device/provider/model on the same usage row, scoped to the caller.
        filters.append(
            select(Usage.id)
            .where(
                Usage.session_id == SessionRecord.session_id,
                *_usage_filters(
                    user_id=criteria.user_id,
                    device_id=criteria.device_id,
                    provider=criteria.provider,
                    model=criteria.model,
                ),
            )
            .exists()
        )
    if criteria.duration_bucket is not None:
        filters.append(
            _fixed_bucket_expr(_duration_s_expr(), DURATION_BUCKETS)
            == criteria.duration_bucket
        )
    if criteria.cost_bucket is not None:
        filters.append(
            _fixed_bucket_expr(SessionRecord.total_cost_usd, COST_BUCKETS)
            == criteria.cost_bucket
        )
    return filters


def _session_record_to_dict(rec: SessionRecord) -> dict[str, Any]:
    """Convert a SessionRecord ORM object to the API response dict."""
    started = rec.started
    ended = rec.ended
    result: dict[str, Any] = {
        "session_id": rec.session_id,
        "project": rec.project_name,
        "client_source": rec.client_source or "",
        "model": rec.primary_model or "",
        "models": _session_models(rec.models_json),
        "started": started,
        "ended": ended,
        "duration_s": _compute_duration_s(started, ended),
        "request_count": rec.request_count,
        "total_tokens": rec.total_tokens,
        "prompt_tokens": rec.prompt_tokens,
        "completion_tokens": rec.completion_tokens,
        "cached_tokens": rec.cached_tokens,
        "cache_creation_tokens": rec.cache_creation_tokens,
        "total_cost_usd": float(rec.total_cost_usd),
        "avg_latency_ms": round(float(rec.avg_latency_ms or 0), 1),
        "latency_sum_ms": float(rec.latency_sum_ms),
        "avg_ttft_ms": round(float(rec.avg_ttft_ms or 0), 1),
        "successful_requests": rec.successful_requests,
        "failed_requests": rec.failed_requests,
    }
    tool_calls = _numeric_json_map(rec.tool_calls_json)
    if tool_calls:
        result["tool_calls_json"] = tool_calls
    else:
        result["tool_calls_json"] = None
    return result


def _selector_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "client_source": row.client_source or "",
        "request_count": row.request_count,
        "started": row.started,
    }


def fetch_sessions(
    *,
    filters: SessionFilters,
    sort_by: str = "ended",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return sessions from the persisted sessions table."""
    conditions = _session_filters(filters)

    sort_col = _SESSION_SORT_COLUMNS.get(sort_by)

    engine = get_engine(db_path)

    if sort_by == "duration_s":
        duration_expr = _duration_s_expr()
        order = duration_expr.desc() if sort_order == "desc" else duration_expr.asc()
    elif sort_col is not None:
        order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    else:
        order = SessionRecord.ended.desc()  # type: ignore[assignment]

    query = (
        select(SessionRecord)
        .order_by(order, SessionRecord.session_id)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        query = query.where(and_(*conditions))

    with Session(engine) as session:
        records = session.execute(query).scalars().all()

    return [_session_record_to_dict(r) for r in records]


def fetch_session_selector_rows(
    *,
    filters: SessionFilters,
    sort_by: str = "started",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return lightweight session rows for selector/dropdown consumers."""
    conditions = _session_filters(filters)
    sort_col = _SESSION_SORT_COLUMNS.get(sort_by, SessionRecord.started)
    order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    query = (
        select(
            SessionRecord.session_id,
            SessionRecord.client_source,
            SessionRecord.request_count,
            SessionRecord.started,
        )
        .order_by(order, SessionRecord.session_id)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        query = query.where(and_(*conditions))
    with get_engine(db_path).connect() as connection:
        return [_selector_row_to_dict(row) for row in connection.execute(query)]


def count_sessions(
    *,
    filters: SessionFilters,
    db_path: str | None = None,
) -> int:
    """Count sessions from the persisted sessions table."""
    conditions = _session_filters(filters)

    query = select(func.count()).select_from(SessionRecord)
    if filters:
        query = query.where(and_(*conditions))
    with get_engine(db_path).connect() as connection:
        result = connection.execute(query).scalar_one()
        return int(result or 0)


def summarize_sessions(
    *,
    filters: SessionFilters,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Return aggregate stats across all sessions matching the filters."""
    conditions = _session_filters(filters)

    query = select(
        func.count().label("session_count"),
        func.sum(SessionRecord.total_tokens).label("total_tokens"),
        func.sum(SessionRecord.total_cost_usd).label("total_cost_usd"),
        func.avg(SessionRecord.avg_latency_ms).label("avg_latency_ms"),
        func.sum(SessionRecord.latency_sum_ms).label("latency_sum_ms"),
        func.avg(_duration_s_expr()).label("avg_duration_s"),
    )
    if filters:
        query = query.where(and_(*conditions))

    with get_engine(db_path).connect() as connection:
        row = connection.execute(query).fetchone()

    if not row or row.session_count == 0:
        return {
            "session_count": 0,
            "avg_duration_s": 0,
            "total_tokens": 0,
            "total_cost_usd": 0,
            "avg_latency_ms": 0,
        }

    return {
        "session_count": int(row.session_count),
        "avg_duration_s": round(float(row.avg_duration_s or 0), 1),
        "total_tokens": int(row.total_tokens or 0),
        "total_cost_usd": float(row.total_cost_usd or 0),
        "avg_latency_ms": round(float(row.avg_latency_ms or 0), 1),
    }


def aggregate_session_distribution(
    *,
    filters: SessionFilters,
    granularity: str = "day",
    tz_offset: str = "+00:00",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Count active sessions over time and aggregate lifetime costs by dimension.

    Time buckets and list filters both use ended >= start and started < end.
    Only timestamps are loaded for the portable linear-time activity sweep.
    """
    conditions = _session_filters(filters)
    dimensions = {
        "by_agent": func.coalesce(SessionRecord.client_source, ""),
        "by_duration": _fixed_bucket_expr(_duration_s_expr(), DURATION_BUCKETS),
        "by_cost": _fixed_bucket_expr(SessionRecord.total_cost_usd, COST_BUCKETS),
    }
    result = {}
    with get_engine(db_path).connect() as connection:
        timestamps = connection.execute(
            select(SessionRecord.started, SessionRecord.ended).where(*conditions)
        ).all()
        for name, dimension in dimensions.items():
            query = (
                select(
                    dimension.label("key"),
                    func.sum(SessionRecord.total_tokens).label("total_tokens"),
                    func.count().label("session_count"),
                    func.sum(SessionRecord.total_cost_usd).label("total_cost_usd"),
                )
                .where(*conditions)
                .group_by(dimension)
                .order_by(dimension)
            )
            result[name] = [dict(row._mapping) for row in connection.execute(query)]
    for rows in result.values():
        for row in rows:
            row["total_cost_usd"] = float(row["total_cost_usd"] or 0)
    for name, buckets in (("by_duration", DURATION_BUCKETS), ("by_cost", COST_BUCKETS)):
        by_key = {row["key"]: row for row in result[name]}
        result[name] = [
            by_key.get(key, {"key": key, "session_count": 0, "total_cost_usd": 0.0})
            for key, _ in buckets
        ]
    total_cost = sum(row["total_cost_usd"] for row in result["by_agent"])
    for row in result["by_agent"]:
        row["cost_share"] = row["total_cost_usd"] / total_cost if total_cost else 0.0
    result["over_time"] = _active_session_buckets(
        timestamps, filters=filters, granularity=granularity, tz_offset=tz_offset
    )
    return result


_PROJECT_TOTALS = (
    "total_cost_usd",
    "request_count",
    "prompt_tokens",
    "cached_tokens",
    "cache_creation_tokens",
)


def _numeric_json_map(payload: str | None) -> dict[str, float]:
    """Ignore malformed maps and non-finite or nonnumeric values."""
    try:
        data = json.loads(payload or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        # Ints above the float range overflow when summed with floats.
        if (
            (type(value) is int and value <= sys.float_info.max)
            or (type(value) is float and math.isfinite(value))
        )
        and value >= 0
    }


def _project_time_buckets(
    timestamps: Sequence[Any],
    *,
    filters: SessionFilters,
    granularity: str,
    tz_offset: str,
) -> list[dict[str, Any]]:
    """One activity sweep; work is linear in sessions, buckets and sparse output."""
    if not timestamps:
        return []
    lower = (
        int(filters.since)
        if filters.since is not None
        else min(row.started for row in timestamps)
    )
    upper = (
        int(filters.until)
        if filters.until is not None
        else int(datetime.now(UTC).timestamp() * 1_000_000)
    )
    if upper <= lower:
        return []
    seconds, fmt = _granularity(granularity)
    size = seconds * 1_000_000
    offset = _tz_seconds(tz_offset) * 1_000_000
    grid_start = (lower + offset) // size * size - offset
    count = (upper - grid_start + size - 1) // size
    if count > MAX_TIME_BUCKETS:
        raise ValueError("Range too long for this granularity")
    changes: list[dict[str, int]] = [{} for _ in range(count + 1)]
    for name, started, ended in timestamps:
        if ended < lower or started >= upper:
            continue
        first = max(0, (started - grid_start) // size)
        last = min(count - 1, (ended - grid_start) // size)
        if first <= last:
            changes[first][name] = changes[first].get(name, 0) + 1
            changes[last + 1][name] = changes[last + 1].get(name, 0) - 1
    active: dict[str, int] = {}
    rows = []
    for index in range(count):
        for name, delta in changes[index].items():
            value = active.get(name, 0) + delta
            if value:
                active[name] = value
            else:
                active.pop(name, None)
        key = grid_start + index * size
        rows.append(
            {
                "key": key,
                "period": key + offset,
                "start": datetime.fromtimestamp(
                    max(key, lower) / 1_000_000, UTC
                ).isoformat(),
                "end": datetime.fromtimestamp(
                    min(key + size, upper) / 1_000_000, UTC
                ).isoformat(),
                "by_project": dict(active),
            }
        )
    _format_periods(rows, fmt, 1_000_000)
    return rows


def aggregate_session_projects(
    *,
    filters: SessionFilters,
    granularity: str = "day",
    tz_offset: str = "+00:00",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Project lifetime totals from session rows in the overlapping window."""
    conditions = [*_session_filters(filters), SessionRecord.project_name.is_not(None)]
    totals = [
        func.sum(getattr(SessionRecord, key)).label(key) for key in _PROJECT_TOTALS
    ]
    agent = func.coalesce(SessionRecord.client_source, "")
    with get_engine(db_path).connect() as connection:
        groups = connection.execute(
            select(
                SessionRecord.project_name.label("key"),
                func.count().label("session_count"),
                *totals,
                func.max(SessionRecord.ended).label("last_active"),
            )
            .where(*conditions)
            .group_by(SessionRecord.project_name)
            .order_by(
                func.sum(SessionRecord.total_cost_usd).desc(),
                SessionRecord.project_name,
            )
        ).all()
        agents = connection.execute(
            select(
                SessionRecord.project_name,
                agent.label("agent"),
                func.count().label("session_count"),
            )
            .where(*conditions)
            .group_by(SessionRecord.project_name, agent)
        ).all()
        timestamps = connection.execute(
            select(
                SessionRecord.project_name, SessionRecord.started, SessionRecord.ended
            ).where(*conditions)
        ).all()
    agent_counts: dict[str | None, dict[str, int]] = {}
    for row in agents:
        counts = agent_counts.setdefault(row.project_name, {})
        counts[row.agent] = counts.get(row.agent, 0) + row.session_count
    projects = []
    for row in groups:
        project = dict(row._mapping)
        for key in _PROJECT_TOTALS:
            project[key] = (
                float(project[key] or 0)
                if key == "total_cost_usd"
                else int(project[key] or 0)
            )
        project["agents"] = agent_counts.get(row.key, {})
        projects.append(project)
    return {
        "projects": projects,
        "over_time": _project_time_buckets(
            timestamps,
            filters=filters,
            granularity=granularity,
            tz_offset=tz_offset,
        ),
    }


def session_project_detail(
    *,
    name: str,
    filters: SessionFilters,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    filters = replace(filters, project=name)
    conditions = _session_filters(filters)
    models: dict[str, float] = {}
    tools: dict[str, float] = {}
    with get_engine(db_path).connect() as connection:
        totals = connection.execute(
            select(
                func.count().label("session_count"),
                func.sum(SessionRecord.failed_requests).label("failed_requests"),
                func.sum(SessionRecord.latency_sum_ms).label("latency_sum_ms"),
                func.min(SessionRecord.started).label("first_started"),
            ).where(*conditions)
        ).one()
        if not totals.session_count:
            return None
        # Stream just the two JSON columns; never materialize full session rows.
        maps = connection.execute(
            select(
                SessionRecord.models_json,
                SessionRecord.tool_calls_json,
            )
            .where(*conditions)
            .execution_options(yield_per=500)
        )
        for models_json, tools_json in maps:
            for combined, payload in ((models, models_json), (tools, tools_json)):
                for key, value in _numeric_json_map(payload).items():
                    combined[key] = combined.get(key, 0) + value
    return {
        "key": name,
        "failed_requests": int(totals.failed_requests or 0),
        "latency_sum_ms": float(totals.latency_sum_ms or 0),
        "first_started": totals.first_started,
        "models": dict(
            sorted(models.items(), key=lambda item: (-item[1], item[0]))[:5]
        ),
        "tools": dict(sorted(tools.items(), key=lambda item: (-item[1], item[0]))[:5]),
        "recent_sessions": fetch_sessions(filters=filters, limit=10, db_path=db_path),
    }


def _active_session_buckets(
    timestamps: Sequence[Any],
    *,
    filters: SessionFilters,
    granularity: str,
    tz_offset: str,
) -> list[dict[str, Any]]:
    if not timestamps:
        return []
    lower = (
        int(filters.since)
        if filters.since is not None
        else min(row[0] for row in timestamps)
    )
    upper = (
        int(filters.until)
        if filters.until is not None
        else int(datetime.now(UTC).timestamp() * 1_000_000)
    )
    if upper <= lower:
        return []
    seconds, fmt = _granularity(granularity)
    size = seconds * 1_000_000
    offset = _tz_seconds(tz_offset) * 1_000_000
    grid_start = (lower + offset) // size * size - offset
    count = (upper - grid_start + size - 1) // size
    if count > MAX_TIME_BUCKETS:
        raise ValueError("Range too long for this granularity")
    changes = [0] * (count + 1)
    for started, ended in timestamps:
        # The outer buckets are clipped; exclude timestamps beyond those edges.
        if ended < lower or started >= upper:
            continue
        first = max(0, (started - grid_start) // size)
        last = min(count - 1, (ended - grid_start) // size)
        if first <= last:
            changes[first] += 1
            changes[last + 1] -= 1
    rows = []
    active = 0
    for i in range(count):
        active += changes[i]
        key = grid_start + i * size
        start, end = max(key, lower), min(key + size, upper)
        rows.append(
            {
                "key": key,
                "session_count": active,
                "start": datetime.fromtimestamp(start / 1_000_000, UTC).isoformat(),
                "end": datetime.fromtimestamp(end / 1_000_000, UTC).isoformat(),
                "period": key + offset,
            }
        )
    _format_periods(rows, fmt, 1_000_000)
    return rows


MAX_CONTEXT_POINTS = 200


def aggregate_session_context(
    session_id: str, user_id: str, db_path: str | None = None
) -> dict[str, Any]:
    """Return per-bucket maximum context in request order."""
    numbered = (
        select(
            Usage.id.label("id"),
            Usage.ts.label("ts"),
            Usage.model.label("model"),
            Usage.total_cost_usd.label("cost"),
            Usage.latency_ms.label("latency_ms"),
            Usage.status.label("status"),
            (
                func.coalesce(Usage.prompt_tokens, 0)
                + func.coalesce(Usage.cache_creation_tokens, 0)
            ).label("context"),
            func.row_number().over(order_by=(Usage.ts, Usage.id)).label("request"),
            func.count().over().label("total"),
        )
        .where(Usage.session_id == session_id, Usage.user_id == user_id)
        .subquery()
    )
    # Width comes from the same snapshot as the numbering, so a concurrent
    # insert cannot add a 201st bucket.
    width = (numbered.c.total + MAX_CONTEXT_POINTS - 1) // MAX_CONTEXT_POINTS
    # Failed requests (e.g. HTTP 500) record no prompt tokens; drop them before
    # ranking so they neither plot as 0 nor hide a bucket's successful peak.
    bucketed = (
        select(
            numbered,
            func.row_number()
            .over(
                partition_by=(numbered.c.request - 1) // width,
                order_by=(numbered.c.context.desc(), numbered.c.request),
            )
            .label("rank"),
        )
        .where(or_(numbered.c.status.is_(None), numbered.c.status < 400))
        .subquery()
    )
    with get_engine(db_path).connect() as connection:
        rows = connection.execute(
            select(
                bucketed.c.request,
                bucketed.c.id,
                bucketed.c.ts,
                bucketed.c.model,
                bucketed.c.cost,
                bucketed.c.latency_ms,
                bucketed.c.context,
                bucketed.c.total,
            )
            .where(bucketed.c.rank == 1)
            .order_by(bucketed.c.request)
        ).all()
    if not rows:
        # Every request failed (or none exist): no points, but the real count.
        with get_engine(db_path).connect() as connection:
            count = connection.execute(
                select(func.count()).where(
                    Usage.session_id == session_id, Usage.user_id == user_id
                )
            ).scalar_one()
        return {"points": [], "peak_tokens": 0, "request_count": count}
    # Each point is the bucket's peak request, so its number, id and details match its value.
    points = [
        {
            "request": row.request,
            "id": row.id,
            "ts": row.ts,
            "model": row.model,
            "cost_usd": float(row.cost or 0),
            "latency_ms": row.latency_ms,
            "context_tokens": int(row.context),
        }
        for row in rows
    ]
    count = rows[0].total
    return {
        "points": points,
        "peak_tokens": max(p["context_tokens"] for p in points),
        "request_count": count,
    }
