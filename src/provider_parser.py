"""Rule-based provider parser for coding agents.

Reads agent config files to find base URLs, then maps them to provider names.
"""

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .utils import normalize_provider_name

# URL-based provider mapping rules (checked in order)
PROVIDER_RULES: list[tuple[str, str]] = [
    ("api.minimaxi", "MiniMax"),
    ("xiaomimimo", "Xiaomi"),
    ("api.anthropic", "Anthropic"),
    ("openrouter.ai", "OpenRouter"),
    ("api.openai", "OpenAI"),
    ("api.github", "GitHub"),
    ("stepfun", "StepFun"),
    ("azure", "Azure"),
    ("google", "Google"),
    ("amazon", "AWS"),
    ("localhost", "local"),
    ("127.0.0.1", "local"),
]


def _load_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _load_toml(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _extract_url(config: dict, *keys: str) -> str | None:
    # First try explicit dotted key paths (e.g. "env.ANTHROPIC_BASE_URL")
    for key in keys:
        val: Any = config
        for part in key.split("."):
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    # Then search nested sections (e.g. model_providers.*.base_url)
    return _find_url_in_nested(config, set(keys))


def _find_url_in_nested(obj: dict, keys: set[str]) -> str | None:
    for key in keys:
        if key in obj:
            val = obj[key]
            if isinstance(val, str) and val.startswith("http"):
                return val
    for val in obj.values():
        if isinstance(val, dict):
            found = _find_url_in_nested(val, keys)
            if found:
                return found
    return None


def _match_url_to_provider(url: str) -> str:
    for pattern, provider in PROVIDER_RULES:
        if pattern in url:
            return provider
    return "unknown"


def _derive_provider_from_base_url(url: str) -> str | None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip(".").lower()
    if not hostname:
        return None

    if hostname == "localhost" or "." not in hostname:
        return hostname

    parts = hostname.split(".")
    if len(parts) < 2:
        return None

    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in {"ac", "co", "com", "edu", "gov", "net", "org"}
    ):
        return parts[-3]

    return parts[-2]


@dataclass(frozen=True)
class ProviderMetadata:
    provider: str
    base_url: str | None
    source: str | None


def parse_claude_base_url() -> str | None:
    """Parse Claude Code base URL from ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    config = _load_json(settings_path)
    if not config:
        return None
    return _extract_url(config, "env.ANTHROPIC_BASE_URL", "base_url", "url")


def _load_codex_config() -> dict | None:
    """Load Codex config, merging $CODEX_HOME/config.toml over global."""
    config = _load_toml(Path.home() / ".codex" / "config.toml") or {}
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        override = _load_toml(Path(codex_home) / "config.toml")
        if override:
            config.update(override)
    return config or None


def parse_codex_base_url() -> str | None:
    """Parse Codex base URL from config.toml.

    Reads ~/.codex/config.toml first, then merges $CODEX_HOME/config.toml
    on top so project-level overrides take precedence.
    """
    config = _load_codex_config()
    if not config:
        return None
    return _extract_url(config, "base_url", "url", "api_base_url")


def parse_opencode_base_url(provider_id: str | None = None) -> str | None:
    """Parse OpenCode base URL from ~/.config/opencode/opencode.json."""
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    config = _load_json(config_path)
    if not config:
        return None
    provider_section = config.get("provider")
    if not isinstance(provider_section, dict):
        return None

    if provider_id:
        provider_conf = provider_section.get(provider_id)
        if isinstance(provider_conf, dict):
            options = provider_conf.get("options")
            if isinstance(options, dict):
                url = options.get("baseURL") or options.get("base_url")
                if url:
                    return str(url)
        return None

    for provider_conf in provider_section.values():
        if not isinstance(provider_conf, dict):
            continue
        options = provider_conf.get("options")
        if not isinstance(options, dict):
            continue
        url = options.get("baseURL") or options.get("base_url")
        if url:
            return str(url)
    return None


PROVIDER_DEFAULTS: dict[str, str] = {
    "claude": "anthropic",
    "codex": "openai",
    "opencode": "unknown",
}


def parse_provider_metadata(
    agent: str, provider_id: str | None = None
) -> ProviderMetadata:
    """Parse provider/base URL metadata for a coding agent config."""
    if agent == "claude":
        base_url = parse_claude_base_url()
        source = "claude_settings"
    elif agent == "codex":
        base_url = parse_codex_base_url()
        source = "codex_config"
    elif agent == "opencode":
        base_url = parse_opencode_base_url(provider_id)
        source = "opencode_config"
    else:
        return ProviderMetadata(
            provider=PROVIDER_DEFAULTS.get(agent, "unknown"),
            base_url=None,
            source=None,
        )

    provider = _match_url_to_provider(base_url) if base_url else "unknown"
    if provider == "unknown" and base_url:
        provider = _derive_provider_from_base_url(base_url) or "unknown"
    if provider == "unknown":
        provider = PROVIDER_DEFAULTS.get(agent, "unknown")

    return ProviderMetadata(
        provider=normalize_provider_name(provider), base_url=base_url, source=source
    )


def parse_provider(agent: str) -> str:
    """Parse provider for a coding agent, with fallback to default."""
    return parse_provider_metadata(agent).provider
