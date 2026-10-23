"""Shared server configuration loader for gunicorn config files."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class ServerConfig:
    host: str
    api_port: int
    base_url: str | None = None
    workers: int = 2


def validate_workers(value: object) -> int:
    """Require a positive integer process count (booleans are not counts)."""
    if type(value) is not int or value < 1:
        raise ValueError("server.workers must be an integer >= 1")
    return value


def resolve_server_urls(config: dict) -> dict[str, str]:
    """Return full URLs for the api and its mounted OTLP collector.

    Uses ``server.base_url`` when set (e.g. ``http://my-nas``), otherwise
    derives the host from ``server.host``.  The port comes from
    ``server.api_port``; OTLP is served under ``/otlp`` on the same port.
    """
    server = config.get("server", {})
    base_url = server.get("base_url")
    base = None
    if base_url:
        raw_base_url = str(base_url)
        parsed = urlparse(
            raw_base_url if "://" in raw_base_url else f"//{raw_base_url.rstrip('/')}"
        )
        if parsed.hostname:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            base = f"{parsed.scheme or 'http'}://{host}"
    if base is None:
        host = str(server.get("host", "127.0.0.1"))
        if host in ("0.0.0.0", "127.0.0.1", "::"):
            host = "localhost"
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        base = f"http://{host}"
    api_url = f"{base}:{int(server.get('api_port', 4001))}"
    return {"api_url": api_url, "otlp_url": f"{api_url}/otlp"}


def load_server_config() -> ServerConfig:
    from .models import get_config_path

    config_path = get_config_path()
    try:
        with open(config_path, encoding="utf-8") as config_file:
            cfg = yaml.safe_load(config_file) or {}
    except OSError:
        cfg = {}

    server = cfg.get("server", {})
    host = str(server.get("host", "127.0.0.1"))
    base_url = server.get("base_url")

    return ServerConfig(
        host=host,
        api_port=int(server.get("api_port", 4001)),
        base_url=base_url,
        workers=validate_workers(server.get("workers", 2)),
    )
