"""Sign-in and credentials.

The server is a fake httpx (interface-compatible: .get for pre-flight, .post
for the exchange); stdin is fed via monkeypatched builtins.input. Agent wiring
lives in client.setup and is exercised through the same entry point.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from client import auth, paths, setup, track
from client import cli as client_cli
from protocol import CURRENT_GENERATION

EXCHANGE_PAYLOAD = {
    "user": {"id": "u1", "email": "a@example.com", "name": "Alice"},
    "device_name": "testhost",
    "cli_token": "tokenage_cli abcdef",
    "ingest_token": "tokenage_ingest abcdef",
    "otlp": {
        "endpoint": "https://api.example.com:4005",
        "logs_endpoint": "https://api.example.com:4005/v1/logs",
    },
}


@pytest.mark.parametrize(
    "command",
    [["status"], ["logout"], ["login"], ["setup"], ["update"], ["echo", "hi"]],
)
@pytest.mark.parametrize("content", ["{secret-token", "[]", "null"])
def test_corrupt_credentials_are_reported_without_a_traceback(
    command, content, capsys, monkeypatch
):
    monkeypatch.delenv("TOKENAGE_SERVER", raising=False)
    monkeypatch.setattr(setup, "installed_agents", lambda: ["codex"])
    monkeypatch.setattr(client_cli.update, "client_root", lambda: paths.tracker_home())
    path = paths.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    assert client_cli.main(command) == (2 if command == ["login"] else 1)
    output = capsys.readouterr()
    assert "JSON object" in output.err
    assert "Traceback" not in output.err
    assert "secret-token" not in output.err
    assert path.read_text() == content


@pytest.fixture(autouse=True)
def client_home(tmp_path, monkeypatch):
    """Credentials and agent config never touch real $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path / "tracker"))


@pytest.fixture(autouse=True)
def no_client_daemon(monkeypatch):
    """A successful login starts the daemon; tests must never spawn processes."""
    from client import service

    monkeypatch.setattr(service, "start", lambda: 0)


class FakeHttpx:
    """The two httpx calls login makes, faked. auth.httpx keeps the real module."""

    def __init__(
        self,
        *,
        exchange_status=200,
        exchange_payload=None,
        get_error=None,
        protocol_min=None,
        protocol_max=None,
    ):
        self.calls = []
        self.post_index = 0
        self.exchange_statuses = (
            list(exchange_status)
            if isinstance(exchange_status, (list, tuple))
            else [exchange_status]
        )
        self.exchange_payload = exchange_payload or EXCHANGE_PAYLOAD
        self.get_error = get_error
        self.protocol_min = CURRENT_GENERATION if protocol_min is None else protocol_min
        self.protocol_max = CURRENT_GENERATION if protocol_max is None else protocol_max

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        if self.get_error is not None:
            raise self.get_error
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "protocol_min": self.protocol_min,
                "protocol_max": self.protocol_max,
            },
        )

    def post(self, url, json=None, **kwargs):
        self.calls.append(("POST", url, dict(json or {})))
        status = self.exchange_statuses[
            min(self.post_index, len(self.exchange_statuses) - 1)
        ]
        self.post_index += 1
        return SimpleNamespace(status_code=status, json=lambda: self.exchange_payload)


def _install_fake_httpx(monkeypatch, fake: FakeHttpx) -> None:
    monkeypatch.setattr(auth.httpx, "get", fake.get)
    monkeypatch.setattr(auth.httpx, "post", fake.post)


def _run_login(
    monkeypatch,
    *,
    inputs,
    no_browser=True,
    ssh_env=False,
    exchange_status=200,
    extra_args=(),
    agents=(),
):
    fake = FakeHttpx(exchange_status=exchange_status)
    _install_fake_httpx(monkeypatch, fake)
    monkeypatch.delenv("TOKENAGE_SERVER", raising=False)
    for key in ("SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(key, raising=False)
    if ssh_env:
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 1234 10.0.0.2 22")
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in agents else None,
    )
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: opened.append(url))

    responses = list(inputs)

    def fake_input(prompt=""):
        if not responses:
            raise EOFError
        return responses.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    server = "https://srv.example"
    for index, value in enumerate(extra_args):
        if value == "--server":
            server = extra_args[index + 1]

    code = auth.login(server, device_name_arg=None, no_browser=no_browser)
    return code, fake, opened


# ------------------------------------------------------------------- login


@pytest.mark.parametrize("invalid_config", [False, True])
def test_login_reports_detected_agents_that_could_not_be_wired(
    monkeypatch, capsys, invalid_config
):
    if invalid_config:
        path = paths.credentials_path().parent.parent / ".codex" / "config.toml"
        path.parent.mkdir()
        path.write_text("[invalid")
    code, _, _ = _run_login(monkeypatch, inputs=["the-code"], agents=["codex"])
    assert code == 1
    assert auth.load_credentials()["cli_token"] == EXCHANGE_PAYLOAD["cli_token"]
    output = capsys.readouterr()
    assert "no detected agents could be wired" in output.err
    assert "No tracked agents detected" not in output.out


def test_logout_keep_agents_does_not_echo_unsafe_stored_collector(capsys):
    auth.save_credentials(
        {
            "server_url": "https://srv.example",
            "otlp_logs_endpoint": "https://user:secret@collector.example/v1/logs?token=secret",
        }
    )
    assert auth.logout(keep_agents=True) == 0
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err


def test_login_writes_credentials_0600(monkeypatch, capsys):
    code, fake, opened = _run_login(monkeypatch, inputs=["the-one-time-code"])
    assert code == 0
    assert opened == []
    # The login URL is always printed.
    captured = capsys.readouterr()
    assert "https://srv.example/auth/cli/start" in captured.out

    # Pre-flight hit /version; exchange hit /auth/cli/exchange with the
    # normalized code (uppercase, hyphens and whitespace stripped).
    assert ("GET", "https://srv.example/version") in fake.calls
    exchange_calls = [c for c in fake.calls if c[0] == "POST"]
    assert len(exchange_calls) == 1
    assert exchange_calls[0][1] == "https://srv.example/auth/cli/exchange"
    assert exchange_calls[0][2]["code"] == "THEONETIMECODE"
    assert set(exchange_calls[0][2]) == {
        "code",
        "code_verifier",
        "installation_key",
        "client_version",
    }

    path = auth.credentials_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["server_url"] == "https://srv.example"
    assert data["email"] == "a@example.com"
    assert data["device_name"] == "testhost"
    assert data["cli_token"] == EXCHANGE_PAYLOAD["cli_token"]
    assert data["ingest_token"] == EXCHANGE_PAYLOAD["ingest_token"]
    assert data["otlp_logs_endpoint"] == EXCHANGE_PAYLOAD["otlp"]["logs_endpoint"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    # Tokens are never printed to the terminal.
    assert "tokenage_cli" not in captured.out
    assert "tokenage_cli" not in captured.err
    assert "tokenage_ingest" not in captured.out
    assert "tokenage_ingest" not in captured.err


def test_login_initializes_the_evaluator_from_detected_agents(monkeypatch):
    (Path.home() / ".codex").mkdir()
    code, _, _ = _run_login(monkeypatch, inputs=["the-code"], agents=["codex"])

    assert code == 0
    config = paths.client_config_path().read_text(encoding="utf-8")
    assert "evaluator: codex" in config


def test_login_leaves_the_evaluator_unset_without_agent_clis(monkeypatch):
    code, _, _ = _run_login(monkeypatch, inputs=["the-code"], agents=[])

    assert code == 0
    assert not paths.client_config_path().exists()


def test_login_retries_on_400_then_succeeds(monkeypatch, capsys):
    code, fake, _ = _run_login(
        monkeypatch,
        inputs=["bad-code", "good-code"],
        exchange_status=[400, 200],
    )
    assert code == 0
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert [p[2]["code"] for p in posts] == ["BADCODE", "GOODCODE"]
    assert "paste it again" in capsys.readouterr().err


def test_login_registers_this_installation_across_logins_and_logout(
    monkeypatch,
):
    """The installation key is stable per machine and survives logout."""
    code, first_fake, _ = _run_login(monkeypatch, inputs=["code-one"])
    assert code == 0

    key_path = paths.installation_key_path()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    key = key_path.read_text(encoding="utf-8").strip()
    assert len(key) >= 43

    code, second_fake, _ = _run_login(monkeypatch, inputs=["code-two"])
    assert code == 0
    first_body = [c for c in first_fake.calls if c[0] == "POST"][0][2]
    second_body = [c for c in second_fake.calls if c[0] == "POST"][0][2]
    assert first_body["installation_key"] == second_body["installation_key"] == key

    assert auth.logout(keep_agents=True) == 0
    assert key_path.read_text(encoding="utf-8").strip() == key
    assert not paths.credentials_path().exists()


@pytest.mark.parametrize(
    ("stored_server", "stored_token", "send_token"),
    [
        ("https://srv.example", "previous-ingest-token", True),
        ("https://other.example", "previous-ingest-token", False),
        ("https://srv.example", None, False),
        ("https://srv.example", "", False),
    ],
)
def test_relogin_only_sends_ingest_token_to_its_server(
    monkeypatch, capsys, stored_server, stored_token, send_token
):
    wired = []
    monkeypatch.setattr(
        setup, "wire_agents", lambda **kwargs: wired.append(kwargs) or []
    )
    auth.save_credentials({"server_url": stored_server, "ingest_token": stored_token})
    code, fake, _ = _run_login(monkeypatch, inputs=["code"])
    assert code == 0
    body = [call[2] for call in fake.calls if call[0] == "POST"][0]
    assert ("ingest_token" in body) == send_token
    if send_token:
        assert body["ingest_token"] == stored_token
    # Old servers can ignore the hint and return a rotated token as before.
    assert auth.load_credentials()["ingest_token"] == EXCHANGE_PAYLOAD["ingest_token"]
    assert wired[0]["token"] == EXCHANGE_PAYLOAD["ingest_token"]
    output = capsys.readouterr()
    assert "previous-ingest-token" not in output.out + output.err


@pytest.mark.parametrize(
    "stored_token",
    ["t" * 300, "t\u00e9", "t\n", "t\x7f"],
    ids=["overlong", "non-ascii", "newline", "delete"],
)
def test_relogin_drops_an_ingest_token_the_server_would_reject(
    monkeypatch, capsys, stored_token
):
    """An invalid stored token must not make every login fail with a 422."""
    auth.save_credentials(
        {"server_url": "https://srv.example", "ingest_token": stored_token}
    )
    code, fake, _ = _run_login(monkeypatch, inputs=["code"])
    assert code == 0
    body = [call[2] for call in fake.calls if call[0] == "POST"][0]
    assert "ingest_token" not in body
    assert auth.load_credentials()["ingest_token"] == EXCHANGE_PAYLOAD["ingest_token"]
    output = capsys.readouterr()
    assert stored_token not in output.out + output.err


def test_relogin_with_kept_ingest_token_leaves_agent_settings_unchanged(monkeypatch):
    code, _, _ = _run_login(monkeypatch, inputs=["first-code"], agents=["claude"])
    assert code == 0
    settings = Path.home() / ".claude" / "settings.json"
    original = settings.read_text()

    code, fake, _ = _run_login(monkeypatch, inputs=["second-code"], agents=["claude"])
    assert code == 0
    body = [call[2] for call in fake.calls if call[0] == "POST"][0]
    assert body["ingest_token"] == EXCHANGE_PAYLOAD["ingest_token"]
    assert settings.read_text() == original


def test_explicit_login_can_replace_corrupt_previous_credentials(monkeypatch):
    path = paths.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{invalid-json")
    code, fake, _ = _run_login(monkeypatch, inputs=["code"])
    assert code == 0
    body = [call[2] for call in fake.calls if call[0] == "POST"][0]
    assert "ingest_token" not in body
    assert auth.load_credentials()["ingest_token"] == EXCHANGE_PAYLOAD["ingest_token"]


def test_login_regenerates_a_corrupt_installation_key(monkeypatch):
    key_path = paths.installation_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)

    for content in ("not-a-valid-key\n", b"\xff\xfe\x00bad"):
        if isinstance(content, str):
            key_path.write_text(content, encoding="utf-8")
        else:
            key_path.write_bytes(content)

        code, fake, _ = _run_login(monkeypatch, inputs=["code"])
        assert code == 0
        body = [c for c in fake.calls if c[0] == "POST"][0][2]
        assert body["installation_key"] not in ("not-a-valid-key", content)
        assert key_path.read_text(encoding="utf-8").strip() == body["installation_key"]


def test_login_starts_the_client_daemon(monkeypatch):
    from client import service

    started = []
    monkeypatch.setattr(service, "start", lambda: started.append(True) or 0)
    code, _, _ = _run_login(monkeypatch, inputs=["the-code"])
    assert code == 0
    assert started == [True]


def test_login_daemon_failure_is_only_a_warning(monkeypatch, capsys):
    from client import service

    monkeypatch.setattr(service, "start", lambda: 1)
    code, _, _ = _run_login(monkeypatch, inputs=["the-code"])
    assert code == 0
    assert "client service did not start" in capsys.readouterr().err


def test_login_three_failures_exit_1_no_credentials(monkeypatch):
    code, fake, _ = _run_login(
        monkeypatch,
        inputs=["bad1", "bad2", "bad3"],
        exchange_status=400,
    )
    assert code == 1
    assert not auth.credentials_path().exists()
    assert len([c for c in fake.calls if c[0] == "POST"]) == 3


def test_login_empty_input_exit_1_no_credentials(monkeypatch):
    code, fake, _ = _run_login(monkeypatch, inputs=["  "])
    assert code == 1
    assert not auth.credentials_path().exists()
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_login_ssh_skips_browser_but_prints_url(monkeypatch, capsys):
    code, _, opened = _run_login(
        monkeypatch,
        inputs=["the-code"],
        no_browser=False,
        ssh_env=True,
    )
    assert code == 0
    assert opened == []  # no webbrowser.open over SSH
    assert "https://srv.example/auth/cli/start" in capsys.readouterr().out


def test_login_unreachable_server_fails_clean(monkeypatch):
    fake = FakeHttpx(get_error=httpx.ConnectError("no route"))
    _install_fake_httpx(monkeypatch, fake)
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: opened.append(url))

    code = auth.login("https://down.example", device_name_arg=None, no_browser=True)
    assert code == 1
    assert opened == []  # pre-flight failed before any browser open
    assert not auth.credentials_path().exists()


def test_login_requires_server(monkeypatch):
    monkeypatch.delenv("TOKENAGE_SERVER", raising=False)
    assert auth.login(None, device_name_arg=None, no_browser=True) == 2


def test_login_server_from_env(monkeypatch):
    monkeypatch.setenv("TOKENAGE_SERVER", "https://env.example")
    fake = FakeHttpx(get_error=httpx.ConnectError("stop here"))
    _install_fake_httpx(monkeypatch, fake)
    auth.login(None, device_name_arg=None, no_browser=True)
    assert ("GET", "https://env.example/version") in fake.calls


def test_login_non_json_200_exits_clean(monkeypatch):
    class BadJsonHttpx(FakeHttpx):
        def post(self, url, json=None, **kwargs):
            self.calls.append(("POST", url, dict(json or {})))
            return SimpleNamespace(
                status_code=200,
                json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            )

    _install_fake_httpx(monkeypatch, BadJsonHttpx())
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "the-code")

    code = auth.login("https://srv.example", device_name_arg=None, no_browser=True)
    assert code == 1
    assert not auth.credentials_path().exists()


@pytest.mark.parametrize(
    ("server", "expected_code"),
    [
        ("http://srv.example", 2),
        ("HTTP://srv.example", 2),
        ("http://localhost:4004", 0),
        ("http://127.0.0.1", 0),
        ("http://[::1]:4004", 0),
        ("http://localhost.attacker.example", 2),
    ],
)
def test_login_accepts_plain_http_only_for_loopback(monkeypatch, server, expected_code):
    code, _, _ = _run_login(
        monkeypatch,
        inputs=["the-code"],
        extra_args=["--server", server],
    )
    assert code == expected_code
    assert auth.credentials_path().exists() is (expected_code == 0)


@pytest.mark.parametrize("generation", [1, 99])
def test_check_server_rejects_an_incompatible_protocol(monkeypatch, capsys, generation):
    _install_fake_httpx(
        monkeypatch, FakeHttpx(protocol_min=generation, protocol_max=generation)
    )

    assert auth.check_server("https://srv.example") == 1
    assert "incompatible" in capsys.readouterr().err


# ------------------------------------------------- credentials + installation


def test_load_credentials_raises_on_corrupt_file():
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        auth.load_credentials()


def test_clear_credentials_reports_whether_there_were_any():
    assert auth.clear_credentials() is False
    auth.save_credentials({"server_url": "https://srv.example"})
    assert auth.clear_credentials() is True
    assert auth.load_credentials() is None


def test_logout_removes_credentials(monkeypatch, capsys):
    auth.save_credentials(
        {
            "server_url": "https://srv.example",
            "otlp_logs_endpoint": "https://srv.example/v1/logs",
        }
    )
    # The collector is passed on, so a foreign collector is left alone.
    seen: list[str | None] = []
    monkeypatch.setattr(
        setup,
        "disable_agents",
        lambda *, expected_endpoint: (
            seen.append(expected_endpoint) or setup.DisableResult(removed=["codex"])
        ),
    )

    assert auth.logout(keep_agents=False) == 0
    assert auth.load_credentials() is None
    assert seen == ["https://srv.example/v1/logs"]
    output = capsys.readouterr()
    assert "un-wired  codex" in output.out
    assert auth.logout(keep_agents=False) == 1
    assert "Not signed in" in capsys.readouterr().err


def test_logout_keep_agents_names_the_collector_not_the_api(capsys):
    auth.save_credentials(
        {
            "server_url": "https://api.example",
            "otlp_logs_endpoint": "https://collector.example/v1/logs",
        }
    )

    assert auth.logout(keep_agents=True) == 0
    warning = capsys.readouterr().err
    assert "https://collector.example/v1/logs" in warning
    assert "https://api.example" not in warning


# --------------------------------------------------------- endpoint checking


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.example.com:4005/v1/logs",
        "http://localhost:4005/v1/logs",
        "http://127.0.0.1:4001/otlp/v1/logs",
        "http://[::1]:4001/otlp/v1/logs",
    ],
)
def test_valid_logs_endpoint_accepts_https_and_loopback(raw):
    assert auth.valid_logs_endpoint(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "http://api.example.com:4005/v1/logs",
        "https://api.example.com:4005/v1/other",
        "https://user:pw@api.example.com/v1/logs",
        "https://api.example.com/v1/logs?x=1",
        "not a url",
    ],
)
def test_valid_logs_endpoint_rejects_everything_else(raw):
    assert auth.valid_logs_endpoint(raw) is False


# ------------------------------------------------------- credentials + bearer


def test_usage_client_sends_bearer_from_credentials():
    auth.save_credentials({"server_url": "https://srv.example", "cli_token": "tok-1"})
    client = track.UsageApiClient()
    assert client.base_url == "https://srv.example"
    assert client.token == "tok-1"


def test_usage_client_without_credentials_has_no_server():
    client = track.UsageApiClient()
    assert client.token is None
    assert client.base_url is None
    with pytest.raises(track.ApiError, match="login --server"):
        client.get_high_watermark()


def test_usage_client_401_surfaces_relogin_message(monkeypatch):
    auth.save_credentials({"server_url": "https://srv.example", "cli_token": "t"})

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(track.httpx, "get", fake_get)
    client = track.UsageApiClient()
    with pytest.raises(track.ApiError) as excinfo:
        client.get_high_watermark()
    assert "tokenage login" in str(excinfo.value)


# ------------------------------------------------------------------ wiring


def _fake_agents(monkeypatch, code=0):
    calls = []

    class Fake:
        def __init__(self, name):
            self.name = name

        def configure(self, target, endpoint, token):
            calls.append((self.name, target, endpoint, token))
            return code

    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        setup, "AGENT_MODULES", {n: Fake(n) for n in setup.AGENT_MODULES}
    )
    return calls


def test_wire_agents_configures_each_detected_agent_in_process(monkeypatch):
    calls = _fake_agents(monkeypatch)
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("codex", "claude") else None,
    )

    wired = setup.wire_agents(
        logs_endpoint="https://api.example.com:4005/v1/logs", token="ingest-secret"
    )
    assert wired == ["codex", "claude"]
    assert [(n, e, t) for n, _, e, t in calls] == [
        ("codex", "https://api.example.com:4005/v1/logs", "ingest-secret"),
        ("claude", "https://api.example.com:4005/v1/logs", "ingest-secret"),
    ]


def test_wire_agents_warns_when_an_agent_is_not_wired(monkeypatch, capsys):
    _fake_agents(monkeypatch, code=1)
    assert setup.wire_agents(logs_endpoint="https://x/v1/logs", token=None) == []
    assert "wiring codex failed" in capsys.readouterr().err
