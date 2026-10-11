"""``tokenage status`` — the component report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from client import service, setup, status


@pytest.fixture
def tracker_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tracker home with no server, no credentials and no agents."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOKENAGE_HOME", str(home / ".tokenage"))
    monkeypatch.delenv("TOKENAGE_ROOT", raising=False)
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)

    return home


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_status_on_a_bare_client_only_machine(tracker_home: Path, capsys) -> None:
    code = status.run_status(as_json=False)
    out = capsys.readouterr().out
    # Not being signed in is a fact, not a fault: nothing installed is broken.
    assert code == 0
    assert "not signed in" in out
    assert "run tokenage login --server <url>" in out
    assert "services" not in out  # the service view is `tokenage server status`


def test_status_json_is_the_service_report_plus_account(
    tracker_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.time", lambda: 1000.0)
    status.run_status(as_json=True)
    data = json.loads(capsys.readouterr().out.strip())
    assert data.pop("account")["signed_in"] is False
    assert data.pop("proxy") is None
    assert data == service.health_payload()
    assert all(found["found"] is False for found in data["detected"].values())


def test_status_reports_a_signed_in_client(
    tracker_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = tracker_home / ".tokenage" / "credentials.json"
    _write(
        credentials,
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "device_name": "hanbo-macbook",
                "cli_token": "tokenage_cli_x",
                "otlp_logs_endpoint": "https://app.example.com/v1/logs",
            }
        ),
    )
    assert status.run_status(as_json=False) == 0
    out = capsys.readouterr().out
    assert "signed in as you@example.com → https://app.example.com" in out
    assert "hanbo-macbook" in out  # device is only shown without a server
    assert "not signed in" not in out


def test_status_reports_agents_pointing_at_the_wrong_collector(
    tracker_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
    _write(
        tracker_home / ".tokenage" / "credentials.json",
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "cli_token": "tokenage_cli_x",
                "otlp_logs_endpoint": "https://app.example.com/v1/logs",
            }
        ),
    )
    _write(
        tracker_home / ".claude" / "settings.json",
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://elsewhere:4005/v1/logs",
                }
            }
        ),
    )
    assert status.run_status(as_json=False) == 1
    out = capsys.readouterr().out
    assert "wrong collector" in out
    assert "run tokenage setup" in out


def test_status_lists_wired_agents_with_their_collector(
    tracker_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
    _write(
        tracker_home / ".tokenage" / "credentials.json",
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "cli_token": "tokenage_cli_x",
                "otlp_logs_endpoint": "https://app.example.com/v1/logs",
            }
        ),
    )
    _write(
        tracker_home / ".claude" / "settings.json",
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://app.example.com/v1/logs",
                }
            }
        ),
    )
    assert status.run_status(as_json=False) == 0
    out = capsys.readouterr().out
    assert "claude → https://app.example.com/v1/logs" in out


def test_unknown_collector_is_not_reported_as_broken(
    tracker_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine that signed in before its server published a collector cannot
    tell whether an agent is right, so it must not call the agent broken."""
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
    _write(
        tracker_home / ".tokenage" / "credentials.json",
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "email": "you@example.com",
                "cli_token": "tokenage_cli_x",
            }
        ),
    )
    _write(
        tracker_home / ".claude" / "settings.json",
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://app.example.com/v1/logs",
                }
            }
        ),
    )
    assert status.run_status(as_json=False) == 0
    out = capsys.readouterr().out
    assert "(target unknown)" in out
    assert "wrong collector" not in out
    assert "none wired" not in out
    # It is still actionable: setup is what discovers the collector.
    assert "run tokenage setup" in out

    data = status.collect()
    claude = data["agents"]["claude"]
    assert claude["endpoint_matches"] is None
    assert claude["configured"] is True


def test_known_collector_with_missing_agent_config_is_unhealthy(
    tracker_home, monkeypatch, capsys
):
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )
    _write(
        tracker_home / ".tokenage" / "credentials.json",
        json.dumps(
            {
                "server_url": "https://app.example.com",
                "otlp_logs_endpoint": "https://app.example.com/v1/logs",
            }
        ),
    )
    assert status.run_status(as_json=True) == 1
    data = json.loads(capsys.readouterr().out)
    codex = data["agents"]["codex"]
    assert codex["configured"] is False
    assert codex["endpoint_matches"] is False


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "kilo"])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@collector.example/v1/logs?token=secret#secret",
        "https://collector.example/secret/v1/logs",
    ],
)
def test_status_redacts_collector_secrets_in_all_output(
    tracker_home, monkeypatch, capsys, agent, as_json, endpoint
):
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == agent else None,
    )
    _write(
        tracker_home / ".tokenage" / "credentials.json",
        json.dumps(
            {
                "server_url": "https://user:secret@api.example?token=secret",
                "otlp_logs_endpoint": "https://tracker.example/v1/logs",
            }
        ),
    )
    if agent == "claude":
        _write(
            tracker_home / ".claude" / "settings.json",
            json.dumps(
                {
                    "env": {
                        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                        "OTEL_LOGS_EXPORTER": "otlp",
                        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": endpoint,
                    }
                }
            ),
        )
    elif agent == "codex":
        _write(
            tracker_home / ".codex" / "config.toml",
            f'[otel.exporter.otlp-http]\nendpoint = "{endpoint}"\n',
        )
    else:
        _write(
            tracker_home / ".config" / agent / "opencode.json",
            json.dumps(
                {
                    "plugin": [
                        [
                            f"/tracker/plugins/{agent}/dist/index.js",
                            {"endpoint": endpoint},
                        ]
                    ]
                }
            ),
        )
    assert status.run_status(as_json=as_json) == 1
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert "collector.example" in output.out
    state = setup.read_agent_states("https://tracker.example/v1/logs")[agent]
    assert state["configured_endpoint"] == endpoint
    assert state["endpoint_matches"] is False


def test_status_shows_live_proxy_url_or_failure_and_ignores_stale(
    tracker_home: Path, capsys
) -> None:
    from client.proxy import runtime

    runtime.record_proxy_state(
        {"state": "running", "port": 4000, "url": "http://127.0.0.1:4000"}
    )
    assert status.run_status(as_json=False) == 0
    assert "  proxy       http://127.0.0.1:4000\n" in capsys.readouterr().out
    status.run_status(as_json=True)
    assert json.loads(capsys.readouterr().out)["proxy"]["port"] == 4000

    runtime.record_proxy_state({"state": "failed", "error": "port 4000 is in use"})
    status.run_status(as_json=False)
    assert "  proxy       failed: port 4000 is in use" in capsys.readouterr().out

    state = service._read_state()
    state["proxy"]["pid"] = 2**22 + 12345  # the writing service is gone
    service._write_state(state)
    status.run_status(as_json=False)
    assert "proxy" not in capsys.readouterr().out
