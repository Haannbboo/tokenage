from __future__ import annotations

import io
import json
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from client import auth, cli, update

SCRIPT = "#!/bin/sh\nREPOSITORY=fork/tracker\nBRANCH=${TOKENAGE_BRANCH:-stable}\n"


@pytest.fixture(autouse=True)
def setup_update(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENAGE_INSTALL_COMMIT", raising=False)
    monkeypatch.delenv("TOKENAGE_BRANCH", raising=False)
    monkeypatch.setattr(update, "client_root", lambda: tmp_path)
    monkeypatch.setattr(update, "client_version", lambda: "0.1.20")
    monkeypatch.setattr(update, "client_commit", lambda: "a" * 40)
    monkeypatch.setattr(
        auth, "load_credentials", lambda: {"server_url": "https://host.test"}
    )
    monkeypatch.setattr(update.shutil, "which", lambda _: "/usr/bin/curl")
    calls = []

    def fake_run(command, env=None):
        calls.append((command, env))
        if command[0] == "/usr/bin/curl":
            Path(command[-1]).write_text(SCRIPT)
        else:
            (tmp_path / "client").mkdir(exist_ok=True)
            (tmp_path / "client" / "COMMIT").write_text(
                env.get("TOKENAGE_INSTALL_COMMIT", "c" * 40)
            )
        return 0

    monkeypatch.setattr(update, "_run", fake_run)

    def fake_open(url, *, timeout):
        return io.BytesIO(
            json.dumps({"sha": "b" * 40}).encode()
            if "api.github.com" in url
            else b"0.1.21\n"
        )

    monkeypatch.setattr(update, "urlopen", fake_open)
    return calls


def test_check_downloads_installer_and_uses_its_source(
    setup_update, monkeypatch, capsys
):
    urls = []

    def fake_open(url, *, timeout):
        urls.append(url)
        return io.BytesIO(
            b'{"sha":"' + b"b" * 40 + b'"}' if "api.github.com" in url else b"0.1.21"
        )

    monkeypatch.setattr(update, "urlopen", fake_open)
    assert update.run_update(check=True, dry_run=False) == 0
    assert len(setup_update) == 1
    assert urls == [
        "https://api.github.com/repos/fork/tracker/commits/stable",
        "https://raw.githubusercontent.com/fork/tracker/"
        + "b" * 40
        + "/client/VERSION",
    ]
    assert "Client update available" in capsys.readouterr().out


def test_branch_override(monkeypatch, tmp_path):
    script = tmp_path / "install.sh"
    script.write_text(SCRIPT)
    monkeypatch.setenv("TOKENAGE_BRANCH", "feature/client")
    assert update._installer_source(script) == ("fork/tracker", "feature/client")
    urls = []
    monkeypatch.setattr(
        update,
        "urlopen",
        lambda url, **kw: (
            urls.append(url)
            or io.BytesIO(
                b'{"sha":"' + b"b" * 40 + b'"}' if len(urls) == 1 else b"0.1.21"
            )
        ),
    )
    update._resolve_target(*update._installer_source(script))
    assert urls[0].endswith("/feature%2Fclient")


@pytest.mark.parametrize("check", [False, True])
def test_installer_parse_failure(tmp_path, check, capsys, setup_update):
    script = tmp_path / "bad.sh"
    script.write_text("#!/bin/sh\n")
    assert update._update_from_installer(
        script, "https://host.test", None, check, False
    ) == int(check)
    assert "invalid response" in capsys.readouterr().err
    assert len(setup_update) == (0 if check else 1)
    if not check:
        assert "TOKENAGE_INSTALL_COMMIT" not in setup_update[0][1]


def test_update_runs_pin_and_verifies_snapshot(setup_update, capsys, monkeypatch):
    monkeypatch.setenv("TOKENAGE_CLIENT_COMMIT", "a" * 40)
    assert update.run_update(check=False, dry_run=False) == 0
    env = setup_update[1][1]
    assert env["TOKENAGE_INSTALL_COMMIT"] == "b" * 40
    assert env["TOKENAGE_SKIP_LOGIN"] == "1"
    assert env["TOKENAGE_SERVER"] == "https://host.test"
    assert "ignored the pin" not in capsys.readouterr().err


def test_ignored_pin_warning(monkeypatch, tmp_path, setup_update, capsys):
    original = update._run

    def ignored(command, env=None):
        result = original(command, env)
        if command[0] == "sh":
            (tmp_path / "client" / "COMMIT").write_text("c" * 40)
        return result

    monkeypatch.setattr(update, "_run", ignored)
    assert update.run_update(check=False, dry_run=False) == 0
    error = capsys.readouterr().err
    assert "bbbbbbb" in error and "ccccccc" in error
    assert "the server's installer ignored the pin; update the server" in error


@pytest.mark.parametrize(
    "error,reason",
    [
        (URLError("secret DNS body"), "offline/DNS"),
        (URLError(ssl.SSLCertVerificationError("secret")), "TLS certificate"),
        (
            HTTPError("https://github.test", 403, "secret", {}, None),
            "GitHub rate limit (HTTP 403)",
        ),
        (
            HTTPError("https://github.test", 429, "secret", {}, None),
            "GitHub rate limit (HTTP 429)",
        ),
        (
            HTTPError("https://github.test", 404, "secret", {}, None),
            "not found (HTTP 404)",
        ),
        (ValueError("secret response"), "invalid response"),
    ],
)
@pytest.mark.parametrize("check", [False, True])
def test_resolve_failure(error, reason, check, monkeypatch, capsys, setup_update):
    def fail(*args):
        raise error

    monkeypatch.setattr(update, "_resolve_target", fail)
    assert update.run_update(check=check, dry_run=False) == int(check)
    output = capsys.readouterr()
    assert reason in output.err and "secret" not in output.err
    assert len(setup_update) == (1 if check else 2)


@pytest.mark.parametrize("version", ["0.1.22", "0.2.0", "0.10.0"])
def test_skip_downgrade(version, monkeypatch, setup_update, capsys):
    monkeypatch.setattr(update, "client_version", lambda: version)
    assert update.run_update(check=False, dry_run=False) == 0
    assert len(setup_update) == 1
    assert (
        "Installed client is newer than stable; not downgrading"
        in capsys.readouterr().out
    )


def test_already_current(monkeypatch, setup_update, capsys):
    monkeypatch.setattr(update, "client_commit", lambda: "b" * 40)
    assert update.run_update(check=False, dry_run=False) == 0
    assert len(setup_update) == 1
    assert "already up to date" in capsys.readouterr().out


def test_dry_run_prose(setup_update, capsys):
    assert update.run_update(check=False, dry_run=True) == 0
    assert len(setup_update) == 1
    assert (
        "Would install client 0.1.21 (commit bbbbbbb) using the installer from https://host.test"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("fail", [False, True])
def test_user_pin_preserved(fail, monkeypatch, setup_update, capsys):
    monkeypatch.setenv("TOKENAGE_INSTALL_COMMIT", "d" * 40)
    urls = []

    def fake_open(url, **kw):
        urls.append(url)
        if fail:
            raise URLError("secret")
        return io.BytesIO(b"0.1.22")

    monkeypatch.setattr(update, "urlopen", fake_open)
    assert update.run_update(check=False, dry_run=False) == 0
    assert urls == [
        "https://raw.githubusercontent.com/fork/tracker/" + "d" * 40 + "/client/VERSION"
    ]
    assert setup_update[1][1]["TOKENAGE_INSTALL_COMMIT"] == "d" * 40
    assert update.os.environ["TOKENAGE_INSTALL_COMMIT"] == "d" * 40
    if not fail:
        assert "Latest client     0.1.22  (commit ddddddd)" in capsys.readouterr().out


def test_invalid_user_pin(monkeypatch, setup_update):
    monkeypatch.setenv("TOKENAGE_INSTALL_COMMIT", "bad")
    assert update.run_update(check=False, dry_run=False) == 1
    assert len(setup_update) == 1


@pytest.mark.parametrize(
    "sha,version",
    [("bad", b"0.1.21"), ("b" * 40, b"bad"), ("b" * 40, "١.٢.٣".encode())],
)
def test_invalid_target_metadata(sha, version, monkeypatch):
    monkeypatch.setattr(
        update,
        "urlopen",
        lambda url, **kw: io.BytesIO(
            json.dumps({"sha": sha}).encode() if "api.github.com" in url else version
        ),
    )
    with pytest.raises(ValueError):
        update._resolve_target("fork/tracker", "stable")


def test_no_snapshot(monkeypatch, capsys):
    monkeypatch.setattr(update, "client_root", lambda: None)
    assert update.run_update(check=False, dry_run=False) == 0
    assert "tokenage server update" in capsys.readouterr().out


def test_invalid_server_url(monkeypatch):
    for stored in ("http://evil.example", "not a url", None, ""):
        monkeypatch.setattr(auth, "load_credentials", lambda: {"server_url": stored})
        assert update.update_client() == 1


def test_parser_rejects_plugin_rebuild():
    with pytest.raises(SystemExit):
        cli._build_subcommand("update").parse_args(["--rebuild-plugins"])
