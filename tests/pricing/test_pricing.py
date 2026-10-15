from __future__ import annotations

import json
import os
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

import src.pricing.sources.base as base_module
import src.pricing.sources.litellm as pricing_module
from src.pricing.models import ModelCost, ModelTier
from src.pricing.sources.base import FetchedSource, SourceEntry
from src.pricing.sources.litellm import (
    _claude_3x_alias,
    _is_chat_model,
    _parse_litellm_json,
    _parse_model_entry,
    _strip_provider_prefix,
    _strip_version_suffix,
    fetch_remote_pricing,
)


def _litellm(costs: dict[str, ModelCost]) -> list[FetchedSource]:
    return [
        FetchedSource(
            name="litellm",
            priority=0,
            entries=tuple(SourceEntry(key, cost) for key, cost in costs.items()),
        )
    ]


def _reset_config(api_module) -> None:
    """Clear CONFIG but keep the DB target: every request resolves to a user."""
    db = api_module.CONFIG.get("db")
    api_module.CONFIG.clear()
    api_module.CONFIG["db"] = db


# --- Key normalization ---


def test_strip_provider_prefix_anthropic():
    assert _strip_provider_prefix("anthropic.claude-sonnet-4-5-20250929-v1:0") == (
        "claude-sonnet-4-5-20250929-v1:0"
    )


def test_strip_provider_prefix_azure_ai():
    assert _strip_provider_prefix("azure_ai/gpt-5.4") == "gpt-5.4"


def test_strip_provider_prefix_bedrock():
    assert (
        _strip_provider_prefix(
            "bedrock_converse.anthropic.claude-sonnet-4-5-20250929-v1:0"
        )
        == "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )


def test_strip_provider_prefix_vertex():
    assert _strip_provider_prefix("vertex_ai/gemini-2.5-pro") == "gemini-2.5-pro"


def test_strip_provider_prefix_noop():
    assert _strip_provider_prefix("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_strip_version_suffix_date():
    assert _strip_version_suffix("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"


def test_strip_version_suffix_iso_date():
    assert _strip_version_suffix("gpt-4o-2024-08-06") == "gpt-4o"


def test_strip_version_suffix_noop():
    assert _strip_version_suffix("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_strip_version_suffix_with_v_suffix():
    assert (
        _strip_version_suffix("claude-sonnet-4-5-20250929-v1:0") == "claude-sonnet-4-5"
    )


# --- Model entry parsing ---


def test_parse_model_cost_with_cache_write(config_module, pricing_maps_module):
    model_config = {
        "cost": {
            "input": 3.0,
            "output": 15.0,
            "cacheRead": 0.3,
            "cacheWrite": 3.75,
        }
    }

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    assert cost.cache_write == 3.75


def test_parse_model_cost_without_cache_write(config_module, pricing_maps_module):
    model_config = {"cost": {"input": 3.0, "output": 15.0, "cacheRead": 0.3}}

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    assert cost.cache_write is None


def test_parse_model_cost_with_tiers(config_module, pricing_maps_module):
    model_config = {
        "cost": {
            "tiers": [
                {"range": [0, 256000], "input": 0.4, "output": 1.6, "cacheRead": 0.08},
                {"range": [256000, 1000000], "input": 1.2, "output": 4.8},
            ]
        }
    }

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    assert len(cost.tiers) == 2
    first, second = cost.tiers
    assert first.min_tokens == 0
    assert first.max_tokens == 256000
    assert first.input == 0.4
    assert first.output == 1.6
    assert first.cache_read == 0.08
    assert second.input == 1.2
    assert second.cache_read == 0.08  # falls back to first-tier flat price
    # Flat prices default to the first tier when not specified
    assert cost.input == 0.4
    assert cost.output == 1.6


def test_parse_model_cost_with_tiers_inherits_flat_from_yaml(
    config_module, pricing_maps_module
):
    model_config = {
        "cost": {
            "input": 3.0,
            "output": 15.0,
            "cacheRead": 0.3,
            "tiers": [
                {"range": [0, 100000], "input": 1.0, "output": 5.0},
                {"range": [100000, None], "input": 2.0, "output": 10.0},
            ],
        }
    }

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    assert cost.input == 3.0
    assert cost.output == 15.0
    assert cost.tiers[0].cache_read == 0.3
    assert cost.tiers[1].max_tokens is None


def test_parse_model_cost_with_tiers_carries_per_tier_cache_write(
    config_module, pricing_maps_module
):
    model_config = {
        "cost": {
            "cacheWrite": 3.0,
            "tiers": [
                {"range": [0, 256000], "input": 0.4, "output": 1.6},
                {
                    "range": [256000, None],
                    "input": 1.2,
                    "output": 4.8,
                    "cacheWrite": 7.5,
                },
            ],
        }
    }

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    first, second = cost.tiers
    assert first.cache_write == 3.0  # falls back to the flat cacheWrite
    assert second.cache_write == 7.5  # explicit per-tier override wins


def test_parse_model_cost_without_tiers_inherits_base_tiers(
    config_module, pricing_maps_module
):
    base = pricing_maps_module.ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        tiers=(
            pricing_maps_module.ModelTier(
                min_tokens=0, max_tokens=256000, input=0.4, output=1.6, cache_read=0.08
            ),
        ),
    )
    # No flat prices and no tiers key: keep the base's tiered pricing.
    model_config = {"cost": {"cacheWrite": 3.0}}

    cost = pricing_maps_module._parse_model_cost(model_config, base)

    assert cost is not None
    assert cost.tiers == base.tiers
    assert cost.cache_write == 3.0
    assert cost.input == 0.4


def test_parse_model_cost_flat_prices_clear_inherited_tiers(
    config_module, pricing_maps_module
):
    base = pricing_maps_module.ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        tiers=(
            pricing_maps_module.ModelTier(
                min_tokens=0, max_tokens=256000, input=0.4, output=1.6, cache_read=0.08
            ),
        ),
    )
    # Explicit flat prices are an override: tiers must not silently win.
    model_config = {"cost": {"input": 3.0, "output": 15.0}}

    cost = pricing_maps_module._parse_model_cost(model_config, base)

    assert cost is not None
    assert cost.tiers == ()
    assert cost.input == 3.0
    assert cost.output == 15.0


def test_parse_model_cost_empty_tiers_disables_tiers(
    config_module, pricing_maps_module
):
    base = pricing_maps_module.ModelCost(
        input=0.4,
        output=1.6,
        cache_read=0.08,
        tiers=(
            pricing_maps_module.ModelTier(
                min_tokens=0, max_tokens=256000, input=0.4, output=1.6, cache_read=0.08
            ),
        ),
    )
    model_config = {"cost": {"tiers": []}}

    cost = pricing_maps_module._parse_model_cost(model_config, base)

    assert cost is not None
    assert cost.tiers == ()
    assert cost.input == 0.4


def test_parse_model_cost_skips_malformed_tiers(config_module, pricing_maps_module):
    model_config = {
        "cost": {
            "tiers": [
                {"range": [None, 1000], "input": 1.0, "output": 2.0},
                {"range": ["bad", "worse"], "input": 1.0, "output": 2.0},
                {"range": [0, 256000], "input": "not-a-number", "output": 2.0},
                {"range": [256000, None], "input": 1.2, "output": 4.8},
            ]
        }
    }

    cost = pricing_maps_module._parse_model_cost(model_config)

    assert cost is not None
    assert len(cost.tiers) == 1
    assert cost.tiers[0].min_tokens == 256000


def test_is_chat_model_chat_mode():
    assert _is_chat_model({"mode": "chat"}) is True


def test_is_chat_model_text_mode():
    assert _is_chat_model({"mode": "text"}) is True


def test_is_chat_model_default():
    assert _is_chat_model({}) is True


def test_is_chat_model_image():
    assert _is_chat_model({"mode": "image_generation"}) is False


def test_is_chat_model_embedding():
    assert _is_chat_model({"mode": "embedding"}) is False


def test_is_chat_model_responses():
    assert _is_chat_model({"mode": "responses"}) is True


def test_parse_model_entry_converts_per_token_to_per_million():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
    }
    result = _parse_model_entry("anthropic.claude-sonnet-4-5-20250929-v1:0", entry)

    assert result is not None
    key, cost = result
    assert key == "claude-sonnet-4-5-20250929-v1:0"
    assert cost.input == 3.0  # 3e-06 * 1M
    assert cost.output == 15.0  # 1.5e-05 * 1M
    assert cost.cache_read == 0.3  # 3e-07 * 1M


def test_parse_model_entry_no_cache_read():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 2.5e-06,
        "output_cost_per_token": 1.5e-05,
    }
    result = _parse_model_entry("azure_ai/gpt-5.4", entry)

    assert result is not None
    _, cost = result
    assert cost.cache_read == 0.0


def test_parse_model_entry_responses_with_cache_read():
    entry = {
        "mode": "responses",
        "input_cost_per_token": 1.25e-06,
        "output_cost_per_token": 1e-05,
        "cache_read_input_token_cost": 1.25e-07,
    }
    result = _parse_model_entry("gpt-5-codex", entry)

    assert result is not None
    key, cost = result
    assert key == "gpt-5-codex"
    assert cost.input == 1.25
    assert cost.output == 10.0
    assert cost.cache_read == 0.125


def test_parse_model_entry_with_cache_creation_cost():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
        "cache_creation_input_token_cost": 3.75e-06,
    }

    result = _parse_model_entry("anthropic.claude-sonnet-4-5-20250929-v1:0", entry)

    assert result is not None
    _, cost = result
    assert cost.cache_write == 3.75


def test_parse_model_entry_without_cache_write():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
    }

    result = _parse_model_entry("azure_ai/gpt-5.4", entry)

    assert result is not None
    _, cost = result
    assert cost.cache_write is None


def test_parse_model_entry_skips_non_chat():
    entry = {
        "mode": "image_generation",
        "output_cost_per_image": 0.06,
    }
    assert _parse_model_entry("dall-e-2", entry) is None


def test_parse_model_entry_skips_no_pricing():
    entry = {"mode": "chat"}
    assert _parse_model_entry("some-model", entry) is None


def test_parse_model_entry_with_tiered_pricing():
    entry = {
        "mode": "chat",
        "tiered_pricing": [
            {
                "cache_read_input_token_cost": 8e-08,
                "input_cost_per_token": 4e-07,
                "output_cost_per_token": 1.6e-06,
                "range": [0, 256000.0],
            },
            {
                "cache_read_input_token_cost": 2.4e-07,
                "input_cost_per_token": 1.2e-06,
                "output_cost_per_token": 4.8e-06,
                "range": [256000.0, 1000000.0],
            },
        ],
    }

    result = _parse_model_entry("dashscope/qwen3.7-plus", entry)

    assert result is not None
    key, cost = result
    assert key == "dashscope/qwen3.7-plus"
    # Flat fields default to the first tier (per-million)
    assert cost.input == 0.4
    assert cost.output == 1.6
    assert cost.cache_read == 0.08
    assert len(cost.tiers) == 2
    first, second = cost.tiers
    assert first.min_tokens == 0
    assert first.max_tokens == 256000
    assert first.input == 0.4
    assert first.output == 1.6
    assert first.cache_read == 0.08
    assert second.min_tokens == 256000
    assert second.max_tokens == 1000000
    assert second.input == 1.2
    assert second.output == 4.8
    assert second.cache_read == 0.24


def test_parse_model_entry_with_empty_tiered_pricing_still_skipped():
    entry = {"mode": "chat", "tiered_pricing": []}
    assert _parse_model_entry("some-model", entry) is None


def test_parse_model_entry_with_above_threshold_context_steps():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 3e-08,
        "output_cost_per_token": 1.3e-07,
        "cache_read_input_token_cost": 6e-09,
        "cache_creation_input_token_cost": 3.8e-08,
        "input_cost_per_token_above_256k_tokens": 2e-07,
        "output_cost_per_token_above_256k_tokens": 8e-07,
        "cache_read_input_token_cost_above_256k_tokens": 4e-08,
        "cache_creation_input_token_cost_above_256k_tokens": 2.5e-07,
    }

    result = _parse_model_entry("openrouter/qwen/qwen3.7-flash", entry)

    assert result is not None
    _, cost = result
    assert (cost.input, cost.output) == (0.03, 0.13)
    assert len(cost.tiers) == 2
    low, high = cost.tiers
    assert (low.min_tokens, low.max_tokens) == (0, 256000)
    assert (low.input, low.output) == (0.03, 0.13)
    assert (high.min_tokens, high.max_tokens) == (256000, None)
    assert (high.input, high.output) == (0.2, 0.8)
    assert (high.cache_read, high.cache_write) == (0.04, 0.25)


def test_parse_model_entry_ignores_suffixed_above_threshold_fields():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 4e-07,
        "output_cost_per_token": 1.6e-06,
        "input_cost_per_token_above_272k_tokens_priority": 1.2e-06,
        "output_cost_per_token_above_272k_tokens_flex": 4.8e-06,
    }

    result = _parse_model_entry("azure/gpt-5.4", entry)

    assert result is not None
    _, cost = result
    assert cost.tiers == ()


def test_parse_model_entry_skips_above_threshold_without_base_rates():
    entry = {
        "mode": "chat",
        "input_cost_per_token_above_256k_tokens": 2e-07,
        "output_cost_per_token_above_256k_tokens": 8e-07,
    }

    # No base rates: the low tier would be all zeros, so skip instead of
    # publishing the model at input=0/output=0.
    assert _parse_model_entry("some-model", entry) is None


def test_parse_model_entry_tier_falls_back_to_top_level_prices():
    entry = {
        "mode": "chat",
        "input_cost_per_token": 4e-07,
        "output_cost_per_token": 1.6e-06,
        "cache_read_input_token_cost": 8e-08,
        "tiered_pricing": [
            {
                "input_cost_per_token": 1.2e-06,
                "output_cost_per_token": 4.8e-06,
                "range": [256000.0, 1000000.0],
            }
        ],
    }

    result = _parse_model_entry("dashscope/some-model", entry)

    assert result is not None
    _, cost = result
    assert len(cost.tiers) == 1
    tier = cost.tiers[0]
    assert tier.input == 1.2
    assert tier.output == 4.8
    assert tier.cache_read == 0.08  # falls back to the entry's top-level value


def test_parse_model_entry_tier_carries_own_cache_write_price():
    entry = {
        "mode": "chat",
        "cache_creation_input_token_cost": 3.75e-06,
        "tiered_pricing": [
            {
                "input_cost_per_token": 4e-07,
                "output_cost_per_token": 1.6e-06,
                "range": [0, 256000.0],
            },
            {
                "input_cost_per_token": 1.2e-06,
                "output_cost_per_token": 4.8e-06,
                "cache_creation_input_token_cost": 7.5e-06,
                "range": [256000.0, 1000000.0],
            },
        ],
    }

    result = _parse_model_entry("anthropic/claude-long-context", entry)

    assert result is not None
    _, cost = result
    first, second = cost.tiers
    # No per-tier override: falls back to the entry's top-level cache_write.
    assert first.cache_write == 3.75
    # Explicit per-tier override wins over the entry's top-level value.
    assert second.cache_write == 7.5


def test_parse_model_entry_skips_malformed_tiers():
    entry = {
        "mode": "chat",
        "tiered_pricing": [
            {"input_cost_per_token": 4e-07, "range": [None, 1000]},
            {
                "input_cost_per_token": "bad",
                "output_cost_per_token": 1.6e-06,
                "range": [0, 256000],
            },
            {
                "input_cost_per_token": 1.2e-06,
                "output_cost_per_token": 4.8e-06,
                "range": [256000.0, 1000000.0],
            },
        ],
    }

    result = _parse_model_entry("dashscope/some-model", entry)

    assert result is not None
    _, cost = result
    assert len(cost.tiers) == 1
    assert cost.tiers[0].min_tokens == 256000
    assert cost.input == 1.2  # flat defaults to the first valid tier


# --- Full JSON parsing ---


def test_parse_litellm_json_extracts_chat_models():
    data = {
        "sample_spec": {"description": "schema"},
        "anthropic.claude-haiku-4-5-20251001-v1:0": {
            "mode": "chat",
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 5e-06,
            "cache_read_input_token_cost": 1e-07,
        },
        "dall-e-2": {
            "mode": "image_generation",
            "output_cost_per_pixel": 0.0,
        },
    }
    costs = _parse_litellm_json(data)

    # Date-stripped base name is kept, full key is deduplicated away
    assert "claude-haiku-4-5" in costs
    assert costs["claude-haiku-4-5"].input == 1.0
    # Image model should be skipped
    assert len(costs) >= 1


def test_parse_litellm_json_creates_base_name_alias():
    data = {
        "anthropic.claude-sonnet-4-5-20250929-v1:0": {
            "mode": "chat",
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
        },
    }
    costs = _parse_litellm_json(data)

    # Full key is deduplicated away, only base name kept
    assert "claude-sonnet-4-5-20250929-v1:0" not in costs
    assert "claude-sonnet-4-5" in costs
    assert costs["claude-sonnet-4-5"].input == 3.0


def test_parse_litellm_json_keys_are_lowercased():
    data = {
        "azure_ai/GPT-5.4": {
            "mode": "chat",
            "input_cost_per_token": 2.5e-06,
            "output_cost_per_token": 1.5e-05,
        },
    }
    costs = _parse_litellm_json(data)

    assert "gpt-5.4" in costs


# --- resolve_all_costs integration ---


def test_resolve_all_costs_yaml_global(config_module, pricing_maps_module):
    config = {
        "models": {
            "claude-sonnet-4-6": {
                "cost": {"input": 3.0, "output": 15.0, "cacheRead": 0.3}
            },
        },
    }

    resolved = pricing_maps_module.resolve_all_costs(config)

    assert resolved["claude-sonnet-4-6"].source == "server"
    assert resolved["claude-sonnet-4-6"].cost.input == 3.0


def test_resolve_all_costs_litellm_gap_fill(config_module, pricing_maps_module):
    config = {
        "models": {"claude-sonnet-4-6": {}},
    }
    remote = {
        "claude-sonnet-4-6": ModelCost(input=3.0, output=15.0, cache_read=0.3),
    }

    resolved = pricing_maps_module.resolve_all_costs(config, _litellm(remote))

    assert resolved["claude-sonnet-4-6"].source == "litellm"
    assert resolved["claude-sonnet-4-6"].cost.input == 3.0


def test_resolve_all_costs_yaml_wins_over_litellm(config_module, pricing_maps_module):
    config = {
        "models": {
            "test-model": {"cost": {"input": 1.0, "output": 2.0, "cacheRead": 0.1}},
        },
    }
    remote = {
        "test-model": ModelCost(input=99.0, output=99.0, cache_read=99.0),
    }

    resolved = pricing_maps_module.resolve_all_costs(config, _litellm(remote))

    assert resolved["test-model"].cost.input == 1.0
    assert resolved["test-model"].source == "server"


def test_resolve_all_costs_partial_yaml_merges_with_litellm(
    config_module, pricing_maps_module
):
    config = {
        "models": {
            "test-model": {"cost": {"input": 9.0}},
        },
    }
    remote = {
        "test-model": ModelCost(
            input=1.0,
            output=2.0,
            cache_read=0.1,
            cache_write=0.25,
        ),
    }

    resolved = pricing_maps_module.resolve_all_costs(config, _litellm(remote))

    cost = resolved["test-model"].cost
    assert resolved["test-model"].source == "server"
    assert cost.input == 9.0
    assert cost.output == 2.0
    assert cost.cache_read == 0.1
    assert cost.cache_write == 0.25


def test_pricing_lists_yaml_and_source_models(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update(
        {
            "models": {
                "test-model": {"cost": {"input": 1.0, "output": 2.0, "cacheRead": 0.1}},
            },
        }
    )
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "remote-model": ModelCost(input=3.0, output=6.0, cache_read=0.3)
        },
    )

    response = TestClient(api_module.app).get("/pricing")

    assert response.status_code == 200
    data = {m["name"]: m for m in response.json()["models"]}
    assert data["test-model"]["input"] == 1.0
    assert data["test-model"]["source"] == "server"
    assert data["remote-model"]["source"] == "litellm"
    assert "scope" not in data["test-model"]
    assert "multiplier" not in data["test-model"]


def test_single_model_pricing_contains_litellm_match(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "openrouter/xiaomi/mimo-v2.5-pro": ModelCost(
                input=1.0, output=3.0, cache_read=0.2
            )
        },
    )

    response = TestClient(api_module.app).get("/pricing/mimo-v2.5-pro")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["model"] == "openrouter/xiaomi/mimo-v2.5-pro"
    assert data["source"] == "litellm"
    assert data["input"] == 1.0
    assert data["output"] == 3.0
    assert data["cache_read"] == 0.2
    assert data["tiers"] == []


def test_single_model_pricing_includes_tiers(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "dashscope/qwen3.7-plus": ModelCost(
                input=0.4,
                output=1.6,
                cache_read=0.08,
                tiers=(
                    ModelTier(
                        min_tokens=0,
                        max_tokens=256000,
                        input=0.4,
                        output=1.6,
                        cache_read=0.08,
                    ),
                    ModelTier(
                        min_tokens=256000,
                        max_tokens=1000000,
                        input=1.2,
                        output=4.8,
                        cache_read=0.24,
                    ),
                ),
            )
        },
    )

    response = TestClient(api_module.app).get("/pricing/qwen3.7-plus")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["tiers"] == [
        {
            "min_tokens": 0,
            "max_tokens": 256000,
            "input": 0.4,
            "output": 1.6,
            "cache_read": 0.08,
            "cache_write": None,
        },
        {
            "min_tokens": 256000,
            "max_tokens": 1000000,
            "input": 1.2,
            "output": 4.8,
            "cache_read": 0.24,
            "cache_write": None,
        },
    ]


def test_single_model_pricing_unresolved_includes_empty_tiers(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing", lambda *args, **kwargs: {}
    )

    response = TestClient(api_module.app).get("/pricing/no-such-model")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is False
    assert data["tiers"] == []


def test_single_model_pricing_yaml_override_beats_litellm(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update(
        {
            "models": {
                "test-model": {"cost": {"input": 2.0, "output": 4.0, "cacheRead": 0.2}},
            },
        }
    )
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "test-model": ModelCost(input=9.0, output=9.0, cache_read=9.0)
        },
    )

    response = TestClient(api_module.app).get("/pricing/test-model")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["model"] == "test-model"
    assert data["source"] == "server"
    assert data["input"] == 2.0
    assert data["output"] == 4.0


def test_single_model_pricing_cheapest_contains_match(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "openrouter/xiaomi/mimo-v2.5-pro": ModelCost(
                input=1.0, output=3.0, cache_read=0.2
            ),
            "gateway/xiaomi/mimo-v2.5-pro": ModelCost(
                input=0.5, output=1.0, cache_read=0.1
            ),
        },
    )

    response = TestClient(api_module.app).get("/pricing/mimo-v2.5-pro")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["model"] == "gateway/xiaomi/mimo-v2.5-pro"
    assert data["input"] == 0.5


def test_single_model_pricing_slashed_model_exact_yaml(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update(
        {
            "models": {
                "z-ai/glm-5.1-20260406": {
                    "cost": {"input": 0.98, "output": 3.08, "cacheRead": 0.182}
                },
            },
        }
    )
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing", lambda *args, **kwargs: {}
    )

    response = TestClient(api_module.app).get("/pricing/z-ai/glm-5.1-20260406")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["model"] == "z-ai/glm-5.1-20260406"
    assert data["source"] == "server"
    assert data["input"] == 0.98


@pytest.mark.parametrize(
    "model, expected",
    [
        ("unknown-model", "unknown-model"),
        (" Unknown-Model ", "unknown-model"),
        ("Space-Bunny-Free", "stealth/space-bunny-alpha"),
    ],
)
def test_single_model_pricing_unresolved(api_module, monkeypatch, model, expected):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing", lambda *args, **kwargs: {}
    )

    response = TestClient(api_module.app).get(f"/pricing/{model}")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is False
    assert data["model"] == expected
    assert data["input"] == 0.0
    assert data["output"] == 0.0


def test_single_model_pricing_case_insensitive(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing",
        lambda *args, **kwargs: {
            "openrouter/xiaomi/mimo-v2.5-pro": ModelCost(
                input=1.0, output=3.0, cache_read=0.2
            )
        },
    )

    response = TestClient(api_module.app).get("/pricing/Mimo-V2.5-Pro")

    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] is True
    assert data["model"] == "openrouter/xiaomi/mimo-v2.5-pro"
    assert data["input"] == 1.0


def test_single_model_pricing_rejects_empty_model(api_module, monkeypatch):
    _reset_config(api_module)
    api_module.CONFIG.update({"models": {}})
    monkeypatch.setattr(
        "src.pricing.sources.litellm.fetch_remote_pricing", lambda *args, **kwargs: {}
    )

    response = TestClient(api_module.app).get("/pricing/")

    assert response.status_code == 422


# --- Claude 3.x alias ---


def test_claude_3x_alias_sonnet():
    assert _claude_3x_alias("claude-3-7-sonnet") == "claude-sonnet-3-7"


def test_claude_3x_alias_opus():
    assert _claude_3x_alias("claude-3-opus") == "claude-opus-3"


def test_claude_3x_alias_haiku():
    assert _claude_3x_alias("claude-3-5-haiku") == "claude-haiku-3-5"


def test_claude_3x_alias_no_match():
    assert _claude_3x_alias("claude-sonnet-4-6") is None


def test_parse_litellm_json_creates_claude_3x_alias():
    data = {
        "claude-3-7-sonnet": {
            "mode": "chat",
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
        },
    }
    costs = _parse_litellm_json(data)

    assert "claude-3-7-sonnet" in costs
    assert "claude-sonnet-3-7" in costs
    assert costs["claude-3-7-sonnet"] == costs["claude-sonnet-3-7"]


def test_parse_litellm_json_claude_3x_alias_with_date():
    data = {
        "claude-3-7-sonnet-20250219": {
            "mode": "chat",
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
        },
    }
    costs = _parse_litellm_json(data)

    # Full key with date is deduplicated away
    assert "claude-3-7-sonnet-20250219" not in costs
    # Date-stripped base name
    assert "claude-3-7-sonnet" in costs
    # Claude 3.x alias
    assert "claude-sonnet-3-7" in costs


# --- Cache TTL ---


def test_fetch_remote_pricing_skips_network_when_cache_fresh(tmp_path, monkeypatch):
    cache_path = tmp_path / "litellm_pricing.json"
    cache_path.write_text(
        json.dumps({"gpt-fresh": {"input_cost_per_token": 1e-06, "mode": "chat"}})
    )
    monkeypatch.setattr(base_module, "cache_path", lambda name: cache_path)
    monkeypatch.setattr(pricing_module, "_remote_costs", None)

    def _fail_if_called():
        raise AssertionError("should not hit the network when cache is fresh")

    monkeypatch.setattr(pricing_module, "_fetch_litellm_json", _fail_if_called)

    costs = fetch_remote_pricing()

    assert "gpt-fresh" in costs


def test_fetch_remote_pricing_refetches_when_cache_stale(tmp_path, monkeypatch):
    cache_path = tmp_path / "litellm_pricing.json"
    cache_path.write_text(
        json.dumps({"gpt-old": {"input_cost_per_token": 1e-06, "mode": "chat"}})
    )
    stale_mtime = time.time() - pricing_module.CACHE_TTL_SECONDS - 1
    os.utime(cache_path, (stale_mtime, stale_mtime))

    monkeypatch.setattr(base_module, "cache_path", lambda name: cache_path)
    monkeypatch.setattr(pricing_module, "_remote_costs", None)
    monkeypatch.setattr(
        pricing_module,
        "_fetch_litellm_json",
        lambda: {"gpt-new": {"input_cost_per_token": 2e-06, "mode": "chat"}},
    )

    costs = fetch_remote_pricing()

    assert "gpt-new" in costs
    assert "gpt-old" not in costs
    # Cache file on disk was refreshed with the newly fetched data.
    assert "gpt-new" in json.loads(cache_path.read_text())


def test_fetch_remote_pricing_refetches_when_fresh_cache_is_corrupt(
    tmp_path, monkeypatch
):
    """A fresh mtime with unparseable content (truncated/corrupt write)
    must not block a real fetch for a full CACHE_TTL_SECONDS."""
    cache_path = tmp_path / "litellm_pricing.json"
    cache_path.write_text("{not valid json")  # fresh mtime, but garbage content

    monkeypatch.setattr(base_module, "cache_path", lambda name: cache_path)
    monkeypatch.setattr(pricing_module, "_remote_costs", None)
    monkeypatch.setattr(
        pricing_module,
        "_fetch_litellm_json",
        lambda: {"gpt-recovered": {"input_cost_per_token": 1e-06, "mode": "chat"}},
    )

    costs = fetch_remote_pricing()

    assert "gpt-recovered" in costs


# --- IPv4-only fetch (scoped, not a global socket.getaddrinfo patch) ---


def test_create_ipv4_connection_requests_af_inet_only(monkeypatch):
    seen_family = {}

    class _FakeSocket:
        def __init__(self):
            self.connected_to = None

        def settimeout(self, _):
            pass

        def connect(self, sockaddr):
            self.connected_to = sockaddr

        def close(self):
            pass

    def _fake_getaddrinfo(host, port, family, socktype):
        seen_family["family"] = family
        return [(family, socktype, 0, "", (host, port))]

    fake_sock = _FakeSocket()
    monkeypatch.setattr(base_module.socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(base_module.socket, "socket", lambda *a, **k: fake_sock)

    result = base_module._create_ipv4_connection(("example.com", 443))

    assert seen_family["family"] == base_module.socket.AF_INET
    assert result is fake_sock
    assert fake_sock.connected_to == ("example.com", 443)


def test_create_ipv4_connection_raises_when_all_candidates_fail(monkeypatch):
    class _FakeSocket:
        def settimeout(self, _):
            pass

        def connect(self, sockaddr):
            raise OSError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr(
        base_module.socket,
        "getaddrinfo",
        lambda host, port, family, socktype: [(family, socktype, 0, "", (host, port))],
    )
    monkeypatch.setattr(base_module.socket, "socket", lambda *a, **k: _FakeSocket())

    try:
        base_module._create_ipv4_connection(("example.com", 443))
        raise AssertionError("expected OSError")
    except OSError as exc:
        assert "connection refused" in str(exc)


def test_fetch_litellm_json_does_not_mutate_global_getaddrinfo(monkeypatch):
    """Regression guard for the original bug: this must not monkeypatch
    socket.getaddrinfo globally, since the same process also resolves DNS
    for concurrent, unrelated live proxy traffic."""
    original_getaddrinfo = base_module.socket.getaddrinfo

    class _RaisingOpener:
        def open(self, *a, **k):
            raise urllib.error.URLError("network unavailable in test")

    monkeypatch.setattr(
        base_module.urllib.request, "build_opener", lambda *a, **k: _RaisingOpener()
    )

    result = pricing_module._fetch_litellm_json()

    assert result is None
    assert base_module.socket.getaddrinfo is original_getaddrinfo
