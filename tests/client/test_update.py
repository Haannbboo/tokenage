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
    (tmp_path / "client").mkdir(exist_ok=True)
    (tmp_path / "client/COMMIT").write_text("a" * 40)
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
            "HTTP 403",
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


def test_already_current(tmp_path, setup_update, capsys):
    (tmp_path / "client/COMMIT").write_text("b" * 40)
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


def test_empty_user_pin_treated_as_unset(monkeypatch, setup_update, capsys):
    monkeypatch.setenv("TOKENAGE_INSTALL_COMMIT", "")
    assert update.run_update(check=False, dry_run=False) == 0
    assert setup_update[1][1]["TOKENAGE_INSTALL_COMMIT"] == "b" * 40
    assert "Latest client     0.1.21  (commit bbbbbbb)" in capsys.readouterr().out


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


@pytest.mark.parametrize("installed_version", ["0.1.30", "0.1.28"])
@pytest.mark.parametrize("installed_commit", ["a" * 40, "b" * 40])
def test_explicit_version_installs_tag(
    setup_update, monkeypatch, tmp_path, installed_version, installed_commit
):
    monkeypatch.setattr(update, "client_version", lambda: installed_version)
    (tmp_path / "client/COMMIT").write_text(installed_commit)
    urls = []

    def fake_open(url, **kw):
        urls.append(url)
        return io.BytesIO(
            json.dumps({"sha": "b" * 40}).encode()
            if "api.github.com" in url
            else b"0.1.28"
        )

    monkeypatch.setattr(update, "urlopen", fake_open)
    monkeypatch.setenv("TOKENAGE_INSTALL_COMMIT", "d" * 40)
    assert update.run_update(check=False, dry_run=False, version="0.1.28") == 0
    assert urls[0].endswith("/commits/refs/tags/client-v0.1.28")
    assert len(urls) == 2  # One tag lookup and one VERSION read.
    assert "TOKENAGE_CLIENT_VERSION" not in setup_update[1][1]
    assert setup_update[1][1]["TOKENAGE_INSTALL_COMMIT"] == "b" * 40


@pytest.mark.parametrize("status", [404, 422])
@pytest.mark.parametrize("version", ["0.1.28", "0.1.26", "0.0.999"])
def test_unknown_explicit_version_never_runs_installer(
    setup_update, monkeypatch, capsys, status, version
):
    def missing(*args, **kw):
        raise HTTPError("https://github.test", status, "private", {}, None)

    monkeypatch.setattr(update, "urlopen", missing)
    assert update.run_update(check=False, dry_run=False, version=version) == 1
    assert len(setup_update) == 1
    assert f"client version {version} not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "versions,expected",
    [
        (["0.1.28", "0.1.30", "0.1.100", "bad"], ["0.1.100", "0.1.30", "0.1.28"]),
        (["0.1.26", "0.1.28", "0.2.0"], ["0.2.0", "0.1.28", "0.1.26"]),
    ],
)
def test_list_numeric_order_in_one_request(monkeypatch, versions, expected):
    urls = []

    def tags(url, **kw):
        urls.append(url)
        return io.BytesIO(
            json.dumps(
                [{"ref": f"refs/tags/client-v{v}"} for v in versions]
                + [{"ref": "refs/tags/v1.0.0"}]
            ).encode()
        )

    monkeypatch.setattr(update, "urlopen", tags)
    assert update._list_versions("fork/tracker") == expected
    assert urls == [
        "https://api.github.com/repos/fork/tracker/git/matching-refs/tags/client-v"
    ]


@pytest.mark.parametrize("tty,yes", [(False, False), (True, False), (True, True)])
def test_list_prints_or_picks(tty, yes, monkeypatch, setup_update, capsys):
    monkeypatch.setattr(update, "client_version", lambda: "0.1.30")
    monkeypatch.setattr(
        update,
        "_resolve_target",
        lambda *args: (
            ("0.1.30", "a" * 40) if args[1].endswith("0.1.30") else ("0.1.28", "b" * 40)
        ),
    )
    monkeypatch.setattr(
        update, "_list_versions", lambda *args: ["0.1.100", "0.1.30", "0.1.28"]
    )
    monkeypatch.setattr(update.prompts, "is_terminal", lambda stream: tty)
    monkeypatch.delenv("TOKENAGE_NO_PROMPT", raising=False)
    monkeypatch.setenv("TOKENAGE_YES", "1" if yes else "0")

    def pick(title, options, default):
        assert options[1] == ("0.1.30", "client-v0.1.30")
        assert default == "0.1.30"
        return "0.1.28"

    monkeypatch.setattr(update.prompts, "select", pick)
    assert update.run_update(check=False, dry_run=False, list_versions=True) == 0
    if tty and not yes:
        assert "TOKENAGE_CLIENT_VERSION" not in setup_update[1][1]
    else:
        output = capsys.readouterr().out
        assert (
            output.index("client-v0.1.100")
            < output.index("client-v0.1.30")
            < output.index("client-v0.1.28")
        )
        assert len(setup_update) == 1


def test_plain_update_never_prompts(setup_update, monkeypatch):
    def unexpected(*args, **kw):
        pytest.fail("plain update prompted")

    monkeypatch.setattr(update.prompts, "select", unexpected)
    assert update.run_update(check=False, dry_run=False) == 0


def test_version_flags_parse():
    parsed = cli._build_subcommand("update").parse_args(["--version", "0.1.28"])
    assert parsed.version == "0.1.28"
    with pytest.raises(SystemExit):
        cli._build_subcommand("update").parse_args(["--list", "--version", "0.1.28"])


@pytest.mark.parametrize("version", ["", "0.1.28\n", "0.1.28\nbad"])
def test_unselectable_version_never_resolves_or_installs(
    version, setup_update, monkeypatch
):
    def unexpected(*args, **kwargs):
        pytest.fail("unselectable version reached GitHub")

    monkeypatch.setattr(update, "urlopen", unexpected)
    assert update.run_update(check=False, dry_run=False, version=version) == 1
    assert setup_update == []


def test_picker_cancel_is_clean(monkeypatch, setup_update, capsys):
    monkeypatch.setattr(update, "_list_versions", lambda *args: ["0.1.28"])
    monkeypatch.setattr(update.prompts, "interactive", lambda **kw: True)

    def cancel(*args, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(update.prompts, "select", cancel)
    assert update.run_update(check=False, dry_run=False, list_versions=True) == 130
    assert "cancelled" in capsys.readouterr().err
    assert len(setup_update) == 1


def test_mismatched_tag_never_installs(setup_update, capsys):
    assert update.run_update(check=False, dry_run=False, version="0.1.28") == 1
    assert len(setup_update) == 1
    error = capsys.readouterr().err
    assert "client tag does not match its VERSION" in error
    assert "invalid response" not in error


@pytest.mark.parametrize("active_version", ["0.1.30", "0.1.28"])
@pytest.mark.parametrize("active_commit", ["a" * 40, "b" * 40])
@pytest.mark.parametrize("explicit_version", [False, True])
def test_active_snapshot_drives_switch_and_downgrade_guard(
    tmp_path,
    monkeypatch,
    setup_update,
    capsys,
    explicit_version,
    active_commit,
    active_version,
):
    (tmp_path / "client").mkdir(exist_ok=True)
    (tmp_path / "client/VERSION").write_text(active_version + "\n")
    (tmp_path / "client/COMMIT").write_text(active_commit)
    monkeypatch.setattr(update, "client_commit", lambda: "c" * 40)
    monkeypatch.setattr(update, "_resolve_target", lambda *args: ("0.1.28", "b" * 40))
    assert (
        update.run_update(
            check=True, dry_run=False, version="0.1.28" if explicit_version else None
        )
        == 0
    )
    output = capsys.readouterr().out
    assert f"Installed client  {active_version}" in output
    if explicit_version:
        assert "Switching client" not in output
        assert "Selected client available" in output
    elif active_version == "0.1.30":
        assert "not downgrading" in output
        assert "Client update available" not in output
    elif active_commit == "b" * 40:
        assert "already up to date" in output
    else:
        assert "Client update available" in output
    assert len(setup_update) == 1


@pytest.mark.parametrize("closed", [False, True])
def test_list_with_missing_or_closed_stdin(closed, monkeypatch, setup_update, capsys):
    import sys

    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr(sys, "stdin", stream if closed else None)
    monkeypatch.setattr(update, "_list_versions", lambda *args: ["0.1.28"])
    assert update.run_update(check=False, dry_run=False, list_versions=True) == 0
    assert "client-v0.1.28" in capsys.readouterr().out
    assert len(setup_update) == 1


def test_rate_limit_needs_zero_remaining():
    error = HTTPError(
        "https://github.test", 403, "Forbidden", {"x-ratelimit-remaining": "0"}, None
    )
    assert update._failure_reason(error) == "GitHub rate limit (HTTP 403)"


def test_invalid_version_precedes_failing_download(monkeypatch, capsys):
    calls = []

    def failed_download(command, env=None):
        calls.append(command)
        return 1

    monkeypatch.setattr(update, "_run", failed_download)
    assert update.run_update(check=False, dry_run=False, version="bad") == 1
    assert (
        "invalid client version; expected MAJOR.MINOR.PATCH" in capsys.readouterr().err
    )
    assert calls == []


def test_installed_summary_uses_active_snapshot_commit(tmp_path, monkeypatch, capsys):
    (tmp_path / "client/VERSION").write_text("0.1.28\n")
    (tmp_path / "client/COMMIT").write_text("b" * 40)
    monkeypatch.setattr(update, "client_commit", lambda: "c" * 40)
    monkeypatch.setattr(update, "update_client", lambda **kwargs: 0)

    assert update.run_update(check=True, dry_run=False) == 0
    assert "Installed client  0.1.28  (commit bbbbbbb)" in capsys.readouterr().out
