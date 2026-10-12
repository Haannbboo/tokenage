"""Agent configuration: the client's job, in both installation modes.

The server never edits agent settings. This module points detected agents at a
collector and takes them back off again, through the ``client.agents`` modules
that own the file formats.
"""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client.agents import DONE, SKIPPED, claude, codex, kilo, opencode
from client.paths import PACKAGE_ROOT, display_endpoint

AGENT_MODULES = {"codex": codex, "claude": claude, "opencode": opencode, "kilo": kilo}

PLUGIN_SUFFIX = {
    "opencode": "plugins/opencode/dist/index.js",
    "kilo": "plugins/kilo/dist/index.js",
}


def agent_targets() -> dict[str, str]:
    """Agent name -> the target its module configures: a settings file, or the
    project root a plugin is built from.

    Resolved on every call so a test (or a changed HOME) is honoured.
    """
    home = Path.home()
    return {
        "codex": str(home / ".codex" / "config.toml"),
        "claude": str(home / ".claude" / "settings.json"),
        "opencode": str(PACKAGE_ROOT),
        "kilo": str(PACKAGE_ROOT),
    }


def plugin_configs() -> dict[str, Path]:
    home = Path.home()
    return {
        "opencode": home / ".config" / "opencode" / "opencode.json",
        "kilo": home / ".config" / "kilo" / "opencode.json",
    }


def agent_path(name: str) -> str | None:
    """Where the agent's command lives, or None when it is not installed."""
    path = shutil.which(name)
    if path is None and name == "kilo":
        # Kilo's installer only adds ~/.kilo/bin to PATH via the shell rc file,
        # which a background service never sources.
        fallback = Path.home() / ".kilo" / "bin" / "kilo"
        path = str(fallback) if fallback.exists() else None
    return path


def installed_agents() -> list[str]:
    return [name for name in AGENT_MODULES if agent_path(name)]


def intended_endpoint() -> str | None:
    """The collector recorded at login, or None. Offline and cheap, because
    `status` asks on every run. `setup` uses `discover_collector`, which can ask.
    """
    from client.auth import stored_collector

    return stored_collector()


def discover_collector() -> str | None:
    from client.auth import discover_collector as _discover

    return _discover()


def ingest_token() -> str | None:
    from client.auth import load_credentials

    token = (load_credentials() or {}).get("ingest_token")
    return token if isinstance(token, str) and token else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {}


def _plugin_endpoint(config: dict[str, Any], suffix: str, default: str) -> str | None:
    """First tracker plugin entry wins, matching the server's setup-health check."""
    entries = config.get("plugin")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, str):
            entry_path = entry
        elif isinstance(entry, list) and entry and isinstance(entry[0], str):
            entry_path = entry[0]
        else:
            continue
        if not entry_path.replace("\\", "/").endswith(suffix):
            continue
        if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], dict):
            return str(entry[1].get("endpoint") or default)
        return default
    return None


def _health(configured: bool, endpoint: str | None, expected: str | None) -> dict:
    # An unknown target is not a wrong one. A machine that signed in before the
    # server published its collector cannot tell whether an agent is right, and
    # must not be told the agent is broken for that.
    matches: bool | None = None
    if not configured:
        status = "missing_config"
        matches = False if expected else None
    elif not expected:
        status = "configured"
    else:
        matches = endpoint == expected
        status = "ready" if matches else "wrong_endpoint"
    return {
        "configured": configured,
        "endpoint_matches": matches,
        "configured_endpoint": endpoint,
        "expected_endpoint": expected,
        "status": status,
    }


def read_agent_states(expected_endpoint: str | None) -> dict[str, dict[str, Any]]:
    """Per-agent wiring state, with the same semantics as ``/local/setup-health``.

    ``tokenage status`` reports this, so the CLI and the dashboard cannot
    disagree about whether an agent is wired.
    """
    claude_env = _read_json(Path(agent_targets()["claude"])).get("env")
    claude_env = claude_env if isinstance(claude_env, dict) else {}
    claude_endpoint = claude_env.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    claude_configured = (
        claude_env.get("CLAUDE_CODE_ENABLE_TELEMETRY") in ("1", "true", "True", True)
        and claude_env.get("OTEL_LOGS_EXPORTER") == "otlp"
        and isinstance(claude_endpoint, str)
    )

    codex_otel = _read_toml(Path(agent_targets()["codex"])).get("otel")
    codex_otel = codex_otel if isinstance(codex_otel, dict) else {}
    codex_exporter = codex_otel.get("exporter")
    codex_exporter = codex_exporter if isinstance(codex_exporter, dict) else {}
    codex_http = codex_exporter.get("otlp-http")
    codex_http = codex_http if isinstance(codex_http, dict) else {}
    codex_endpoint = codex_http.get("endpoint")
    codex_configured = not (
        codex_otel.get("enabled") is False or codex_http.get("enabled") is False
    ) and isinstance(codex_endpoint, str)

    # Match the plugin runtime default for legacy entries with no options.
    fallback = "http://localhost:4005/v1/logs"
    states = {
        "claude": _health(
            claude_configured,
            claude_endpoint if isinstance(claude_endpoint, str) else None,
            expected_endpoint,
        ),
        "codex": _health(
            codex_configured,
            codex_endpoint if isinstance(codex_endpoint, str) else None,
            expected_endpoint,
        ),
    }
    for name, path in plugin_configs().items():
        endpoint = _plugin_endpoint(_read_json(path), PLUGIN_SUFFIX[name], fallback)
        states[name] = _health(endpoint is not None, endpoint, expected_endpoint)
    return states


def wire_agents(
    *, logs_endpoint: str, token: str | None, agents: list[str] | None = None
) -> list[str]:
    """Point detected agents at a collector. Returns the agents that were wired."""
    if not logs_endpoint:
        return []
    wired: list[str] = []
    for name in agents if agents is not None else installed_agents():
        code = AGENT_MODULES[name].configure(
            agent_targets()[name], logs_endpoint, token
        )
        if code == DONE:
            wired.append(name)
        else:
            status = "skipped" if code == SKIPPED else "failed"
            print(f"warning: wiring {name} {status}", file=sys.stderr)
    return wired


@dataclass
class DisableResult:
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def disable_agents(*, expected_endpoint: str | None = None) -> DisableResult:
    """Remove tokenage's telemetry settings from the agents it manages.

    Remove settings only when their collector matches this installation's known
    endpoint. Unknown ownership leaves the configuration untouched.
    """
    outcome = DisableResult()
    agents = installed_agents()
    if not expected_endpoint:
        outcome.skipped.extend(agents)
        if agents:
            print(
                "warning: collector unknown; agent settings left unchanged",
                file=sys.stderr,
            )
        return outcome
    for name in agents:
        code = AGENT_MODULES[name].disable(agent_targets()[name], expected_endpoint)
        if code == DONE:
            outcome.removed.append(name)
        else:
            (outcome.skipped if code == SKIPPED else outcome.failed).append(name)
    return outcome


# Distinct from 1 so the installer can tolerate a signed-out machine.
EXIT_NOT_SIGNED_IN = 3


def run_setup(*, disable: bool) -> int:
    """``tokenage setup`` — wire, or un-wire, the agents found on this machine."""
    agents = installed_agents()
    if not agents:
        print("No tracked agents detected; nothing to wire.")
        return 0
    expected = intended_endpoint()
    if disable:
        outcome = disable_agents(expected_endpoint=expected)
        if outcome.removed:
            print("  un-wired  " + ", ".join(outcome.removed))
        elif not outcome.failed:
            print("No matching telemetry removed; agent settings left unchanged.")
        if outcome.failed:
            print("  un-wiring failed: " + ", ".join(outcome.failed), file=sys.stderr)
        return 1 if outcome.failed or not expected else 0

    # A login made before the client recorded the collector needs one lookup
    # against the server; the answer is then remembered for later commands.
    endpoint = discover_collector() or expected
    if not endpoint:
        print(
            "No server configured. Run tokenage login --server URL.",
            file=sys.stderr,
        )
        return 1
    token = ingest_token()
    if not token:
        print("Not signed in. Run tokenage login.", file=sys.stderr)
        return EXIT_NOT_SIGNED_IN
    print(f"  collector   {display_endpoint(endpoint)}")
    wired = wire_agents(logs_endpoint=endpoint, token=token)
    if not wired:
        print("  no agents could be wired")
        return 1
    print("  wired       " + ", ".join(wired))
    return 0
