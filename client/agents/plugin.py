"""Shared OpenCode / Kilo Code plugin registration.

The plugin registers itself in the agent's config as a tracked plugin, built from
``plugins/<name>`` under the project root, so tokenage's health check can detect
it. The per-agent modules only say which agent and which config file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from client.agents import DONE, FAILED, SKIPPED, write_private
from client.agents import info as _info

# A cold `npm install` can take minutes. This bounds a hang, not slow work.
BUILD_TIMEOUT = 300


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} configuration; left unchanged") from exc
    if not isinstance(data, dict) or (
        "plugin" in data and not isinstance(data["plugin"], list)
    ):
        raise ValueError(f"invalid {label} configuration; left unchanged")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    write_private(path, json.dumps(data, indent=2) + "\n")


def warn_skip(label: str, message: str, *, failed: bool = False) -> int:
    print(f"WARNING: {message}; skipping {label} plugin configuration", file=sys.stderr)
    return FAILED if failed else SKIPPED


def run_npm(
    args: list[str], plugin_dir: Path
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["npm", *args],
            cwd=plugin_dir,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
        )
    except FileNotFoundError:
        return None


def tracker_suffixes(name: str) -> tuple[str, str]:
    """Both ways a tracker build is registered: the built directory, or its
    ``index.js``. Older installs registered the file."""
    return f"plugins/{name}/dist", f"plugins/{name}/dist/index.js"


def _is_tracker_entry(entry: object, name: str) -> bool:
    entry_path = (
        entry
        if isinstance(entry, str)
        else entry[0]
        if isinstance(entry, list) and entry
        else ""
    )
    normalized = str(entry_path).replace("\\", "/").rstrip("/")
    return normalized.endswith(tracker_suffixes(name))


def disable(name: str, label: str, config_path: Path, expected_endpoint: str | None):
    """Remove tracker entries for the expected collector, preserving others.

    Plugin entries are identified by build path and collector endpoint. The
    built dist/ is left in place: rebuilding is cheaper than being wrong.
    """
    try:
        return _disable(name, label, config_path, expected_endpoint)
    except (OSError, UnicodeError, ValueError):
        print(f"cannot update {label} configuration; left unchanged", file=sys.stderr)
        return FAILED


def _disable(
    name: str, label: str, config_path: Path, expected_endpoint: str | None
) -> int:
    if not expected_endpoint:
        print(
            f"collector unknown; {label} configuration left unchanged", file=sys.stderr
        )
        return SKIPPED
    if not config_path.exists():
        _info(f"No {label} config at {config_path}")
        return SKIPPED
    config = load_json(config_path, label)
    plugins = config.get("plugin")
    if not isinstance(plugins, list):
        _info(f"No tokenage plugin in {config_path}")
        return SKIPPED
    kept = []
    removed = 0
    for entry in plugins:
        if _is_tracker_entry(entry, name):
            options = (
                entry[1]
                if isinstance(entry, list)
                and len(entry) >= 2
                and isinstance(entry[1], dict)
                else {}
            )
            endpoint = options.get("endpoint")
            if endpoint != expected_endpoint:
                kept.append(entry)
                _info(
                    f"Plugin in {config_path} points at another collector; left alone"
                )
                continue
            removed += 1
            continue
        kept.append(entry)
    if not removed:
        _info(f"No matching tokenage plugin in {config_path}")
        return SKIPPED
    if kept:
        config["plugin"] = kept
    else:
        config.pop("plugin", None)
    save_json(config_path, config)
    _info(f"tokenage plugin removed from {config_path}")
    return DONE


def configure(
    name: str,
    label: str,
    config_path: Path,
    project_root: str | Path,
    endpoint: str,
    token: str | None,
    *,
    register_directory: bool = False,
) -> int:
    try:
        return _configure(
            name,
            label,
            config_path,
            project_root,
            endpoint,
            token,
            register_directory,
        )
    except subprocess.TimeoutExpired:
        return warn_skip(label, "plugin build timed out", failed=True)
    except (OSError, UnicodeError, ValueError):
        print(f"cannot update {label} configuration; left unchanged", file=sys.stderr)
        return FAILED


def _configure(
    name: str,
    label: str,
    config_path: Path,
    project_root: str | Path,
    endpoint: str,
    token: str | None,
    register_directory: bool,
) -> int:
    project_root = Path(project_root).expanduser().resolve()
    load_json(config_path, label)  # refuse a bad config before building
    plugin_dir = project_root / "plugins" / name
    dist_dir = plugin_dir / "dist"

    if not (dist_dir / "index.js").exists():
        node_modules = plugin_dir / "node_modules"
        if not node_modules.exists():
            _info(f"Installing {label} plugin dependencies in {plugin_dir}")
            # `npm ci` needs a committed lock file. A checkout without one still
            # has to build, so fall back rather than skipping the agent.
            locked = (plugin_dir / "package-lock.json").exists()
            command = ["ci"] if locked else ["install"]
            result = run_npm(command, plugin_dir)
            if result is None:
                return warn_skip(label, "npm not found")
            if result.returncode != 0:
                return warn_skip(
                    label, f"npm {command[0]} failed:\n{result.stderr}", failed=True
                )
        _info(f"Building {label} plugin from {plugin_dir}")
        result = run_npm(["run", "build"], plugin_dir)
        if result is None:
            return warn_skip(label, "npm not found")
        if result.returncode != 0:
            return warn_skip(
                label, f"plugin build failed:\n{result.stderr}", failed=True
            )
        _info("Plugin built successfully")
    else:
        _info("Plugin already built")

    # Builds can take minutes; merge into the latest user settings, not the
    # snapshot used to validate the file before npm ran.
    config = load_json(config_path, label)
    plugins = config.get("plugin")
    if not isinstance(plugins, list):
        plugins = []

    plugin_options: dict[str, Any] = {"endpoint": endpoint}
    if token:
        plugin_options["token"] = token

    # The agent loads every configured plugin. Keep one tracker build so stale
    # worktree entries cannot emit duplicate or unauthenticated telemetry.
    plugins = [entry for entry in plugins if not _is_tracker_entry(entry, name)]
    # OpenCode 2 only loads a directory (it rejects a file path); OpenCode 1
    # accepts either. A directory is therefore the form both can share.
    entry_path = dist_dir if register_directory else dist_dir / "index.js"
    plugins.append([str(entry_path), plugin_options])

    config["plugin"] = plugins
    save_json(config_path, config)
    _info(f"tokenage plugin registered in {config_path}")
    return DONE
