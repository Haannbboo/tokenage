"""Session-related database operations.

Extracted from database/__init__.py during Phase 5 refactoring.
"""

from __future__ import annotations

import calendar
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from ..utils import secs_to_micros
from .engine import get_engine
from .models import (
    VALID_OUTCOMES,
    VALID_SOURCES,
    SessionRecord,
    ToolCall,
    Usage,
)

# Sources that emit one usage row per assistant completion, so
# single-request sessions can still be real user-visible sessions.
_SINGLE_REQUEST_SOURCES = frozenset({"opencode", "kilo"})


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


def _load_cost_map(payload: str | None) -> dict[str, float]:
    return json.loads(payload or "{}")


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

    existing = get_session_record(session, usage.session_id, user_id=usage.user_id)
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
    else:
        session.add(
            SessionRecord(
                **_session_record_kwargs(
                    usage.session_id,
                    [usage],
                    now=now,
                    missing_ttft_as_zero=True,
                )
            )
        )


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


def upsert_session_from_tool_call(
    tool_call: ToolCall, db_path: str | None = None
) -> None:
    """Incrementally update tool_calls_json on the session for a single tool call."""
    if not tool_call.session_id:
        return

    engine = get_engine(db_path)
    with Session(engine) as session:
        existing = get_session_record(
            session, tool_call.session_id, user_id=tool_call.user_id
        )
        if not existing:
            return

        tools = _load_tool_calls_json(existing.tool_calls_json)
        tools[tool_call.tool_name] = tools.get(tool_call.tool_name, 0) + 1
        existing.tool_calls_json = json.dumps(tools)
        existing.updated_at = datetime.now(UTC).isoformat()
        session.commit()


def summarize_session_tool_calls(
    session_id: str, user_id: str, db_path: str | None = None
) -> list[dict[str, Any]] | None:
    """Return [{tool_name, count}] for a session, or None if it doesn't exist."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        rec = get_session_record(session, session_id, user_id=user_id)
        if not rec:
            return None
        tools = _load_tool_calls_json(rec.tool_calls_json)
        return [
            {"tool_name": name, "count": count}
            for name, count in sorted(tools.items(), key=lambda x: -x[1])
        ]


def rebuild_sessions_from_usage(db_path: str | None = None) -> int:
    """Clear and rebuild sessions table from usage rows. Returns count of sessions."""
    engine = get_engine(db_path)

    with engine.begin() as conn:
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
            tool_counts = tool_calls_by_session.get(sid)
            if tool_counts:
                kwargs["tool_calls_json"] = json.dumps(tool_counts)
            session.add(SessionRecord(**kwargs))
            count += 1

        session.commit()

    return count


def upsert_session_evaluation(
    session_id: str,
    outcome: str,
    source: str = "manual",
    confidence: float | None = None,
    task_title: str | None = None,
    task_title_zh: str | None = None,
    summary: str | None = None,
    evidence: list[str] | None = None,
    failure_reason: str | None = None,
    project: str | None = None,
    skip_if_manual: bool = False,
    *,
    user_id: str,
    db_path: str | None = None,
) -> None:
    """Set evaluation on an existing session. Raises ValueError if session not found."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome}. Must be one of {VALID_OUTCOMES}")
    if source not in VALID_SOURCES:
        raise ValueError(f"Invalid source: {source}. Must be one of {VALID_SOURCES}")

    now = datetime.now(UTC).isoformat()
    engine = get_engine(db_path)
    with Session(engine) as session:
        record = get_session_record(session, session_id, user_id=user_id)
        if not record:
            raise ValueError(f"Session not found: {session_id}")
        if skip_if_manual and record.source == "manual":
            return
        record.outcome = outcome
        record.source = source
        record.confidence = confidence
        record.task_title = task_title if source == "llm" else None
        record.task_title_zh = task_title_zh if source == "llm" else None
        record.summary = summary
        record.evidence_json = json.dumps(evidence) if evidence else None
        record.failure_reason = failure_reason
        record.project = project if source == "llm" else (project or record.project)
        record.evaluated_at = now
        session.commit()


def get_session_evaluation(
    session_id: str,
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """Return evaluation dict for a session, or None if not found or not evaluated."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        rec = get_session_record(session, session_id, user_id=user_id)
        if not rec or rec.outcome is None:
            return None
        return {
            "session_id": rec.session_id,
            "outcome": rec.outcome,
            "source": rec.source,
            "confidence": float(rec.confidence) if rec.confidence is not None else None,
            "task_title": rec.task_title,
            "task_title_zh": rec.task_title_zh,
            "summary": rec.summary,
            "evidence": json.loads(rec.evidence_json) if rec.evidence_json else [],
            "failure_reason": rec.failure_reason,
            "evaluated_at": rec.evaluated_at,
            "project": rec.project,
        }


def delete_session_evaluation(
    session_id: str, user_id: str, db_path: str | None = None
) -> bool:
    """Clear evaluation columns on a session. Returns True if session found."""
    engine = get_engine(db_path)
    with Session(engine) as session:
        rec = get_session_record(session, session_id, user_id=user_id)
        if not rec:
            return False
        rec.outcome = None
        rec.source = None
        rec.confidence = None
        rec.task_title = None
        rec.task_title_zh = None
        rec.summary = None
        rec.evidence_json = None
        rec.failure_reason = None
        rec.evaluated_at = None
        rec.project = None
        session.commit()
        return True


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

_MODEL_EFFECTIVENESS_GROUPS = {"model", "provider", "source"}
_EVALUATED_OUTCOMES = ("solved", "partial", "failed", "stuck")


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
    return (SessionRecord.ended - SessionRecord.started) / 1_000_000


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


def _session_filters(
    *,
    user_id: str,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    hide_noop: bool = False,
) -> list[Any]:
    filters: list[Any] = [SessionRecord.user_id == user_id]
    if client_source:
        filters.append(SessionRecord.client_source == client_source)
    if since:
        filters.append(SessionRecord.started >= _normalize_timestamp_filter(since))
    if until:
        filters.append(SessionRecord.ended <= _normalize_timestamp_filter(until))
    if hide_noop:
        filters.append(
            or_(
                SessionRecord.outcome != "no_op",
                SessionRecord.outcome.is_(None),
            )
        )
        filters.append(
            or_(
                SessionRecord.request_count > 1,
                SessionRecord.client_source.in_(_SINGLE_REQUEST_SOURCES),
            )
        )
    return filters


def _session_record_to_dict(rec: SessionRecord) -> dict[str, Any]:
    """Convert a SessionRecord ORM object to the API response dict."""
    started = rec.started
    ended = rec.ended
    result: dict[str, Any] = {
        "session_id": rec.session_id,
        "client_source": rec.client_source or "",
        "model": rec.primary_model or "",
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
    tool_calls = json.loads(rec.tool_calls_json or "{}")
    if tool_calls:
        result["tool_calls_json"] = tool_calls
    else:
        result["tool_calls_json"] = None
    if rec.outcome is not None:
        result["evaluation"] = {
            "session_id": rec.session_id,
            "outcome": rec.outcome,
            "source": rec.source,
            "confidence": float(rec.confidence) if rec.confidence is not None else None,
            "task_title": rec.task_title,
            "task_title_zh": rec.task_title_zh,
            "summary": rec.summary,
            "evidence": json.loads(rec.evidence_json) if rec.evidence_json else [],
            "failure_reason": rec.failure_reason,
            "evaluated_at": rec.evaluated_at,
            "project": rec.project,
        }
    else:
        result["evaluation"] = None
    return result


def _selector_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "client_source": row.client_source or "",
        "request_count": row.request_count,
        "started": row.started,
    }


def aggregate_model_effectiveness(
    *,
    user_id: str,
    group_by: str = "model",
    since: str | None = None,
    until: str | None = None,
    client_source: str | None = None,
    hide_noop: bool = False,
    db_path: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate evaluated session outcomes by model, provider, or client source."""
    if group_by not in _MODEL_EFFECTIVENESS_GROUPS:
        raise ValueError(
            f"Invalid group_by: {group_by}. Must be one of {sorted(_MODEL_EFFECTIVENESS_GROUPS)}"
        )

    filters = _session_filters(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )

    if group_by == "provider":
        group_key = func.coalesce(SessionRecord.primary_provider, "unknown").label(
            "key"
        )
    elif group_by == "source":
        group_key = func.coalesce(SessionRecord.client_source, "unknown").label("key")
    else:
        group_key = func.coalesce(SessionRecord.primary_model, "unknown").label("key")

    session_count = func.count(SessionRecord.session_id)
    evaluated_count = _count_when(SessionRecord.outcome.in_(_EVALUATED_OUTCOMES))
    solved_count = _count_when(SessionRecord.outcome == "solved")
    partial_count = _count_when(SessionRecord.outcome == "partial")
    failed_count = _count_when(SessionRecord.outcome == "failed")
    stuck_count = _count_when(SessionRecord.outcome == "stuck")
    no_op_count = _count_when(SessionRecord.outcome == "no_op")
    unknown_count = _count_when(
        (SessionRecord.outcome.is_(None)) | (SessionRecord.outcome == "unknown")
    )
    total_cost = func.coalesce(func.sum(SessionRecord.total_cost_usd), 0)
    evaluated_cost = func.coalesce(
        func.sum(
            case(
                (
                    SessionRecord.outcome.in_(_EVALUATED_OUTCOMES),
                    SessionRecord.total_cost_usd,
                ),
                else_=0,
            )
        ),
        0,
    )
    total_duration = func.coalesce(func.sum(_duration_s_expr()), 0)

    query = (
        select(
            group_key,
            session_count.label("session_count"),
            evaluated_count.label("evaluated_count"),
            solved_count.label("solved_count"),
            partial_count.label("partial_count"),
            failed_count.label("failed_count"),
            stuck_count.label("stuck_count"),
            no_op_count.label("no_op_count"),
            unknown_count.label("unknown_count"),
            total_cost.label("total_cost_usd"),
            evaluated_cost.label("_evaluated_cost_usd"),
            case(
                (session_count > 0, total_duration / session_count),
                else_=0,
            ).label("avg_duration_s"),
            case(
                (evaluated_count > 0, (solved_count * 1.0) / evaluated_count),
                else_=None,
            ).label("solve_rate"),
            case(
                (solved_count > 0, evaluated_cost / solved_count),
                else_=None,
            ).label("cost_per_solved"),
        )
        .group_by(group_key)
        .order_by(session_count.desc(), group_key.asc())
    )

    if filters:
        query = query.where(and_(*filters))

    with Session(get_engine(db_path)) as session:
        rows = session.execute(query).all()

    groups = [
        {
            "key": row.key,
            "session_count": _as_int(row.session_count),
            "evaluated_count": _as_int(row.evaluated_count),
            "solved_count": _as_int(row.solved_count),
            "partial_count": _as_int(row.partial_count),
            "failed_count": _as_int(row.failed_count),
            "stuck_count": _as_int(row.stuck_count),
            "no_op_count": _as_int(row.no_op_count),
            "unknown_count": _as_int(row.unknown_count),
            "solve_rate": _as_float(row.solve_rate),
            "total_cost_usd": float(row.total_cost_usd or 0),
            "cost_per_solved": _as_float(row.cost_per_solved),
            "avg_duration_s": round(float(row.avg_duration_s or 0), 1),
        }
        for row in rows
    ]

    return {"groups": groups}


def _parse_report_date(value: str) -> datetime:
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value}. Expected YYYY-MM-DD") from exc
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _count_when(condition: Any) -> Any:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _as_int(value: Any) -> int:
    return int(value or 0)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _plural(value: int, singular: str, plural: str | None = None) -> str:
    return singular if value == 1 else plural or f"{singular}s"


def _format_group_name(group: dict[str, Any]) -> str:
    return f"{group['client_source']} / {group['model']}"


def _build_daily_report_text(
    *,
    session_count: int,
    evaluated_count: int,
    total_cost: float,
    groups: list[dict[str, Any]],
) -> tuple[str, list[str], list[str], list[str]]:
    summary = (
        f"You ran {session_count} AI {_plural(session_count, 'session')}. "
        f"{evaluated_count} {_plural(evaluated_count, 'was', 'were')} evaluated. "
        f"Total cost was ${total_cost:.2f}."
    )

    highlights: list[str] = []
    needs_attention: list[str] = []
    model_takeaways: list[str] = []

    solved_groups = [
        group
        for group in groups
        if group["evaluated_count"] > 0 and group["solved_count"] > 0
    ]
    solved_groups.sort(
        key=lambda group: (
            -(group["solve_rate"] or 0),
            -group["solved_count"],
            group["client_source"],
            group["model"],
        )
    )
    if solved_groups:
        best = solved_groups[0]
        highlights.append(
            f"{_format_group_name(best)} solved {best['solved_count']}/{best['evaluated_count']} evaluated sessions"
        )

    for group in groups:
        if group["stuck_count"] > 0:
            needs_attention.append(
                f"{_format_group_name(group)} had {group['stuck_count']} stuck {_plural(group['stuck_count'], 'session')}"
            )
        elif group["failed_count"] > 0 and group["solved_count"] == 0:
            needs_attention.append(
                f"{_format_group_name(group)} had {group['failed_count']} failed {_plural(group['failed_count'], 'session')}"
            )

    for group in solved_groups:
        model_takeaways.append(
            f"{_format_group_name(group)} solved {group['solved_count']}/{group['evaluated_count']} evaluated sessions"
        )

    return summary, highlights, needs_attention, model_takeaways


def daily_session_effectiveness_report(
    *,
    user_id: str,
    date: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Return a computed daily effectiveness report using SQL aggregate reads."""
    day_start = _parse_report_date(date)
    next_day_start = day_start + timedelta(days=1)
    started_filter = and_(
        *_session_filters(user_id=user_id),
        SessionRecord.started >= secs_to_micros(calendar.timegm(day_start.timetuple())),
        SessionRecord.started
        < secs_to_micros(calendar.timegm(next_day_start.timetuple())),
    )

    evaluated_count_expr = _count_when(SessionRecord.outcome.in_(_EVALUATED_OUTCOMES))
    solved_count_expr = _count_when(SessionRecord.outcome == "solved")
    partial_count_expr = _count_when(SessionRecord.outcome == "partial")
    failed_count_expr = _count_when(SessionRecord.outcome == "failed")
    stuck_count_expr = _count_when(SessionRecord.outcome == "stuck")
    no_op_count_expr = _count_when(SessionRecord.outcome == "no_op")
    unknown_count_expr = _count_when(
        (SessionRecord.outcome.is_(None)) | (SessionRecord.outcome == "unknown")
    )
    total_cost_expr = func.coalesce(func.sum(SessionRecord.total_cost_usd), 0)

    totals_query = select(
        func.count(SessionRecord.session_id).label("session_count"),
        evaluated_count_expr.label("evaluated_count"),
        solved_count_expr.label("solved_count"),
        partial_count_expr.label("partial_count"),
        failed_count_expr.label("failed_count"),
        stuck_count_expr.label("stuck_count"),
        no_op_count_expr.label("no_op_count"),
        unknown_count_expr.label("unknown_count"),
        total_cost_expr.label("total_cost_usd"),
    ).where(started_filter)

    model_key = func.coalesce(SessionRecord.primary_model, "unknown").label("model")
    source_key = func.coalesce(SessionRecord.client_source, "unknown").label(
        "client_source"
    )
    group_session_count = func.count(SessionRecord.session_id)
    group_evaluated_count = _count_when(SessionRecord.outcome.in_(_EVALUATED_OUTCOMES))
    group_solved_count = _count_when(SessionRecord.outcome == "solved")
    group_failed_count = _count_when(SessionRecord.outcome == "failed")
    group_stuck_count = _count_when(SessionRecord.outcome == "stuck")
    group_total_cost = func.coalesce(func.sum(SessionRecord.total_cost_usd), 0)

    groups_query = (
        select(
            model_key,
            source_key,
            group_session_count.label("session_count"),
            group_evaluated_count.label("evaluated_count"),
            group_solved_count.label("solved_count"),
            group_failed_count.label("failed_count"),
            group_stuck_count.label("stuck_count"),
            group_total_cost.label("total_cost_usd"),
            case(
                (
                    group_solved_count > 0,
                    group_total_cost / group_solved_count,
                ),
                else_=None,
            ).label("cost_per_solved"),
            case(
                (
                    group_evaluated_count > 0,
                    (group_solved_count * 1.0) / group_evaluated_count,
                ),
                else_=None,
            ).label("solve_rate"),
        )
        .where(started_filter)
        .group_by(model_key, source_key)
        .order_by(group_session_count.desc(), source_key.asc(), model_key.asc())
    )

    with Session(get_engine(db_path)) as session:
        totals = session.execute(totals_query).one()
        group_rows = session.execute(groups_query).all()

    groups = [
        {
            "model": row.model,
            "client_source": row.client_source,
            "session_count": _as_int(row.session_count),
            "evaluated_count": _as_int(row.evaluated_count),
            "solved_count": _as_int(row.solved_count),
            "failed_count": _as_int(row.failed_count),
            "stuck_count": _as_int(row.stuck_count),
            "total_cost_usd": float(row.total_cost_usd or 0),
            "cost_per_solved": _as_float(row.cost_per_solved),
            "solve_rate": _as_float(row.solve_rate),
        }
        for row in group_rows
    ]

    session_count = _as_int(totals.session_count)
    evaluated_count = _as_int(totals.evaluated_count)
    no_op_count = _as_int(totals.no_op_count)
    total_cost = float(totals.total_cost_usd or 0)
    summary, highlights, needs_attention, model_takeaways = _build_daily_report_text(
        session_count=session_count,
        evaluated_count=evaluated_count,
        total_cost=total_cost,
        groups=groups,
    )

    return {
        "date": date,
        "summary": summary,
        "session_count": session_count,
        "evaluated_count": evaluated_count,
        "classified_count": evaluated_count + no_op_count,
        "solved_count": _as_int(totals.solved_count),
        "partial_count": _as_int(totals.partial_count),
        "failed_count": _as_int(totals.failed_count),
        "stuck_count": _as_int(totals.stuck_count),
        "no_op_count": no_op_count,
        "unknown_count": _as_int(totals.unknown_count),
        "total_cost_usd": total_cost,
        "highlights": highlights,
        "needs_attention": needs_attention,
        "model_takeaways": model_takeaways,
        "groups": groups,
    }


def fetch_sessions(
    *,
    user_id: str,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sort_by: str = "ended",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    hide_noop: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return sessions from the persisted sessions table."""
    filters = _session_filters(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )

    sort_col = _SESSION_SORT_COLUMNS.get(sort_by)

    engine = get_engine(db_path)

    if sort_by == "duration_s":
        duration_expr = _duration_s_expr()
        order = duration_expr.desc() if sort_order == "desc" else duration_expr.asc()
    elif sort_col is not None:
        order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    else:
        order = SessionRecord.ended.desc()  # type: ignore[assignment]

    query = select(SessionRecord).order_by(order).limit(limit).offset(offset)
    if filters:
        query = query.where(and_(*filters))

    with Session(engine) as session:
        records = session.execute(query).scalars().all()

    return [_session_record_to_dict(r) for r in records]


def fetch_session_selector_rows(
    *,
    user_id: str,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sort_by: str = "started",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    hide_noop: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return lightweight session rows for selector/dropdown consumers."""
    filters = _session_filters(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )
    sort_col = _SESSION_SORT_COLUMNS.get(sort_by) or SessionRecord.started
    order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    query = (
        select(
            SessionRecord.session_id,
            SessionRecord.client_source,
            SessionRecord.request_count,
            SessionRecord.started,
        )
        .order_by(order)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        return [_selector_row_to_dict(row) for row in connection.execute(query)]


def count_sessions(
    *,
    user_id: str,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    hide_noop: bool = False,
    db_path: str | None = None,
) -> int:
    """Count sessions from the persisted sessions table."""
    filters = _session_filters(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )

    query = select(func.count()).select_from(SessionRecord)
    if filters:
        query = query.where(and_(*filters))
    with get_engine(db_path).connect() as connection:
        result = connection.execute(query).scalar_one()
        return int(result or 0)


def summarize_sessions(
    *,
    user_id: str,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    hide_noop: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Return aggregate stats across all sessions matching the filters."""
    filters = _session_filters(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )

    query = select(
        func.count().label("session_count"),
        func.sum(SessionRecord.total_tokens).label("total_tokens"),
        func.sum(SessionRecord.total_cost_usd).label("total_cost_usd"),
        func.avg(SessionRecord.avg_latency_ms).label("avg_latency_ms"),
        func.sum(SessionRecord.latency_sum_ms).label("latency_sum_ms"),
        func.avg(_duration_s_expr()).label("avg_duration_s"),
    )
    if filters:
        query = query.where(and_(*filters))

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
