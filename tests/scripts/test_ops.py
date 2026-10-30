"""src/ops.py with supervisord, git and npm mocked and HOME in a tmp dir."""

from __future__ import annotations

import http.client
import http.server
import json
import os
import subprocess
import threading
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from src import ops


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake checkout + tokenage home. `box.calls` records external commands."""
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "config.example.yaml").write_text(
        "server:\n  host: 127.0.0.1\n  api_port: 4001\n"
    )
    (root / ".venv").mkdir()
    home = tmp_path / "home"
    monkeypatch.setattr(ops, "ROOT", root)
    monkeypatch.setenv("TOKENAGE_HOME", str(home))
    monkeypatch.delenv("TOKENAGE_CONFIG", raising=False)
    monkeypatch.setattr(ops.time, "sleep", lambda _: None)

    b = SimpleNamespace(root=root, home=home, calls=[], states={}, busy=set())

    def fake_run(cmd, **kw):
        b.calls.append(cmd)
        out = ""
        if cmd[0].endswith("supervisorctl") and cmd[3] == "status":
            names = cmd[4:] or ops.PROGRAMS
            out = "".join(
                f"{n} {b.states.get(n, 'STOPPED')} pid 1, uptime 0\n" for n in names
            )
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(ops, "_run", fake_run)
    monkeypatch.setattr(ops, "_bindable", lambda host, port: port not in b.busy)
    monkeypatch.setattr(
        ops,
        "_listeners",
        lambda port, host="127.0.0.1": (
            [ops.PortListener(9, "other")] if port in b.busy else []
        ),
    )
    monkeypatch.setattr(
        ops, "migrate", lambda args=None: b.calls.append("migrate") or 0
    )
    b.stamp = lambda: (
        ops._stamp_file().parent.mkdir(exist_ok=True),
        ops._stamp_file().write_text(ops._requirements_hash()),
    )
    return b


def ctl(b, *args):
    return [c for c in b.calls if isinstance(c, list) and tuple(c[3:]) == args]


def test_start_refuses_a_stale_stamp(box, capsys):
    ops._stamp_file().write_text("0" * 64)

    assert ops.start() == 1
    out = capsys.readouterr().out
    assert "Dependencies are out of date" in out
    assert "run tokenage server bootstrap" in out
    assert not (box.home / "config.yaml").exists()


def test_start_creates_config_checks_ports_then_migrates_then_starts(box):
    box.stamp()

    assert ops.start() == 0

    assert (box.home / "config.yaml").exists()
    assert "path" not in ops._load_config(box.home / "config.yaml").get("db", {})
    conf = (box.home / "supervisord.conf").read_text()
    for prog in ops.PROGRAMS:
        assert f"[program:{prog}]" in conf
    assert any(
        str(c[0]).endswith("supervisord") for c in box.calls if isinstance(c, list)
    )
    for prog in ops.PROGRAMS:
        assert ctl(box, "start", prog)
    assert "migrate" in box.calls


def test_first_run_moves_busy_ports_but_an_existing_config_fails(box, capsys):
    box.stamp()
    box.busy = {4001}

    assert ops.start() == 0
    server = yaml.safe_load((box.home / "config.yaml").read_text())["server"]
    assert server["api_port"] != 4001

    server["api_port"] = 4001
    (box.home / "config.yaml").write_text(yaml.safe_dump({"server": server}))
    assert ops.start() == 1
    assert "Port check failed" in capsys.readouterr().out


def test_restart_reloads_named_programs(box):
    (box.home).mkdir()
    (box.home / "supervisord.conf").write_text("")
    (box.home / "config.yaml").write_text("server:\n  api_port: 4001\n")
    box.states = {"tokenage-api": "RUNNING"}
    (box.home / "run").mkdir()
    (box.home / "run" / "api-binds.json").write_text(
        json.dumps({"pid": 1, "bind": ["127.0.0.1:4001"]})
    )

    assert ops.PROGRAMS == ("tokenage-api",)  # OTLP is mounted in the API
    assert ops.restart(["tokenage-api"]) == 0
    assert ctl(box, "signal", "HUP", "tokenage-api")
    assert "migrate" in box.calls

    for args in (["nope"], ["tokenage-otlp"], ["--otlp-port", "5505"]):
        with pytest.raises(SystemExit):
            ops.restart(args)


def test_stop_named_or_everything(box):
    box.home.mkdir()
    (box.home / "supervisord.conf").write_text("")

    ops.stop(["tokenage-api"])
    assert ctl(box, "stop", "tokenage-api") and not ctl(box, "shutdown")
    ops.stop()
    assert ctl(box, "stop", "all") and ctl(box, "shutdown")


@pytest.mark.parametrize(
    "host,local_host",
    [("127.0.0.1", "127.0.0.1"), ("::1", "[::1]"), ("[::1]", "[::1]")],
)
def test_bootstrap_records_the_stamp_and_reports_display_urls(
    box, monkeypatch, capsys, host, local_host
):
    (box.home).mkdir()
    (box.home / "config.yaml").write_text(
        f"server:\n  host: '{host}'\n  base_url: https://tok.example.com\n"
    )
    (box.root / "frontend" / "dist").mkdir(parents=True)
    cli = box.root / "client" / "bin" / "tokenage"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    cli.chmod(0o755)
    monkeypatch.setattr(ops, "_bin_dir", lambda: cli.parent)
    monkeypatch.setattr(ops, "_wait_for_port", lambda host, port, retries=10: True)
    monkeypatch.setattr(ops, "_content_type", lambda url: "text/html")
    monkeypatch.setattr(ops, "start", lambda args=None: 0)
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)

    assert ops.bootstrap() == 0

    out = capsys.readouterr().out
    assert ops._stamp_file().read_text().strip() == ops._requirements_hash()
    assert f"API running: http://{local_host}:4001" in out
    assert f"Dashboard: http://{local_host}:4001" in out
    assert "tokenage is LIVE → https://tok.example.com" in out
    assert "OTLP" not in out  # no separate collector port to check
    assert ctl(box, "restart", "tokenage-api")  # new frontend/dist is served


def test_bootstrap_reports_invalid_base_url(box, monkeypatch, capsys):
    box.home.mkdir()
    (box.home / "config.yaml").write_text('server:\n  base_url: "https://[::1"\n')
    monkeypatch.setattr(ops, "start", lambda: 0)
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as exc:
        ops.bootstrap()
    assert exc.value.code == 1
    assert "server.base_url" in capsys.readouterr().out


def test_bootstrap_reports_missing_cli_wrapper(box, monkeypatch, capsys):
    box.home.mkdir()
    (box.home / "config.yaml").write_text("server:\n  api_port: 4001\n")
    bin_dir = box.home / "bin"
    bin_dir.mkdir()
    (bin_dir / "tokenage").touch()
    monkeypatch.setattr(ops, "_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(ops, "start", lambda: 0)
    monkeypatch.setattr(ops, "_wait_for_port", lambda *args: True)
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)

    assert ops.bootstrap() == 1
    assert (
        "CLI wrapper: client/bin/tokenage (not executable)" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "built,content_types,reachable,expected",
    [
        (False, [], True, 0),
        (False, [], False, 1),
        (True, ["", "application/json", "text/html; charset=utf-8"], True, 0),
        (True, [""] * 10, True, 1),
    ],
)
def test_bootstrap_dashboard_readiness(
    box, monkeypatch, capsys, built, content_types, reachable, expected
):
    box.home.mkdir()
    (box.home / "config.yaml").write_text(
        (box.root / "config.example.yaml").read_text()
    )
    cli = box.root / "client" / "bin" / "tokenage"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    cli.chmod(0o755)
    monkeypatch.setattr(ops, "_bin_dir", lambda: cli.parent)
    if built:
        (box.root / "frontend" / "dist").mkdir(parents=True)
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    monkeypatch.setattr(ops, "start", lambda: 0)
    monkeypatch.setattr(ops, "_wait_for_port", lambda *args: reachable)
    content_type = Mock(side_effect=content_types)
    monkeypatch.setattr(ops, "_content_type", content_type)

    assert ops.bootstrap() == expected
    assert content_type.call_count == len(content_types)
    assert bool(ctl(box, "restart", "tokenage-api")) == built
    out = capsys.readouterr().out
    if not built:
        assert "Dashboard unavailable" in out
    elif expected:
        assert "frontend not served" in out
    else:
        assert "✓ Dashboard:" in out


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("worker still starting"),
        http.client.BadStatusLine("incomplete response"),
        urllib.error.HTTPError(
            "http://127.0.0.1:4001/",
            503,
            "not ready",
            {"content-type": "text/html"},
            None,
        ),
    ],
)
def test_content_type_rejects_http_and_protocol_failures(monkeypatch, error):
    monkeypatch.setattr(ops._LOCAL_OPENER, "open", Mock(side_effect=error))

    assert ops._content_type("http://127.0.0.1:4001/") == ""


def test_content_type_bypasses_http_proxy(monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert ops._content_type(f"http://127.0.0.1:{server.server_port}/") == (
                "text/html; charset=utf-8"
            )
        finally:
            server.shutdown()
            thread.join()


def test_socket_fallback_sees_an_occupied_port(monkeypatch):
    import socket

    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        assert ops._listeners(sock.getsockname()[1])


# ── update, against real git repos ──────────────────────────────────


@pytest.fixture
def clone(box, monkeypatch, tmp_path):
    # Commit hooks export Git paths such as GIT_INDEX_FILE. Fixture repositories
    # must not inherit them or their Git commands can alter the checkout's index.
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    real_run = subprocess.run

    def git(*args, cwd):
        real_run(["git", *args], cwd=cwd, check=True, capture_output=True)

    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    git("config", "user.email", "t@t", cwd=seed)
    git("config", "user.name", "t", cwd=seed)
    (seed / "a").write_text("1")
    git("add", ".", cwd=seed)
    git("commit", "-m", "one", cwd=seed)
    origin = tmp_path / "origin.git"
    git("clone", "--bare", str(seed), str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    git("clone", str(origin), str(work), cwd=tmp_path)
    monkeypatch.setattr(ops, "ROOT", work)

    def run(cmd, **kw):
        if cmd[0] == "git":
            return real_run(cmd, **{"text": True, **kw})
        box.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ops, "_run", run)
    (seed / "a").write_text("2")
    git("commit", "-am", "two", cwd=seed)
    git("push", str(origin), "main", cwd=seed)
    return work


def test_update_fast_forwards_then_bootstraps_and_restarts(box, clone, capsys):
    assert ops.update() == 0

    assert (clone / "a").read_text() == "2"
    names = [" ".join(map(str, c)) for c in box.calls]
    assert names[0].startswith("bash ") and names[0].endswith(
        "src/scripts/bootstrap.sh"
    )
    assert names[1].endswith("-m src.cli restart")
    assert ops.update() == 0
    assert "Already up to date" in capsys.readouterr().out


def test_clone_preserves_inherited_hook_index(monkeypatch, tmp_path, request):
    index = tmp_path / "hook-index"
    index.write_bytes(b"outer Git index")
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))

    request.getfixturevalue("clone")

    assert index.read_bytes() == b"outer Git index"


def test_update_check_and_dry_run_change_nothing(box, clone):
    assert ops.update(["--check"]) == 0
    assert ops.update(["--dry-run"]) == 0
    assert (clone / "a").read_text() == "1"
    assert box.calls == []


def test_update_refuses_a_dirty_worktree(box, clone):
    (clone / "a").write_text("dirty")
    with pytest.raises(SystemExit):
        ops.update()
    assert box.calls == []


@pytest.mark.parametrize("enabled", [True, False])
def test_metrics_port_preflight_and_status(box, monkeypatch, capsys, enabled):
    box.stamp()
    box.home.mkdir()
    (box.home / "supervisord.conf").write_text("")
    (box.home / "config.yaml").write_text(
        yaml.safe_dump({"metrics": {"enabled": enabled, "host": "[::1]", "port": 9108}})
    )
    box.busy = {9108}
    assert ops.start() == (1 if enabled else 0)
    monkeypatch.setattr(
        "src.shared_state.get_shared_state", lambda: SimpleNamespace(status=lambda: [])
    )
    ops.status()
    output = capsys.readouterr().out
    if enabled:
        assert "METRICS: [::1]:9108" in output
        assert "cannot bind" in output
        assert "migrate" not in box.calls
    else:
        assert "METRICS" not in output


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        {"pid": 2, "bind": ["127.0.0.1:4001"]},
        {"pid": 1, "bind": ["127.0.0.1:4001", "127.0.0.1:9109"]},
    ],
)
def test_restart_refuses_changed_or_unknown_bind(box, capsys, stamp):
    box.home.mkdir()
    (box.home / "supervisord.conf").write_text("")
    (box.home / "config.yaml").write_text(
        "server: {api_port: 4001}\nmetrics: {enabled: true, port: 9108}\n"
    )
    box.states = {"tokenage-api": "RUNNING"}
    if stamp:
        (box.home / "run").mkdir()
        (box.home / "run" / "api-binds.json").write_text(json.dumps(stamp))
    with pytest.raises(SystemExit):
        ops.restart()
    assert not ctl(box, "signal", "HUP", "tokenage-api")
    assert "migrate" not in box.calls
    assert "stop tokenage-api then tokenage server start" in capsys.readouterr().out
