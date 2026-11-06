"""Silent, local SessionStart helper. Only a project name enters the spool."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from protocol.events import Agent


def hook_command(agent: str) -> str:
    """Use the installed launcher, which survives replacement of snapshots."""
    launcher = (
        Path(os.environ.get("TOKENAGE_BIN_DIR", "~/.local/bin")).expanduser()
        / "tokenage"
    )
    return f"{shlex.quote(str(launcher))} hook session-start --agent {agent}"


def _owned_hook(command: object, agent: str) -> bool:
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return (
        len(parts) == 5
        and Path(parts[0]).name == "tokenage"
        and parts[1:] == ["hook", "session-start", "--agent", agent]
    )


def update_session_hooks(settings: dict, agent: str, *, remove: bool = False) -> bool:
    """Merge/remove owned launcher commands, preserving other hooks."""
    from copy import deepcopy

    hooks = deepcopy(settings.get("hooks", {}))
    if not isinstance(hooks, dict):
        raise ValueError("unsupported hooks layout")
    groups = hooks.get("SessionStart", [])
    if not isinstance(groups, list) or any(
        not isinstance(group, dict) or not isinstance(group.get("hooks", []), list)
        for group in groups
    ):
        raise ValueError("unsupported SessionStart hooks layout")
    command = hook_command(agent)
    found = False
    updated = []
    for group in groups:
        handlers = []
        for handler in group.get("hooks", []):
            if isinstance(handler, dict) and _owned_hook(handler.get("command"), agent):
                if remove or found:
                    continue
                found = True
                handler["command"] = command
                handler["timeout"] = 10
            handlers.append(handler)
        if handlers or not group.get("hooks"):
            updated.append({**group, "hooks": handlers} if "hooks" in group else group)
    if not remove and not found:
        updated.append(
            {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        )
    if updated:
        hooks["SessionStart"] = updated
    else:
        hooks.pop("SessionStart", None)
    if hooks == settings.get("hooks", {}):
        return False
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return True


def project_name(cwd: str) -> str:
    from protocol.events import sanitize_project

    folder = Path(cwd)
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("invalid working directory")
    name = folder.name
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # --path-format=absolute needs Git 2.31; older Git falls back to the folder name.
    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--is-bare-repository",
                "--path-format=absolute",
                "--git-common-dir",
                "--show-toplevel",
            ],
            cwd=folder,
            env=env,
            capture_output=True,
            text=True,
            timeout=0.65,
        )
        lines = result.stdout.splitlines()
        if result.returncode == 0 and len(lines) == 3 and lines[0] == "false":
            common, top = Path(lines[1]), Path(lines[2])
            if common.is_absolute() and top.is_absolute():
                name = common.parent.name if common.name == ".git" else top.name
    except (OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return sanitize_project(name[:100])


def session_start(agent: Agent, payload: dict) -> None:
    from client.event_spool import write_report
    from protocol.events import SpoolReport

    session_id, cwd = payload.get("session_id"), payload.get("cwd")
    if not isinstance(session_id, str) or not isinstance(cwd, str):
        return
    report = SpoolReport(
        schema_version=1,
        agent=agent,
        session_id=session_id,
        project=project_name(cwd),
        ts=time.time_ns() // 1000,
    )
    write_report(report)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="tokenage hook",
        description="Locally spool a session's project name; no network or stdout.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("session-start", help="read SessionStart JSON from stdin")
    start.add_argument("--agent", choices=("claude", "codex"), required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0

    def expired(*_):
        raise TimeoutError()

    old = None
    try:
        if hasattr(signal, "SIGALRM"):
            try:
                old = signal.signal(signal.SIGALRM, expired)
                signal.setitimer(signal.ITIMER_REAL, 0.95)
            except ValueError:  # Signals are unavailable outside the main thread.
                pass
        raw = sys.stdin.read(65537)
        if len(raw) <= 65536:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                session_start(args.agent, payload)
    except Exception:
        pass
    finally:
        if old is not None:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old)
            except Exception:
                pass
    return 0
