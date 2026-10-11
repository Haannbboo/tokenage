"""Shared data classes and utilities for model cost and provider configuration.

Pricing-specific data classes live here (under ``src/``); generic provider
config and path helpers remain in ``config/models.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime


@dataclass(frozen=True)
class ModelTier:
    """Pricing tier applied when token counts fall within [min_tokens, max_tokens).

    Bounds are in tokens (litellm `range`). Prices are per-million tokens.
    """

    min_tokens: int
    max_tokens: int | None
    input: float
    output: float
    cache_read: float
    cache_write: float | None = None


@dataclass(frozen=True)
class ModelCost:
    input: float
    output: float
    cache_read: float
    cache_write: float | None = None
    tiers: tuple[ModelTier, ...] = ()
    time_rates: tuple[TimeRate, ...] = ()


@dataclass(frozen=True)
class TimeRate:
    """A time-of-day window with its own flat rates.

    ``days`` is a set of ``datetime.weekday()`` values (0=Mon .. 6=Sun) or
    ``None`` for every day. ``start_minute``/``end_minute`` are minutes since
    UTC midnight; the window is half-open ``[start, end)`` and wraps past
    midnight only when ``end_minute < start_minute``. ``end_minute == 0`` with a
    non-zero start means 24:00. ``start_minute == end_minute`` is invalid.
    """

    days: frozenset[int] | None
    start_minute: int
    end_minute: int
    cost: ModelCost


@dataclass(frozen=True)
class ResolvedCost:
    cost: ModelCost
    source: str


def cost_rank(cost: ModelCost) -> tuple[float, ...]:
    """Ordering key for comparing model costs, cheapest first."""
    return (
        cost.input + cost.output + cost.cache_read + (cost.cache_write or 0.0),
        cost.input,
        cost.output,
        cost.cache_read,
        cost.cache_write or 0.0,
    )


def build_segment_index(
    costs: dict[str, ModelCost],
) -> dict[str, tuple[str, ModelCost]]:
    """Index pricing keys by trailing path segment, cheapest per segment.

    Enables O(1) containing-name resolution for vendor-prefixed keys like
    'openrouter/xiaomi/mimo-v2.5-pro' without scanning the full map on every
    lookup. On equal ranks the first key wins, matching min()'s tie-break.
    """
    index: dict[str, tuple[str, ModelCost]] = {}
    for key, cost in costs.items():
        segment = key.rsplit("/", maxsplit=1)[-1]
        existing = index.get(segment)
        if existing is None or cost_rank(cost) < cost_rank(existing[1]):
            index[segment] = (key, cost)
    return index


def _window_matches(rate: TimeRate, weekday: int, minute: int) -> bool:
    if rate.days is not None and weekday not in rate.days:
        return False
    end = rate.end_minute
    # end == 0 with a non-zero start is an explicit end-of-day (24:00), not a
    # wrap: match [start, 24:00), excluding the early-morning segment.
    if end == 0 and rate.start_minute != 0:
        end = 24 * 60
    if end > rate.start_minute:
        return rate.start_minute <= minute < end
    # Wrap past midnight (end < start).
    return minute >= rate.start_minute or minute < rate.end_minute


def resolve_effective_cost(cost: ModelCost, ts_micros: int) -> ModelCost:
    """Return the flat rates in effect at ``ts_micros`` (UTC).

    Applies time-of-day windows: the last matching window wins; no match (or no
    ``time_rates``) falls back to the base flat rates. The result never carries
    ``time_rates``, so callers can serialize it directly into a snapshot.
    """
    if not cost.time_rates:
        return cost
    ts_seconds = ts_micros // 1_000_000
    dt = datetime.fromtimestamp(ts_seconds, tz=UTC)
    minute = dt.hour * 60 + dt.minute
    weekday = dt.weekday()

    matched: TimeRate | None = None
    for rate in cost.time_rates:
        if _window_matches(rate, weekday, minute):
            matched = rate

    if matched is None:
        return replace(cost, time_rates=())
    return ModelCost(
        input=matched.cost.input,
        output=matched.cost.output,
        cache_read=matched.cost.cache_read,
        cache_write=matched.cost.cache_write,
        tiers=matched.cost.tiers,
    )
