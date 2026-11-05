"""The server operator CLI and the installed launcher that fronts both halves.

Everything a per-user client does — tracking, sign-in, agent wiring — is tested
in tests/client/ against the client package. What is left here is what only the
server can answer (`tokenage server token ...`) and the contract of
client/bin/tokenage, the one installed command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "client" / "bin" / "tokenage"
CLIENT = Path(__file__).resolve().parents[1] / "client"


def _scrubbed_env(tmp_path):
    """Launcher test env: no machine home, no machine shell state.

    `TOKENAGE_ROOT` and `TOKENAGE_SKIP_BANNER` are exactly the exports a
    developer's shell may carry, and they change which component the launcher
    resolves and whether the banner prints at all.
    """
    env = os.environ.copy()
    for key in (
        "TOKENAGE_ROOT",
        "TOKENAGE_SKIP_BANNER",
        "TOKENAGE_CLIENT_COMMIT",
        "NO_COLOR",
    ):
        env.pop(key, None)
    env["HOME"] = str(tmp_path / "os-home")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_launcher(tmp_path, *args):
    env = _scrubbed_env(tmp_path)
    # A test-owned client snapshot keeps the launcher's discovery off this
    # machine, and no tty plus a fixed width keeps the banner out of captured
    # output.
    env["TOKENAGE_HOME"] = str(_client_snapshot(tmp_path / "tracker-home"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "100"
    return subprocess.run(
        [str(LAUNCHER), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


# ------------------------------------------------------------- operator CLI


def test_parse_token_args_defaults_to_a_cli_token(cli_module, isolated_home):
    args = cli_module.parse_token_args(["create", "--email", "a@example.com"])

    assert args.action == "create"
    assert args.email == "a@example.com"
    assert args.kind == "cli"
    assert args.name is None


def test_run_token_command_prints_the_token_once(cli_module, isolated_home, capsys):
    from src.auth.tokens import resolve_token

    code = cli_module.run_token_command(
        ["create", "--email", "a@example.com", "--name", "laptop"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Minted cli token for a@example.com" in out
    token = out.strip().splitlines()[-1]
    assert token.startswith("tokenage_cli_")
    assert resolve_token(token) is not None


def test_main_without_a_command_prints_usage(cli_module, isolated_home, capsys):
    assert cli_module.main([]) == 2
    assert "usage: tokenage server token create --email" in capsys.readouterr().err


# ------------------------------------------------------------ launcher wiring


def test_dev_scripts_live_under_scripts_dev():
    scripts_dir = Path(__file__).resolve().parents[1] / "src" / "scripts"

    assert not (scripts_dir / "dev-start.sh").exists()
    assert not (scripts_dir / "dev-stop.sh").exists()
    assert (scripts_dir / "dev" / "dev-start.sh").exists()
    assert (scripts_dir / "dev" / "dev-stop.sh").exists()


def test_tokenage_script_routes_to_client_and_server():
    content = LAUNCHER.read_text(encoding="utf-8")

    # Anything that is not a server command runs the client. `-P` matters: without
    # it a `python -m` run from inside some other checkout would import that
    # checkout's client instead of the installed one.
    assert 'exec "$python" -P -m client "$@"' in content
    # `tokenage server ...` is the server half: src.cli, except bootstrap.
    assert "run_server() {" in content
    assert '-m src.cli "$@"' in content


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
@pytest.mark.parametrize(
    "command",
    [None, "stop", "restart", "status", "start", "bootstrap", "update", "login-link"],
)
def test_server_help_prints_to_stdout_and_exits_zero(tmp_path, command, help_flag):
    args = ["server"]
    if command is not None:
        args.append(command)
    result = _run_launcher(tmp_path, *args, help_flag)

    assert result.returncode == 0
    assert "usage: tokenage server <command>" in result.stdout
    assert "[service...]" not in result.stdout
    assert "services:" not in result.stdout
    assert result.stderr == ""


def test_server_restart_rejects_stray_arguments(tmp_path):
    result = _run_launcher(tmp_path, "server", "restart", "foo")

    assert result.returncode == 2
    assert "restart takes no arguments" in result.stderr
    assert result.stdout == ""


def test_server_rejects_unknown_subcommand_even_with_help(tmp_path):
    result = _run_launcher(tmp_path, "server", "unknown", "--help")

    assert result.returncode == 2
    assert "unknown server command: unknown" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("command", ["stop", "restart", "status"])
@pytest.mark.parametrize("service", ["foo", "tokenage-api"])
def test_server_service_commands_reject_arguments(
    cli_module, monkeypatch, capsys, tmp_path, command, service
):
    def unexpected_call(*args):
        pytest.fail("invalid arguments must not reach service control")

    monkeypatch.setitem(cli_module.ops.COMMANDS, command, unexpected_call)
    assert cli_module.main([command, service]) == 2
    assert f"{command} takes no arguments" in capsys.readouterr().err
    result = _run_launcher(tmp_path, "server", command, service)
    assert result.returncode == 2
    assert f"{command} takes no arguments" in result.stderr


@pytest.mark.parametrize("command", ["stop", "restart", "status"])
def test_server_service_commands_dispatch_without_arguments(
    cli_module, monkeypatch, command
):
    monkeypatch.setitem(cli_module.ops.COMMANDS, command, lambda: 0)
    assert cli_module.main([command]) == 0


def test_minting_a_token_is_marked_operator_only(cli_module, capsys):
    """`token create` hits the database directly; `--help` must say so."""
    with pytest.raises(SystemExit):
        cli_module.parse_token_args(["create", "--help"])

    assert "operator-only" in capsys.readouterr().out
    assert "mint an operator token" in LAUNCHER.read_text(encoding="utf-8")


def test_tokenage_server_routes_bootstrap_to_the_script(tmp_path):
    content = LAUNCHER.read_text(encoding="utf-8")

    assert 'exec bash "${root}/src/scripts/${name}.sh" "$@"' in content
    assert 'bootstrap) shift; run_server_script bootstrap "$@"' in content
    assert "start|update|token|login-link) shift; run_server_python" in content

    result = _run_launcher(tmp_path, "server")
    assert result.returncode == 2
    assert "bootstrap" in result.stderr


def test_status_is_not_a_legacy_alias(tmp_path):
    """`tokenage status` reports components; the service view is `server status`."""
    result = _run_launcher(tmp_path, "status")

    assert result.returncode in (0, 1)
    assert "is now" not in result.stderr
    assert "Service Status" not in result.stdout
    # It is the component report, not just an exit code.
    assert "account" in result.stdout


def test_server_command_without_the_server_component_says_so(tmp_path):
    """A launcher copied outside any checkout is a client-only install."""
    launcher = tmp_path / "tokenage"
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    env = _scrubbed_env(tmp_path)
    env["TOKENAGE_HOME"] = str(tmp_path / "tracker-home")
    env["TOKENAGE_BIN_DIR"] = str(tmp_path / "bin")
    result = subprocess.run(
        [str(launcher), "server", "status"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "the server component is not installed on this machine" in result.stderr
    assert "install.sh" in result.stderr


def _client_snapshot(home: Path) -> Path:
    """A snapshot install of this repo's real client with its protocol module.

    The launcher accepts the dev checkout as a client only when its bootstrap-
    made `.venv/bin/python` exists — true on a developer machine, not in CI —
    so shipping the snapshot keeps these tests off that ambient state."""
    snapshot = home / "versions" / "test"
    if (home / "current").is_symlink():
        return home
    snapshot.parent.mkdir(parents=True)
    shutil.copytree(CLIENT, snapshot / "client")
    # The real installer always records a commit for the installed snapshot.
    (snapshot / "client" / "COMMIT").write_text("t" * 40)
    shutil.copytree(CLIENT.parent / "protocol", snapshot / "protocol")
    stub_bin = snapshot / ".venv" / "bin"
    stub_bin.mkdir(parents=True)
    python = stub_bin / "python"
    import shlex

    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"')
    python.chmod(0o755)
    (home / "current").symlink_to("versions/test")
    return home


def test_banner_is_suppressed_for_machine_readable_output(tmp_path):
    """`--json` output must stay parseable, so nothing decorates it."""
    result = _run_launcher(tmp_path, "status", "--json")

    assert result.returncode in (0, 1)
    assert "████" not in result.stdout
    assert "████" not in result.stderr
    # One compact line, so a caller can parse it directly.
    assert len(result.stdout.strip().splitlines()) == 1
    assert json.loads(result.stdout)["account"]["signed_in"] is False


def _run_on_pty(tmp_path, *args):
    """Run the launcher on a real pty; the banner speech lands on stderr."""
    import pty

    env = _scrubbed_env(tmp_path)
    env["TOKENAGE_HOME"] = str(_client_snapshot(tmp_path / "tracker-home"))
    controller, follower = pty.openpty()
    try:
        process = subprocess.Popen(
            [str(LAUNCHER), *args],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=follower,
            text=True,
        )
        os.close(follower)
        follower = None
        captured = _read_available(controller)
        process.wait(timeout=60)
    finally:
        if follower is not None:
            os.close(follower)
        os.close(controller)
    return process, captured


def test_banner_prints_on_a_terminal(tmp_path):
    """Every human-facing command starts with the banner, on a real tty."""
    process, captured = _run_on_pty(tmp_path, "--help")

    assert process.returncode == 0
    assert "████████" in captured
    # And it did not contaminate the child's stdout channel.
    assert "usage: tokenage" in process.stdout.read()

    # The same holds under --json: the banner is suppressed on a tty too.
    process, captured = _run_on_pty(tmp_path, "status", "--json")
    assert process.returncode in (0, 1)
    assert "████████" not in captured
    stdout = process.stdout.read()
    assert len(stdout.strip().splitlines()) == 1
    assert json.loads(stdout)["account"]["signed_in"] is False


def _read_available(fd: str | int, limit: float = 45.0) -> str:
    import select

    chunks = []
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        chunks.append(data.decode("utf-8", "replace"))
    return "".join(chunks)


def _client_install(root: Path, label: str, version: str, commit: str) -> None:
    package = root / "client"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "VERSION").write_text(version)
    (package / "COMMIT").write_text(commit)
    (package / "__main__.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "print(json.dumps({'root': str(Path(__file__).parent.parent), "
        "'interpreter': os.environ['TEST_CLIENT_INTERPRETER'], "
        "'commit': os.environ['TOKENAGE_CLIENT_COMMIT']}))\n"
    )
    interpreter = root / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    import shlex

    interpreter.write_text(
        f"#!/bin/sh\nexport TEST_CLIENT_INTERPRETER={shlex.quote(label)}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    interpreter.chmod(0o755)


def test_snapshot_source_interpreter_and_version_agree_with_server_present(tmp_path):
    home = tmp_path / "tracker"
    snapshot = home / "versions" / "snapshot"
    server = home / "src"
    _client_install(snapshot, "snapshot", "1.2.3", "a" * 40)
    _client_install(server, "server", "4.5.6", "b" * 40)
    (server / "VERSION").write_text("7.8.9")
    (home / "current").symlink_to("versions/snapshot")
    launcher = tmp_path / "bin" / "tokenage"
    launcher.parent.mkdir()
    launcher.write_text(LAUNCHER.read_text())
    launcher.chmod(0o755)
    env = {**os.environ, "TOKENAGE_HOME": str(home)}
    env.pop("TOKENAGE_ROOT", None)
    env["TOKENAGE_CLIENT_COMMIT"] = "c" * 40

    result = subprocess.run(
        [str(launcher), "status"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "root": str(snapshot),
        "interpreter": "snapshot",
        "commit": "a" * 40,
    }
    version = subprocess.run(
        [str(launcher), "--version"], env=env, capture_output=True, text=True
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == "tokenage 1.2.3 (client aaaaaaa) · server 7.8.9"

    env["TOKENAGE_ROOT"] = str(server)
    override = subprocess.run(
        [str(launcher), "status"], env=env, capture_output=True, text=True
    )
    assert override.returncode == 0, override.stderr
    assert json.loads(override.stdout) == {
        "root": str(server),
        "interpreter": "server",
        "commit": "b" * 40,
    }
    version = subprocess.run(
        [str(launcher), "--version"], env=env, capture_output=True, text=True
    )
    assert "4.5.6 (client bbbbbbb)" in version.stdout


def test_tokenage_identity_defaults_and_env_overrides_agree_with_launcher(
    tmp_path, monkeypatch
):
    from client import paths

    home = tmp_path / "os-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TOKENAGE_HOME", raising=False)
    monkeypatch.delenv("TOKENAGE_CONFIG", raising=False)
    default_home = home / ".tokenage"
    assert paths.tracker_home() == default_home
    assert paths.credentials_path() == default_home / "credentials.json"

    snapshot = default_home / "versions" / "test"
    _client_install(snapshot, "snapshot", "1.2.3", "a" * 40)
    (default_home / "current").symlink_to("versions/test")
    launcher = home / ".local" / "bin" / "tokenage"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(LAUNCHER.read_text())
    launcher.chmod(0o755)
    env = _scrubbed_env(tmp_path)
    result = subprocess.run(
        [str(launcher), "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tokenage 1.2.3 (client aaaaaaa)\n"

    override_home = tmp_path / "custom-home"
    _client_install(override_home / "versions" / "test", "override", "4.5.6", "b" * 40)
    (override_home / "current").symlink_to("versions/test")
    monkeypatch.setenv("TOKENAGE_HOME", str(override_home))
    monkeypatch.setenv("TOKENAGE_CONFIG", str(tmp_path / "custom.yaml"))
    assert paths.tracker_home() == override_home
    assert paths.credentials_path() == override_home / "credentials.json"
    env["TOKENAGE_HOME"] = str(override_home)
    result = subprocess.run(
        [str(launcher), "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tokenage 4.5.6 (client bbbbbbb)\n"


def test_client_never_runs_from_the_server_clone(tmp_path):
    checkout = tmp_path / "checkout"
    _client_install(checkout, "checkout", "1.2.3", "a" * 40)
    (checkout / "client" / "bin").mkdir(parents=True, exist_ok=True)
    (checkout / "src").mkdir()
    (checkout / "install.sh").write_text("")
    launcher = checkout / "client" / "bin" / "tokenage"
    launcher.write_text(LAUNCHER.read_text())
    launcher.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    linked = bin_dir / "tokenage"
    linked.symlink_to("../checkout/client/bin/tokenage")
    unrelated_cwd = tmp_path / "other"
    unrelated_cwd.mkdir()
    env = {**os.environ, "TOKENAGE_HOME": str(tmp_path / "empty-home")}
    env.pop("TOKENAGE_ROOT", None)

    result = subprocess.run(
        [str(linked), "status"],
        cwd=unrelated_cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    # The clone has a client and an environment, but without a snapshot the
    # client is simply not installed.
    assert result.returncode == 1
    assert "client component is not installed" in result.stderr


def test_hosted_launcher_does_not_use_unrelated_home_virtualenv_as_server(tmp_path):
    home = tmp_path / "home"
    tracker = home / ".tokenage"
    snapshot = tracker / "versions" / "client"
    _client_install(snapshot, "snapshot", "1.2.3", "a" * 40)
    (tracker / "current").symlink_to("versions/client")
    python = home / ".local" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    launcher = home / ".local" / "bin" / "tokenage"
    launcher.parent.mkdir()
    launcher.write_text(LAUNCHER.read_text())
    launcher.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "TOKENAGE_HOME": str(tracker)}
    env.pop("TOKENAGE_ROOT", None)
    result = subprocess.run(
        [str(launcher), "--version"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "server" not in result.stdout
    result = subprocess.run(
        [str(launcher), "server", "status"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "server component is not installed" in result.stderr


def test_server_dispatch_consumes_no_banner_without_changing_other_arguments(tmp_path):
    root = tmp_path / "checkout"
    _client_install(root, "checkout", "1.2.3", "a" * 40)
    src = root / "src"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "cli.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    env = {
        **os.environ,
        "TOKENAGE_ROOT": str(root),
        "TOKENAGE_HOME": str(tmp_path / "tracker"),
    }
    for args, expected in [
        (
            ["server", "restart", "--no-banner"],
            json.dumps(["restart"]) + "\n",
        ),
        (
            ["server", "--no-banner", "token", "create", "--email", "ops@example.com"],
            json.dumps(["token", "create", "--email", "ops@example.com"]) + "\n",
        ),
    ]:
        result = subprocess.run(
            [str(LAUNCHER), *args], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
