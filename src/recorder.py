"""Unified usage recording pipeline.

Both proxy and OTLP paths converge here for cost calculation, base URL
resolution, Usage construction, and persistence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database.base_url import resolve_base_url_id
from .database.errors import TRANSIENT_DB_ERRORS
from .database.model_prices import load_model_price_overrides
from .database.models import ToolCall, Usage
from .database.usage import log_usage, merge_duplicate_usage
from .pricing.costs import ResolvedPricing, calculate_costs, resolve_pricing
from .pricing.snapshots import ensure_price_snapshot
from .utils import (
    micros_to_secs,
    normalize_model_name,
    normalize_provider_name,
    normalize_tool_name,
)

log = logging.getLogger(__name__)


def record_usage(
    *,
    ts: int | None = None,
    provider: str,
    model: str,
    user_id: str,
    device_id: str,
    client_source: str | None = None,
    session_id: str | None = None,
    endpoint: str,
    prompt_tokens: int | None = None,
    prompt_length: int | None = None,
    completion_tokens: int | None = None,
    cached_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    tool_tokens: int | None = None,
    total_tokens: int | None = None,
    latency_ms: int | None = None,
    ttft_ms: int | None = None,
    status: int | None = None,
    base_url: str | None = None,
    base_url_provider: str | None = None,
    base_url_source: str | None = None,
    tool_calls: Sequence[tuple[str, str]] = (),
    session: Session,
) -> Usage | None:
    """Record a single LLM usage event. Returns the Usage object, or None if skipped.

    *tool_calls* are ``(tool_use_id, tool_name)`` pairs reported with the usage
    itself (the proxy path); they are stored with the row in one transaction.
    Repeated OTLP events return None so callers skip tools and follow-up work.
    Callers that learn about tool calls later use ``record_tool_call``.
    """
    # Skip if successful and no token counts are available (None or zero).
    if status == 200 and not (
        prompt_tokens or completion_tokens or cached_tokens or total_tokens
    ):
        return None

    provider = normalize_provider_name(provider)
    model = normalize_model_name(model)
    usage_ts = ts if ts is not None else time.time_ns() // 1000

    # Repeated OTLP events are dropped without follow-up work.
    # Proxy and OTLP are independent collection paths for the same agent;
    # either can land first, so check for a same-shaped row already recorded
    # by the other path, enrich it, and return it instead of inserting a
    # duplicate (the caller can still attach tool calls to its id).
    match = merge_duplicate_usage(
        client_source=client_source,
        is_otlp=(endpoint == "otlp"),
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        latency_ms=latency_ms,
        status=status,
        ts=usage_ts,
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        session=session,
    )
    if match is not None:
        duplicate, repeated = match
        if repeated:
            return None
        for tool_use_id, tool_name in tool_calls:
            record_tool_call(
                tool_use_id=tool_use_id,
                usage_id=duplicate.id,
                tool_name=tool_name,
                user_id=user_id,
                client_source=client_source,
                ts=duplicate.ts // 1000,  # ToolCall.ts is milliseconds
                session=session,
            )
        return duplicate

    # ponytail: one overrides query per recorded row; cache per user if it shows up.
    overrides = load_model_price_overrides(user_id=user_id, session=session)
    resolved = resolve_pricing(model, usage_ts, overrides=overrides, user_id=user_id)
    costs = calculate_costs(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cache_creation_tokens=cache_creation_tokens,
        model=model,
        model_cost=resolved.cost,
    )
    base_url_id = resolve_base_url_id(
        base_url=base_url,
        session=session,
        provider_name=base_url_provider or provider,
        source=base_url_source,
    )

    # Snapshot the pricing that was in effect so the row's cost split can be
    # recomputed exactly later. Best-effort: a snapshot failure must never turn
    # a persisted usage row into a failed request.
    snapshot_id = _record_price_snapshot(resolved, model, usage_ts, session)

    usage = Usage(
        ts=usage_ts,
        provider=provider,
        model=model,
        user_id=user_id,
        device_id=device_id,
        client_source=client_source,
        session_id=session_id,
        endpoint=endpoint,
        prompt_tokens=prompt_tokens,
        prompt_length=prompt_length or 0,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        tool_tokens=tool_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        status=status,
        base_url_id=base_url_id,
        price_snapshot_id=snapshot_id,
        **costs,
    )
    log_usage(
        usage,
        session=session,
        tool_calls=[
            {
                "tool_use_id": tool_use_id,
                "user_id": user_id,
                "tool_name": normalize_tool_name(tool_name),
                "client_source": client_source,
                "ts": usage_ts // 1000,  # ToolCall.ts is milliseconds
            }
            for tool_use_id, tool_name in tool_calls
        ],
    )
    return usage


def _record_price_snapshot(
    resolved: ResolvedPricing,
    model: str,
    usage_ts: int,
    session: Session,
) -> int | None:
    """Persist the exact rates used for this row, returning the snapshot id."""
    if resolved.match is None:
        return None
    date = datetime.fromtimestamp(micros_to_secs(usage_ts), tz=UTC).strftime("%Y-%m-%d")
    try:
        with session.begin_nested():
            return ensure_price_snapshot(
                date=date,
                model=model,
                source=resolved.source or "unknown",
                cost=resolved.cost,
                session=session,
            )
    except TRANSIENT_DB_ERRORS:
        raise
    except Exception as exc:
        log.warning("Failed to record price snapshot error=%s", type(exc).__name__)
        return None


def update_tool_call_duration(
    *,
    tool_use_id: str,
    user_id: str,
    client_source: str,
    duration_ms: int,
    session: Session,
) -> None:
    """Update the duration on an already-recorded tool call, if it exists."""
    session.execute(
        update(ToolCall)
        .where(
            ToolCall.tool_use_id == tool_use_id,
            ToolCall.user_id == user_id,
            ToolCall.client_source == client_source,
        )
        .values(duration_ms=duration_ms)
    )
    session.flush()


def record_tool_call(
    *,
    tool_use_id: str,
    usage_id: str | None = None,
    session_id: str | None = None,
    tool_name: str,
    user_id: str,
    client_source: str | None = None,
    duration_ms: int | None = None,
    ts: int,
    session: Session,
) -> None:
    """Record a tool call and update session tool_calls_json."""
    from .database.sessions import upsert_session_from_tool_call

    normalized_tool_name = normalize_tool_name(tool_name)
    tc = ToolCall(
        tool_use_id=tool_use_id,
        user_id=user_id,
        usage_id=usage_id,
        session_id=session_id,
        tool_name=normalized_tool_name,
        client_source=client_source,
        duration_ms=duration_ms,
        ts=ts,
    )
    if session.get(ToolCall, tool_use_id) is not None:
        return  # already recorded; avoid double-counting on redelivery
    try:
        with session.begin_nested():
            session.add(tc)
            session.flush()
    except IntegrityError:
        # Concurrent redelivery raced us to the same PK; already recorded.
        return
    upsert_session_from_tool_call(tc, session=session)
