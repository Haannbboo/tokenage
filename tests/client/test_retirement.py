"""Retirement cleanup never reads agent transcripts or delays usage tracking."""

import signal
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from client import retirement, service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path))
    return tmp_path


def test_signed_out_cleanup_preserves_settings_permissions_and_native_files(home):
    settings = {
        "proxy": {"providers": {"local": {"api_key": "secret"}}, "port": 1234},
        "tracking": {"enabled": True},
        "evaluation": {"auto_enabled": True},
    }
    config = home / "client.yaml"
    config.write_text(yaml.safe_dump(settings))
    config.chmod(0o640)
    credentials = home / "credentials.json"
    credentials.write_bytes(b'{"token":"secret"}')
    native = home / "native-transcript.jsonl"
    native.write_bytes(b"native data")
    results = home / "evaluations"
    results.mkdir()
    (results / "broken.json").write_bytes(b"\xffnot json")
    (results / "native-link").symlink_to(native)
    assert retirement.cleanup_once()
    assert not results.exists()
    del settings["evaluation"]
    assert yaml.safe_load(config.read_text()) == settings
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert credentials.read_bytes() == b'{"token":"secret"}'
    assert native.read_bytes() == b"native data"


def test_results_symlink_only_removes_link(home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("native")
    native = outside / "session.jsonl"
    native.write_text("untouched")
    (home / "evaluations").symlink_to(outside, target_is_directory=True)
    assert retirement.cleanup_once()
    assert not (home / "evaluations").is_symlink()
    assert native.read_text() == "untouched"


@pytest.mark.parametrize("content", [b"evaluation: [", b"\xff", b"[]", b"null"])
def test_malformed_config_left_in_place(home, content):
    config = home / "client.yaml"
    config.write_bytes(content)
    assert not retirement.cleanup_once()
    assert config.read_bytes() == content


def test_config_symlink_is_not_followed(home):
    target = home / "other.yaml"
    target.write_text("evaluation: {}")
    (home / "client.yaml").symlink_to(target)
    assert not retirement.cleanup_once()
    assert target.read_text() == "evaluation: {}"


@pytest.mark.parametrize(
    "content, expected",
    [
        (
            b"# comment\napi_key: no\nevaluation:\n  enabled: true\nother: 1\n",
            b"# comment\napi_key: no\nother: 1\n",
        ),
        (
            b"nested:\n  evaluation: true\nevaluation: {}\n",
            b"nested:\n  evaluation: true\n",
        ),
        (b"api_key: no\r\nevaluation:\r\n  enabled: true\r\n", b"api_key: no\r\n"),
    ],
)
def test_config_edit_preserves_other_bytes(home, content, expected):
    config = home / "client.yaml"
    config.write_bytes(content)
    assert retirement.cleanup_once()
    assert config.read_bytes() == expected


def test_no_evaluation_does_not_write(home):
    config = home / "client.yaml"
    config.write_text("nested:\n  evaluation: true\n")
    inode = config.stat().st_ino
    assert retirement.cleanup_once()
    assert config.stat().st_ino == inode


def test_retry_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(retirement, "RETRY_SECONDS", 0)
    monkeypatch.setattr(retirement, "cleanup_once", lambda: calls.append(1) or False)
    thread = threading.Event()
    monkeypatch.setattr(
        retirement.threading,
        "Thread",
        lambda target, **kw: SimpleNamespace(start=lambda: (target(), thread.set())),
    )
    retirement.start_cleanup()
    assert thread.is_set() and len(calls) == retirement.MAX_ATTEMPTS


def test_service_tracks_while_cleanup_waits_and_starts_no_evaluator(home, monkeypatch):
    from client.proxy import runtime

    started = threading.Event()
    release = threading.Event()

    def waiting_cleanup():
        started.set()
        release.wait(5)
        return False

    monkeypatch.setattr(retirement, "cleanup_once", waiting_cleanup)
    handlers = {}
    monkeypatch.setattr(service.signal, "signal", lambda n, h: handlers.update({n: h}))
    monkeypatch.setattr(service.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr(
        runtime, "start_proxy", lambda: SimpleNamespace(stop=lambda: None)
    )
    # Any process launch or HTTP request on the new service path is a failure.
    monkeypatch.setattr(
        service.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected process")
    )
    monkeypatch.setattr(
        service.httpx, "post", lambda *a, **kw: pytest.fail("unexpected request")
    )
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        assert not any(p in path.parts for p in (".codex", ".claude", "sessions"))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    checks = []

    def check(state):
        assert started.wait(1)
        checks.append(True)
        handlers[signal.SIGTERM](None, None)

    monkeypatch.setattr(service, "_check_once", check)
    try:
        assert service.run_foreground() == 0
        assert checks
    finally:
        release.set()


@pytest.mark.parametrize("running, restart_code", [(True, 0), (True, 1), (False, 0)])
def test_installer_hook_restarts_only_running_services(
    home, monkeypatch, running, restart_code
):
    from client import cli

    events = []
    monkeypatch.setattr(service, "_manager", lambda: "fake")
    monkeypatch.setattr(service, "_manager_active", lambda manager: running)
    monkeypatch.setattr(
        service, "restart", lambda: events.append("restart") or restart_code
    )
    monkeypatch.setattr(
        retirement, "cleanup_once", lambda: events.append("cleanup") or False
    )
    assert (
        cli._run_subcommand("client", ["client", "restart", "--if-running"])
        == restart_code
    )
    assert events == (["restart"] if running else ["cleanup"])


def test_invalid_config_stops_without_retry(home, monkeypatch, caplog):
    (home / "client.yaml").write_text("evaluation: [")
    monkeypatch.setattr(retirement, "RETRY_SECONDS", 0)
    monkeypatch.setattr(
        retirement.threading,
        "Thread",
        lambda target, **kw: SimpleNamespace(start=target),
    )
    retirement.start_cleanup()
    assert len(caplog.records) == 1
    assert "cleanup stopped" in caplog.text
