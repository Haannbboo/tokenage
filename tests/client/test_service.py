"""The per-device client service and its health payload.

These are the tests that used to live in ``tests/test_local_setup_health.py``:
agent detection and wiring now belong to the client, not the server.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from client import service, setup
from protocol.device_status import DeviceStatusReport

REPO_ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "http://localhost:4001/otlp/v1/logs"


@pytest.fixture
def device_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A device home with a recorded collector and no detected agents."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOKENAGE_HOME", str(home / ".tokenage"))
    monkeypatch.setenv("TOKENAGE_CLIENT_COMMIT", "a" * 40)
    monkeypatch.delenv("TOKENAGE_ROOT", raising=False)
    monkeypatch.delenv("TOKENAGE_SERVER_ROOT", raising=False)
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    _write_json(
        home / ".tokenage" / "credentials.json",
        {"otlp_logs_endpoint": ENDPOINT},
    )
    return home


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data) -> None:
    _write(path, json.dumps(data))


def _counts(payload: dict) -> tuple[int, int]:
    """(configured, matching) agent counts."""
    agents = payload["agents"].values()
    return (
        sum(1 for agent in agents if agent["configured"]),
        sum(1 for agent in agents if agent["endpoint_matches"] is True),
    )


def _claude_env(endpoint: str | None, **extra: str) -> str:
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "API_KEY": "super-secret",
        **extra,
    }
    if endpoint is not None:
        env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = endpoint
    return json.dumps({"env": env})


def test_health_reports_agent_otlp_config_status(
    device_home: Path,
) -> None:
    _write(device_home / ".claude" / "settings.json", _claude_env(ENDPOINT))
    _write(
        device_home / ".codex" / "config.toml",
        """[otel]
enabled = true
[otel.exporter.otlp-http]
endpoint = "http://localhost:9999/v1/logs"
protocol = "json"
api_key = "super-secret"
""",
    )

    payload = service.health_payload()

    assert len(payload["agents"]) == 4
    assert _counts(payload) == (2, 1)
    assert payload["agents"]["claude"]["expected_endpoint"] == ENDPOINT

    agents = payload["agents"]
    assert agents["claude"]["configured"] is True
    assert agents["claude"]["endpoint_matches"] is True
    assert agents["claude"]["configured_endpoint"] == ENDPOINT
    assert agents["codex"]["configured"] is True
    assert agents["codex"]["endpoint_matches"] is False
    assert agents["codex"]["configured_endpoint"] == "http://localhost:9999/v1/logs"
    assert agents["kilo"]["status"] == "missing_config"
    assert agents["kilo"]["configured"] is False
    assert agents["kilo"]["configured_endpoint"] is None
    assert agents["kilo"]["endpoint_matches"] is False
    assert "gemini" not in agents

    text = json.dumps(payload)
    assert "super-secret" not in text
    assert "api_key" not in text.lower()


def test_health_accepts_codex_otlp_http_enabled(device_home: Path) -> None:
    _write(
        device_home / ".codex" / "config.toml",
        """[otel]
environment = "dev"
[otel.exporter]
[otel.exporter.otlp-http]
endpoint = "http://localhost:4001/otlp/v1/logs"
enabled = true
api_key = "super-secret"
""",
    )

    payload = service.health_payload()

    assert _counts(payload) == (1, 1)
    assert payload["agents"]["codex"]["configured"] is True
    assert payload["agents"]["codex"]["endpoint_matches"] is True
    assert payload["agents"]["codex"]["configured_endpoint"] == ENDPOINT
    assert "super-secret" not in json.dumps(payload)


def test_health_accepts_codex_endpoint_only_otlp_http_config(
    device_home: Path,
) -> None:
    _write(
        device_home / ".codex" / "config.toml",
        """[otel]
environment = "dev"

[otel.exporter]
[otel.exporter.otlp-http]
endpoint = "http://localhost:4001/otlp/v1/logs"
protocol = "json"

[plugins."superpowers@openai-curated"]
enabled = true
""",
    )

    payload = service.health_payload()

    assert _counts(payload) == (1, 1)
    assert payload["agents"]["codex"]["configured"] is True
    assert payload["agents"]["codex"]["endpoint_matches"] is True


def test_health_rejects_codex_otel_explicit_false(device_home: Path) -> None:
    _write(
        device_home / ".codex" / "config.toml",
        """[otel]
enabled = false

[otel.exporter.otlp-http]
endpoint = "http://localhost:4001/otlp/v1/logs"
protocol = "json"
""",
    )

    payload = service.health_payload()

    assert _counts(payload) == (0, 0)
    assert payload["agents"]["codex"]["configured"] is False
    assert payload["agents"]["codex"]["endpoint_matches"] is False
    assert payload["agents"]["codex"]["configured_endpoint"] == ENDPOINT
    assert payload["agents"]["codex"]["status"] == "missing_config"


def test_health_rejects_codex_otlp_http_explicit_false(device_home: Path) -> None:
    _write(
        device_home / ".codex" / "config.toml",
        """[otel]
environment = "dev"

[otel.exporter.otlp-http]
enabled = false
endpoint = "http://localhost:4001/otlp/v1/logs"
protocol = "json"
""",
    )

    payload = service.health_payload()

    assert _counts(payload) == (0, 0)
    assert payload["agents"]["codex"]["configured"] is False
    assert payload["agents"]["codex"]["endpoint_matches"] is False
    assert payload["agents"]["codex"]["status"] == "missing_config"


def test_health_payload_round_trips_the_protocol_model(device_home: Path) -> None:
    payload = service.health_payload()

    report = DeviceStatusReport.model_validate(payload)

    assert report.model_dump(mode="json") == payload
    assert "installation_key" not in payload


def test_health_handles_missing_agent_configs(device_home: Path) -> None:
    payload = service.health_payload()

    assert len(payload["agents"]) == 4
    assert _counts(payload) == (0, 0)
    for agent in payload["agents"].values():
        assert agent["configured"] is False
        assert agent["endpoint_matches"] is False
        assert agent["configured_endpoint"] is None
        assert agent["status"] == "missing_config"
    assert payload["agents"]["kilo"]["expected_endpoint"] == ENDPOINT


def test_health_detects_claude_endpoint_without_protocol_key(
    device_home: Path,
) -> None:
    _write(
        device_home / ".claude" / "settings.json",
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": ENDPOINT,
                }
            }
        ),
    )
    payload = service.health_payload()
    assert payload["agents"]["claude"]["status"] == "ready"


def test_health_opencode_ready(device_home: Path) -> None:
    config_path = device_home / ".config" / "opencode" / "opencode.json"
    _write_json(
        config_path,
        {
            "plugin": [
                [
                    "/some/path/plugins/opencode/dist/index.js",
                    {"endpoint": ENDPOINT},
                ]
            ]
        },
    )

    agent = service.health_payload()["agents"]["opencode"]

    assert agent["status"] == "ready"
    assert agent["configured"] is True
    assert agent["endpoint_matches"] is True


def test_health_opencode_missing(device_home: Path) -> None:
    agent = service.health_payload()["agents"]["opencode"]
    assert agent["status"] == "missing_config"
    assert agent["configured"] is False


def test_health_opencode_wrong_endpoint(device_home: Path) -> None:
    config_path = device_home / ".config" / "opencode" / "opencode.json"
    _write_json(
        config_path,
        {
            "plugin": [
                [
                    "/some/path/plugins/opencode/dist/index.js",
                    {"endpoint": "http://localhost:9999/v1/logs"},
                ]
            ]
        },
    )

    agent = service.health_payload()["agents"]["opencode"]

    assert agent["status"] == "wrong_endpoint"
    assert agent["configured"] is True
    assert agent["endpoint_matches"] is False


def test_health_opencode_bare_plugin_uses_plugin_default_endpoint(
    device_home: Path,
) -> None:
    expected = "http://localhost:4102/v1/logs"
    _write_json(
        device_home / ".tokenage" / "credentials.json",
        {"otlp_logs_endpoint": expected},
    )
    config_path = device_home / ".config" / "opencode" / "opencode.json"
    _write_json(config_path, {"plugin": ["/some/path/plugins/opencode/dist/index.js"]})

    agent = service.health_payload()["agents"]["opencode"]

    assert agent["status"] == "wrong_endpoint"
    assert agent["configured"] is True
    assert agent["endpoint_matches"] is False
    assert agent["configured_endpoint"] == "http://localhost:4005/v1/logs"
    assert agent["expected_endpoint"] == expected


def test_health_opencode_ignores_kilo_plugin(device_home: Path) -> None:
    config_path = device_home / ".config" / "opencode" / "opencode.json"
    _write_json(
        config_path,
        {
            "plugin": [
                [
                    "/some/path/plugins/kilo/dist/index.js",
                    {"endpoint": ENDPOINT},
                ]
            ]
        },
    )

    agent = service.health_payload()["agents"]["opencode"]

    assert agent["status"] == "missing_config"
    assert agent["configured"] is False
    assert agent["endpoint_matches"] is False
    assert agent["configured_endpoint"] is None


def test_health_kilo_ready(device_home: Path) -> None:
    config_path = device_home / ".config" / "kilo" / "opencode.json"
    _write_json(
        config_path,
        {"plugin": [["/some/path/plugins/kilo/dist/index.js", {"endpoint": ENDPOINT}]]},
    )

    agent = service.health_payload()["agents"]["kilo"]

    assert agent["status"] == "ready"
    assert agent["configured"] is True
    assert agent["endpoint_matches"] is True
    assert agent["configured_endpoint"] == ENDPOINT


def test_health_kilo_wrong_endpoint(device_home: Path) -> None:
    config_path = device_home / ".config" / "kilo" / "opencode.json"
    _write_json(
        config_path,
        {
            "plugin": [
                [
                    "/some/path/plugins/kilo/dist/index.js",
                    {"endpoint": "http://localhost:9999/v1/logs"},
                ]
            ]
        },
    )

    agent = service.health_payload()["agents"]["kilo"]

    assert agent["status"] == "wrong_endpoint"
    assert agent["configured"] is True
    assert agent["endpoint_matches"] is False
    assert agent["configured_endpoint"] == "http://localhost:9999/v1/logs"


# --------------------------------------------------------------- service lifecycle


class FakeManager:
    """Records the systemctl/launchctl calls; ``fail`` makes matching ones exit 1."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd):
        self.calls.append(cmd)
        code = 1 if any(word in cmd for word in self.fail) else 0
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")


@pytest.fixture
def managed(device_home: Path, monkeypatch: pytest.MonkeyPatch):
    def install(manager: str | None, **kwargs) -> FakeManager:
        fake = FakeManager(**kwargs)
        monkeypatch.setattr(service, "_run", fake)
        monkeypatch.setattr(service, "_manager", lambda: manager)
        return fake

    launcher = device_home / "bin" / "tokenage"
    _write(launcher, "#!/bin/sh\n")
    monkeypatch.setattr(service, "_launcher", lambda: launcher)
    monkeypatch.setenv("TOKENAGE_ROOT", "/dev/checkout")
    monkeypatch.setenv("PATH", "/home/u/.local/bin:/usr/bin")
    monkeypatch.setattr(service, "PROXY_WAIT_SECONDS", 0)
    install.launcher = launcher
    return install


def test_systemd_start_writes_unit_and_enables(managed, device_home, capsys) -> None:
    fake = managed("systemd")
    assert service.start() == 0
    unit = service.unit_path().read_text()
    assert f'ExecStart="{managed.launcher}" client run' in unit
    assert f'Environment="TOKENAGE_HOME={device_home / ".tokenage"}"' in unit
    assert 'Environment="TOKENAGE_ROOT=/dev/checkout"' in unit
    # The manager's bare PATH would hide agents in ~/.local/bin.
    assert 'Environment="PATH=/home/u/.local/bin:/usr/bin"' in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit
    assert fake.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", service.UNIT_NAME],
        ["systemctl", "--user", "restart", service.UNIT_NAME],
    ]
    assert "started" in capsys.readouterr().out


def test_systemd_start_is_idempotent_when_unit_unchanged(managed) -> None:
    fake = managed("systemd")
    service.start()
    fake.calls.clear()
    assert service.start() == 0
    assert fake.calls == [["systemctl", "--user", "enable", "--now", service.UNIT_NAME]]
    fake.calls.clear()
    assert service.restart() == 0
    assert fake.calls[-1] == ["systemctl", "--user", "restart", service.UNIT_NAME]


def test_systemd_stop_disables_and_tolerates_missing_unit(managed) -> None:
    fake = managed("systemd", fail=("disable",))
    assert service.stop() == 0
    assert fake.calls == [
        ["systemctl", "--user", "disable", "--now", service.UNIT_NAME]
    ]
    _write(service.unit_path(), "x")
    assert service.stop() == 1


def test_launchd_start_writes_plist_and_bootstraps(managed) -> None:
    import plistlib

    fake = managed("launchd", fail=("print",))
    assert service.start() == 0
    plist = plistlib.loads(service.plist_path().read_bytes())
    assert plist["Label"] == service.LAUNCHD_LABEL
    assert plist["ProgramArguments"] == [str(managed.launcher), "client", "run"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["EnvironmentVariables"]["TOKENAGE_ROOT"] == "/dev/checkout"
    assert plist["StandardOutPath"] == str(service.log_path())
    domain = f"gui/{os.getuid()}"
    assert fake.calls[-1] == [
        "launchctl",
        "bootstrap",
        domain,
        str(service.plist_path()),
    ]
    assert ["launchctl", "enable", f"{domain}/{service.LAUNCHD_LABEL}"] in fake.calls


def test_launchd_stop_boots_out_and_disables(managed) -> None:
    fake = managed("launchd")
    assert service.stop() == 0
    target = f"gui/{os.getuid()}/{service.LAUNCHD_LABEL}"
    assert fake.calls == [
        ["launchctl", "bootout", target],
        ["launchctl", "disable", target],
    ]


def test_unsupported_platform_tells_the_user_to_run_it_themselves(
    managed, capsys
) -> None:
    fake = managed(None)
    assert service.start() == 1
    assert service.stop() == 1
    assert fake.calls == []
    assert "tokenage client run" in capsys.readouterr().err


def test_start_without_launcher_fails_clearly(managed, monkeypatch, capsys) -> None:
    managed("systemd")
    monkeypatch.setattr(service, "_launcher", lambda: None)
    assert service.start() == 1
    assert "launcher" in capsys.readouterr().err
    assert not service.unit_path().exists()


def test_start_failure_reports_the_command(managed, capsys) -> None:
    managed("systemd", fail=("enable",))
    assert service.start() == 1
    assert "enable --now" in capsys.readouterr().err


def test_manager_requires_a_working_systemctl_user(monkeypatch) -> None:
    monkeypatch.setattr(service.sys, "platform", "linux")
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/systemctl")
    for code, expected in ((0, "systemd"), (1, None)):
        monkeypatch.setattr(
            service,
            "_run",
            lambda cmd, code=code: subprocess.CompletedProcess(cmd, code, "", ""),
        )
        assert service._manager() == expected
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    assert service._manager() is None
    monkeypatch.setattr(service.sys, "platform", "darwin")
    assert service._manager() == "launchd"


def test_status_reports_the_manager_and_last_report(managed, capsys) -> None:
    fake = managed("systemd")
    assert service.run_status(as_json=True) == 0
    assert fake.calls == [
        ["systemctl", "--user", "is-active", "--quiet", service.UNIT_NAME]
    ]
    _write_json(
        service.state_path(),
        {"last_check_at": 2, "last_report_at": 3, "last_report_status": "ok"},
    )
    managed("systemd", fail=("is-active",))
    assert service.run_status(as_json=True) == 1
    data = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert data["running"] is False
    assert data["manager"] == "systemd"
    assert data["last_report_at"] == 3
    assert data["last_report_status"] == "ok"


def test_run_foreground_daemon_writes_state_and_stops_on_sigterm(
    device_home: Path,
) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "TOKENAGE_HOME": str(device_home / ".tokenage"),
        "HOME": str(device_home),
        "TOKENAGE_CLIENT_COMMIT": "a" * 40,
    }
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "client", "client", "run"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not service._read_state().get(
            "last_check_at"
        ):
            time.sleep(0.05)
        assert service._read_state()["last_check_at"] is not None
        process.terminate()
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


# ------------------------------------------------------------------- reporting


class FakePost:
    def __init__(self, *, status_code: int = 204, error: Exception | None = None):
        self.calls: list[dict] = []
        self.status_code = status_code
        self.error = error

    def __call__(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status_code, request=httpx.Request("POST", url))


def _signed_in(device_home: Path) -> None:
    _write_json(
        device_home / ".tokenage" / "credentials.json",
        {
            "server_url": "https://srv.example",
            "cli_token": "cli-token",
            "otlp_logs_endpoint": ENDPOINT,
        },
    )


def test_report_signed_in_posts_with_bearer(device_home: Path, monkeypatch) -> None:
    _signed_in(device_home)
    fake = FakePost()
    monkeypatch.setattr(service.httpx, "post", fake)
    state = {"last_report_at": None, "last_report_status": None}

    service._report_once(state, service.health_payload())

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://srv.example/devices/status"
    assert call["headers"] == {"Authorization": "Bearer cli-token"}
    assert call["timeout"] == service.REPORT_TIMEOUT_SECONDS
    assert call["json"]["device_name"]
    assert "installation_key" not in call["json"]
    assert state["last_report_status"] == "ok"
    assert isinstance(state["last_report_at"], int)


def test_report_unsigned_client_with_local_server_makes_no_request(
    device_home: Path, monkeypatch, tmp_path
) -> None:
    server_root = tmp_path / "server"
    (server_root / ".venv" / "bin").mkdir(parents=True)
    (server_root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    monkeypatch.setenv("TOKENAGE_ROOT", str(server_root))
    fake = FakePost()
    monkeypatch.setattr(service.httpx, "post", fake)
    state = {"last_report_at": None, "last_report_status": None}

    service._report_once(state, service.health_payload())

    assert fake.calls == []


def test_report_without_server_makes_no_request(device_home: Path, monkeypatch) -> None:
    fake = FakePost()
    monkeypatch.setattr(service.httpx, "post", fake)
    state = {"last_report_at": None, "last_report_status": None}

    service._report_once(state, service.health_payload())

    assert fake.calls == []
    assert state == {"last_report_at": None, "last_report_status": None}


@pytest.mark.parametrize(
    "fake",
    [
        FakePost(error=httpx.ConnectError("down")),
        FakePost(status_code=500),
    ],
    ids=["connection-error", "http-500"],
)
def test_report_failure_marks_state_and_the_loop_continues(
    device_home: Path, monkeypatch, fake: FakePost
) -> None:
    _signed_in(device_home)
    monkeypatch.setattr(service.httpx, "post", fake)
    state = {"last_check_at": None, "last_report_at": None, "last_report_status": None}

    service._check_once(state)

    assert state["last_report_status"] == "failed"
    assert state["last_report_at"] is None
    assert state["last_check_at"] is not None


def test_kilo_outside_path_is_detected_and_wired(device_home: Path) -> None:
    # The background service never sources the shell rc that adds ~/.kilo/bin.
    _write(device_home / ".kilo" / "bin" / "kilo", "")

    assert setup.installed_agents() == ["kilo"]
    assert service.health_payload()["detected"]["kilo"] == {"found": True}


# ------------------------------------------------------------------- local proxy


def _record(state: dict, pid: int | None = None) -> None:
    from client.proxy import runtime

    runtime.record_proxy_state(state)
    if pid is not None:  # pretend another process wrote it
        data = service._read_state()
        data["proxy"]["pid"] = pid
        _write_json(service.state_path(), data)


RUNNING = {"state": "running", "port": 4000, "url": "http://127.0.0.1:4000"}
FAILED = {"state": "failed", "error": "port 4000 (from client.yaml) is in use"}


def test_foreground_starts_proxy_and_stops_it_on_shutdown(
    device_home: Path, monkeypatch
) -> None:
    from client.proxy import runtime

    events: list[str] = []

    class Handle:
        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(
        runtime, "start_proxy", lambda: events.append("start") or Handle()
    )
    handlers: dict = {}
    monkeypatch.setattr(service.signal, "signal", lambda n, h: handlers.update({n: h}))

    def check(state):
        events.append("check")
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(service, "_check_once", check)
    monkeypatch.setattr(service.logging, "basicConfig", lambda **kw: None)
    assert service.run_foreground() == 0
    assert events[0] == "start" and "check" in events and events[-1] == "stop"


def test_foreground_starts_and_stops_the_evaluation_worker(
    device_home: Path, monkeypatch
) -> None:
    from client import evaluation
    from client.proxy import runtime

    events: list[str] = []

    class Handle:
        def stop(self) -> None:
            events.append("proxy-stop")

    class Worker:
        def __init__(self) -> None:
            events.append("eval-new")

        def start(self) -> None:
            events.append("eval-start")

        def stop(self) -> None:
            events.append("eval-stop")

    monkeypatch.setattr(
        runtime, "start_proxy", lambda: events.append("proxy-start") or Handle()
    )
    monkeypatch.setattr(evaluation, "EvaluationWorker", Worker)
    handlers: dict = {}
    monkeypatch.setattr(service.signal, "signal", lambda n, h: handlers.update({n: h}))

    def check(state):
        events.append("check")
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(service, "_check_once", check)
    monkeypatch.setattr(service.logging, "basicConfig", lambda **kw: None)
    assert service.run_foreground() == 0
    assert events[0] == "proxy-start"
    assert "eval-start" in events and "check" in events
    assert events[-2:] == ["eval-stop", "proxy-stop"]


def test_service_keeps_running_when_proxy_cannot_bind(device_home: Path) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    _write(
        device_home / ".tokenage" / "client.yaml",
        f"proxy:\n  port: {port}\n  providers: {{}}\n",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "TOKENAGE_HOME": str(device_home / ".tokenage"),
        "HOME": str(device_home),
        "TOKENAGE_CLIENT_COMMIT": "a" * 40,
    }
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "client", "client", "run"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not service._read_state().get(
            "last_check_at"
        ):
            time.sleep(0.05)
        state = service._read_state()
        assert state["last_check_at"] is not None  # still reporting
        assert state["proxy"]["state"] == "failed"
        assert state["proxy"]["pid"] == process.pid
        assert process.poll() is None
        process.terminate()
        assert process.wait(timeout=10) == 0
    finally:
        blocker.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def test_client_status_shows_proxy_only_while_the_service_is_alive(
    managed, capsys
) -> None:
    _record(RUNNING)
    managed("systemd")
    assert service.run_status(as_json=False) == 0
    assert "  proxy       http://127.0.0.1:4000\n" in capsys.readouterr().out
    assert service.service_status()["proxy"]["url"] == RUNNING["url"]

    managed("systemd", fail=("is-active",))  # service stopped: record is stale
    assert service.service_status()["proxy"] is None

    _record(RUNNING, pid=2**22 + 12345)  # active unit, but dead writer
    managed("systemd")
    assert service.service_status()["proxy"] is None


def test_client_status_shows_failure_and_hides_disabled(managed, capsys) -> None:
    managed("systemd")
    _record(FAILED)
    service.run_status(as_json=False)
    assert (
        "  proxy       failed: port 4000 (from client.yaml) is in use"
        in capsys.readouterr().out
    )
    _record({"state": "disabled"})
    service.run_status(as_json=False)
    assert "proxy" not in capsys.readouterr().out


def test_client_start_prints_proxy_outcome(managed, capsys) -> None:
    managed("systemd")
    _record(RUNNING)
    assert service.start() == 0
    assert "  proxy     http://127.0.0.1:4000" in capsys.readouterr().out
    _record(FAILED)
    service.restart()
    assert "  proxy     failed: port 4000" in capsys.readouterr().out
    _record({"state": "disabled"})
    service.start()
    assert "  proxy " not in capsys.readouterr().out
    _record(RUNNING, pid=2**22 + 12345)  # old service's record: not shown
    service.start()
    assert "  proxy " not in capsys.readouterr().out
