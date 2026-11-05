"""Local-only hook reports, Git names, delivery and agent hook preservation."""

import io
import json
import os
import stat
import subprocess
import time
import tomllib
from pathlib import Path

import httpx
import pytest

from client import event_spool, hooks
from protocol.events import SpoolReport


@pytest.fixture(autouse=True)
def isolated_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path / ".tokenage"))
    from client.auth import save_credentials

    save_credentials(
        {"server_url": "https://collector.example", "ingest_token": "fake-ingest"}
    )


def git(cwd, *args):
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env
    )


def report():
    return SpoolReport(
        schema_version=1,
        agent="codex",
        session_id="session-1",
        project="orchard",
        ts=time.time_ns() // 1000,
    )


def test_git_checkout_subdir_and_worktree(tmp_path, monkeypatch):
    repo = tmp_path / "orchard"
    repo.mkdir()
    git(repo, "init")
    git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    linked = tmp_path / "feature"
    git(repo, "worktree", "add", str(linked), "-b", "feature")
    nested = repo / "src"
    nested.mkdir()
    monkeypatch.setenv("GIT_DIR", "/invalid")
    for cwd in (repo, linked, nested):
        assert hooks.project_name(str(cwd)) == "orchard"


def test_submodule_nonstandard_and_bare(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(
        source,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init")
    git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(source),
        "module",
    )
    assert hooks.project_name(str(parent / "module")) == "module"
    nonstandard = tmp_path / "separate"
    nonstandard.mkdir()
    git(nonstandard, "init", "--separate-git-dir", str(tmp_path / "metadata"))
    assert hooks.project_name(str(nonstandard)) == "separate"
    bare = tmp_path / "bare.git"
    bare.mkdir()
    git(bare, "init", "--bare")
    assert hooks.project_name(str(bare)) == "bare.git"


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("git", 0.1),
        subprocess.CompletedProcess(
            [], 0, "false\n--path-format=absolute\nmetadata\n/repo\n", ""
        ),
    ],
)
def test_git_fallback(tmp_path, monkeypatch, failure):
    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(hooks.subprocess, "run", run)
    assert hooks.project_name(str(tmp_path)) == tmp_path.name


def test_hook_is_private_and_metadata_only(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "project"
    cwd.mkdir()
    raw = {
        "session_id": "s1",
        "cwd": str(cwd),
        "transcript_path": "/private/transcript",
        "prompt": "secret",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(raw)))
    assert hooks.main(["session-start", "--agent", "claude"]) == 0
    assert capsys.readouterr().out == ""
    (path,) = event_spool.spool_dir().glob("*.json")
    body = path.read_text()
    assert json.loads(body)["project"] == "project"
    assert "private" not in body and "secret" not in body and str(tmp_path) not in body
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        "{}",
        '{"session_id":1,"cwd":"/"}',
        '{"session_id":"s","cwd":"missing"}',
        "x" * 65537,
    ],
)
def test_hook_fails_open(raw, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert hooks.main(["session-start", "--agent", "codex"]) == 0
    assert capsys.readouterr().out == ""
    assert not event_spool.spool_dir().exists()


def test_spool_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(event_spool, "MAX_FILES", 1)
    event_spool.write_report(report())
    event_spool.write_report(report())
    assert len(list(event_spool.spool_dir().glob("*.json"))) == 1


@pytest.mark.parametrize(
    "status,retained",
    [
        (200, False),
        (204, False),
        (400, False),
        (401, True),
        (403, True),
        (404, False),
        (408, True),
        (422, False),
        (429, True),
        (500, True),
        (503, True),
        (0, True),
    ],
)
def test_drain_delivery(monkeypatch, status, retained):
    event_spool.write_report(report())

    def post(url, **kwargs):
        assert url == "https://collector.example/ingest/v1/events"
        assert kwargs["json"]["events"][0]["data"] == {"project": "orchard"}
        if not status:
            raise httpx.ConnectError("offline")
        return httpx.Response(status)

    monkeypatch.setattr(event_spool.httpx, "post", post)
    assert event_spool.drain_once() is not retained
    assert bool(list(event_spool.spool_dir().glob("*.json"))) is retained


def test_drain_drops_old_identity_and_invalid_symlink(tmp_path, monkeypatch):
    event_spool.write_report(report())
    target = tmp_path / "private.json"
    target.write_text("private")
    event_spool.spool_dir().joinpath("symlink.json").symlink_to(target)
    monkeypatch.setattr(
        event_spool,
        "_identity",
        lambda: ("https://new.example", "new-token", "new-binding"),
    )
    monkeypatch.setattr(
        event_spool.httpx,
        "post",
        lambda *a, **kw: pytest.fail("must not upload stale login"),
    )
    assert event_spool.drain_once()
    assert not list(event_spool.spool_dir().glob("*.json"))
    assert target.read_text() == "private"


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_setup_preserves_and_adds_hook_idempotently(tmp_path, agent, monkeypatch):
    from client.agents import claude, codex

    existing = {
        "SessionStart": [
            {
                "matcher": "resume",
                "hooks": [{"type": "command", "command": "user-hook"}],
            }
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "stop-hook"}]}],
    }
    path = tmp_path / ("settings.json" if agent == "claude" else "config.toml")
    if agent == "claude":
        path.write_text(json.dumps({"hooks": existing, "user": "keep"}))

        def load():
            return json.loads(path.read_text())

        module = claude
    else:
        path.write_text(
            'model = "keep"\n[hooks]\n'
            + "\n".join(
                f"{key} = {codex._inline_toml(value)}"
                for key, value in existing.items()
            )
        )

        def load():
            return tomllib.loads(path.read_text())

        module = codex
    for bin_dir in ("/old/bin", "/new/bin"):
        monkeypatch.setenv("TOKENAGE_BIN_DIR", bin_dir)
        assert module.configure(path, "https://collector.example/otlp/v1/logs") == 0
        after = load()["hooks"]
        assert after["Stop"] == existing["Stop"]
        assert after["SessionStart"][0] == existing["SessionStart"][0]
        assert len(after["SessionStart"]) == 2
        assert after["SessionStart"][1]["hooks"][0]["command"] == hooks.hook_command(
            agent
        )


@pytest.mark.parametrize(
    "content",
    [
        'hooks = { SessionStart = [{ hooks = [{ type = "command", command = "user" }] }] }\n',
        '[[hooks.SessionStart]]\nmatcher = "resume"\n[[hooks.SessionStart.hooks]]\ntype = "command"\ncommand = "user"\n',
    ],
)
def test_codex_hook_toml_layouts(content):
    from client.agents.codex import update_session_hook

    updated = update_session_hook(content)
    assert (
        tomllib.loads(updated)["hooks"]["SessionStart"][0]
        == tomllib.loads(content)["hooks"]["SessionStart"][0]
    )
    assert update_session_hook(updated) == updated


def test_hook_stdin_watchdog_exits_silently(tmp_path):
    import sys

    root = Path(__file__).resolve().parents[2]
    started = time.monotonic()
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "client.cli",
            "hook",
            "session-start",
            "--agent",
            "codex",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.wait(timeout=3) == 0
        assert time.monotonic() - started < 2.5
        assert child.stdout.read() == b""
        assert child.stderr.read() == b""
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate()


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize(
    "endpoint", [None, "https://other.example/logs", "https://collector.example/logs"]
)
def test_disable_removes_only_owned_hook(tmp_path, agent, endpoint):
    from client.agents import claude, codex

    module = claude if agent == "claude" else codex
    path = tmp_path / ("settings.json" if agent == "claude" else "config.toml")
    assert module.configure(path, "https://collector.example/logs") == 0
    load = json.loads if agent == "claude" else tomllib.loads
    data = load(path.read_text())
    data["hooks"]["SessionStart"][0]["hooks"].append(
        {"type": "command", "command": "user-hook"}
    )
    if agent == "claude":
        path.write_text(json.dumps(data))
    else:
        path.write_text(
            "[otel]\nexporter = "
            + codex._inline_toml(data["otel"]["exporter"])
            + "\n[hooks]\nSessionStart = "
            + codex._inline_toml(data["hooks"]["SessionStart"])
            + "\n"
        )
    assert module.disable(path, endpoint) == 0
    assert load(path.read_text())["hooks"]["SessionStart"][0]["hooks"] == [
        {"type": "command", "command": "user-hook"}
    ]


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_hook_layout_failure_does_not_fail_telemetry(tmp_path, agent, capsys):
    from client.agents import claude, codex

    path = tmp_path / ("settings.json" if agent == "claude" else "config.toml")
    path.write_text(
        '{"hooks": "unsupported"}' if agent == "claude" else 'hooks = "unsupported"\n'
    )
    module = claude if agent == "claude" else codex
    assert module.configure(path, "https://collector.example/logs") == 0
    data = (json.loads if agent == "claude" else tomllib.loads)(path.read_text())
    assert data["hooks"] == "unsupported"
    assert "hook skipped" in capsys.readouterr().out
    if agent == "claude":
        assert (
            data["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"]
            == "https://collector.example/logs"
        )
    else:
        assert (
            data["otel"]["exporter"]["otlp-http"]["endpoint"]
            == "https://collector.example/logs"
        )


def test_disable_clears_spool_even_without_agents(monkeypatch):
    from client import setup

    event_spool.write_report(report())
    monkeypatch.setattr(setup, "installed_agents", lambda: [])
    assert setup.run_setup(disable=True) == 0
    assert not list(event_spool.spool_dir().iterdir())


@pytest.mark.parametrize(
    "content",
    [
        '["hooks"]\nSessionStart = []\n',
        "hooks.SessionStart = []\n",
        "[hooks]\nSessionStart = [\n [{ hooks = [] }],\n]\n",
    ],
)
def test_codex_unrecognized_hook_text_keeps_telemetry(tmp_path, content, capsys):
    from client.agents import codex

    path = tmp_path / "config.toml"
    path.write_text(content)
    before = tomllib.loads(content)["hooks"]
    assert codex.configure(path, "https://collector.example/logs") == 0
    after = tomllib.loads(path.read_text())
    assert after["hooks"] == before
    assert (
        after["otel"]["exporter"]["otlp-http"]["endpoint"]
        == "https://collector.example/logs"
    )
    assert "hook skipped" in capsys.readouterr().out


def test_hook_helper_deduplicates_owned_commands_and_preserves_others():
    handler = {"type": "command", "command": hooks.hook_command("claude"), "timeout": 1}
    settings = {
        "hooks": {
            "SessionStart": [
                {"hooks": [handler, dict(handler), {"command": "user-hook"}]}
            ]
        }
    }
    assert hooks.update_session_hooks(settings, "claude")
    assert settings["hooks"]["SessionStart"][0]["hooks"] == [
        {**handler, "timeout": 10},
        {"command": "user-hook"},
    ]


@pytest.mark.parametrize("watchdog", ["missing", "thread"])
def test_hook_signal_setup_fails_open(monkeypatch, capsys, watchdog):
    if watchdog == "missing":
        monkeypatch.delattr(hooks.signal, "SIGALRM")
    else:

        def unavailable(*args):
            raise ValueError("not main thread")

        monkeypatch.setattr(hooks.signal, "signal", unavailable)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert hooks.main(["session-start", "--agent", "codex"]) == 0
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_owned_hook_survives_launcher_change(monkeypatch, agent):
    settings = {}
    monkeypatch.setenv("TOKENAGE_BIN_DIR", "/old/bin")
    assert hooks.update_session_hooks(settings, agent)
    group = settings["hooks"]["SessionStart"][0]
    group["matcher"] = "resume"
    group["hooks"].append({"type": "command", "command": "user-hook"})
    monkeypatch.setenv("TOKENAGE_BIN_DIR", "/new/bin")
    assert hooks.update_session_hooks(settings, agent)
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert group["matcher"] == "resume"
    commands = [
        h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]
    ]
    assert commands == [hooks.hook_command(agent), "user-hook"]
    assert not hooks.update_session_hooks(settings, agent)
    monkeypatch.setenv("TOKENAGE_BIN_DIR", "/third/bin")
    assert hooks.update_session_hooks(settings, agent, remove=True)
    assert settings["hooks"]["SessionStart"][0]["hooks"] == [
        {"type": "command", "command": "user-hook"}
    ]


def test_spool_tmp_files_do_not_count_and_are_cleaned(monkeypatch):
    monkeypatch.setattr(event_spool, "MAX_FILES", 1)
    directory = event_spool.spool_dir()
    directory.mkdir(parents=True)
    stale = directory / ".stale.tmp"
    stale.write_text("unfinished")
    os.utime(stale, (time.time() - 61, time.time() - 61))
    recent = directory / ".recent.tmp"
    recent.write_text("active")
    event_spool.write_report(report())
    assert len(list(directory.glob("*.json"))) == 1
    monkeypatch.setattr(event_spool.httpx, "post", lambda *a, **kw: httpx.Response(204))
    assert event_spool.drain_once()
    assert not stale.exists()
    assert recent.exists()
    assert not list(directory.glob("*.json"))


def test_git_environment_and_client_truncation(tmp_path, monkeypatch):
    folder = tmp_path / ("x" * 101)
    folder.mkdir()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    def run(*args, **kwargs):
        assert {
            key: value for key, value in kwargs["env"].items() if key.startswith("GIT_")
        } == {"GIT_OPTIONAL_LOCKS": "0"}
        return subprocess.CompletedProcess([], 1, "")

    monkeypatch.setattr(hooks.subprocess, "run", run)
    assert hooks.project_name(str(folder)) == "x" * 100
