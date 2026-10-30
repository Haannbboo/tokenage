"""The local sign-in provider: loopback owner, login links, OTLP, migration."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import drain, ingest_write

OWNER = "owner@localhost"
SESSION_COOKIE = "tokenage_session"


def _remote(api_module, **kwargs):
    kwargs.setdefault("client", ("203.0.113.9", 5))
    kwargs.setdefault("base_url", "http://tracker.example")
    return TestClient(api_module.app, **kwargs)


def _set_provider(monkeypatch, provider):
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG, "auth", {"provider": provider, "allowlist": []}
    )


def _me_email(client, **kwargs):
    user = client.get("/auth/me", **kwargs).json()["user"]
    return user["email"] if user else None


# ------------------------------------------------------------ loopback rule


@pytest.mark.parametrize(
    "client_kwargs,headers",
    [
        ({}, {}),
        ({"client": ("::1", 1)}, {"host": "[::1]:4001"}),
        ({"client": ("127.0.0.1", 1), "base_url": "http://127.0.0.1:4001"}, {}),
        ({}, {"origin": "http://localhost:5173"}),
        ({}, {"host": "LOCALHOST.:4001"}),
        ({}, {"host": "127.0.0.1.:4001"}),
    ],
)
def test_direct_loopback_is_the_owner(api_module, client_kwargs, headers):
    client = TestClient(api_module.app, **client_kwargs)
    assert _me_email(client, headers=headers) == OWNER


@pytest.mark.parametrize(
    "client_kwargs,headers",
    [
        ({"client": ("203.0.113.9", 1)}, {}),  # non-loopback peer
        ({"client": ("::ffff:10.0.0.1", 1)}, {}),
        ({}, {"x-forwarded-for": "203.0.113.9"}),
        (
            {"client": ("127.0.0.1", 1)},
            {"host": "usage.example.com", "x-forwarded-for": "203.0.113.9"},
        ),
        ({}, {"forwarded": "for=203.0.113.9"}),
        ({}, {"x-real-ip": "203.0.113.9"}),
        ({}, {"host": "evil.example"}),  # DNS rebinding
        ({}, {"host": "localhost.evil.example"}),
        ({}, {"host": "localhost.evil.example."}),
        ({}, {"host": ".localhost"}),
        ({}, {"host": "localhost.."}),
        ({"client": ("203.0.113.9", 1)}, {"host": "localhost."}),
        ({}, {"host": "localhost.", "x-forwarded-for": "127.0.0.1"}),
        ({}, {"host": ""}),
    ],
)
def test_non_direct_loopback_is_not_the_owner(api_module, client_kwargs, headers):
    client = TestClient(api_module.app, **client_kwargs)
    assert _me_email(client, headers=headers) is None
    assert client.get("/usage", headers=headers).status_code == 401


def test_cross_site_write_is_not_the_owner(api_module):
    client = TestClient(api_module.app)
    evil = {"origin": "https://evil.example"}
    assert client.post("/auth/logout", headers=evil).status_code == 401
    assert client.get("/auth/me", headers=evil).json()["user"]["email"] == OWNER
    assert client.post("/auth/logout").status_code == 204
    assert (
        client.post(
            "/auth/logout", headers={"origin": "http://LOCALHOST.:5173"}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/auth/logout", headers={"origin": "https://localhost.evil.example."}
        ).status_code
        == 401
    )


def test_google_provider_never_uses_the_loopback_owner(
    api_module, fresh_db, monkeypatch
):
    _set_provider(monkeypatch, "google")
    client = TestClient(api_module.app)
    assert _me_email(client) is None
    assert client.get("/usage").status_code == 401
    # ... and never creates the owner row.
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 0


def test_token_wins_over_loopback_and_bad_token_falls_back_to_owner(
    api_module, fresh_db
):
    from src.auth.tokens import mint_token

    token, user = mint_token("a@example.com", kind="cli", db_path=fresh_db.db_path)
    client = TestClient(api_module.app)
    assert (
        _me_email(client, headers={"Authorization": f"Bearer {token}"})
        == "a@example.com"
    )
    # A bad bearer token is rejected, never silently the owner.
    assert _me_email(client, headers={"Authorization": "Bearer nope"}) is None
    # A stale cookie on loopback must not lock the owner's browser out.
    assert _me_email(client, headers={"Cookie": f"{SESSION_COOKIE}=nope"}) == OWNER


def test_local_data_is_scoped_to_the_owner_not_shared(api_module, fresh_db):
    from src.auth.tokens import mint_token
    from src.recorder import record_usage

    _, third = mint_token("c@example.com", kind="cli", db_path=fresh_db.db_path)
    ingest_write(
        record_usage,
        device_id="test-device",
        provider="p",
        model="m",
        endpoint="/e",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        status=200,
        user_id=third.id,
        db_path=fresh_db.db_path,
    )
    token, _ = mint_token("a@example.com", kind="cli", db_path=fresh_db.db_path)
    client = TestClient(api_module.app)
    # The row belongs to a third user: neither the owner nor another user can
    # read it.
    assert client.get("/usage/count").json() == {"total": 0}
    other = client.get("/usage/count", headers={"Authorization": f"Bearer {token}"})
    assert other.json() == {"total": 0}


def test_owner_is_created_once_on_demand(api_module, fresh_db):
    client = TestClient(api_module.app)
    first = client.get("/auth/me").json()["user"]["id"]
    assert client.get("/auth/me").json()["user"]["id"] == first


# --------------------------------------------------------------- login-link


def _login_link(cli_module, capsys):
    assert cli_module.main(["login-link"]) == 0
    return capsys.readouterr().out.strip()


def test_login_link_signs_a_remote_browser_in_once(
    api_module, cli_module, fresh_db, capsys
):
    link = _login_link(cli_module, capsys)
    assert re.fullmatch(r"http://localhost:\d+/auth/local/login\?code=\S+", link)
    path = link.split("localhost:", 1)[1].split("/", 1)[1]

    remote = _remote(api_module)
    assert remote.get("/usage").status_code == 401

    response = remote.get("/" + path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=tokenage_web_" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert _me_email(remote) == OWNER
    assert remote.get("/usage").status_code == 200

    # Single use.
    replay = _remote(api_module).get("/" + path, follow_redirects=False)
    assert replay.status_code == 400


def test_login_link_second_login_retires_the_first_session(
    api_module, cli_module, fresh_db, capsys
):
    sessions = []
    for _ in range(2):
        link = _login_link(cli_module, capsys)
        remote = _remote(api_module)
        remote.get("/auth/local/login?" + link.split("?", 1)[1])
        sessions.append(remote)
    assert _me_email(sessions[0]) is None
    assert _me_email(sessions[1]) == OWNER


def test_login_link_rejects_bad_codes(api_module):
    remote = _remote(api_module)
    assert remote.get("/auth/local/login").status_code == 400
    assert remote.get("/auth/local/login?code=nope").status_code == 400


def test_login_link_is_unavailable_under_google(
    api_module, cli_module, monkeypatch, capsys
):
    _set_provider(monkeypatch, "google")
    assert cli_module.main(["login-link"]) == 2
    assert _remote(api_module).get("/auth/local/login?code=x").status_code == 404


# ---------------------------------------------------------------- CLI login


def test_cli_login_flow_works_without_google_on_loopback(api_module, fresh_db):
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = {"code_challenge": challenge, "device_name": "laptop"}
    client = TestClient(api_module.app)

    confirm = client.get("/auth/cli/start", params=params)
    assert confirm.status_code == 200 and OWNER in confirm.text
    approved = client.post("/auth/cli/start", data=params)
    code = re.search(r'id="cli-code"[^>]*>([^<]+)<', approved.text).group(1)

    exchanged = client.post(
        "/auth/cli/exchange",
        json={
            "code": code,
            "code_verifier": verifier,
            "installation_key": secrets.token_urlsafe(32),
        },
    )
    assert exchanged.status_code == 200
    body = exchanged.json()
    assert body["user"]["email"] == OWNER
    assert body["cli_token"].startswith("tokenage_cli_")
    assert body["ingest_token"].startswith("tokenage_ingest_")


# -------------------------------------------------------------------- OTLP


def _otlp_body():
    from tests.test_otlp import _minimal_otlp_body

    return _minimal_otlp_body()


def _capture_usage(otlp_module, monkeypatch):
    captured = []
    monkeypatch.setattr(
        otlp_module,
        "record_usage",
        lambda **fields: (
            captured.append(fields) or type("U", (), {"id": "u1", "session_id": "s"})()
        ),
    )
    return captured


def test_otlp_tokenless_loopback_is_rejected(otlp_module, fresh_db, monkeypatch):
    captured = _capture_usage(otlp_module, monkeypatch)
    response = TestClient(otlp_module.app).post("/v1/logs", json=_otlp_body())
    drain()
    assert response.status_code == 401
    assert captured == []


@pytest.mark.parametrize(
    "client_kwargs,headers",
    [
        ({"client": ("203.0.113.9", 1)}, {}),
        ({}, {"x-forwarded-for": "203.0.113.9"}),
        ({}, {"host": "evil.example"}),
        ({}, {"x-tokenage-token": "tokenage_ingest_wrong"}),  # no owner fallback
    ],
)
def test_otlp_tokenless_non_loopback_or_bad_token_is_401(
    otlp_module, fresh_db, monkeypatch, client_kwargs, headers
):
    captured = _capture_usage(otlp_module, monkeypatch)
    client = TestClient(otlp_module.app, **client_kwargs)
    response = client.post("/v1/logs", json=_otlp_body(), headers=headers)
    drain()
    assert response.status_code == 401
    assert captured == []


def test_otlp_tokenless_loopback_is_401_under_google(
    otlp_module, fresh_db, monkeypatch
):
    _set_provider(monkeypatch, "google")
    captured = _capture_usage(otlp_module, monkeypatch)
    response = TestClient(otlp_module.app).post("/v1/logs", json=_otlp_body())
    drain()
    assert response.status_code == 401
    assert captured == []


# --------------------------------------------------------------- migration


def test_api_is_rate_limited_per_user_but_loopback_owner_is_exempt(
    api_module, fresh_db, monkeypatch
):
    import src.config.app
    from src.auth.tokens import mint_token

    monkeypatch.setitem(src.config.app.CONFIG["rate_limits"], "api_per_minute", 2)
    _set_provider(monkeypatch, "google")
    token, _ = mint_token("u@example.com", kind="web", db_path=fresh_db.db_path)
    client = _remote(api_module)
    headers = {"authorization": f"Bearer {token}"}

    assert [client.get("/usage", headers=headers).status_code for _ in range(2)] == [
        200,
        200,
    ]
    limited = client.get("/usage", headers=headers)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1

    _set_provider(monkeypatch, "local")
    owner = TestClient(api_module.app)  # direct loopback: no token, never limited
    assert all(owner.get("/usage").status_code == 200 for _ in range(5))
