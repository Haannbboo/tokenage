"""Runtime pricing maps: resolution, YAML parsing, and source tracking.

The module-level maps hold the pricing currently in effect (YAML overrides
plus fetched LiteLLM pricing). ``src.config.app.refresh_runtime_config`` calls
``refresh_pricing_maps`` to repopulate them whenever the config reloads.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..utils import normalize_model_name
from ..utils import replace_contents as _replace_contents
from .models import (
    ModelCost,
    ModelTier,
    ResolvedCost,
    build_segment_index,
)
from .sources.base import FetchedSource

logger = logging.getLogger(__name__)


def _parse_tiered_cost(cost: dict[str, Any], flat: ModelCost) -> tuple[ModelTier, ...]:
    """Parse YAML `cost.tiers` (per-million prices with `range` token bounds).

    Missing per-tier prices fall back to the flat cost values.
    """
    raw_tiers = cost.get("tiers")
    if not isinstance(raw_tiers, list):
        return ()

    tiers: list[ModelTier] = []
    for raw in raw_tiers:
        if not isinstance(raw, dict):
            continue
        try:
            rng = raw.get("range")
            if not isinstance(rng, list) or not rng:
                continue
            min_tokens = int(float(rng[0]))
            max_tokens = (
                int(float(rng[1])) if len(rng) > 1 and rng[1] is not None else None
            )
            tier_input = raw.get("input")
            tier_output = raw.get("output")
            tier_cache_read = raw.get("cacheRead")
            tier_cache_write = raw.get("cacheWrite")
            tiers.append(
                ModelTier(
                    min_tokens=min_tokens,
                    max_tokens=max_tokens,
                    input=flat.input if tier_input is None else float(tier_input),
                    output=flat.output if tier_output is None else float(tier_output),
                    cache_read=(
                        flat.cache_read
                        if tier_cache_read is None
                        else float(tier_cache_read)
                    ),
                    cache_write=(
                        flat.cache_write
                        if tier_cache_write is None
                        else float(tier_cache_write)
                    ),
                )
            )
        except (TypeError, ValueError):
            # Malformed tier (bad range or non-numeric price): skip it rather
            # than crashing config load at import time.
            continue
    return tuple(tiers)


def _parse_model_cost(
    model_config: Any, base: ModelCost | None = None
) -> ModelCost | None:
    if not isinstance(model_config, dict):
        return None

    cost = model_config.get("cost")
    if not isinstance(cost, dict):
        return None

    flat = ModelCost(
        input=float(cost.get("input", base.input if base else 0)),
        output=float(cost.get("output", base.output if base else 0)),
        cache_read=float(cost.get("cacheRead", base.cache_read if base else 0)),
        cache_write=(
            float(cost["cacheWrite"])
            if "cacheWrite" in cost
            else base.cache_write
            if base
            else None
        ),
    )

    # YAML tiers without flat prices: default flat values to the first tier
    # (mirrors litellm, which uses the first tier as the flat price).
    raw_tiers = cost.get("tiers")
    has_flat = bool({"input", "output", "cacheRead"}.intersection(cost))
    effective_flat = flat
    if isinstance(raw_tiers, list) and raw_tiers and not has_flat:
        raw_first = raw_tiers[0]
        if isinstance(raw_first, dict) and isinstance(raw_first.get("range"), list):
            try:
                effective_flat = ModelCost(
                    input=float(raw_first.get("input", flat.input)),
                    output=float(raw_first.get("output", flat.output)),
                    cache_read=float(raw_first.get("cacheRead", flat.cache_read)),
                    cache_write=flat.cache_write,
                )
            except (TypeError, ValueError):
                effective_flat = flat

    if "tiers" in cost:
        # Explicit tiers (even `tiers: []`) fully define the pricing shape.
        tiers = _parse_tiered_cost(cost, effective_flat)
    elif has_flat:
        # Explicit flat prices are an override: clear any inherited tiers so
        # the flat rates are actually billed.
        tiers = ()
    else:
        tiers = base.tiers if base is not None else ()
    if effective_flat is not flat:
        flat = effective_flat

    return ModelCost(
        input=flat.input,
        output=flat.output,
        cache_read=flat.cache_read,
        cache_write=flat.cache_write,
        tiers=tiers,
    )


def resolve_all_costs(
    config: dict[str, Any],
    fetched: list[FetchedSource] | None = None,
) -> dict[str, ResolvedCost]:
    """Resolve model costs with source metadata.

    Sources are layered lowest-priority first so higher-priority entries
    overwrite lower ones; YAML config is applied last as the top layer.
    """
    costs: dict[str, ResolvedCost] = {}

    # Process lowest priority first; on equal priority, later list entries are
    # processed first so the earlier config source wins the final overwrite.
    for _index, source in sorted(
        enumerate(fetched or []),
        key=lambda pair: (pair[1].priority, pair[0]),
        reverse=True,
    ):
        for entry in source.entries:
            costs[normalize_model_name(entry.key)] = ResolvedCost(
                cost=entry.cost, source=source.name
            )

    for model_name, model_config in config.get("models", {}).items():
        normalized_model = normalize_model_name(model_name)
        base_cost = costs.get(normalized_model)
        model_cost = _parse_model_cost(
            model_config,
            base_cost.cost if base_cost is not None else None,
        )
        if model_cost is not None:
            costs[normalized_model] = ResolvedCost(cost=model_cost, source="yaml")

    return costs


# Runtime pricing maps, populated by refresh_pricing_maps().
MODEL_COSTS: dict[str, ModelCost] = {}
MODEL_COST_SOURCES: dict[str, str] = {}
MODEL_SEGMENT_COSTS: dict[str, tuple[str, ModelCost]] = {}

# Guards the runtime pricing maps so a concurrent config refresh cannot be
# observed half-applied (new rates with an old source). Reentrant so
# refresh_runtime_config can hold it across both refresh_pricing_maps() and the
# CONFIG swap.
MAPS_LOCK = threading.RLock()


def refresh_pricing_maps(
    config: dict[str, Any],
    fetched: list[FetchedSource] | None = None,
) -> None:
    """Rebuild the runtime pricing maps from a config dict + fetched sources."""
    resolved = resolve_all_costs(config, fetched)
    model_costs = {key: rc.cost for key, rc in resolved.items()}
    model_cost_sources = {key: rc.source for key, rc in resolved.items()}
    model_segment_costs = build_segment_index(model_costs)

    with MAPS_LOCK:
        _replace_contents(MODEL_COSTS, model_costs)
        _replace_contents(MODEL_COST_SOURCES, model_cost_sources)
        _replace_contents(MODEL_SEGMENT_COSTS, model_segment_costs)
