"""Build and fetch configured price sources in priority order.

Source order comes from ``pricing.sources`` in the config; the list index is the
priority (lower = higher). An absent block defaults to ``[litellm]``, preserving
the single-source behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import FetchedSource, PriceSource
from .litellm import CACHE_TTL_SECONDS, LiteLLMSource
from .openrouter import OpenRouterSource

log = logging.getLogger(__name__)

_DEFAULT_SOURCE_SPECS: list[dict[str, Any]] = [
    {"name": "litellm", "type": "litellm", "enabled": True}
]
_DEFAULT_OPENROUTER_TTL_SECONDS = 6 * 60 * 60


def _ttl_seconds(spec: dict[str, Any], default: int) -> int:
    """Parse a source's configured ``ttl_seconds``, falling back on bad input."""
    try:
        return int(spec.get("ttl_seconds", default))
    except (TypeError, ValueError):
        return default


def _source_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    pricing = config.get("pricing") if isinstance(config, dict) else None
    specs = pricing.get("sources") if isinstance(pricing, dict) else None
    if not isinstance(specs, list) or not specs:
        return _DEFAULT_SOURCE_SPECS
    return [spec for spec in specs if isinstance(spec, dict)]


def build_sources(config: dict[str, Any]) -> list[PriceSource]:
    sources: list[PriceSource] = []
    for spec in _source_specs(config):
        if not spec.get("enabled", True):
            continue
        source_type = spec.get("type") or spec.get("name")
        name = spec.get("name") or source_type or "unknown"
        if source_type == "litellm":
            source: PriceSource = LiteLLMSource(
                ttl_seconds=_ttl_seconds(spec, CACHE_TTL_SECONDS)
            )
        elif source_type == "openrouter":
            source = OpenRouterSource(
                ttl_seconds=_ttl_seconds(spec, _DEFAULT_OPENROUTER_TTL_SECONDS)
            )
        else:
            log.warning("Unknown pricing source type: %s", source_type)
            continue
        source.name = name  # config name is for provenance/display only
        sources.append(source)
    return sources


def fetch_sources(
    config: dict[str, Any], *, force: bool = False
) -> list[FetchedSource]:
    """Fetch every enabled source, isolated: one failure never blocks another."""
    fetched: list[FetchedSource] = []
    for priority, source in enumerate(build_sources(config)):
        if force:
            source.ttl_seconds = 0
        try:
            entries = tuple(source.fetch())
        except Exception:
            log.exception("Pricing source %s failed", getattr(source, "name", "?"))
            entries = ()
        fetched.append(
            FetchedSource(name=source.name, priority=priority, entries=entries)
        )
    return fetched
