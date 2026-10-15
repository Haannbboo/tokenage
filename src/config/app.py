import os
import threading
from typing import Any

import yaml

from ..utils import replace_contents as _replace_contents
from . import merge as merge_helpers
from .models import expand_path, get_tracker_home

CONFIG_ENV_VAR = "TOKENAGE_CONFIG"

merge_missing_config_defaults = merge_helpers.merge_missing_config_defaults
sync_config_file_with_defaults = merge_helpers.sync_config_file_with_defaults


def get_config_path(path: str | None = None) -> str:
    if path:
        return expand_path(path)
    return expand_path(
        os.environ.get(CONFIG_ENV_VAR, os.path.join(get_tracker_home(), "config.yaml"))
    )


CONFIG_PATH = get_config_path()


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(get_config_path(path), encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    config = config or {}
    server = config.setdefault("server", {})
    db = config.setdefault("db", {})
    config.setdefault("models", {})

    server.setdefault("host", "127.0.0.1")
    server.setdefault("api_port", 4001)

    auth = config.setdefault("auth", {})
    auth.setdefault("provider", "local")
    if auth["provider"] not in ("local", "google"):
        raise ValueError("auth.provider must be 'local' or 'google'")
    auth.setdefault("allowlist", [])
    # Google OAuth credentials: config.yaml by default (0600), with TOKENAGE_AUTH__GOOGLE_CLIENT_* overriding when
    # set — so a deployment can still inject them without touching the file.
    for key in ("google_client_id", "google_client_secret"):
        env_value = os.environ.get(f"TOKENAGE_AUTH__{key.upper()}")
        auth[key] = env_value or str(auth.get(key, "") or "")

    otlp = config.setdefault("otlp", {})
    otlp.setdefault("max_body_bytes", 2_000_000)  # 2 MB
    # Byte cap bounds bytes, not records: each record costs a DB transaction.
    max_records = otlp.setdefault("max_records_per_request", 1_000)
    if not isinstance(max_records, int) or max_records < 1:
        # A zero/negative cap would silently drop every export. Fall back to the
        # default instead of raising: this runs at import time.
        otlp["max_records_per_request"] = 1_000

    rate_limits = config.setdefault("rate_limits", {})
    rate_limits.setdefault("ingest_per_minute", 1000)  # per token: OTLP + usage
    rate_limits.setdefault("api_per_minute", 1200)  # per user: authenticated API

    if "url" not in db:
        db.setdefault("path", os.path.join(get_tracker_home(), "usage.db"))
        db["path"] = expand_path(db["path"])
        db["url"] = f"sqlite:///{db['path']}"
    elif isinstance(db["url"], str) and db["url"].startswith("sqlite:///"):
        sqlite_path = db["url"][10:]
        if sqlite_path.startswith("~"):
            db["url"] = f"sqlite:///{expand_path(sqlite_path)}"

    if "path" in db and isinstance(db["path"], str):
        db["path"] = expand_path(db["path"])
    return config


_config_lock = threading.Lock()


def refresh_runtime_config(path: str | None = None) -> dict[str, Any]:
    from src.pricing.maps import MAPS_LOCK, refresh_pricing_maps
    from src.pricing.sources.registry import fetch_sources

    updated_config = load_config(path)

    # Fetch configured price sources (each uses its local cache on failure).
    auto_fetch = updated_config.get("pricing", {}).get("auto_fetch", True)
    fetched = fetch_sources(updated_config) if auto_fetch else []

    # Swap the pricing maps and CONFIG under one lock so a record never
    # observes them half-applied.
    with MAPS_LOCK:
        refresh_pricing_maps(updated_config, fetched)
        with _config_lock:
            _replace_contents(CONFIG, updated_config)

    return CONFIG


CONFIG: dict[str, Any] = {}
refresh_runtime_config(CONFIG_PATH)
