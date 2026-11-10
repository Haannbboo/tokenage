from __future__ import annotations

import json
import os
import pty
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "install.sh"
SHARED_LAUNCHER = REPO_ROOT / "client" / "bin" / "tokenage"
CLIENT_PYPROJECT = REPO_ROOT / "client" / "pyproject.toml"
PROMPTS = REPO_ROOT / "client" / "prompts.py"
COMMIT = "a" * 40
NEXT_COMMIT = "b" * 40
# The snapshot interpreter swaps current, lists plugin configs, and runs client
# commands. This shim receives the interpreter flags verbatim.
FAKE_PYTHON = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args[0] == '-c':
    os.replace(args[2], args[3])
    raise SystemExit(0)
if args[:2] == ['-P', '-c']:
    assert 'from client.setup import plugin_configs' in args[2]
    if os.environ.get('TOKENAGE_FAKE_CONFIGS_FAIL') == '1':
        raise SystemExit(1)
    configs = json.loads(os.environ.get('TOKENAGE_FAKE_PLUGIN_CONFIGS', 'null'))
    if configs is None:
        configs = [os.path.join(os.environ['HOME'], '.config', agent, 'opencode.json')
                   for agent in ('opencode', 'kilo')]
    print('\\n'.join(configs))
    raise SystemExit(0)
with open(os.environ['TOKENAGE_FAKE_PYTHON_LOG'], 'a') as log:
    log.write(f"{' '.join(args)} tty={'yes' if sys.stdin.isatty() else 'no'}\\n")
    if 'login' in args:
        log.write(f"login-input={sys.stdin.readline().strip()}\\n")
if 'check-server' in args and os.environ.get('TOKENAGE_FAKE_PROTOCOL_FAIL') == '1':
    raise SystemExit(1)
if 'setup' in args and os.environ.get('TOKENAGE_FAKE_SETUP_FAIL') == '1':
    raise SystemExit(1)
if 'restart' in args and os.environ.get('TOKENAGE_FAKE_RESTART_FAIL') == '1':
    raise SystemExit(1)
"""


def _write_snapshot(source: Path) -> None:
    """The minimum a real client-only snapshot contains for the installer."""
    (source / "client").mkdir(parents=True)
    (source / "client" / "pyproject.toml").write_text(CLIENT_PYPROJECT.read_text())
    (source / "client" / "bin").mkdir()
    # The installer copies the shared launcher out of the snapshot.
    (source / "client" / "bin" / "tokenage").write_text(SHARED_LAUNCHER.read_text())


def _age_directory(path: Path) -> None:
    old = time.time() - 7200
    os.utime(path, (old, old))


def _render_installer(
    text: str | None = None,
    *,
    server_url: str = "https://host.example",
    commit: str = "",
) -> str:
    return (
        (text if text is not None else INSTALLER.read_text())
        .replace("__TOKENAGE_SERVER_URL__", shlex.quote(server_url))
        .replace("__TOKENAGE_INSTALL_COMMIT__", shlex.quote(commit))
    )


def _fixture(tmp_path: Path, *, sha: str = COMMIT) -> tuple[Path, Path, Path]:
    home = tmp_path / "home with spaces"
    home.mkdir()
    tracker_home = home / ".tokenage"
    tracker_home.mkdir()
    (tracker_home / "config.yaml").write_text("provider: keep\n")
    (tracker_home / "credentials.json").write_text('{"token":"keep"}\n')
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    archive = tmp_path / "snapshot.tar.gz"
    source = tmp_path / "source" / f"tokenage-{sha}"
    _write_snapshot(source)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=source.name)

    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "url = next(arg for arg in args if arg.startswith('https://'))\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "with open(os.environ.get('TOKENAGE_FAKE_UV_LOG', str(out.parent / 'curl')) + '.requests', 'a') as log: log.write(url + '\\n')\n"
        "if url.endswith('/client/prompts.py'):\n"
        f"    out.write_text(pathlib.Path({str(PROMPTS)!r}).read_text())\n"
        "    pathlib.Path(os.environ['TOKENAGE_FAKE_UV_LOG'] + '.prompts').write_text(url)\n"
        "elif 'api.github.com' in url:\n"
        "    if os.environ.get('TOKENAGE_FAKE_API_FAIL') == '1': sys.exit(79)\n"
        f"    out.write_text({json.dumps({'sha': sha})!r})\n"
        "else:\n"
        "    out.write_bytes(pathlib.Path(os.environ['TOKENAGE_FIXTURE_ARCHIVE']).read_bytes())\n"
    )
    curl.chmod(0o755)

    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['TOKENAGE_FAKE_UV_LOG'], 'a') as log:\n"
        "    log.write(' '.join(args) + '\\n')\n"
        "if args[0] == 'run' and os.environ.get('TOKENAGE_FAKE_UV_PYTHON'):\n"
        "    python = os.environ['TOKENAGE_FAKE_UV_PYTHON']\n"
        "    os.execv(python, [python, *args[args.index('python') + 1:]])\n"
        "if args[0] == 'venv':\n"
        "    path = pathlib.Path(args[-1]); path.mkdir(parents=True)\n"
        "    bindir = path / 'bin'; bindir.mkdir()\n"
        "    python = bindir / 'python'\n"
        f"    python.write_text({FAKE_PYTHON!r})\n"
        "    python.chmod(0o755)\n"
    )
    uv.chmod(0o755)
    return home, bin_dir, archive


def _run_install(
    tmp_path: Path,
    *,
    installer_text: str | None = None,
    sha: str = COMMIT,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    home, fake_bin, archive = _fixture(tmp_path, sha=sha)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer(installer_text))
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
    }
    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    return result, home, fake_bin


def test_installs_sha_snapshot_and_preserves_user_state(tmp_path: Path) -> None:
    result, home, _ = _run_install(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    tracker_home = home / ".tokenage"
    version_dir = tracker_home / "versions" / COMMIT
    assert (tracker_home / "current").resolve() == version_dir
    assert (version_dir / "client" / "COMMIT").read_text().strip() == COMMIT
    uv_log = (tmp_path / "uv.log").read_text()
    assert "python install 3.13" in uv_log
    assert "venv --managed-python --python 3.13" in uv_log
    # Whatever the snapshot pins — httpx and pyyaml today — not a baked-in list.
    pip = next(line for line in uv_log.splitlines() if line.startswith("pip install"))
    assert pip.endswith("/client/pyproject.toml")
    check_server = (tmp_path / "python.log").read_text()
    assert (
        "-P -m client check-server --server https://host.example tty=no" in check_server
    )
    assert "No interactive terminal is available" in result.stdout
    assert "TOKENAGE_CLIENT_COMMIT" in INSTALLER.read_text()

    config = tracker_home / "config.yaml"
    credentials = tracker_home / "credentials.json"
    assert config.read_text() == "provider: keep\n"
    assert credentials.read_text() == '{"token":"keep"}\n'


@pytest.mark.parametrize(
    "previous", [None, "", "relative", "absolute", "trailing", "outside", "nested"]
)
def test_prunes_only_plain_version_children(tmp_path: Path, previous) -> None:
    home, fake_bin, archive = _fixture(tmp_path)
    tracker = home / ".tokenage"
    versions = tracker / "versions"
    versions.mkdir()
    old = versions / NEXT_COMMIT
    old.mkdir()
    stale = versions / ".stale"
    stale.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched")
    (versions / "linked").symlink_to(outside, target_is_directory=True)
    (versions / "dangling").symlink_to(tmp_path / "missing")
    (stale / "nested-link").symlink_to(outside, target_is_directory=True)
    staging = versions / ".install-other-1"
    staging.mkdir()
    failed_delete = versions / ".removing-failed"
    failed_delete.mkdir()
    _age_directory(old)
    _age_directory(stale)
    _age_directory(staging)
    _age_directory(failed_delete)
    (versions / "plain-file").write_text("untouched")
    if previous is not None:
        target = {
            "": "versions/missing",
            "relative": f"versions/{NEXT_COMMIT}",
            "absolute": str(old),
            "trailing": f"versions/{NEXT_COMMIT}/",
            "outside": str(outside),
            "nested": f"versions/{NEXT_COMMIT}/nested",
        }[previous]
        (tracker / "current").symlink_to(target)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())
    result = subprocess.run(
        ["/bin/sh", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "HOME": str(home),
            "TOKENAGE_HOME": str(tracker),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
            "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
            "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tracker / "current").resolve() == versions / COMMIT
    assert old.exists() == (previous in {"relative", "absolute", "trailing"})
    assert not stale.exists()
    assert staging.is_dir()
    assert not failed_delete.exists()
    removed = 2 if old.exists() else 3
    assert result.stdout.count(f"Removed {removed} old client version(s).") == 1
    assert (versions / "linked").is_symlink()
    assert (versions / "dangling").is_symlink()
    assert (versions / "plain-file").read_text() == "untouched"
    assert (outside / "keep").read_text() == "untouched"


def test_prune_does_not_traverse_a_symlinked_versions_directory(tmp_path: Path) -> None:
    installer = INSTALLER.read_text().replace(
        "# Replace current via",
        'mv "$VERSIONS_DIR" "$TRACKER_HOME/external"\n'
        'ln -s "$TRACKER_HOME/external" "$VERSIONS_DIR"\n'
        'mkdir "$VERSIONS_DIR/stale"\n# Replace current via',
    )
    result, home, _ = _run_install(tmp_path, installer_text=installer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning:" in result.stderr
    assert (home / ".tokenage" / "external" / "stale").is_dir()


@pytest.mark.parametrize("pinned_agent", [None, "opencode", "kilo", "custom"])
def test_two_updates_keep_only_current_and_previous(
    tmp_path: Path, pinned_agent: str | None, monkeypatch
) -> None:
    first, home, fake_bin = _run_install(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    tracker = home / ".tokenage"
    versions = tracker / "versions"
    if pinned_agent:
        config = home / ".config" / pinned_agent / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "plugin": [
                        [
                            str(versions / COMMIT / "plugins" / pinned_agent / "dist"),
                            {"endpoint": "https://host.example/otlp/v1/logs"},
                        ]
                    ]
                }
            )
        )
        if pinned_agent == "custom":
            monkeypatch.setenv(
                "TOKENAGE_FAKE_PLUGIN_CONFIGS", json.dumps([str(config)])
            )
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(tracker),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
        "TOKENAGE_SKIP_LOGIN": "1",
    }
    for sha, previous in [(NEXT_COMMIT, COMMIT), ("c" * 40, NEXT_COMMIT)]:
        for existing in versions.iterdir():
            _age_directory(existing)
        # Already downloaded snapshots still exercise the swap and cleanup.
        root = versions / sha
        _write_snapshot(root)
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(FAKE_PYTHON)
        python.chmod(0o755)
        script = tmp_path / "install.sh"
        script.write_text(_render_installer(commit=sha))
        result = subprocess.run(
            ["/bin/sh", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tracker / "current").resolve() == root
        kept = {sha, previous}
        if pinned_agent:
            kept.add(COMMIT)
        assert {path.name for path in versions.iterdir()} == kept
        if pinned_agent and sha == "c" * 40:
            assert "referenced by an agent" in result.stderr
            assert "could not clean up" not in result.stderr
            assert "Removed " not in result.stdout


def test_prune_failure_warns_and_still_restarts(tmp_path: Path) -> None:
    # Fail only snapshot deletion; the installer's temporary cleanup still runs.
    installer = INSTALLER.read_text().replace('rm -rf "./$doomed"', "(exit 1)")
    # The injected failure needs a stale child to attempt to remove.
    installer = installer.replace(
        "# Replace current via",
        'mkdir "$VERSIONS_DIR/stale"\n'
        "python3 -c 'import os, sys; os.utime(sys.argv[1], (0, 0))' "
        '"$VERSIONS_DIR/stale"\n'
        "# Replace current via",
    )
    result, home, _ = _run_install(tmp_path, installer_text=installer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning:" in result.stderr
    assert (home / ".tokenage" / "versions" / ".removing-stale").is_dir()
    assert "client client restart --if-running" in (tmp_path / "python.log").read_text()


@pytest.mark.parametrize(
    "mode", ["normal", "configs-fail", "find-fail", "current-changed"]
)
def test_prune_preserves_in_flight_versions(
    tmp_path: Path, monkeypatch, mode: str
) -> None:
    old = "c" * 40
    young = "d" * 40
    installer = INSTALLER.read_text().replace(
        "# Replace current via",
        f'mkdir "$VERSIONS_DIR/{old}" "$VERSIONS_DIR/{young}"\n'
        "python3 -c 'import os, sys; os.utime(sys.argv[1], (0, 0))' "
        f'"$VERSIONS_DIR/{old}"\n# Replace current via',
    )
    if mode == "configs-fail":
        monkeypatch.setenv("TOKENAGE_FAKE_CONFIGS_FAIL", "1")
    elif mode == "find-fail":
        installer = installer.replace(
            'find "./$version" -maxdepth 0 -mmin -60', "false"
        )
    elif mode == "current-changed":
        installer = installer.replace(
            "  prune_versions ||",
            f'  rm "$TRACKER_HOME/current"\n'
            f'  ln -s "versions/{old}/" "$TRACKER_HOME/current"\n'
            "  prune_versions ||",
        )
    result, home, _ = _run_install(tmp_path, installer_text=installer)
    assert result.returncode == 0, result.stdout + result.stderr
    versions = home / ".tokenage" / "versions"
    assert (versions / young).is_dir()
    assert (versions / old).exists() == (mode != "normal")
    assert (home / ".tokenage" / "current").resolve().is_dir()
    assert ("Removed 1 old client version(s)." in result.stdout) == (mode == "normal")
    assert ("could not clean up" in result.stderr) == (mode == "configs-fail")


def test_preview_uses_pinned_commit_without_looking_up_main(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TOKENAGE_FAKE_API_FAIL", "1")
    result, home, _ = _run_install(
        tmp_path,
        sha=NEXT_COMMIT,
        installer_text=_render_installer(commit=NEXT_COMMIT),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".tokenage" / "current").resolve() == (
        home / ".tokenage" / "versions" / NEXT_COMMIT
    )
    assert "Resolving the latest client source" not in result.stdout


def test_launcher_runs_the_current_snapshot_and_failed_sha_keeps_it(
    tmp_path: Path,
) -> None:
    result, home, fake_bin = _run_install(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    tracker_home = home / ".tokenage"
    config = tracker_home / "config.yaml"
    credentials = tracker_home / "credentials.json"

    launcher = home / ".local" / "bin" / "tokenage"
    # The shared launcher, copied out of the snapshot — not a private shim.
    assert launcher.read_text() == SHARED_LAUNCHER.read_text()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755

    launcher_env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
    }
    # The fake interpreter exists only in the snapshot's own virtualenv, so a
    # logged call proves the launcher resolved `current` and used its client.
    run_cli = subprocess.run(
        [str(launcher), "check-server", "--server", "https://launcher.example"],
        text=True,
        capture_output=True,
        env=launcher_env,
        timeout=10,
    )
    assert run_cli.returncode == 0, run_cli.stderr
    assert (
        "-m client check-server --server https://launcher.example tty=no"
        in (tmp_path / "python.log").read_text()
    )
    assert config.read_text() == "provider: keep\n"
    assert credentials.read_text() == '{"token":"keep"}\n'

    invalid_script = tmp_path / "bad-installer.sh"
    invalid_script.write_text(_render_installer())
    env = {
        **launcher_env,
        "TOKENAGE_SERVER": "https://host.example",
    }
    # The fake API response contains a short SHA; the installer must reject it before switching.
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        f"args=sys.argv[1:]; pathlib.Path(args[args.index('-o')+1]).write_text({json.dumps({'sha': 'bad'})!r})\n"
    )
    curl.chmod(0o755)
    previous = (tracker_home / "current").resolve()
    failed = subprocess.run(
        ["/bin/sh", str(invalid_script)],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert failed.returncode != 0
    assert "source commit SHA is invalid" in failed.stderr
    assert (tracker_home / "current").resolve() == previous
    assert config.read_text() == "provider: keep\n"
    assert credentials.read_text() == '{"token":"keep"}\n'


def test_reinstall_checks_protocol_before_replacing_current_symlink(
    tmp_path: Path,
) -> None:
    first, home, fake_bin = _run_install(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    tracker_home = home / ".tokenage"
    current = tracker_home / "current"
    original_version = current.resolve()
    stale = tracker_home / "versions" / "stale"
    stale.mkdir()
    _age_directory(stale)

    next_source = tmp_path / "source" / f"tokenage-{NEXT_COMMIT}"
    _write_snapshot(next_source)
    next_archive = tmp_path / "next-snapshot.tar.gz"
    with tarfile.open(next_archive, "w:gz") as tar:
        tar.add(next_source, arcname=next_source.name)
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "url = next(arg for arg in args if arg.startswith('https://'))\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "with open(os.environ.get('TOKENAGE_FAKE_UV_LOG', str(out.parent / 'curl')) + '.requests', 'a') as log: log.write(url + '\\n')\n"
        "if 'api.github.com' in url:\n"
        f"    out.write_text({json.dumps({'sha': NEXT_COMMIT})!r})\n"
        "else:\n"
        "    out.write_bytes(pathlib.Path(os.environ['TOKENAGE_FIXTURE_ARCHIVE']).read_bytes())\n"
    )
    curl.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FIXTURE_ARCHIVE": str(next_archive),
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
        "TOKENAGE_FAKE_PROTOCOL_FAIL": "1",
    }
    command = ["/bin/sh", str(tmp_path / "install.sh")]
    incompatible = subprocess.run(
        command, text=True, capture_output=True, env=env, timeout=30
    )
    assert incompatible.returncode != 0
    assert current.resolve() == original_version
    assert stale.is_dir()

    env.pop("TOKENAGE_FAKE_PROTOCOL_FAIL")
    updated = subprocess.run(
        command, text=True, capture_output=True, env=env, timeout=30
    )
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert current.is_symlink()
    assert current.resolve() == tracker_home / "versions" / NEXT_COMMIT
    assert original_version.is_dir()
    assert not stale.exists()
    assert not list(original_version.glob(".current-*"))
    assert (tracker_home / "config.yaml").read_text() == "provider: keep\n"
    assert (tracker_home / "credentials.json").read_text() == '{"token":"keep"}\n'


def test_refuses_to_overwrite_a_foreign_launcher(tmp_path: Path) -> None:
    home, fake_bin, _ = _fixture(tmp_path)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / "tokenage"
    foreign.write_text("#!/bin/sh\nexit 0\n")
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())
    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "TOKENAGE_HOME": str(home / ".tokenage"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "TOKENAGE_FIXTURE_ARCHIVE": str(tmp_path / "snapshot.tar.gz"),
            "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
            "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
        },
        timeout=10,
    )
    assert result.returncode != 0
    assert "already exists" in result.stderr
    # Refused before anything was written: the other installation still works.
    assert foreign.read_text() == "#!/bin/sh\nexit 0\n"
    assert not (home / ".tokenage" / "current").exists()
    assert not (tmp_path / "uv.log").exists()


def test_existing_current_directory_fails_before_installing_launcher(
    tmp_path: Path,
) -> None:
    home, fake_bin, _ = _fixture(tmp_path)
    (home / ".tokenage" / "current").mkdir()
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())
    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "TOKENAGE_HOME": str(home / ".tokenage"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
        timeout=10,
    )
    assert result.returncode != 0
    assert "not a managed link" in result.stderr
    assert not (home / ".local" / "bin" / "tokenage").exists()
    assert not (tmp_path / "uv.log").exists()


def test_login_reads_code_from_tty_when_installer_runs_from_pipe(
    tmp_path: Path,
) -> None:
    home, fake_bin, archive = _fixture(tmp_path)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
    }
    # Enter keeps the component and server defaults.
    output = _run_in_terminal(script, env, b"\n\nbrowser-code-1234\n")
    log = (tmp_path / "python.log").read_text()
    assert "-m client login --server https://host.example tty=yes" in log
    assert log.index("client client restart --if-running") < log.index("client login")
    assert "login-input=browser-code-1234" in log
    assert b"No interactive terminal is available" not in output


def _run_in_terminal(
    script: Path,
    env: dict,
    typed: bytes | list[tuple[bytes, bytes]],
    *args: str,
    shell: tuple[str, ...] = ("/bin/sh",),
    expected_returncode: int | None = None,
) -> bytes:
    """Run the installer as `curl | sh` does: stdin is the script, a tty is there.

    ``typed`` is written up front, or as (wait for this output, then type) steps.
    """
    pid, terminal = pty.fork()
    if pid == 0:
        os.execve(
            "/bin/sh",
            [
                "/bin/sh",
                "-c",
                'trap ":" INT; script=$1; shift; cat "$script" | "$@"',
                "sh",
                str(script),
                *shell,
                "-s",
                "--",
                *args,
            ],
            env,
        )
    steps = [(b"", typed)] if isinstance(typed, bytes) else list(typed)
    output = bytearray()
    seen = 0  # only output after the last marker can satisfy the next step
    deadline = time.monotonic() + 30
    status = None
    while time.monotonic() < deadline:
        if steps and (found := output.find(steps[0][0], seen)) >= 0:
            seen = found + len(steps[0][0])
            os.write(terminal, steps.pop(0)[1])
        ready, _, _ = select.select([terminal], [], [], 0.2)
        if ready:
            try:
                output.extend(os.read(terminal, 4096))
            except OSError:
                break
        finished, child_status = os.waitpid(pid, os.WNOHANG)
        if finished:
            status = child_status
            break
    else:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        raise AssertionError(f"installer did not finish; output: {bytes(output)!r}")
    os.close(terminal)
    if status is None:
        _, status = os.waitpid(pid, 0)
    if expected_returncode is not None:
        assert os.waitstatus_to_exitcode(status) == expected_returncode, bytes(output)
    return bytes(output)


@pytest.mark.parametrize("restart_fails", [False, True])
def test_updater_skip_login_mode_preserves_existing_credentials(
    tmp_path: Path,
    restart_fails: bool,
) -> None:
    home, fake_bin, archive = _fixture(tmp_path)
    stale = home / ".tokenage" / "versions" / "stale"
    stale.mkdir(parents=True)
    _age_directory(stale)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
        "TOKENAGE_SKIP_LOGIN": "1",
        "TOKENAGE_FAKE_RESTART_FAIL": "1" if restart_fails else "0",
    }

    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == (1 if restart_fails else 0), (
        result.stdout + result.stderr
    )
    log = (tmp_path / "python.log").read_text()
    assert "-m client check-server --server https://host.example" in log
    assert "-P -m client setup" in log
    assert "-P -m client client restart --if-running" in log
    assert stale.exists() == restart_fails
    if restart_fails:
        assert "service restart failed" in result.stderr
        return
    assert "login" not in log
    assert "Existing credentials were preserved; sign-in skipped." in result.stdout
    assert (home / ".tokenage" / "credentials.json").read_text() == (
        '{"token":"keep"}\n'
    )


def test_updater_reports_agent_configuration_refresh_failure(tmp_path: Path) -> None:
    home, fake_bin, archive = _fixture(tmp_path)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer())

    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "TOKENAGE_HOME": str(home / ".tokenage"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
            "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
            "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
            "TOKENAGE_SKIP_LOGIN": "1",
            "TOKENAGE_FAKE_SETUP_FAIL": "1",
        },
        timeout=30,
    )

    assert result.returncode != 0
    assert "agent configuration refresh failed" in result.stderr
    log = (tmp_path / "python.log").read_text()
    assert log.index("client client restart --if-running") < log.index("client setup")
    log = (tmp_path / "python.log").read_text()
    assert "-P -m client setup" in log
    assert "login" not in log
    assert (home / ".tokenage" / "credentials.json").read_text() == (
        '{"token":"keep"}\n'
    )


def test_rejects_insecure_server_url_before_install(tmp_path: Path) -> None:
    home, fake_bin, _ = _fixture(tmp_path)
    script = tmp_path / "install.sh"
    script.write_text(_render_installer(server_url="http://remote.example"))
    result = subprocess.run(
        ["/bin/sh", str(script)],
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"},
        timeout=10,
    )
    assert result.returncode != 0
    assert "must use HTTPS" in result.stderr


def _both_fixture(tmp_path: Path, *, api_port: int = 4123, server_commit: str = COMMIT):
    """A fake server clone, bootstrap and git next to the client fixtures."""
    home, fake_bin, archive = _fixture(tmp_path)
    if server_commit != COMMIT:
        source = tmp_path / "source" / f"tokenage-{server_commit}"
        _write_snapshot(source)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(source, arcname=source.name)
    src = home / ".tokenage" / "src"
    (src / ".git").mkdir(parents=True)
    (src / "src" / "scripts").mkdir(parents=True)
    (src / "src" / "scripts" / "bootstrap.sh").write_text(
        "#!/bin/bash\n"
        'echo "bootstrap $*" >> "$TOKENAGE_FAKE_PYTHON_LOG.server"\n'
        f'printf "server:\\n  api_port: {api_port}\\n" > "$HOME/.tokenage/config.yaml"\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'ln -sf "$HOME/.tokenage/src/client/bin/tokenage" "$HOME/.local/bin/tokenage"\n'
    )
    (src / "client" / "bin").mkdir(parents=True)
    (src / "client" / "bin" / "tokenage").write_text(SHARED_LAUNCHER.read_text())
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"remote get-url"*) echo https://github.com/Haannbboo/tokenage.git ;;\n'
        f"  *rev-parse*) echo {server_commit} ;;\n"
        "esac\n"
    )
    git.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "TOKENAGE_HOME": str(home / ".tokenage"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TOKENAGE_FIXTURE_ARCHIVE": str(archive),
        "TOKENAGE_FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "TOKENAGE_FAKE_PYTHON_LOG": str(tmp_path / "python.log"),
    }
    return home, env


def _run_raw(tmp_path: Path, env: dict, *args: str):
    return subprocess.run(
        ["/bin/sh", str(INSTALLER), *args],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )


@pytest.mark.parametrize("flags", [[], ["--yes"]])
def test_unattended_default_installs_both_locally(
    tmp_path: Path, flags: list[str]
) -> None:
    _, env = _both_fixture(tmp_path)

    result = _run_raw(tmp_path, env, *flags)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "app.tokenage.dev" not in result.stdout
    assert "What do you want to install?" not in result.stdout
    assert (tmp_path / "python.log.server").exists()
    log = (tmp_path / "python.log").read_text()
    assert "check-server --server http://127.0.0.1:4123" in log
    assert "tokenage login --server http://127.0.0.1:4123" in result.stdout


def test_unattended_client_requires_server_url(tmp_path):
    _, env = _both_fixture(tmp_path)
    result = _run_raw(tmp_path, env, "--client")
    assert result.returncode == 1
    assert "no server URL: set TOKENAGE_SERVER=<url>" in result.stderr
    assert not (tmp_path / "uv.log.requests").exists()


# The fake uv cannot run the shared prompts, so these are the numbered fallback.
@pytest.mark.parametrize(
    "args,typed,server,questions",
    [
        # Enter keeps both defaults: the client, on the hosted server.
        ((), b"\n\n", "https://app.tokenage.dev", 2),
        ((), b"4\n1\nhttps://mine.example/\n", "https://mine.example", 2),
        ((), b"1\nHTTPS://mine.example/\n", "https://mine.example", 2),
        ((), b"3\n", "http://127.0.0.1:4123", 1),
        (("--client",), b"\n", "https://app.tokenage.dev", 1),
        (("--yes",), b"", "http://127.0.0.1:4123", 0),
        (("--server", "--client"), b"", "http://127.0.0.1:4123", 0),
    ],
)
def test_terminal_install_asks_what_flags_leave_open(
    tmp_path: Path, args, typed, server, questions
) -> None:
    _, env = _both_fixture(tmp_path)

    output = _run_in_terminal(INSTALLER, env, typed + b"code\n", *args)

    asked = [b"What do you want to install?" in output, b"Server URL [" in output]
    assert sum(asked) == questions, output.decode(errors="replace")
    if questions:
        assert (
            output.count(
                b"note: shared installer prompt failed; falling back to numbered questions"
            )
            == 1
        )
        assert b"Traceback" not in output
    log = (tmp_path / "python.log").read_text()
    assert f"-m client login --server {server} tty=yes" in log
    assert "login-input=code" in log
    assert (tmp_path / "python.log.server").exists() == server.startswith("http://")


def test_update_never_asks_even_with_a_terminal(tmp_path: Path) -> None:
    home, env = _both_fixture(tmp_path)
    env["TOKENAGE_SKIP_LOGIN"] = "1"
    env["TOKENAGE_SERVER"] = "https://host.example"

    output = _run_in_terminal(INSTALLER, env, b"")

    assert b"What do you want to install?" not in output
    assert b"Server URL [" not in output
    log = (tmp_path / "python.log").read_text()
    assert "-P -m client setup" in log
    assert "login" not in log


def test_both_flags_install_the_server_then_a_client_pointed_at_it(
    tmp_path: Path,
) -> None:
    home, env = _both_fixture(tmp_path)

    result = _run_raw(tmp_path, env, "--server", "--client")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "python.log.server").read_text() == "bootstrap \n"
    tracker_home = home / ".tokenage"
    # The client has its own snapshot, and the launcher no longer points into the
    # server clone.
    assert (tracker_home / "current").resolve() == tracker_home / "versions" / COMMIT
    launcher = home / ".local" / "bin" / "tokenage"
    assert not launcher.is_symlink()
    log = (tmp_path / "python.log").read_text()
    assert "check-server --server http://127.0.0.1:4123" in log
    assert "No interactive terminal is available" in result.stdout
    assert "tokenage login --server http://127.0.0.1:4123" in result.stdout
    assert "-P -m client client start" in log


def test_server_only_installs_no_client(tmp_path: Path) -> None:
    home, env = _both_fixture(tmp_path)

    result = _run_raw(tmp_path, env, "--server")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "python.log.server").exists()
    assert not (home / ".tokenage" / "current").exists()
    assert not (tmp_path / "uv.log").exists()
    assert (home / ".local" / "bin" / "tokenage").is_symlink()


@pytest.mark.parametrize("flags", [["--client"], ["--server"], []])
def test_unknown_option_is_refused_before_any_install(
    tmp_path: Path, flags: list[str]
) -> None:
    _, env = _both_fixture(tmp_path)

    result = _run_raw(tmp_path, env, *flags, "--bogus")

    assert result.returncode != 0
    assert "unknown option: --bogus" in result.stderr


def test_update_env_pins_commit_without_looking_up_main(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENAGE_INSTALL_COMMIT", NEXT_COMMIT)
    monkeypatch.setenv("TOKENAGE_FAKE_API_FAIL", "1")
    result, home, _ = _run_install(tmp_path, sha=NEXT_COMMIT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".tokenage" / "current").resolve().name == NEXT_COMMIT


@pytest.mark.parametrize(
    "steps,server",
    [
        # Enter keeps both defaults: the client, on the hosted server.
        (
            [(b"What do you want to install?", b"\r"), (b"Server URL", b"\r")],
            "https://app.tokenage.dev",
        ),
        (
            [
                (b"What do you want to install?", b"\r"),
                (b"Server URL", b"\x01\x0bhttps://mine.example/\r"),
            ],
            "https://mine.example",
        ),
        (
            [(b"What do you want to install?", b"\x1b[B\x1b[B\r")],
            "http://127.0.0.1:4123",
        ),
    ],
)
def test_terminal_install_asks_with_the_shared_prompts(tmp_path: Path, steps, server):
    pytest.importorskip("prompt_toolkit")
    both = server.startswith("http://")
    client_commit = NEXT_COMMIT if both else COMMIT
    home, env = _both_fixture(tmp_path, server_commit=client_commit)
    env["TOKENAGE_FAKE_UV_PYTHON"] = sys.executable
    env["TOKENAGE_PYTHON_VERSION"] = "3.12"
    env["PROMPT_TOOLKIT_NO_CPR"] = "1"

    output = _run_in_terminal(
        INSTALLER, env, [*steps, (b"Installing: client", b"code\n")]
    )

    assert b"Answer 1, 2 or 3" not in output and b"Server URL [" not in output
    assert "◇  What do you want to install?".encode() in output
    assert (
        b"Preparing the installer (first run downloads Python and prompt_toolkit)"
        in output
    )
    assert (
        "run --quiet --no-project --python 3.12 " in (tmp_path / "uv.log").read_text()
    )
    assert (tmp_path / "uv.log.prompts").read_text() == (
        f"https://raw.githubusercontent.com/Haannbboo/tokenage/{COMMIT}/client/prompts.py"
    )
    requests = (tmp_path / "uv.log.requests").read_text().splitlines()
    assert sum("api.github.com" in url for url in requests) == 1
    assert [url for url in requests if url.endswith(".tar.gz")] == [
        f"https://github.com/Haannbboo/tokenage/archive/{client_commit}.tar.gz"
    ]
    assert (home / ".tokenage" / "current").resolve().name == client_commit
    log = (tmp_path / "python.log").read_text()
    assert f"-m client login --server {server} tty=yes" in log
    assert "login-input=code" in log
    assert (tmp_path / "python.log.server").exists() == both


def test_ctrl_c_in_the_shared_prompts_cancels_the_install(tmp_path: Path) -> None:
    pytest.importorskip("prompt_toolkit")
    home, env = _both_fixture(tmp_path)
    env["TOKENAGE_FAKE_UV_PYTHON"] = sys.executable
    env["PROMPT_TOOLKIT_NO_CPR"] = "1"

    output = _run_in_terminal(
        INSTALLER,
        env,
        [(b"What do you want to install?", b"\x03")],
        expected_returncode=130,
    )

    assert b"Answer 1, 2 or 3" not in output
    assert b"Installing:" not in output
    assert not (tmp_path / "python.log").exists()


@pytest.mark.parametrize("skip", ["1", "true", "tRUE", "yes", "yEs"])
def test_skip_login_uses_one_boolean_parser(tmp_path, skip):
    _, env = _both_fixture(tmp_path)
    env["TOKENAGE_SKIP_LOGIN"] = skip
    env["TOKENAGE_SERVER"] = "https://host.example"
    result = _run_raw(tmp_path, env, "--client")
    assert result.returncode == 0, result.stderr
    log = (tmp_path / "python.log").read_text()
    assert "-m client setup" in log
    assert "-m client login" not in log


def test_mixed_case_no_prompt_skips_questions_with_a_terminal(tmp_path):
    _, env = _both_fixture(tmp_path)
    env["TOKENAGE_NO_PROMPT"] = "tRUE"
    output = _run_in_terminal(INSTALLER, env, b"code\n", expected_returncode=0)
    assert b"What do you want to install?" not in output
    assert (
        "login --server http://127.0.0.1:4123" in (tmp_path / "python.log").read_text()
    )


def test_numbered_server_prompt_reasks_mistyped_url(tmp_path):
    _, env = _both_fixture(tmp_path)
    output = _run_in_terminal(
        INSTALLER,
        env,
        [
            (b"What do you want to install?", b"1\n"),
            (b"Server URL [", b"app.example.com\n"),
            (b"Enter an HTTPS URL", b"https://user:pass@mine.example\n"),
            (b"Enter an HTTPS URL", b"http://LOCALHOST:4123\n"),
            (b"Installing: client", b"code\n"),
        ],
    )
    assert b"error:" not in output
    assert (
        "login --server http://LOCALHOST:4123" in (tmp_path / "python.log").read_text()
    )


@pytest.mark.parametrize("question", ["component", "server"])
@pytest.mark.parametrize("shell", [("sh",), ("bash", "--posix"), ("busybox", "sh")])
def test_numbered_prompt_cancels_on_eof(tmp_path, question, shell):
    executable = shutil.which(shell[0])
    if executable is None:
        pytest.skip(f"{shell[0]} is not installed")
    home, env = _both_fixture(tmp_path)
    if question == "server":
        # An invalid default used to loop forever when EOF became an empty
        # answer and the installer kept trying that same invalid URL.
        env["TOKENAGE_SERVER"] = "http://remote.example"
        args = ("--client",)
        marker = b"Server URL ["
    else:
        args = ()
        marker = b"> "
    output = _run_in_terminal(
        INSTALLER, env, [(marker, b"\x04")], *args, shell=(executable, *shell[1:])
    )
    assert b"installer input closed; installation cancelled" in output
    assert b"Installing:" not in output
    assert not (home / ".tokenage/current").exists()


@pytest.mark.parametrize("shell", [("dash",), ("bash", "--posix"), ("busybox", "sh")])
def test_numbered_prompt_ctrl_c_exits_130(tmp_path, shell):
    executable = shutil.which(shell[0])
    if executable is None:
        pytest.skip(f"{shell[0]} is not installed")
    _, env = _both_fixture(tmp_path)
    output = _run_in_terminal(
        INSTALLER,
        env,
        [(b"> ", b"\x03")],
        shell=(executable, *shell[1:]),
        expected_returncode=130,
    )
    assert b"Installing:" not in output


@pytest.mark.parametrize(
    "url,normalized",
    [
        ("HTTPS://host.example", "https://host.example"),
        ("HTTP://localhost:4567", "http://localhost:4567"),
        ("HTTP://LOCALHOST:4567", "http://LOCALHOST:4567"),
        ("HTTP://[::1]:4567", "http://[::1]:4567"),
    ],
)
def test_install_accepts_uppercase_server_scheme(tmp_path, url, normalized):
    _, env = _both_fixture(tmp_path)
    env["TOKENAGE_SERVER"] = url
    result = _run_raw(tmp_path, env, "--client", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"check-server --server {normalized}" in (tmp_path / "python.log").read_text()
    )


def test_shared_prompt_failure_prints_only_a_note_before_fallback(tmp_path):
    _, env = _both_fixture(tmp_path)
    uv = Path(env["PATH"].split(":")[0]) / "uv"
    uv.write_text(
        uv.read_text().replace(
            "args = sys.argv[1:]\n",
            "args = sys.argv[1:]\nif args[0] == 'run':\n    print('Traceback: noisy prompt failure\\nsecond error line', file=sys.stderr)\n    raise SystemExit(1)\n",
        )
    )
    output = _run_in_terminal(INSTALLER, env, b"1\n\ncode\n")
    assert (
        output.count(
            b"note: shared installer prompt failed; falling back to numbered questions"
        )
        == 1
    )
    assert b"Traceback" not in output
    assert b"second error line" not in output
    assert "login-input=code" in (tmp_path / "python.log").read_text()


@pytest.mark.parametrize(
    "url", ["https://user:pass@mine.example", "https://@mine.example"]
)
def test_unattended_installer_rejects_server_userinfo(tmp_path, url):
    _, env = _both_fixture(tmp_path)
    env["TOKENAGE_SERVER"] = url
    result = _run_raw(tmp_path, env, "--client")
    assert result.returncode == 1
    assert "without userinfo" in result.stderr
    assert url not in result.stderr
    assert not (tmp_path / "python.log").exists()


@pytest.mark.parametrize(
    "answers",
    [
        "",
        "\nhttps://mine.example\n",
        "client\n",
        "client\n\n",
        "client\nhttps\n",
        "client\nhttps://mine.example:bad\n",
        "client\nhttps://user@mine.example\n",
        "both\n",
        "server\ninvalid\n",
        "invalid\nhttps://mine.example\n",
    ],
)
def test_invalid_shared_answers_fall_back_to_numbered_questions(tmp_path, answers):
    _, env = _both_fixture(tmp_path)
    uv = Path(env["PATH"].split(":")[0]) / "uv"
    uv.write_text(
        uv.read_text().replace(
            "args = sys.argv[1:]\n",
            "args = sys.argv[1:]\n"
            "if args[0] == 'run':\n"
            f"    pathlib.Path(args[args.index('install') + 1]).write_text({answers!r})\n"
            "    raise SystemExit(0)\n",
        )
    )
    output = _run_in_terminal(
        INSTALLER,
        env,
        [
            (b"What do you want to install?", b"1\n"),
            (b"Server URL [", b"https://fallback.example\n"),
            (b"Installing: client", b"code\n"),
        ],
        expected_returncode=0,
    )
    assert b"falling back to numbered questions" in output
    assert (
        "login --server https://fallback.example"
        in (tmp_path / "python.log").read_text()
    )
    assert not (tmp_path / "python.log.server").exists()
