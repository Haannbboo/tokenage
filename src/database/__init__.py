"""Database models and query helpers for tokenage.

The steady-state pattern in this module is:
- ORM models for entity lifecycle operations such as inserts and base URL resolution
- `select(...)`-style projection queries for usage reporting and aggregation
"""

# Re-export sqlalchemy helpers used by tests and consumers via this module
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .base_url import get_or_create_base_url, resolve_base_url_id
from .engine import DB_URL_ENV_VAR, get_db_url, get_engine, init_db
from .model_prices import (
    delete_model_price,
    list_model_prices,
    load_model_price_overrides,
    upsert_model_price,
)
from .models import (
    AuthToken,
    Base,
    BaseUrl,
    Device,
    PriceSnapshot,
    SessionRecord,
    Usage,
    UsageHourly,
    User,
    UserModelPrice,
    metadata,
)
from .sessions import (
    SessionFilters,
    aggregate_session_context,
    aggregate_session_distribution,
    aggregate_session_projects,
    count_sessions,
    fetch_session_selector_rows,
    fetch_sessions,
    get_session_record,
    rebuild_sessions_from_usage,
    session_project_detail,
    summarize_sessions,
    upsert_session_from_usage,
)
from .usage import (
    CostRecalcResult,
    aggregate_daily_by_dimension,
    aggregate_usage_by_period,
    count_usage,
    distinct_client_sources,
    distinct_tool_names,
    fetch_recent_usage,
    fetch_tool_calls,
    get_usage_high_watermark_ts,
    log_usage,
    recalculate_usage_cost,
    reprice_estimated_rows,
    summarize_tool_calls,
    summarize_usage_by_model,
    summarize_usage_by_provider,
    summarize_usage_by_source,
    summarize_usage_window,
    upsert_hourly_aggregate,
)


def record_usage(*args, **kwargs):
    from ..recorder import record_usage as _record_usage

    return _record_usage(*args, **kwargs)


__all__ = [
    "SessionFilters",
    "aggregate_session_projects",
    "session_project_detail",
    "AuthToken",
    "Base",
    "BaseUrl",
    "CostRecalcResult",
    "DB_URL_ENV_VAR",
    "Device",
    "PriceSnapshot",
    "Session",
    "SessionRecord",
    "Usage",
    "UsageHourly",
    "User",
    "UserModelPrice",
    "aggregate_daily_by_dimension",
    "aggregate_session_context",
    "aggregate_session_distribution",
    "aggregate_usage_by_period",
    "count_sessions",
    "count_usage",
    "delete_model_price",
    "distinct_client_sources",
    "distinct_tool_names",
    "fetch_recent_usage",
    "fetch_session_selector_rows",
    "fetch_sessions",
    "fetch_tool_calls",
    "get_db_url",
    "get_engine",
    "get_or_create_base_url",
    "get_session_record",
    "get_usage_high_watermark_ts",
    "init_db",
    "list_model_prices",
    "load_model_price_overrides",
    "log_usage",
    "recalculate_usage_cost",
    "reprice_estimated_rows",
    "metadata",
    "rebuild_sessions_from_usage",
    "record_usage",
    "resolve_base_url_id",
    "select",
    "summarize_sessions",
    "summarize_tool_calls",
    "summarize_usage_by_provider",
    "summarize_usage_by_source",
    "summarize_usage_by_model",
    "summarize_usage_window",
    "text",
    "upsert_hourly_aggregate",
    "upsert_model_price",
    "upsert_session_from_usage",
]
