"""Server operations behind `tokenage server <command>`.

start/stop/restart/status/update and the post-venv half of bootstrap.
`src/scripts/bootstrap.sh` is the only shell left: it has to run before the
venv exists. Everything external (supervisord, git, npm) goes through `_run`.
Heavy imports (src.config.app, migrations) are lazy so `start` can create the
config before anything reads it.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from src.config.merge import sync_config_file_with_defaults
from src.config.models import get_tracker_home
from src.config.runtime_ports import (
    PortListener,
    detect_port_issues,
    format_bind,
    format_port_issue,
    get_blocking_port_issues,
    get_configured_service_ports,
    parse_supervisor_status,
)
from src.config.server_config import resolve_api_bind, resolve_server_urls

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("api",)  # the OTLP collector is mounted in the API at /otlp
PROGRAMS = tuple(f"tokenage-{s}" for s in SERVICES)
PORT_KEYS = ("api_port",)  # Only API ports may be auto-assigned; metrics is explicit.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── Output ──────────────────────────────────────────────────────────
def _say(text: str) -> None:
    print(text, flush=True)


def step(title: str) -> None:
    _say(f"\n==> {title}")


def ok(msg: str) -> None:
    _say(f"  ✓ {msg}")


def bad(msg: str) -> None:
    _say(f"  ✗ {msg}")


def note(msg: str) -> None:
    _say(f"  {msg}")


def die(msg: str, *details: str) -> SystemExit:
    """Print a failure and return the SystemExit to raise: `raise die(...)`."""
    bad(msg)
    for line in details:
        note(line)
    return SystemExit(1)


# ── Paths and helpers ───────────────────────────────────────────────
def _home() -> Path:
    return Path(get_tracker_home())


def _config_path() -> Path:
    override = os.environ.get("TOKENAGE_CONFIG")
    return Path(override).expanduser() if override else _home() / "config.yaml"


def _conf() -> Path:
    return _home() / "supervisord.conf"


def _venv_bin(name: str) -> str:
    return str(ROOT / ".venv" / "bin" / name)


def _bin_dir() -> Path:
    return Path(os.environ.get("TOKENAGE_BIN_DIR") or Path.home() / ".local" / "bin")


def _run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    kw.setdefault("check", False)
    kw.setdefault("text", True)
    return subprocess.run(cmd, **kw)


def _ctl(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(
        [_venv_bin("supervisorctl"), "-c", str(_conf()), *args], capture_output=capture
    )


def _load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    host, port = resolve_api_bind(config)
    config.setdefault("server", {}).update(host=host, api_port=port)
    return config


def _set_server_keys(path: Path, values: dict[str, Any]) -> None:
    """Update `server.*` keys in place, keeping the file's comments and layout."""
    from ruamel.yaml import YAML

    rt = YAML()
    rt.preserve_quotes = True
    config = rt.load(path.read_text(encoding="utf-8")) or {}
    config.setdefault("server", {}).update(values)
    with path.open("w", encoding="utf-8") as f:
        rt.dump(config, f)


def _no_args(command: str, args: list[str]) -> None:
    if args:
        raise die(f"{command} takes no arguments: {' '.join(args)}")


def _stamp_file() -> Path:
    return ROOT / ".venv" / ".requirements.sha256"


def _requirements_hash() -> str:
    return hashlib.sha256((ROOT / "src" / "pyproject.toml").read_bytes()).hexdigest()


def _status_of(program: str) -> str:
    out = _ctl("status", program, capture=True).stdout.split()
    return out[1] if len(out) > 1 else ""


# ── Ports ───────────────────────────────────────────────────────────
def _listeners(port: int, host: str = "127.0.0.1") -> list[PortListener]:
    if shutil.which("lsof"):
        r = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True)
        if r.returncode in (0, 1):
            return [
                PortListener(pid=int(p[1]), command=p[0])
                for p in (line.split() for line in r.stdout.splitlines()[1:])
                if len(p) > 1
            ]
    # No lsof: probe by binding.
    return [] if _bindable(host, port) else [PortListener(-1, "(unknown)")]


def _bindable(host: str, port: int) -> bool:
    try:
        addresses = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
        for family, kind, proto, _, address in addresses:
            with socket.socket(family, kind, proto) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(address)
    except OSError:
        return False
    return True


def _port_issues(config: dict[str, Any], strict: bool) -> list[str]:
    ports = get_configured_service_ports(config)
    states = {}
    if _conf().exists():
        states = parse_supervisor_status(_ctl("status", capture=True).stdout)
    issues = detect_port_issues(
        service_ports=ports,
        supervisor_states=states,
        listeners_by_port={p.port: _listeners(p.port, p.host) for p in ports},
    )
    if strict:
        issues = get_blocking_port_issues(issues)
    return [format_port_issue(i) for i in issues]


def _assign_free_ports(path: Path, config: dict[str, Any]) -> None:
    server = config.setdefault("server", {})
    host = str(server.get("host", "127.0.0.1"))
    metrics_port = (
        config.get("metrics", {}).get("port")
        if config.get("metrics", {}).get("enabled")
        else None
    )
    free = [p for p in range(4001, 4200) if p != metrics_port and _bindable(host, p)][
        : len(PORT_KEYS)
    ]
    if len(free) < len(PORT_KEYS):
        raise die(f"Could not find {len(PORT_KEYS)} free ports on {host} (4001-4199)")
    _set_server_keys(path, dict(zip(PORT_KEYS, free, strict=True)))


# ── Migrations and config ───────────────────────────────────────────
def migrate(args: list[str] | None = None) -> int:
    """Apply schema migrations to the configured DB (TOKENAGE_DB_URL overrides)."""
    _no_args("migrate", args or [])
    from src.schema_migrations import migrate_database

    changes = migrate_database()
    note(
        f"Applied schema migrations: {', '.join(changes)}"
        if changes
        else "Schema is up to date."
    )
    return 0


def sync_config(args: list[str] | None = None) -> int:
    """Merge missing defaults from config.example.yaml into the user config."""
    _no_args("sync-config", args or [])
    path = _config_path()
    changed = sync_config_file_with_defaults(
        str(path), str(ROOT / "config.example.yaml")
    )
    note(f"{'Updated' if changed else 'Config already includes'} defaults: {path}")
    return 0


# ── Supervisord ─────────────────────────────────────────────────────
def _render_conf() -> str:
    run = _home() / "run"
    logs = ROOT / "logs"
    py = _venv_bin("python")
    text = f"""[unix_http_server]
file={run / "supervisor.sock"}

[supervisord]
logfile={logs}/supervisord.log
pidfile={run / "supervisord.pid"}
childlogdir={logs}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://{run / "supervisor.sock"}
"""
    for s in SERVICES:
        text += f"""
[program:tokenage-{s}]
command={py} -m gunicorn -c {ROOT}/src/config/{s}.conf.py src.{s}:app
environment=OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
directory={ROOT}
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
stdout_logfile={logs}/{s}.stdout.log
stderr_logfile={logs}/{s}.stderr.log
"""
    return text


def _supervisord_pid() -> int | None:
    try:
        pid = int((_home() / "run" / "supervisord.pid").read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


# ── start ───────────────────────────────────────────────────────────
def start(args: list[str] | None = None) -> int:
    _no_args("start", args or [])
    if (
        not _stamp_file().exists()
        or _stamp_file().read_text().strip() != _requirements_hash()
    ):
        bad("Dependencies are out of date (src/pyproject.toml changed)")
        note("run tokenage server bootstrap")
        return 1
    ok("Dependencies up to date")

    (ROOT / "logs").mkdir(exist_ok=True)
    run = _home() / "run"
    run.mkdir(parents=True, exist_ok=True)

    path = _config_path()
    created = not os.path.lexists(path)
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "config.example.yaml", path)
        ok(f"Config created: {path}")
    else:
        ok(f"Config exists: {path}")
    sync_config()

    config = _load_config(path)
    issues = _port_issues(config, strict=True)
    if issues and created:
        # A fresh config that collides with something already running: move it.
        _assign_free_ports(path, config)
        config = _load_config(path)
        issues = _port_issues(config, strict=True)
        if not issues:
            ok("Ports auto-assigned")
    if issues:
        bad("Port check failed")
        for line in issues:
            _say(line)
        note("Change the configured service ports before starting tokenage.")
        return 1
    ok("Port check passed")

    # Agent telemetry is the client's job (`tokenage setup`); the server never
    # edits user agent settings.
    note("Applying schema migrations...")
    migrate()
    ok("Migrations applied")

    _conf().write_text(_render_conf(), encoding="utf-8")
    pid = _supervisord_pid()
    if pid:
        note(f"Reloading supervisord (pid {pid})...")
        _ctl("reread")
        _ctl("update")
        time.sleep(1)
        ok("Supervisord reloaded")
    else:
        sock = run / "supervisor.sock"
        sock.unlink(missing_ok=True)
        (run / "supervisord.pid").unlink(missing_ok=True)
        note("Starting supervisord...")
        _run([_venv_bin("supervisord"), "-c", str(_conf())])
        for _ in range(10):
            if sock.exists():
                break
            time.sleep(0.3)
        ok("Supervisord started")

    for prog in PROGRAMS:
        state = _status_of(prog)
        if state in ("RUNNING", "STARTING"):
            ok(f"{prog}: {state.lower()}")
        else:
            note(f"Starting {prog}...")
            if _ctl("start", prog).returncode:
                bad(f"{prog}: failed to start (see logs/{prog[9:]}.stderr.log)")
                return 1
            ok(f"{prog}: started")
    return 0


# ── stop ────────────────────────────────────────────────────────────
def stop(args: list[str] | None = None) -> int:
    names = _programs(args or [])
    if not _conf().exists():
        note("Not running.")
        return 0
    if names:
        for prog in names:
            note(f"Stopping {prog}...")
            _ctl("stop", prog)
            ok(f"{prog}: stopped")
    else:
        note("Stopping all programs and shutting down supervisord...")
        _ctl("stop", "all")
        _ctl("shutdown")
        ok("All services stopped")
    return 0


def _programs(names: list[str]) -> list[str]:
    for name in names:
        if name not in PROGRAMS:
            raise die(f"Unknown program: {name}", f"Available: {', '.join(PROGRAMS)}")
    return names


# ── restart ─────────────────────────────────────────────────────────
def restart(args: list[str] | None = None) -> int:
    """Migrate, then reload running programs (SIGHUP)."""
    names = _programs(list(args or []))
    if not _conf().exists():
        raise die("Not running — run tokenage server start")

    if (
        "tokenage-api" in (names or PROGRAMS)
        and _status_of("tokenage-api") == "RUNNING"
    ):
        desired = [
            format_bind(p.host, p.port)
            for p in get_configured_service_ports(_load_config(_config_path()))
        ]
        try:
            running = json.loads((_home() / "run" / "api-binds.json").read_text())
            state = parse_supervisor_status(
                _ctl("status", "tokenage-api", capture=True).stdout
            )
            matches = (
                running["bind"] == desired
                and running["pid"] == state["tokenage-api"].pid
            )
        except (OSError, ValueError, KeyError, TypeError):
            matches = False
        if not matches:
            raise die(
                "API bind changed or running bind is unknown; refusing SIGHUP.",
                "Run tokenage server stop tokenage-api then tokenage server start (brief outage).",
            )

    step("Applying schema migrations")
    migrate()
    ok("Migrations applied")

    step("Reloading services")
    down = []
    for prog in names or PROGRAMS:
        if _status_of(prog) != "RUNNING":
            down.append(prog)
        else:
            _ctl("signal", "HUP", prog)
            ok(f"{prog}: reloaded")
    for prog in down:
        note(f"{prog}: not running, left stopped")
    if down:
        note("run tokenage server start to bring them up")
    return 0


# ── status ──────────────────────────────────────────────────────────
def status(args: list[str] | None = None) -> int:
    names = _programs(args or [])
    if not _conf().exists():
        raise die("Services not configured (missing supervisord.conf)")
    step("Service status")
    _ctl("status", *names)
    path = _config_path()
    if path.exists():
        config = _load_config(path)
        step("Ports")
        for p in get_configured_service_ports(config):
            note(f"{p.service}: {format_bind(p.host, p.port)}")
        issues = _port_issues(config, strict=False)
        step("Port check")
        for line in issues or ["no issues"]:
            _say(line if issues else f"  ✓ {line}")
    from .shared_state import StoreUnavailable, get_shared_state

    step("Ingest queue")
    now = time.time()
    try:
        partitions = get_shared_state().status()
    except (ValueError, OSError, StoreUnavailable) as exc:
        note(f"Queue unavailable ({type(exc).__name__})")
        return 1
    for partition in partitions:
        lag = (
            max(0, now - partition.oldest_pending)
            if partition.oldest_pending is not None
            else 0
        )
        note(
            f"partition {partition.partition:02d}: depth={partition.depth} lag={lag:.1f}s owner={partition.owner or '-'}"
        )
    return 0


# ── bootstrap (after bootstrap.sh installed the venv) ───────────────
def _build_frontend() -> None:
    frontend = ROOT / "frontend"
    node, npm = shutil.which("node"), shutil.which("npm")
    if not (node and npm):
        note(
            "Node.js not found: skipping the frontend build; no dashboard until you run"
        )
        note("  cd frontend && npm install && npm run build")
        return
    version = _run([node, "-v"], capture_output=True).stdout.strip().lstrip("v")
    if int(version.split(".")[0] or 0) < 18:
        note(
            f"Node.js {version} is too old (v18 required): skipping the frontend build."
        )
        return
    if not frontend.is_dir():
        return
    note(f"Building frontend (Node {version})...")
    for cmd in ([npm, "install", "--ignore-scripts"], [npm, "run", "build"]):
        if _run(cmd, cwd=frontend).returncode:
            raise die(
                "Frontend build failed.",
                "If you see 'Cannot find native binding', clean and retry:",
                "  rm -rf frontend/node_modules frontend/package-lock.json && "
                "bash src/scripts/bootstrap.sh",
            )
    ok("Frontend built")


def _wait_for_port(host: str, port: int, retries: int = 10) -> bool:
    for _ in range(retries):
        try:
            socket.create_connection((host, port), timeout=3).close()
            return True
        except OSError:
            time.sleep(1)
    return False


def _content_type(url: str) -> str:
    try:
        with _LOCAL_OPENER.open(url, timeout=3) as response:
            return response.headers.get("content-type", "")
    except (OSError, http.client.HTTPException):
        return ""


def _wait_for_html(url: str, retries: int = 10) -> bool:
    for _ in range(retries):
        if _content_type(url).startswith("text/html"):
            return True
        time.sleep(1)
    return False


def bootstrap(args: list[str] | None = None) -> int:
    """Everything after deps are installed: stamp, build, start, verify."""
    _no_args("bootstrap", args or [])
    # bootstrap.sh just installed src/pyproject.toml, so this is the point to
    # record it; `start` refuses to run on a stale stamp.
    _stamp_file().write_text(_requirements_hash() + "\n")
    step("Building the dashboard")
    _build_frontend()
    bin_dir = _bin_dir()
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        note(
            f"{bin_dir} is not in your PATH; add it to your shell profile to use 'tokenage'."
        )

    step("Starting services")
    if start():
        return 1

    step("Running post-start checks")
    path = _config_path()
    config = _load_config(path) if path.exists() else {}
    try:
        urls = resolve_server_urls(config)
    except ValueError as exc:
        raise die(str(exc)) from exc
    services = get_configured_service_ports(config)
    host = services[0].host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port_of = {p.service: p.port for p in services}
    local_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    local = f"http://{local_host}:{port_of['API']}"
    failures = 0

    def check(passed: bool, good: str, broken: str) -> None:
        nonlocal failures
        if passed:
            ok(good)
        else:
            bad(broken)
            failures += 1

    cli = ROOT / "client" / "bin" / "tokenage"
    launcher = _bin_dir() / "tokenage"
    check(path.exists(), f"Config: {path}", f"Config: {path} (not found)")
    check(
        os.access(cli, os.X_OK),
        "CLI wrapper: client/bin/tokenage",
        "CLI wrapper: client/bin/tokenage (not executable)",
    )
    check(
        launcher.exists(), f"Launcher: {launcher}", f"Launcher: {launcher} (not found)"
    )
    check(
        _wait_for_port(host, port_of["API"]),
        f"API running: {local}",
        f"API reachable: {local} (not responding)",
    )
    for service in services[1:]:
        probe_host = service.host
        if probe_host in ("0.0.0.0", "::"):
            probe_host = "::1" if probe_host == "::" else "127.0.0.1"
        address = format_bind(service.host, service.port)
        check(
            _wait_for_port(probe_host, service.port),
            f"{service.service} running: {address}",
            f"{service.service}: {address} (not responding)",
        )

    # src/api.py mounts frontend/dist at import time, so a build is only served
    # after the API restarts. The only command that builds is the one that restarts.
    if (ROOT / "frontend" / "dist").is_dir():
        note("Restarting the API to serve the new dashboard...")
        _ctl("restart", "tokenage-api")
        html = _wait_for_html(f"{local}/")
        check(
            html,
            f"Dashboard: {local}",
            f"Dashboard: {local} (frontend not served)",
        )
    else:
        note(
            "Dashboard unavailable: install Node.js and npm, then rerun "
            "tokenage server bootstrap."
        )

    if failures:
        _say(f"\n  ⚠ tokenage started with {failures} issue(s) → {urls['api_url']}")
        return 1
    _say(f"\n  tokenage is LIVE → {urls['api_url']}")
    return 0


# ── update ──────────────────────────────────────────────────────────
def update(args: list[str] | None = None) -> int:
    """Fast-forward the clone, re-run bootstrap.sh (deps may change), restart."""
    args = list(args or [])
    if any(a not in ("--check", "--dry-run") for a in args):
        raise die("usage: tokenage server update [--check] [--dry-run]")

    def git(*a: str) -> subprocess.CompletedProcess[str]:
        return _run(["git", "-C", str(ROOT), *a], capture_output=True)

    refuse = "tokenage server update refused"
    step("Preflight checks")
    if git("rev-parse", "--is-inside-work-tree").returncode:
        raise die(f"Not a git repository: {ROOT}")
    if git("status", "--porcelain").stdout.strip():
        raise die(
            f"{refuse}: local changes detected.",
            "Commit, stash, or discard your changes, then retry.",
        )
    if git("symbolic-ref", "-q", "HEAD").returncode:
        raise die(f"{refuse}: detached HEAD.", "Check out a branch first, then retry.")
    branch = git("symbolic-ref", "--short", "HEAD").stdout.strip()
    up = git("rev-parse", "--abbrev-ref", "@{upstream}")
    if up.returncode:
        raise die(
            f"{refuse}: branch '{branch}' has no upstream.",
            "Set an upstream or run git pull manually.",
        )
    upstream = up.stdout.strip()
    remote = upstream.split("/")[0]
    if git("remote", "get-url", remote).returncode:
        raise die(f"{refuse}: remote '{remote}' not found.")
    ok(f"Branch: {branch} → {upstream}")

    step("Fetching updates")
    if git("fetch", remote).returncode:
        raise die(f"git fetch {remote} failed")
    ok(f"Fetched from {remote}")
    local = git("rev-parse", "HEAD").stdout.strip()
    remote_sha = git("rev-parse", "@{upstream}").stdout.strip()
    ahead = git("rev-list", "--count", f"{remote_sha}..{local}").stdout.strip()
    behind = git("rev-list", "--count", f"{local}..{remote_sha}").stdout.strip()

    if args:
        note(f"Branch: {branch}  Upstream: {upstream}")
        note(f"Local: {local}  Remote: {remote_sha}  Ahead: {ahead}  Behind: {behind}")
        if "--dry-run" in args:
            note("Planned commands:")
            for cmd in (
                f"git -C {ROOT} fetch {remote}",
                f"git -C {ROOT} pull --ff-only",
                f"bash {ROOT}/src/scripts/bootstrap.sh",
                f"{_venv_bin('python')} -m src.cli restart",
            ):
                note(f"  {cmd}")
        return 0
    if ahead == behind == "0":
        ok(f"Already up to date ({local})")
        return 0

    step("Pulling updates")
    if git("pull", "--ff-only").returncode:
        raise die(
            f"{refuse}: upstream cannot be fast-forwarded.",
            "Resolve with git manually, then rerun src/scripts/bootstrap.sh.",
        )
    head = git("rev-parse", "HEAD").stdout.strip()
    ok(f"Updated {local} → {head}")

    step("Running bootstrap")
    if _run(["bash", str(ROOT / "src" / "scripts" / "bootstrap.sh")]).returncode:
        raise die(
            "Source update succeeded, but bootstrap failed.",
            f"The repo is now at {head}. Fix the error above, then rerun:",
            "  tokenage server update",
        )
    ok("Bootstrap complete")

    step("Restarting servers")
    # A fresh interpreter, so the restart runs the code that was just pulled.
    if _run([_venv_bin("python"), "-m", "src.cli", "restart"], cwd=ROOT).returncode:
        raise die(
            "Bootstrap succeeded, but server restart failed.",
            f"The repo is updated at {head}. Fix the error above, then rerun:",
            "  tokenage server restart",
        )
    ok(f"tokenage server updated to {head}")
    return 0


COMMANDS = {
    "bootstrap": bootstrap,
    "start": start,
    "stop": stop,
    "restart": restart,
    "status": status,
    "update": update,
    "migrate": migrate,
    "sync-config": sync_config,
}
