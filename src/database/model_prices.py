"""Per-user model price overrides (``user_model_prices``)."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite

from ..pricing.models import ModelCost
from ..utils import normalize_model_name
from .engine import get_engine
from .models import UserModelPrice

_FIELDS = ("model", "input", "output", "cache_read", "cache_write", "updated_at")


def list_model_prices(*, user_id: str, db_path: str | None = None) -> list[dict]:
    query = (
        select(*(getattr(UserModelPrice, f) for f in _FIELDS))
        .where(UserModelPrice.user_id == user_id)
        .order_by(UserModelPrice.model)
    )
    with get_engine(db_path).connect() as connection:
        return [dict(row._mapping) for row in connection.execute(query)]


def load_model_price_overrides(
    *, user_id: str, db_path: str | None = None
) -> dict[str, ModelCost]:
    """The user's overrides keyed by normalized model, ready for resolution."""
    return {
        row["model"]: ModelCost(
            input=row["input"],
            output=row["output"],
            cache_read=row["cache_read"] or 0.0,
            cache_write=row["cache_write"],
        )
        for row in list_model_prices(user_id=user_id, db_path=db_path)
    }


def upsert_model_price(
    *,
    user_id: str,
    model: str,
    input: float,
    output: float,
    cache_read: float | None = None,
    cache_write: float | None = None,
    db_path: str | None = None,
) -> dict:
    """Insert or replace the user's price for ``model``; return the saved row."""
    engine = get_engine(db_path)
    values: dict[str, Any] = {
        "model": normalize_model_name(model),
        "input": input,
        "output": output,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "updated_at": time.time_ns() // 1000,
    }
    dialect = postgresql if engine.dialect.name == "postgresql" else sqlite
    stmt = dialect.insert(UserModelPrice).values(user_id=user_id, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "model"],
        set_={k: v for k, v in values.items() if k != "model"},
    )
    with engine.begin() as connection:
        connection.execute(stmt)
    return values


def delete_model_price(*, user_id: str, model: str, db_path: str | None = None) -> bool:
    """Delete the user's price for ``model``; return whether one existed."""
    stmt = delete(UserModelPrice).where(
        UserModelPrice.user_id == user_id,
        UserModelPrice.model == normalize_model_name(model),
    )
    with get_engine(db_path).begin() as connection:
        return connection.execute(stmt).rowcount > 0
