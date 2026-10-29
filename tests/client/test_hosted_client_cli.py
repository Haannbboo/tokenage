"""The CLI surface, plus the login outcomes only an end-to-end run reaches.

``test_client_auth.py`` covers login, credentials and
agent wiring through ``client.auth`` and ``client.setup`` directly. What is left
for this file is the argparse dispatch in ``client.cli`` and the outcomes that
only matter across a whole run: token rotation over two logins, a rejection
that must not be retried, and a bad payload that must not overwrite working
credentials.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from client import auth, cli, setup
from protocol import CURRENT_GENERATION


def _payload(index: int, *, logs_endpoint: str = "https://example.test/v1/logs") -> Any:
    return {
        "user": {"id": "u1", "email": "person@example.test"},
        "device_name": "machine",
        "cli_token": f"cli-{index}",
        "ingest_token": f"ingest-{index}",
        "otlp": {"logs_endpoint": logs_endpoint},
    }


class FakeHttpx:
    """The two httpx calls login makes. auth.httpx stays the real module."""

    def __init__(
        self,
        *,
        exchange: Callable[[int], tuple[int, Any]] | None = None,
        protocol_min: int = CURRENT_GENERATION,
        protocol_max: int = CURRENT_GENERATION,
    ) -> None:
        self.exchanges: list[dict[str, str]] = []
        self.protocol_min = protocol_min
        self.protocol_max = protocol_max
        self.exchange = exchange or (lambda index: (200, _payload(index)))

    def get(self, url, **_kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "protocol_min": self.protocol_min,
                "protocol_max": self.protocol_max,
            },
        )

    def post(self, url, *, json: dict[str, str], **_kwargs):
        self.exchanges.append(dict(json))
        status, payload = self.exchange(len(self.exchanges))
        return SimpleNamespace(status_code=status, json=lambda: payload)


def _install_fake_httpx(monkeypatch, fake: FakeHttpx) -> None:
    monkeypatch.setattr(auth.httpx, "get", fake.get)
    monkeypatch.setattr(auth.httpx, "post", fake.post)


@pytest.fixture(autouse=True)
def client_home(tmp_path, monkeypatch):
    """Credentials and agent config never touch real $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOKENAGE_CLIENT_COMMIT", "a" * 40)
    monkeypatch.delenv("TOKENAGE_SERVER", raising=False)
    # Nothing is detected on PATH, so a login wires no real agent config.
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
    # A successful login starts the daemon; tests must never spawn processes.
    from client import service

    monkeypatch.setattr(service, "start", lambda: 0)


# -------------------------------------------------------------- cli.py surface


def test_check_server_checks_protocol_without_writing(monkeypatch, capsys):
    fake = FakeHttpx()
    _install_fake_httpx(monkeypatch, fake)

    assert cli.main(["check-server", "--server", "https://example.test"]) == 0
    assert not auth.credentials_path().parent.exists()

    fake.protocol_min = CURRENT_GENERATION + 1
    fake.protocol_max = CURRENT_GENERATION + 1
    assert cli.main(["check-server", "--server", "https://example.test"]) == 1
    assert "incompatible" in capsys.readouterr().err
    # A refused box must leave no trace to sign in against later.
    assert not auth.credentials_path().parent.exists()


# ------------------------------------------------------------------ full login


def test_login_replaces_tokens_and_keeps_the_installation_key(monkeypatch, capsys):
    fake = FakeHttpx()
    _install_fake_httpx(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "ab-cd")
    for _ in range(2):
        assert (
            cli.main(["login", "--server", "https://example.test", "--no-browser"]) == 0
        )
    first, second = fake.exchanges
    assert first["code"] == "ABCD"
    # Both logins register the same machine and report its client build.
    assert set(first) == {
        "code",
        "code_verifier",
        "installation_key",
        "client_version",
        "client_commit",
    }
    assert set(second) == set(first) | {"ingest_token"}
    assert second["ingest_token"] == "ingest-1"
    assert first["installation_key"] == second["installation_key"]
    credentials = json.loads(auth.credentials_path().read_text(encoding="utf-8"))
    assert credentials["cli_token"] == "cli-2"
    assert credentials["ingest_token"] == "ingest-2"
    assert stat.S_IMODE(auth.credentials_path().stat().st_mode) == 0o600
    output = capsys.readouterr()
    assert "cli-" not in output.out + output.err
    assert "ingest-" not in output.out + output.err


def test_malformed_field_rejection_reports_the_reason_once(monkeypatch, capsys):
    fake = FakeHttpx(
        exchange=lambda _index: (422, {"detail": "invalid exchange request"})
    )
    _install_fake_httpx(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "ab-cd")

    assert cli.main(["login", "--server", "https://example.test", "--no-browser"]) == 1
    # One attempt only — a 422 is not a mistyped code, so re-pasting is futile.
    assert len(fake.exchanges) == 1
    assert "invalid exchange request" in capsys.readouterr().err


def test_invalid_otlp_endpoint_does_not_replace_existing_tokens(monkeypatch):
    fake = FakeHttpx(
        exchange=lambda index: (
            200,
            _payload(index, logs_endpoint="http://public.example.test/v1/logs"),
        )
    )
    _install_fake_httpx(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "code")
    auth.save_credentials(
        {"server_url": "https://example.test", "cli_token": "prior", "user_id": "u1"}
    )

    assert cli.main(["login", "--server", "https://example.test", "--no-browser"]) == 1
    assert auth.load_credentials() == {
        "server_url": "https://example.test",
        "cli_token": "prior",
        "user_id": "u1",
    }


# --------------------------------------------------------------------- wiring


def test_wiring_skips_a_script_that_reports_no_config(monkeypatch, capsys):
    # A `codex` on PATH with no ~/.codex yet: the configure script exits 0
    # after announcing a skip, which must not read as "wired".
    monkeypatch.setattr(
        setup.shutil, "which", lambda name: "/usr/bin/x" if name == "codex" else None
    )

    assert (
        setup.wire_agents(
            logs_endpoint="https://example.test/v1/logs", token="ingest-1"
        )
        == []
    )
    assert "skipping" in capsys.readouterr().err
