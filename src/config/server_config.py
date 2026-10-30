"""Shared server configuration loader for gunicorn config files."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int | None = None
    allowed_hosts: tuple[str, ...] = ()


def resolve_api_bind(config: dict) -> tuple[str, int]:
    server = config.get("server", {})
    return (
        os.environ.get("TOKENAGE_API_HOST", str(server.get("host", "127.0.0.1"))).strip(
            "[]"
        ),
        int(os.environ.get("TOKENAGE_API_PORT", server.get("api_port", 4001))),
    )


def _metrics_host(host: object) -> str:
    if not isinstance(host, str):
        raise ValueError("metrics host must be a hostname or IP address")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not re.fullmatch(r"[\w.:-]+", host, re.ASCII):
        raise ValueError("metrics host must be a hostname or IP address")
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError("metrics host must be a hostname or IP address") from None
    return host.lower()


def validate_metrics(config: dict, api_port: int) -> MetricsConfig:
    raw = config.get("metrics", {})
    if not isinstance(raw, dict):
        raise ValueError("metrics must be a mapping")
    enabled = raw.get("enabled", False)
    host = _metrics_host(raw.get("host", "127.0.0.1"))
    port = raw.get("port")
    if type(enabled) is not bool:
        raise ValueError("metrics.enabled must be a boolean")
    allowed_hosts = raw.get("allowed_hosts", [])
    if not isinstance(allowed_hosts, list):
        raise ValueError(
            "metrics.allowed_hosts must be a list of hostnames or IP addresses"
        )
    allowed_hosts = tuple(_metrics_host(value) for value in allowed_hosts)
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("metrics.port must be an integer from 1 to 65535")
    if enabled and port is None:
        raise ValueError("metrics.port is required when metrics.enabled is true")
    if enabled and port == api_port:
        raise ValueError("metrics.port must differ from the resolved API port")
    return MetricsConfig(enabled, host, port, allowed_hosts)


@dataclass(frozen=True)
class ServerConfig:
    host: str
    api_port: int
    base_url: str | None = None
    workers: int = 2
    metrics: MetricsConfig = MetricsConfig()


def validate_workers(value: object) -> int:
    """Require a positive integer process count (booleans are not counts)."""
    if type(value) is not int or value < 1:
        raise ValueError("server.workers must be an integer >= 1")
    return value


def resolve_server_urls(config: dict) -> dict[str, str]:
    """Return full URLs for the api and its mounted OTLP collector.

    Uses ``server.base_url`` when set (e.g. ``http://my-nas``), otherwise
    derives the host and port from ``server.host`` and ``server.api_port``.
    OTLP is served under ``/otlp`` on the same origin.
    """
    server = config.get("server", {})
    base_url = server.get("base_url")
    base = None
    if base_url:
        raw_base_url = str(base_url)
        try:
            parsed = urlparse(
                raw_base_url
                if "://" in raw_base_url
                else f"//{raw_base_url.rstrip('/')}"
            )
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid server.base_url: {exc}") from exc
        if parsed.hostname:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            scheme = parsed.scheme or "http"
            base = f"{scheme}://{host}"
            if port is not None and (scheme, port) not in (
                ("http", 80),
                ("https", 443),
            ):
                base = f"{base}:{port}"
    if base is None:
        host = str(server.get("host", "127.0.0.1"))
        if host in ("0.0.0.0", "127.0.0.1", "::"):
            host = "localhost"
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        base = f"http://{host}:{int(server.get('api_port', 4001))}"
    api_url = base
    return {"api_url": api_url, "otlp_url": f"{api_url}/otlp"}


def load_server_config() -> ServerConfig:
    from .models import get_config_path

    config_path = get_config_path()
    try:
        with open(config_path, encoding="utf-8") as config_file:
            cfg = yaml.safe_load(config_file) or {}
    except OSError:
        cfg = {}

    resolve_server_urls(cfg)
    server = cfg.get("server", {})
    host, api_port = resolve_api_bind(cfg)
    base_url = server.get("base_url")

    return ServerConfig(
        host=host,
        api_port=api_port,
        base_url=base_url,
        workers=validate_workers(server.get("workers", 2)),
        metrics=validate_metrics(cfg, api_port),
    )
