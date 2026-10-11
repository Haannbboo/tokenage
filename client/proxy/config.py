"""``$TOKENAGE_HOME/client.yaml``: the ``proxy:`` section.

proxy:
  port: 4000                  # optional
  providers:
    DeepSeek:
      base_url: https://api.deepseek.com
      api_key: sk-...         # literal; no env indirection
      enabled: true           # optional; disabled providers are not routed
      auth_scheme: bearer     # or x-api-key
      models: [a, b]          # list of names, or a mapping name -> {...}
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Any

import yaml

from client.paths import client_config_path

logger = logging.getLogger("tokenage.client.proxy")

AUTH_SCHEMES = {"bearer", "x-api-key"}


def normalize_provider_name(name: str) -> str:
    """Fold provider casing so names share one identity (matches the server)."""
    return name.strip().lower()


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    auth_scheme: str = "bearer"  # "bearer" or "x-api-key" (Anthropic-native)


@dataclass(frozen=True)
class ProxyConfig:
    port: int | None
    providers: dict[str, ProviderConfig]  # by normalized name
    models: dict[str, ProviderConfig]  # exact model name -> provider


def _model_names(provider: dict[str, Any]) -> list[str]:
    models = provider.get("models") or []
    if not isinstance(models, (list, dict)):
        raise ValueError("models must be a list or a mapping")
    return [str(m) for m in models]  # a mapping iterates its names


def parse_proxy_config(raw: Any) -> ProxyConfig:
    """Validate a ``proxy:`` mapping. Raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("proxy must be a mapping")
    port = raw.get("port")
    if port is not None and (
        isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536
    ):
        raise ValueError("proxy.port must be an integer between 1 and 65535")
    raw_providers = {} if raw.get("providers") is None else raw["providers"]
    if not isinstance(raw_providers, dict):
        raise ValueError("proxy.providers must be a mapping")
    providers: dict[str, ProviderConfig] = {}
    models: dict[str, ProviderConfig] = {}
    for name, entry in raw_providers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"proxy.providers.{name} must be a mapping")
        if not entry.get("enabled", True):
            continue  # kept in the file, not routed
        base_url = entry.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise ValueError(f"proxy.providers.{name}.base_url is required")
        scheme = entry.get("auth_scheme", "bearer")
        if scheme not in AUTH_SCHEMES:
            raise ValueError(
                f"proxy.providers.{name}.auth_scheme must be one of "
                f"{sorted(AUTH_SCHEMES)}"
            )
        key = entry.get("api_key")
        provider = ProviderConfig(
            name=normalize_provider_name(str(name)),
            base_url=base_url,
            api_key=str(key) if key else None,
            auth_scheme=scheme,
        )
        providers[provider.name] = provider
        try:
            for model in _model_names(entry):
                models[model] = provider
        except ValueError as exc:
            raise ValueError(f"proxy.providers.{name}.{exc}") from exc
    return ProxyConfig(port=port, providers=providers, models=models)


def load_proxy_config() -> ProxyConfig | None:
    """The proxy config, or None when client.yaml has no ``proxy:`` section.

    A group/world-readable client.yaml holds provider keys, so it logs a
    warning; it still loads, because refusing would turn a permissions slip
    into a silent outage. Malformed content raises ValueError.
    """
    path = client_config_path()
    try:
        text = path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {path}") from exc
    if mode & 0o077:
        logger.warning(
            "%s is readable by other users (mode %o); run: chmod 600 %s",
            path,
            mode,
            path,
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    if data.get("proxy") is None:
        return None
    return parse_proxy_config(data["proxy"])
