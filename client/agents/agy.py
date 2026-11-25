"""Antigravity: a standalone global plugin in ``~/.gemini/config/plugins/tokenage``.

The plugin only registers hook commands. Antigravity's own ``hooks.json`` and
``config.json`` are never written, and no token enters the plugin directory:
the hooks spool locally and the client service delivers with its credentials.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from client.agents import DONE, FAILED, SKIPPED
from client.agents import info as _info
from client.agy_hooks import _prune, _write_atomic, state_dir
from client.hooks import launcher_command
from client.paths import client_version

LABEL = "Antigravity"
PLUGIN_NAME = "tokenage"
FILES = ("plugin.json", "hooks.json")

_FLAT = {
    "SessionStart": "session-start",
    "PreInvocation": "pre-invocation",
    "PostInvocation": "post-invocation",
    "Stop": "stop",
}
_GROUPED = {"PreToolUse": "pre-tool-use", "PostToolUse": "post-tool-use"}


def plugin_document() -> dict[str, str]:
    try:
        version = client_version()
    except OSError:
        version = ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        version = "0.1.0"
    return {"name": PLUGIN_NAME, "version": version, "author": PLUGIN_NAME}


def hooks_document() -> dict[str, Any]:
    def handler(event: str) -> dict[str, Any]:
        return {
            "type": "command",
            "command": f"{launcher_command()} hook agy {event}",
            "timeout": 5,
        }

    hooks: dict[str, Any] = {name: [handler(event)] for name, event in _FLAT.items()}
    for name, event in _GROUPED.items():
        hooks[name] = [{"matcher": "*", "hooks": [handler(event)]}]
    return {PLUGIN_NAME: hooks}


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _disabled(directory: Path) -> bool:
    """Turned off in Antigravity's own config, which is never written here."""
    plugins = _read(directory.parent.parent / "config.json").get("plugins")
    entry = plugins.get(PLUGIN_NAME) if isinstance(plugins, dict) else None
    return isinstance(entry, dict) and entry.get("enabled") is False


def _owned(directory: Path) -> bool:
    return _read(directory / "plugin.json").get("author") == PLUGIN_NAME


def _check(directory: Path) -> None:
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("refusing a plugin directory that is not a directory")
    if any((directory / name).is_symlink() for name in FILES):
        raise ValueError("refusing to replace a symlink")


def state(target: str | Path) -> bool:
    """Plugin installed and not disabled. It holds no endpoint: the hooks spool
    for whichever collector the client is signed in to."""
    directory = Path(target).expanduser()
    try:
        _check(directory)
    except (OSError, ValueError):
        return False
    if _disabled(directory) or not _owned(directory):
        return False
    hooks = _read(directory / "hooks.json").get(PLUGIN_NAME)
    if not isinstance(hooks, dict):
        return False

    def has_handler(handlers: object) -> bool:
        return isinstance(handlers, list) and any(
            isinstance(handler, dict)
            and isinstance(handler.get("command"), str)
            and " hook agy " in handler["command"]
            for handler in handlers
        )

    for event in _FLAT:
        if not has_handler(hooks.get(event)):
            return False
    for event in _GROUPED:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            isinstance(group, dict) and has_handler(group.get("hooks"))
            for group in groups
        ):
            return False
    return True


def configure(target: str | Path, logs_endpoint: str, token: str | None = None) -> int:
    directory = Path(target).expanduser()
    manifest_written = False
    try:
        _check(directory)
        if any((directory / name).exists() for name in FILES) and not _owned(directory):
            raise ValueError("refusing to replace a plugin tokenage did not install")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        _write_atomic(
            directory / "plugin.json", json.dumps(plugin_document(), indent=2) + "\n"
        )
        manifest_written = True
        _write_atomic(
            directory / "hooks.json", json.dumps(hooks_document(), indent=2) + "\n"
        )
    except (OSError, ValueError):
        detail = (
            "plugin.json written, hooks.json update failed"
            if manifest_written
            else "left unchanged"
        )
        print(f"cannot update Antigravity configuration; {detail}", file=sys.stderr)
        return FAILED
    if _disabled(directory):
        print(
            "the tokenage plugin is disabled in Antigravity, so nothing is tracked; "
            "run `agy plugin enable tokenage`",
            file=sys.stderr,
        )
        return SKIPPED
    _info(f"Antigravity plugin installed in {directory}")
    return DONE


def disable(target: str | Path, expected_endpoint: str | None) -> int:
    """Remove the plugin whatever the collector: the directory is tokenage's own
    name, and only the two files tokenage wrote are deleted."""
    directory = Path(target).expanduser()
    try:
        if not directory.exists() and not directory.is_symlink():
            _info(f"No Antigravity plugin at {directory}")
            return SKIPPED
        _check(directory)
        if not _owned(directory):
            _info(f"Plugin in {directory} was not installed by tokenage; left alone")
            return SKIPPED
        for name in FILES:
            (directory / name).unlink(missing_ok=True)
        if not any(directory.iterdir()):
            directory.rmdir()
        states = state_dir()
        if states.is_dir() and not states.is_symlink():
            for path in states.glob("*.json"):
                path.unlink(missing_ok=True)
            _prune(states)  # Its locks, unless a hook holds one right now.
            if not any(states.iterdir()):
                states.rmdir()
    except (OSError, ValueError):
        print(
            "cannot update Antigravity configuration; left unchanged", file=sys.stderr
        )
        return FAILED
    _info(f"Antigravity plugin removed from {directory}")
    return DONE
