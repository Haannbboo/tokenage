"""``tokenage setup`` — the client owns agent configuration.

These tests run the real ``client.agents`` modules, so they cover the whole path:
the client decides the endpoint and the modules write only their own keys. The
server deliberately does not do this any more.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from client import auth, setup

ENDPOINT = "https://app.example.com/v1/logs"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOKENAGE_HOME", str(home / ".tokenage"))
    monkeypatch.delenv("TOKENAGE_ROOT", raising=False)
    monkeypatch.delenv("TOKENAGE_CONFIG", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("TOKENAGE_INGEST_TOKEN", raising=False)
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"codex", "claude"} else None,
    )
    # An installed agent has its config directory. the codex module
    # deliberately refuses to create it.
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    return home


def _sign_in(home: Path, endpoint: str = ENDPOINT) -> None:
    path = home / ".tokenage" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "cli_token": "tokenage_cli_x",
                "ingest_token": "tokenage_ingest_x",
                "otlp_logs_endpoint": endpoint,
            }
        ),
        encoding="utf-8",
    )


def test_setup_wires_detected_agents_at_the_signed_in_collector(
    machine: Path, capsys
) -> None:
    _sign_in(machine)
    assert setup.run_setup(disable=False) == 0
    out = capsys.readouterr().out
    assert f"collector   {ENDPOINT}" in out
    assert "wired       codex, claude" in out

    claude = json.loads((machine / ".claude" / "settings.json").read_text())
    assert claude["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT
    # The token is required for an authenticated collector.
    assert (
        claude["env"]["OTEL_EXPORTER_OTLP_HEADERS"]
        == "x-tokenage-token=tokenage_ingest_x"
    )

    codex = (machine / ".codex" / "config.toml").read_text()
    assert ENDPOINT in codex
    assert "tokenage_ingest_x" in codex


def test_setup_is_idempotent(machine: Path) -> None:
    _sign_in(machine)
    assert setup.run_setup(disable=False) == 0
    claude_path = machine / ".claude" / "settings.json"
    first = claude_path.read_text()
    assert setup.run_setup(disable=False) == 0
    assert claude_path.read_text() == first


def test_setup_needs_a_collector(machine: Path, capsys) -> None:
    assert setup.run_setup(disable=False) == 1
    assert "login --server URL" in capsys.readouterr().err


def test_setup_refuses_without_an_ingest_token(machine: Path, capsys) -> None:
    path = machine / ".tokenage" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"server_url": "https://app.example.com", "otlp_logs_endpoint": ENDPOINT}
        ),
        encoding="utf-8",
    )

    assert setup.run_setup(disable=False) == setup.EXIT_NOT_SIGNED_IN
    assert "tokenage login" in capsys.readouterr().err
    assert not (machine / ".claude" / "settings.json").exists()


def test_setup_disable_removes_only_our_keys(machine: Path, capsys) -> None:
    _sign_in(machine)
    settings = machine / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"model": "opus", "env": {"MY_OWN": "keep"}}), encoding="utf-8"
    )
    assert setup.run_setup(disable=False) == 0
    assert setup.run_setup(disable=True) == 0
    assert "un-wired" in capsys.readouterr().out
    remaining = json.loads(settings.read_text())
    assert remaining == {"model": "opus", "env": {"MY_OWN": "keep"}}


def test_setup_disable_leaves_a_foreign_collector_alone(machine: Path, capsys) -> None:
    _sign_in(machine)
    settings = machine / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "env": {
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://other:9999/v1/logs",
                }
            }
        ),
        encoding="utf-8",
    )
    assert setup.run_setup(disable=True) == 0
    assert "left alone" in capsys.readouterr().err
    assert "http://other:9999/v1/logs" in settings.read_text()


def test_setup_with_no_agents_is_not_a_failure(machine: Path, capsys) -> None:
    monkeypatch_which = setup.shutil.which
    setup.shutil.which = lambda name: None
    try:
        assert setup.run_setup(disable=False) == 0
    finally:
        setup.shutil.which = monkeypatch_which
    assert "No tracked agents detected" in capsys.readouterr().out


def test_setup_strips_a_stale_local_otlp_override(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing OTEL override must not beat the endpoint we are wiring."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://stale:1/v1/logs")
    _sign_in(machine)
    assert setup.run_setup(disable=False) == 0
    claude = json.loads((machine / ".claude" / "settings.json").read_text())
    assert claude["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT


def _sign_in_without_collector(home: Path) -> None:
    """A login made before the client started recording the collector."""
    path = home / ".tokenage" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "cli_token": "tokenage_cli_x",
                "ingest_token": "tokenage_ingest_x",
            }
        ),
        encoding="utf-8",
    )


def test_signed_in_client_asks_the_server_where_its_collector_is(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OTLP port is not the API port, so only the server can say."""
    _sign_in_without_collector(machine)
    requested: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "protocol_min": 1,
                "protocol_max": 1,
                "otlp_logs_endpoint": ENDPOINT,
            }

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse()

    monkeypatch.setattr(auth.httpx, "get", fake_get)
    assert setup.run_setup(disable=False) == 0
    assert requested == ["https://app.example.com/version"]
    claude = json.loads((machine / ".claude" / "settings.json").read_text())
    assert claude["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT
    # And the answer is remembered, so later commands need no network.
    remembered = json.loads((machine / ".tokenage" / "credentials.json").read_text())[
        "otlp_logs_endpoint"
    ]
    assert remembered == ENDPOINT
    monkeypatch.setattr(
        auth.httpx,
        "get",
        lambda *a, **k: pytest.fail("a remembered collector must not be looked up"),
    )
    assert auth.discover_collector() == ENDPOINT


def test_status_never_guesses_a_local_collector_when_signed_in_remotely(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local fallback here would point agents away from their own server."""
    _sign_in_without_collector(machine)
    server_root = machine / ".tokenage" / "src"
    (server_root / ".venv" / "bin").mkdir(parents=True)
    (server_root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (server_root / ".venv" / "bin" / "python").chmod(0o755)
    monkeypatch.setenv("TOKENAGE_ROOT", str(server_root))
    (machine / ".tokenage" / "config.yaml").write_text(
        "server:\n  host: 127.0.0.1\n  otlp_port: 4102\n", encoding="utf-8"
    )

    assert setup.intended_endpoint() is None

    # And setup refuses rather than wiring the local collector.
    monkeypatch.setattr(
        auth.httpx,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    assert setup.run_setup(disable=False) == 1
    assert not (machine / ".claude" / "settings.json").exists()


def test_logout_unwires_with_the_collector_it_recorded(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(machine)
    assert setup.run_setup(disable=False) == 0
    settings = machine / ".claude" / "settings.json"
    assert "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT" in settings.read_text()

    assert auth.logout(keep_agents=False) == 0
    assert "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT" not in settings.read_text()
    assert not (machine / ".tokenage" / "credentials.json").exists()


def test_setup_uses_explicit_agent_status(machine, monkeypatch):
    class Fake:
        code = 0

        def configure(self, *args):
            return self.code

    fake = Fake()
    monkeypatch.setattr(setup, "AGENT_MODULES", {"codex": fake, "claude": fake})
    assert setup.wire_agents(logs_endpoint=ENDPOINT, token=None) == ["codex", "claude"]
    fake.code = 2
    assert setup.wire_agents(logs_endpoint=ENDPOINT, token=None) == []


def test_setup_can_wire_again_after_disable(machine):
    _sign_in(machine)
    assert setup.run_setup(disable=False) == 0
    assert setup.run_setup(disable=True) == 0
    assert setup.run_setup(disable=False) == 0
    assert setup.read_agent_states(ENDPOINT)["codex"]["status"] == "ready"


def test_bare_plugin_entry_reports_its_actual_runtime_default(machine):
    path = machine / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"plugin": ["/old/tracker/plugins/opencode/dist/index.js"]})
    )
    state = setup.read_agent_states("http://localhost:4002/v1/logs")["opencode"]
    assert state["configured_endpoint"] == "http://localhost:4005/v1/logs"
    assert state["status"] == "wrong_endpoint"


@pytest.mark.parametrize("legacy_login", [False, True])
def test_disable_unknown_collector_preserves_existing_settings(
    machine, legacy_login, capsys
):
    if legacy_login:
        _sign_in_without_collector(machine)
    claude = machine / ".claude" / "settings.json"
    codex = machine / ".codex" / "config.toml"
    claude.write_text(
        json.dumps({"env": {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": ENDPOINT}})
    )
    codex.write_text(f'[otel.exporter.otlp-http]\nendpoint = "{ENDPOINT}"\n')
    before = [path.read_bytes() for path in (claude, codex)]
    assert setup.run_setup(disable=True) == 1
    assert "collector unknown" in capsys.readouterr().err
    assert [path.read_bytes() for path in (claude, codex)] == before
    if legacy_login:
        assert auth.logout(keep_agents=False) == 0
        assert [path.read_bytes() for path in (claude, codex)] == before
        assert not (machine / ".tokenage" / "credentials.json").exists()


def test_disable_reports_agent_failures(machine, capsys):
    _sign_in(machine)
    (machine / ".codex" / "config.toml").write_text("[invalid")
    (machine / ".claude" / "settings.json").write_text("{invalid")
    assert setup.run_setup(disable=True) == 1
    output = capsys.readouterr()
    assert "un-wiring failed: codex, claude" in output.err
    assert "Nothing to un-wire" not in output.out


def test_logout_reports_cleanup_failure_after_removing_credentials(machine, capsys):
    _sign_in(machine)
    (machine / ".codex" / "config.toml").write_text("[invalid")
    assert auth.logout(keep_agents=False) == 1
    assert "Signed out, but agent cleanup failed" in capsys.readouterr().err
    assert not (machine / ".tokenage" / "credentials.json").exists()
