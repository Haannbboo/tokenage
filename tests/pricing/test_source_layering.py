"""Ranked source layering: priority, gap-fill, tie-break, YAML precedence."""

from __future__ import annotations

from src.pricing.maps import resolve_all_costs
from src.pricing.models import ModelCost
from src.pricing.sources.base import FetchedSource, SourceEntry


def _source(name: str, priority: int, entries: list[SourceEntry]) -> FetchedSource:
    return FetchedSource(name=name, priority=priority, entries=tuple(entries))


def test_higher_priority_wins_and_lower_fills_gaps():
    high = _source("high", 0, [SourceEntry("m", ModelCost(1.0, 1.0, 0.0))])
    low = _source(
        "low",
        1,
        [
            SourceEntry("m", ModelCost(2.0, 2.0, 0.0)),
            SourceEntry("n", ModelCost(5.0, 5.0, 0.0)),
        ],
    )

    resolved = resolve_all_costs({}, [high, low])

    assert resolved["m"].cost.input == 1.0
    assert resolved["m"].source == "high"
    assert resolved["n"].source == "low"  # gap filled by lower source


def test_equal_priority_earlier_source_wins():
    first = _source("first", 0, [SourceEntry("m", ModelCost(1.0, 1.0, 0.0))])
    second = _source("second", 0, [SourceEntry("m", ModelCost(2.0, 2.0, 0.0))])

    resolved = resolve_all_costs({}, [first, second])

    assert resolved["m"].cost.input == 1.0


def test_yaml_overrides_sources_and_records_provenance():
    low = _source("low", 0, [SourceEntry("m", ModelCost(1.0, 1.0, 0.0))])
    config = {
        "models": {"m": {"cost": {"input": 9.0, "output": 9.0, "cacheRead": 0.0}}}
    }

    resolved = resolve_all_costs(config, [low])

    assert resolved["m"].cost.input == 9.0
    assert resolved["m"].source == "server"
