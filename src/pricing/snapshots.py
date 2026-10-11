"""Per-day pricing snapshots enabling exact cost-split recomputation.

At record time the prices used for a usage row are stored once per
``(date, model, source, rates_hash)``, and the usage row binds to the
exact snapshot via ``usage.price_snapshot_id``. Because the row is
content-addressed, a later price change inserts a new snapshot instead of
mutating what earlier rows point to, and read-time enrichment can replay the
exact rates that priced each row.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.dialects import postgresql, sqlite

from ..database import PriceSnapshot, get_engine
from ..utils import micros_to_secs, normalize_model_name
from .costs import (
    compute_input_split,
    resolve_cost_match,
    resolve_token_rates,
)
from .models import ModelCost, ModelTier

COST_SPLIT_KEYS = (
    "normal_input_cost_usd",
    "cache_read_cost_usd",
    "cache_write_cost_usd",
)


def serialize_rates(cost: ModelCost) -> str:
    # Canonical JSON (sorted keys, no whitespace) so identical rate sets hash
    # identically regardless of construction order or serializer.
    return json.dumps(
        {
            "input": cost.input,
            "output": cost.output,
            "cache_read": cost.cache_read,
            "cache_write": cost.cache_write,
            "tiers": [
                {
                    "min_tokens": tier.min_tokens,
                    "max_tokens": tier.max_tokens,
                    "input": tier.input,
                    "output": tier.output,
                    "cache_read": tier.cache_read,
                    "cache_write": tier.cache_write,
                }
                for tier in cost.tiers
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def rates_hash(payload: str) -> str:
    """Content hash of a serialized rate set (used for snapshot versioning)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_rates(payload: str) -> ModelCost:
    data = json.loads(payload)
    tiers = tuple(
        ModelTier(
            min_tokens=tier["min_tokens"],
            max_tokens=tier["max_tokens"],
            input=tier["input"],
            output=tier["output"],
            cache_read=tier["cache_read"],
            cache_write=tier.get("cache_write"),
        )
        for tier in data.get("tiers", [])
    )
    return ModelCost(
        input=data["input"],
        output=data["output"],
        cache_read=data["cache_read"],
        cache_write=data.get("cache_write"),
        tiers=tiers,
    )


def _ts_date(ts_micros: int | None) -> str:
    return datetime.fromtimestamp(micros_to_secs(ts_micros or 0), tz=UTC).strftime(
        "%Y-%m-%d"
    )


def ensure_price_snapshot(
    *,
    date: str,
    model: str,
    source: str,
    cost: ModelCost,
    db_path: str | None = None,
) -> int:
    """Insert a snapshot if this exact rate set is new; return its row id.

    Idempotent and race-safe (content-addressed ``ON CONFLICT DO NOTHING``),
    called from the hot record path. ``cost`` must already be time-of-day
    resolved (see ``resolve_effective_cost``).
    """
    model = normalize_model_name(model)
    engine = get_engine(db_path)
    payload = serialize_rates(cost)
    digest = rates_hash(payload)
    values = {
        "date": date,
        "model": model,
        "source": source,
        "rates_hash": digest,
        "rates_json": payload,
        "recorded_at": time.time_ns() // 1000,
    }
    key_columns = ["date", "model", "source", "rates_hash"]
    insert_stmt: Any
    if engine.dialect.name == "postgresql":
        insert_stmt = postgresql.insert(PriceSnapshot).values(**values)
    else:
        insert_stmt = sqlite.insert(PriceSnapshot).values(**values)
    stmt = insert_stmt.on_conflict_do_nothing(index_elements=key_columns)

    with engine.begin() as connection:
        connection.execute(stmt)
        row_id = connection.execute(
            select(PriceSnapshot.id).where(
                PriceSnapshot.date == date,
                PriceSnapshot.model == model,
                PriceSnapshot.source == source,
                PriceSnapshot.rates_hash == digest,
            )
        ).scalar()
    if row_id is None:  # pragma: no cover - insert guarantees a row
        raise RuntimeError("price snapshot insert did not return a row id")
    return int(row_id)


def get_price_snapshot(
    *,
    date: str,
    model: str,
    db_path: str | None = None,
) -> tuple[ModelCost, str] | None:
    """Return the most recent snapshot for (date, model).

    Legacy/unbound lookup only; new rows read via ``price_snapshot_id``.
    """
    model = normalize_model_name(model)
    engine = get_engine(db_path)
    with engine.connect() as connection:
        record = connection.execute(
            select(PriceSnapshot.rates_json, PriceSnapshot.source)
            .where(
                PriceSnapshot.date == date,
                PriceSnapshot.model == model,
            )
            .order_by(PriceSnapshot.recorded_at.desc(), PriceSnapshot.id.desc())
            .limit(1)
        ).one_or_none()
    if record is None:
        return None
    rates_json, source = record
    return parse_rates(rates_json), source


def _load_snapshots_by_id(
    ids: set[int], db_path: str | None = None
) -> dict[int, tuple[ModelCost, str]]:
    """Batch-load snapshots for the bound ``usage.price_snapshot_id`` values."""
    if not ids:
        return {}
    engine = get_engine(db_path)
    with engine.connect() as connection:
        result = connection.execute(
            select(
                PriceSnapshot.id, PriceSnapshot.rates_json, PriceSnapshot.source
            ).where(PriceSnapshot.id.in_(list(ids)))
        )
        return {
            int(row_id): (parse_rates(payload), source)
            for row_id, payload, source in result
        }


def _lookup_key(row: dict) -> tuple[str, str] | None:
    model = row.get("model")
    if not model:
        return None
    return (_ts_date(row.get("ts")), normalize_model_name(model))


def _load_snapshot_cache(
    rows: list[dict], db_path: str | None = None
) -> dict[tuple[str, str], tuple[ModelCost, str]]:
    """Fetch newest snapshots for unbound rows in a single query (avoids N+1)."""
    keys = {key for key in (_lookup_key(row) for row in rows) if key is not None}
    if not keys:
        return {}
    engine = get_engine(db_path)
    with engine.connect() as connection:
        result = connection.execute(
            select(
                PriceSnapshot.date,
                PriceSnapshot.model,
                PriceSnapshot.rates_json,
                PriceSnapshot.source,
            )
            .where(tuple_(PriceSnapshot.date, PriceSnapshot.model).in_(list(keys)))
            .order_by(PriceSnapshot.recorded_at.desc(), PriceSnapshot.id.desc())
        )
        cache: dict[tuple[str, str], tuple[ModelCost, str]] = {}
        for date, model, payload, source in result:
            cache.setdefault((date, model), (parse_rates(payload), source))
        return cache


def _split(cost: ModelCost, row: dict) -> dict[str, float]:
    split = compute_input_split(
        prompt_tokens=row.get("prompt_tokens"),
        cached_tokens=row.get("cached_tokens"),
        cache_creation_tokens=row.get("cache_creation_tokens"),
        cost=cost,
    )
    return {key: float(value) for key, value in split.items()}


def _row_input_tokens(row: dict) -> int:
    """Total input tokens used for tier selection.

    ``cache_creation_tokens`` is disjoint from ``prompt_tokens`` (Anthropic
    semantics), so it's added rather than subtracted.
    """
    prompt = int(row.get("prompt_tokens") or 0)
    cached = int(row.get("cached_tokens") or 0)
    cache_created = int(row.get("cache_creation_tokens") or 0)
    return max(prompt - cached, 0) + cached + cache_created


def _resolve_row_rates(
    row: dict,
    db_path: str | None = None,
    snapshot_cache: dict[tuple[str, str], tuple[ModelCost, str]] | None = None,
    snapshots_by_id: dict[int, tuple[ModelCost, str]] | None = None,
) -> tuple[ModelCost, str | None, int | None] | None:
    """Resolve the rates that priced a row.

    Returns ``(cost, source, snapshot_id)`` or ``None`` when the
    row's model cannot be priced. ``snapshot_id`` is non-None only for
    rows pinned to their bound snapshot; legacy/unbound rows fall back to the
    newest snapshot or current config and read as estimated.
    """
    key = _lookup_key(row)
    if key is None:
        return None

    snapshot_id = row.get("price_snapshot_id")
    if snapshot_id is not None:
        sid = int(snapshot_id)
        snapshot = (
            snapshots_by_id.get(sid)
            if snapshots_by_id is not None
            else _load_snapshots_by_id({sid}, db_path).get(sid)
        )
        if snapshot is not None:
            cost, source = snapshot
            return cost, source, sid

    if snapshot_cache is not None:
        snapshot = snapshot_cache.get(key)
    else:
        snapshot = get_price_snapshot(date=key[0], model=key[1], db_path=db_path)
    if snapshot is not None:
        cost, source = snapshot
        return cost, source, None

    match = resolve_cost_match(str(row.get("model") or ""))
    if match is None:
        return None
    return match.cost, match.source, None


def build_pricing_detail(
    cost: ModelCost,
    source: str | None,
    row: dict,
    *,
    snapshot_id: int | None,
) -> dict[str, Any]:
    """Describe how a row's cost was calculated, for display in the UI."""
    effective, tier = resolve_token_rates(cost, _row_input_tokens(row))
    return {
        "source": source,
        "input": effective.input,
        "output": effective.output,
        "cache_read": effective.cache_read,
        "cache_write": effective.cache_write,
        "tier": None
        if tier is None
        else {"min_tokens": tier.min_tokens, "max_tokens": tier.max_tokens},
        "snapshot_id": snapshot_id,
        "estimated": snapshot_id is None,
    }


def enrich_rows(rows: list[dict], db_path: str | None = None) -> list[dict]:
    """Merge the input-cost split and pricing detail into each usage row.

    Rows bound to a snapshot (``price_snapshot_id``) use exactly that snapshot's
    rates. Unbound/legacy rows fall back to the newest legacy snapshot or the
    current config and are marked ``cost_estimated: true``.
    """
    bound_ids = {
        int(row["price_snapshot_id"])
        for row in rows
        if row.get("price_snapshot_id") is not None
    }
    by_id = _load_snapshots_by_id(bound_ids, db_path)
    legacy_cache = _load_snapshot_cache(
        [row for row in rows if row.get("price_snapshot_id") is None], db_path
    )

    result: list[dict] = []
    for row in rows:
        rates = _resolve_row_rates(
            row, db_path, snapshot_cache=legacy_cache, snapshots_by_id=by_id
        )
        if rates is None:
            split = {split_key: 0.0 for split_key in COST_SPLIT_KEYS}
            pricing: dict[str, Any] | None = None
            estimated = True
        else:
            cost, source, snapshot_id = rates
            split = _split(cost, row)
            pricing = build_pricing_detail(cost, source, row, snapshot_id=snapshot_id)
            estimated = snapshot_id is None
        result.append({**row, **split, "pricing": pricing, "cost_estimated": estimated})
    return result
