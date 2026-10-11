"""OpenRouter source parsing and time-of-day rate resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from src.pricing.models import ModelCost, TimeRate, resolve_effective_cost
from src.pricing.sources.openrouter import _hhmm_to_minutes, parse_openrouter_json


def _payload() -> dict:
    return {
        "data": [
            {
                "id": "deepseek/deepseek-v4.1-flash",
                "pricing": {
                    "prompt": "0.00000015",
                    "completion": "0.0000006",
                    "input_cache_read": "0.000000003",
                    "overrides": [
                        {
                            "utc_days": ["saturday", "sunday"],
                            "prompt": "0.00000015",
                            "completion": "0.0000006",
                            "input_cache_read": "0.000000003",
                        },
                        {
                            "utc_days": [
                                "monday",
                                "tuesday",
                                "wednesday",
                                "thursday",
                                "friday",
                            ],
                            "utc_start": 100,
                            "utc_end": 400,
                            "prompt": "0.0000003",
                            "completion": "0.0000012",
                            "input_cache_read": "0.000000006",
                        },
                    ],
                },
            },
            {
                "id": "vendor/free-model:free",
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {"id": "~vendor/latest", "pricing": {"prompt": "1", "completion": "1"}},
            {
                "id": "vendor/m1:free:variant",
                "pricing": {"prompt": "1", "completion": "1"},
            },
            {
                "id": "vendor/m2",
                "is_free": True,
                "pricing": {"prompt": "1", "completion": "1"},
            },
            {"id": "vendor/bad", "pricing": "not-a-dict"},
        ]
    }


def _at(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1_000_000)


def test_parse_skips_variants_and_converts_to_per_million():
    costs = parse_openrouter_json(_payload())

    assert "deepseek/deepseek-v4.1-flash" in costs
    assert "deepseek-v4.1-flash" in costs  # vendor-stripped alias
    assert "vendor/free-model:free" not in costs
    assert "~vendor/latest" not in costs
    assert "vendor/m1:free:variant" not in costs  # marker anywhere, not just suffix
    assert "vendor/m2" not in costs  # is_free entries skipped
    assert "vendor/bad" not in costs

    cost = costs["deepseek/deepseek-v4.1-flash"]
    assert (cost.input, cost.output, cost.cache_read) == (0.15, 0.6, 0.003)


def test_parse_tolerates_garbage():
    assert parse_openrouter_json(None) == {}
    assert parse_openrouter_json({"data": "nope"}) == {}


def test_hhmm_parsing():
    assert _hhmm_to_minutes(0) == 0
    assert _hhmm_to_minutes(100) == 60
    assert _hhmm_to_minutes(130) == 90  # 01:30, not 1.30 hours
    assert _hhmm_to_minutes(2359) == 23 * 60 + 59


def test_time_resolution_peak_offpeak_and_weekend():
    cost = parse_openrouter_json(_payload())["deepseek/deepseek-v4.1-flash"]

    # 2026-09-14 is a Monday; 02:00 UTC is inside the weekday peak window.
    assert resolve_effective_cost(cost, _at("2026-09-14T02:00:00")).input == 0.3
    # 05:00 UTC is outside it -> base/off-peak.
    assert resolve_effective_cost(cost, _at("2026-09-14T05:00:00")).input == 0.15
    # Weekend whole-day window.
    assert resolve_effective_cost(cost, _at("2026-09-12T12:00:00")).input == 0.15
    assert resolve_effective_cost(cost, _at("2026-09-13T12:00:00")).input == 0.15


def test_min_prompt_tokens_override_becomes_context_tiers_not_time_window():
    payload = {
        "data": [
            {
                "id": "qwen/qwen3.7-plus",
                "pricing": {
                    "prompt": "0.00000032",
                    "completion": "0.00000128",
                    "input_cache_read": "0.000000064",
                    "overrides": [
                        {
                            "min_prompt_tokens": 256000,
                            "prompt": "0.00000096",
                            "completion": "0.00000384",
                            "input_cache_read": "0.000000192",
                        }
                    ],
                },
            }
        ]
    }

    cost = parse_openrouter_json(payload)["qwen/qwen3.7-plus"]

    assert cost.time_rates == ()
    assert len(cost.tiers) == 2
    low, high = cost.tiers
    assert (low.min_tokens, low.max_tokens) == (0, 256000)
    assert (low.input, low.output, low.cache_read) == (0.32, 1.28, 0.064)
    assert (high.min_tokens, high.max_tokens) == (256000, None)
    assert (high.input, high.output, high.cache_read) == (0.96, 3.84, 0.192)


def test_cache_write_propagates_to_flat_tiers_and_windows():
    payload = {
        "data": [
            {
                "id": "vendor/cw-model",
                "pricing": {
                    "prompt": "0.0000004",
                    "completion": "0.0000016",
                    "input_cache_read": "0.00000008",
                    "input_cache_write": "0.000002",
                    "overrides": [
                        {
                            "min_prompt_tokens": 100000,
                            "prompt": "0.0000012",
                            "completion": "0.0000048",
                            "input_cache_read": "0.00000024",
                        },
                        {
                            "utc_start": 100,
                            "utc_end": 400,
                            "prompt": "0.0000008",
                            "completion": "0.0000032",
                        },
                    ],
                },
            }
        ]
    }

    cost = parse_openrouter_json(payload)["vendor/cw-model"]

    assert cost.cache_write == 2.0
    low, high = cost.tiers
    # Both the padded base tier and a tier override that omits cache-write
    # inherit the top-level rate.
    assert (low.cache_write, high.cache_write) == (2.0, 2.0)
    # Time-window overrides inherit it too.
    assert cost.time_rates[0].cost.cache_write == 2.0


def test_end_of_day_window_is_not_a_wrap():
    """end_minute == 0 with a non-zero start means 24:00, not a wrap."""
    cost = ModelCost(
        input=1.0,
        output=1.0,
        cache_read=0.0,
        time_rates=(
            TimeRate(
                days=None,
                start_minute=18 * 60,
                end_minute=0,
                cost=ModelCost(input=3.0, output=3.0, cache_read=0.0),
            ),
        ),
    )
    assert resolve_effective_cost(cost, _at("2026-09-14T20:00:00")).input == 3.0
    assert resolve_effective_cost(cost, _at("2026-09-14T05:00:00")).input == 1.0
