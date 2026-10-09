"""The per-device client service.

A user-level background process that owns device-local work. It is a purely
outbound component: it never accepts connections, so it runs the same whether
the server is on this machine or in the cloud. It records this machine's
client/agent status on an interval and reports it to whichever server applies:
the one this machine signed in to, or the local server component.

The OS supervises it: a systemd user unit on Linux, a launchd agent on macOS.
``tokenage client start|stop|restart|status`` drive that manager, and the unit
restarts the service after a crash and at user login. ``run`` is the foreground
loop the manager executes; run it under your own supervisor where neither
exists. ``login`` and the installer start it, and it runs on every device, with
or without a server component on the same machine.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from client.auth import save_private_object
from client.paths import client_commit, client_version, display_endpoint, tracker_home
from client.setup import AGENT_MODULES, agent_path, intended_endpoint, read_agent_states
from protocol.device_status import DeviceStatusReport

CHECK_INTERVAL_SECONDS = 60.0
REPORT_TIMEOUT_SECONDS = 10.0

UNIT_NAME = "tokenage-client.service"
LAUNCHD_LABEL = "ai.tokenage.client"

logger = logging.getLogger("tokenage.client.service")


def state_path() -> Path:
    """The daemon's last-check record, shown by ``status``.

    Structure: ``{"last_check_at": int | null, "last_report_at": int | null,
    "last_report_status": str | null}`` (unix seconds). The daemon rewrites it
    atomically on every check. Liveness comes from the OS service manager, never
    from this file.
    """
    return tracker_home() / "run" / "client.json"


def log_path() -> Path:
    return tracker_home() / "logs" / "client.log"


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    # The proxy records its own "proxy" entry in this file; don't clobber it.
    if "proxy" not in state and isinstance(_read_state().get("proxy"), dict):
        state = {**state, "proxy": _read_state()["proxy"]}
    save_private_object(state_path(), state)


# ------------------------------------------------------------ OS service manager


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _manager() -> str | None:
    """ "systemd", "launchd", or None when this machine has no user service manager.

    systemd counts only when ``systemctl --user`` really works: WSL1, containers
    and SSH sessions without a user bus have the binary but not a manager.
    """
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        try:
            if _run(["systemctl", "--user", "show-environment"]).returncode == 0:
                return "systemd"
        except OSError:
            pass
    return None


def _launcher() -> Path | None:
    found = shutil.which("tokenage")
    if found:
        return Path(found)
    bin_dir = os.environ.get("TOKENAGE_BIN_DIR", "~/.local/bin")
    candidate = Path(bin_dir).expanduser() / "tokenage"
    return candidate if candidate.is_file() else None


def _service_env() -> dict[str, str]:
    env = {"TOKENAGE_HOME": str(tracker_home()), "TOKENAGE_SKIP_BANNER": "1"}
    # systemd/launchd start services with a bare PATH, so agents installed in
    # ~/.local/bin and the like would read as missing; keep the user's PATH
    # from when the service was installed (`client start` rewrites it).
    for key in ("TOKENAGE_ROOT", "PATH"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _unit_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + escaped.replace("$", "$$") + '"'


def render_unit(launcher: Path) -> str:
    env = "".join(
        f"Environment={_unit_quote(f'{k}={v}')}\n" for k, v in _service_env().items()
    )
    return (
        "[Unit]\nDescription=tokenage client service\n\n"
        f"[Service]\nType=simple\nExecStart={_unit_quote(str(launcher))} client run\n"
        f"{env}Restart=on-failure\nRestartSec=10\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def render_plist(launcher: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [str(launcher), "client", "run"],
            "EnvironmentVariables": _service_env(),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "StandardOutPath": str(log_path()),
            "StandardErrorPath": str(log_path()),
        }
    )


def _write_if_changed(path: Path, content: bytes) -> bool:
    try:
        if path.read_bytes() == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def _fail(message: str) -> int:
    print(f"tokenage: {message}", file=sys.stderr)
    return 1


def _unsupported() -> int:
    return _fail(
        "no user service manager here (needs systemd --user or launchd). "
        "Run `tokenage client run` under your own supervisor "
        "(tmux, nohup, a container entrypoint...)."
    )


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_loaded() -> bool:
    return (
        _run(["launchctl", "print", f"{_gui_domain()}/{LAUNCHD_LABEL}"]).returncode == 0
    )


def _launchd_bootout() -> None:
    _run(["launchctl", "bootout", f"{_gui_domain()}/{LAUNCHD_LABEL}"])


def start(*, restart: bool = False) -> int:
    """Install the unit/agent (rewritten when its contents change), enable it at
    login and start it. ``restart`` also bounces an already running service."""
    manager = _manager()
    if manager is None:
        return _unsupported()
    launcher = _launcher()
    if launcher is None:
        return _fail(
            "could not find the tokenage launcher on PATH or in "
            "$TOKENAGE_BIN_DIR (~/.local/bin); install it first"
        )
    log_path().parent.mkdir(parents=True, exist_ok=True)
    if manager == "systemd":
        changed = _write_if_changed(unit_path(), render_unit(launcher).encode())
        steps = [["systemctl", "--user", "daemon-reload"]] if changed else []
        steps.append(["systemctl", "--user", "enable", "--now", UNIT_NAME])
        if changed or restart:
            steps.append(["systemctl", "--user", "restart", UNIT_NAME])
    else:
        changed = _write_if_changed(plist_path(), render_plist(launcher))
        if changed or restart:
            _launchd_bootout()
        steps = [["launchctl", "enable", f"{_gui_domain()}/{LAUNCHD_LABEL}"]]
        if not _launchd_loaded():
            steps.append(["launchctl", "bootstrap", _gui_domain(), str(plist_path())])
    for cmd in steps:
        result = _run(cmd)
        if result.returncode != 0:
            return _fail(
                f"could not start the client service ({' '.join(cmd)}): "
                f"{(result.stderr or result.stdout).strip()}"
            )
    print(f"  started   client service ({manager})")
    print(f"  log       {log_path()}")
    return 0


def stop() -> int:
    """Stop the service and disable its start at login; ``start`` re-enables."""
    manager = _manager()
    if manager is None:
        return _unsupported()
    if manager == "systemd":
        result = _run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        # A unit that was never installed is already stopped.
        if result.returncode != 0 and unit_path().exists():
            return _fail(f"could not stop the client service: {result.stderr.strip()}")
    else:
        _launchd_bootout()
        _run(["launchctl", "disable", f"{_gui_domain()}/{LAUNCHD_LABEL}"])
    print("  stopped   client service")
    return 0


def restart() -> int:
    return start(restart=True)


def _manager_active(manager: str | None) -> bool:
    if manager == "systemd":
        return (
            _run(["systemctl", "--user", "is-active", "--quiet", UNIT_NAME]).returncode
            == 0
        )
    if manager == "launchd":
        return _launchd_loaded()
    return False


def service_status() -> dict[str, Any]:
    manager = _manager()
    state = _read_state()
    return {
        "running": _manager_active(manager),
        "manager": manager,
        "log": str(log_path()),
        "client_version": client_version(),
        "client_commit": client_commit(),
        "last_check_at": state.get("last_check_at"),
        "last_report_at": state.get("last_report_at"),
        "last_report_status": state.get("last_report_status"),
    }


def health_payload() -> dict[str, Any]:
    """The device's status: build, detected agents and collector wiring.

    The same payload is printed locally and sent to the server, wherever that
    runs. Endpoints are redacted (no credentials or private paths), so nothing
    about this machine's layout leaves it.
    """
    states = read_agent_states(intended_endpoint())
    report = DeviceStatusReport.model_validate(
        {
            "device_name": (socket.gethostname() or "device")[:64],
            "client_version": client_version(),
            "client_commit": client_commit(),
            "collected_at": int(time.time()),
            "agents": {
                name: {
                    **states[name],
                    "configured_endpoint": display_endpoint(
                        states[name].get("configured_endpoint")
                    ),
                    "expected_endpoint": display_endpoint(
                        states[name].get("expected_endpoint")
                    ),
                }
                for name in AGENT_MODULES
            },
            "detected": {
                name: {"found": agent_path(name) is not None} for name in AGENT_MODULES
            },
        }
    )
    return report.model_dump(mode="json")


def _report_target() -> tuple[str, dict[str, str]] | None:
    """(server URL, headers) for the report, or None when not signed in.

    A client that has not signed in has no identity to report under.
    """
    from client.auth import load_credentials

    try:
        credentials = load_credentials() or {}
    except ValueError:
        credentials = {}
    server_url = credentials.get("server_url")
    cli_token = credentials.get("cli_token")
    if server_url and cli_token:
        return server_url, {"Authorization": f"Bearer {cli_token}"}
    return None


def _report_once(state: dict[str, Any], payload: dict[str, Any]) -> None:
    """Send this device's status to its server, if it has one. Never raises."""
    try:
        target = _report_target()
        if target is None:
            return
        server_url, headers = target
        response = httpx.post(
            f"{server_url}/devices/status",
            json=payload,
            headers=headers,
            timeout=REPORT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        state["last_report_status"] = "failed"
        logger.exception("device report failed")
        return
    state["last_report_at"] = int(time.time())
    state["last_report_status"] = "ok"


def _check_once(state: dict[str, Any]) -> None:
    payload = health_payload()
    state["last_check_at"] = payload["collected_at"]
    _report_once(state, payload)
    _write_state(state)
    agents = payload["agents"].values()
    logger.info(
        "device check: %s/%s agents configured, %s matching",
        sum(1 for agent in agents if agent["configured"]),
        len(agents),
        sum(1 for agent in agents if agent["endpoint_matches"] is True),
    )


def run_foreground() -> int:
    log_path().parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path()),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Signals only flip the flag; the loop below owns the shutdown.
    stop = False

    def _request_stop(signum, frame):  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    state: dict[str, Any] = {
        "last_check_at": None,
        "last_report_at": None,
        "last_report_status": None,
    }
    print(f"tokenage client service running (pid {os.getpid()})")
    logger.info("client service started (pid %s)", os.getpid())
    try:
        while not stop:
            try:
                _check_once(state)
            except Exception:
                # A broken agent config must fail the check, never the daemon.
                logger.exception("device check failed")
            # Sleep in slices so a stop signal is honored within half a second.
            deadline = time.monotonic() + CHECK_INTERVAL_SECONDS
            while not stop and time.monotonic() < deadline:
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    finally:
        logger.info("client service stopped (pid %s)", os.getpid())
    return 0


def run_status(*, as_json: bool) -> int:
    data = service_status()
    if as_json:
        print(json.dumps(data, separators=(",", ":"), sort_keys=True))
    elif data["running"]:
        print(f"tokenage client service running ({data['manager']})")
        if data.get("last_check_at"):
            stamp = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(data["last_check_at"])
            )
            print(f"  last check  {stamp}")
        if data.get("last_report_at"):
            stamp = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(data["last_report_at"])
            )
            print(f"  last report {stamp} ({data.get('last_report_status')})")
        elif data.get("last_report_status"):
            print(f"  last report {data['last_report_status']}")
        print(f"  log         {data['log']}")
    elif data["manager"] is None:
        print("tokenage client service is not managed here; run `tokenage client run`")
    else:
        print("tokenage client service is not running")
    return 0 if data["running"] else 1
