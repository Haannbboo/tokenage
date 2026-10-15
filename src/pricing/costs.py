from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..utils import normalize_model_name
from .maps import (
    MAPS_LOCK,
    MODEL_COST_SOURCES,
    MODEL_COSTS,
    MODEL_SEGMENT_COSTS,
)
from .models import (
    ModelCost,
    ModelTier,
    build_segment_index,
    resolve_effective_cost,
)


@dataclass(frozen=True)
class CostMatch:
    cost: ModelCost
    key: str
    source: str  # "litellm", "server" (config.yaml models:), or future sources (e.g. "openrouter")


def resolve_cost_match(
    model: str,
    model_costs: dict[str, ModelCost] | None = None,
    model_cost_sources: dict[str, str] | None = None,
) -> CostMatch | None:
    """Resolve a model to a cost, honoring config overrides before LiteLLM.

    Priority: exact -> containing (cheapest).

    When explicit ``model_costs`` is supplied, pass the matching
    ``model_cost_sources`` so provenance stays consistent with it; otherwise the
    runtime source map is used.
    """
    using_runtime_maps = model_costs is None
    model_costs = MODEL_COSTS if model_costs is None else model_costs
    sources = MODEL_COST_SOURCES if model_cost_sources is None else model_cost_sources
    normalized_model = normalize_model_name(model)

    cost = model_costs.get(normalized_model)
    if cost is not None:
        return CostMatch(
            cost=cost,
            key=normalized_model,
            source=sources.get(normalized_model, "server"),
        )

    # Hot record path: use the prebuilt segment index to avoid a full scan of
    # the LiteLLM pricing map on every lookup that misses an exact key.
    segments = (
        MODEL_SEGMENT_COSTS if using_runtime_maps else build_segment_index(model_costs)
    )
    segment_match = segments.get(normalized_model)
    if segment_match is not None:
        key, cost = segment_match
        return CostMatch(cost=cost, key=key, source=sources.get(key, "server"))

    return None


def resolve_model_cost(model: str) -> ModelCost | None:
    match = resolve_cost_match(model)
    return match.cost if match is not None else None


@dataclass(frozen=True)
class ResolvedPricing:
    """Everything needed to price one usage row consistently.

    ``cost`` is already time-of-day resolved for the row timestamp.
    """

    match: CostMatch | None
    cost: ModelCost
    source: str | None


def resolve_pricing(
    model: str,
    ts_micros: int,
    model_costs: dict[str, ModelCost] | None = None,
    model_cost_sources: dict[str, str] | None = None,
    overrides: dict[str, ModelCost] | None = None,
) -> ResolvedPricing:
    """Resolve a model to a time-resolved flat cost.

    The single hot-path entry point: ``calculate_costs`` and the snapshot both
    use its output. The maps lock is held for the whole resolution so a
    concurrent config refresh (which swaps the maps under the same lock) is
    never observed half-applied.

    ``overrides`` are the row owner's own prices; a match there (same exact
    then last-segment matching) beats every shared source.
    """
    match = None
    if overrides:
        match = resolve_cost_match(model, overrides, dict.fromkeys(overrides, "user"))
    if match is None:
        with MAPS_LOCK:
            match = resolve_cost_match(model, model_costs, model_cost_sources)
    if match is None:
        return ResolvedPricing(
            match=None,
            cost=ModelCost(input=0.0, output=0.0, cache_read=0.0),
            source=None,
        )
    return ResolvedPricing(
        match=match,
        cost=resolve_effective_cost(match.cost, ts_micros),
        source=match.source,
    )


def _select_tier(tiers: tuple[ModelTier, ...], tokens: int) -> ModelTier:
    """Pick the pricing tier whose [min_tokens, max_tokens) range holds `tokens`.

    Falls back to the last tier when tokens exceed every range, and to the
    first tier when tokens are below every range.
    """
    chosen = tiers[0]
    for tier in tiers:
        if tokens < tier.min_tokens:
            break
        chosen = tier
        if tier.max_tokens is None or tokens < tier.max_tokens:
            break
    return chosen


def _tier_prices(cost: ModelCost, tokens: int) -> ModelCost:
    """Resolve the effective flat prices for a token count (tiered or not)."""
    if not cost.tiers:
        return cost
    tier = _select_tier(cost.tiers, tokens)
    return ModelCost(
        input=tier.input,
        output=tier.output,
        cache_read=tier.cache_read,
        cache_write=tier.cache_write
        if tier.cache_write is not None
        else cost.cache_write,
    )


def resolve_token_rates(
    cost: ModelCost, tokens: int
) -> tuple[ModelCost, ModelTier | None]:
    """Return the flat rates actually applied for a token count, plus the tier.

    ``tokens`` is the total input token count used for tier selection. The
    returned tier is ``None`` for non-tiered pricing.
    """
    if not cost.tiers:
        return cost, None
    tier = _select_tier(cost.tiers, tokens)
    return _tier_prices(cost, tokens), tier


def compute_input_split(
    *,
    prompt_tokens: int | None,
    cached_tokens: int | None,
    cache_creation_tokens: int | None,
    cost: ModelCost,
) -> dict[str, Decimal]:
    """Split input cost into normal (cache-miss), cache-read, and cache-write.

    Used both by ``calculate_costs`` at record time and by the read path to
    recompute the split from a stored price snapshot.
    """
    prompt = int(prompt_tokens or 0)
    cached = int(cached_tokens or 0)
    cache_created = int(cache_creation_tokens or 0)
    uncached = max(prompt - cached, 0)

    # Tiered pricing (litellm/dashscope): the tier is chosen by context
    # length — the total input token count.
    effective = _tier_prices(cost, uncached + cached + cache_created)

    normal_cost = Decimal(uncached) * Decimal(str(effective.input)) / Decimal(1_000_000)
    cache_read_cost = (
        Decimal(cached) * Decimal(str(effective.cache_read)) / Decimal(1_000_000)
    )
    # Cache-write tokens (Anthropic's cache_creation_input_tokens) have no
    # dedicated cost column, so they're folded into the input-cost bucket.
    cache_write_cost = (
        Decimal(cache_created)
        * Decimal(str(effective.cache_write or 0.0))
        / Decimal(1_000_000)
    )
    return {
        "normal_input_cost_usd": normal_cost,
        "cache_read_cost_usd": cache_read_cost,
        "cache_write_cost_usd": cache_write_cost,
    }


def calculate_costs(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cached_tokens: int | None,
    cache_creation_tokens: int | None = None,
    model: str | None = None,
    model_cost: ModelCost | None = None,
) -> dict[str, Decimal]:
    cost = model_cost
    if cost is None and model is not None:
        cost = resolve_model_cost(model)
    if cost is None:
        cost = ModelCost(input=0.0, output=0.0, cache_read=0.0)

    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    cached = int(cached_tokens or 0)
    cache_created = int(cache_creation_tokens or 0)
    uncached = max(prompt - cached, 0)

    split = compute_input_split(
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost=cost,
    )

    # Tiered pricing (litellm/dashscope): the tier is chosen by context
    # length — the total input token count.
    effective = _tier_prices(cost, uncached + cached + cache_created)
    output_cost = (
        Decimal(completion) * Decimal(str(effective.output)) / Decimal(1_000_000)
    )

    total_input_cost = (
        split["normal_input_cost_usd"]
        + split["cache_read_cost_usd"]
        + split["cache_write_cost_usd"]
    )
    return {
        "input_cost_usd": total_input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_input_cost + output_cost,
    }
