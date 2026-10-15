"""Shared model identity across price imports, overrides, and lookups."""

from decimal import Decimal


def test_price_sources_use_shared_model_names():
    from src.pricing.sources.litellm import _parse_litellm_json
    from src.pricing.sources.openrouter import parse_openrouter_json

    litellm = _parse_litellm_json(
        {
            " Space-Bunny-Free ": {
                "input_cost_per_token": 0,
                "output_cost_per_token": 0,
            }
        }
    )
    openrouter = parse_openrouter_json(
        {
            "data": [
                {
                    "id": " Space-Bunny-Free ",
                    "pricing": {"prompt": "0", "completion": "0"},
                }
            ]
        }
    )
    assert set(litellm) == {"stealth/space-bunny-alpha"}
    assert "stealth/space-bunny-alpha" in openrouter
    assert "space-bunny-free" not in openrouter


def test_aliases_preserve_price_override_precedence():
    from src.pricing.costs import resolve_cost_match
    from src.pricing.maps import resolve_all_costs
    from src.pricing.models import ModelCost
    from src.pricing.sources.base import FetchedSource, SourceEntry

    resolved = resolve_all_costs(
        {
            "models": {
                " STEALTH/SPACE-BUNNY-ALPHA ": {"cost": {"input": 1, "output": 2}}
            },
        },
        [
            FetchedSource(
                name="test-source",
                priority=10,
                entries=(
                    SourceEntry(
                        key=" Space-Bunny-Free ",
                        cost=ModelCost(input=0, output=0, cache_read=0.5),
                    ),
                ),
            )
        ],
    )
    key = "stealth/space-bunny-alpha"
    assert set(resolved) == {key}
    costs = {key: rc.cost for key, rc in resolved.items()}
    for name in ("Space-Bunny-Free", " STEALTH/SPACE-BUNNY-ALPHA "):
        match = resolve_cost_match(name, costs)
        assert match is not None
        assert (match.key, match.source) == (key, "server")
        assert match.cost == ModelCost(input=1, output=2, cache_read=0.5)


def test_unmapped_free_name_has_no_cost():
    from src.pricing.costs import calculate_costs, resolve_pricing
    from src.pricing.models import ModelCost

    costs = {"deepseek-v4.1-flash": ModelCost(input=1, output=2, cache_read=0)}
    resolved = resolve_pricing("deepseek-v4.1-flash-free", 1779148800000000, costs)
    assert resolved.match is None
    assert calculate_costs(
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        model_cost=resolved.cost,
    )["total_cost_usd"] == Decimal("0")


def test_snapshot_enrichment_resolves_alias(fresh_db):
    from src.pricing.models import ModelCost
    from src.pricing.snapshots import enrich_rows, ensure_price_snapshot

    ensure_price_snapshot(
        date="2026-05-19",
        model="stealth/space-bunny-alpha",
        source="server",
        cost=ModelCost(input=1, output=2, cache_read=0),
        db_path=fresh_db.db_path,
    )
    row = enrich_rows(
        [
            {
                "ts": 1779148800000000,
                "provider": "Custom",
                "model": " Space-Bunny-Free ",
                "prompt_tokens": 1000,
            }
        ],
        db_path=fresh_db.db_path,
    )[0]
    assert row["normal_input_cost_usd"] == 0.001
    assert row["pricing"]["source"] == "server"


def test_snapshot_writes_and_lookups_share_model_normalization(fresh_db):
    from src.pricing.models import ModelCost
    from src.pricing.snapshots import ensure_price_snapshot, get_price_snapshot

    snapshot_ids = []
    for name in (" Space-Bunny-Free ", "STEALTH/SPACE-BUNNY-ALPHA"):
        snapshot_ids.append(
            ensure_price_snapshot(
                date="2026-05-19",
                model=name,
                source="server",
                cost=ModelCost(input=0, output=0, cache_read=0),
                db_path=fresh_db.db_path,
            )
        )
        snapshot = get_price_snapshot(
            date="2026-05-19", model=name, db_path=fresh_db.db_path
        )
        assert snapshot is not None
        assert snapshot[0].input == 0
    assert snapshot_ids[0] == snapshot_ids[1]
