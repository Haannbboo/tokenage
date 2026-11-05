from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

# Only these top-level sections are safe to backfill from src/config/config.example.yaml
# without introducing example provider stanzas into an existing user config.
SYNCABLE_TOP_LEVEL_KEYS = ("models", "server", "db", "pricing")


def _merge_missing_dict_values(
    target: dict[str, Any],
    defaults: dict[str, Any],
) -> bool:
    """Recursively copy only keys that are missing from ``target``."""
    changed = False

    for key, default_value in defaults.items():
        if key not in target:
            target[key] = deepcopy(default_value)
            changed = True
            continue

        current_value = target[key]
        if isinstance(current_value, dict) and isinstance(default_value, dict):
            changed = (
                _merge_missing_dict_values(current_value, default_value) or changed
            )

    return changed


def merge_missing_config_defaults(
    user_config: dict[str, Any] | None,
    default_config: dict[str, Any] | None,
    *,
    syncable_top_level_keys: tuple[str, ...] = SYNCABLE_TOP_LEVEL_KEYS,
) -> dict[str, Any]:
    """Backfill missing config keys while preserving existing user values."""
    merged_config = deepcopy(user_config or {})

    for section in syncable_top_level_keys:
        default_value = (default_config or {}).get(section)
        if default_value is None:
            continue

        current_value = merged_config.get(section)
        if current_value is None:
            merged_config[section] = deepcopy(default_value)
            continue

        if isinstance(current_value, dict) and isinstance(default_value, dict):
            _merge_missing_dict_values(current_value, default_value)

    return merged_config


def sync_config_file_with_defaults(
    config_path: str,
    default_config_path: str,
) -> bool:
    """Merge missing defaults from the example config into a real config file."""
    config_file = Path(config_path).expanduser()
    defaults_file = Path(default_config_path)

    user_config: dict = {}
    if config_file.exists():
        user_config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}

    default_config = yaml.safe_load(defaults_file.read_text(encoding="utf-8")) or {}
    merged_config = merge_missing_config_defaults(user_config, default_config)

    if merged_config == user_config:
        return False

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        yaml.safe_dump(merged_config, sort_keys=False),
        encoding="utf-8",
    )
    return True
