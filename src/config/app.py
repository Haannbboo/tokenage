import os
import threading
from typing import Any

import yaml

from ..utils import replace_contents as _replace_contents
from . import merge as merge_helpers
from .models import expand_path, get_config_path, get_tracker_home
from .server_config import resolve_api_bind, validate_metrics, validate_workers

merge_missing_config_defaults = merge_helpers.merge_missing_config_defaults
sync_config_file_with_defaults = merge_helpers.sync_config_file_with_defaults


CONFIG_PATH = get_config_path()


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(get_config_path(path), encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    config = config or {}
    server = config.setdefault("server", {})
    db = config.setdefault("db", {})
    config.setdefault("models", {})
    config.setdefault("queue", {})

    server["host"], server["api_port"] = resolve_api_bind(config)
    server["workers"] = validate_workers(server.get("workers", 2))
    metrics = validate_metrics(config, server["api_port"])
    config["metrics"] = {
        "enabled": metrics.enabled,
        "host": metrics.host,
        "port": metrics.port,
        "allowed_hosts": list(metrics.allowed_hosts),
    }

    auth = config.setdefault("auth", {})
    auth.setdefault("provider", "local")
    if auth["provider"] not in ("local", "google"):
        raise ValueError("auth.provider must be 'local' or 'google'")
    # `allowlist:` with no value (null) means no allowlist: any verified account.
    if auth.get("allowlist") is None:
        auth["allowlist"] = []
    allowlist = auth["allowlist"]
    if not isinstance(allowlist, list) or not all(
        isinstance(entry, str) for entry in allowlist
    ):
        raise ValueError("auth.allowlist must be a list of email addresses")
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
    # signin: per client IP (IPv6 by /64), anonymous Google/CLI sign-in starts
    # and login links. Needs real client IPs (FORWARDED_ALLOW_IPS) before
    # enabling, or every caller behind the proxy shares one budget.
    # cli_approve: per signed-in user, CLI login approvals (each stores a
    # pending code). 0 = off for both.
    for key, default in (("signin_per_minute", 0), ("cli_approve_per_minute", 10)):
        if rate_limits.get(key) is None:  # unset or left empty
            rate_limits[key] = default
        value = rate_limits[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"rate_limits.{key} must be a non-negative integer")

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
