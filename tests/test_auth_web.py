"""Tests for PR 2 Google OAuth web login
(docs/design/specs/pr2-google-oauth-web-login.md).

The Google handshake (authorize URL, code exchange, ID-token verification) is
mocked at the src.auth.google seam functions; the final test drives the real
authlib code path with a mocked HTTP transport and a joserfc-signed ID token.
"""

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

SESSION_COOKIE = "tokenage_session"


def _routes_module():
    """Fresh handle to the running app's src.auth.routes module.

    Must be called after the api_module fixture has loaded (it triggers the
    isolated, per-test reimport); a module-level import would bind a stale
    instance from an earlier test.
    """
    import src.auth.routes as auth_routes

    return auth_routes


def _enable_auth(
    monkeypatch,
    allowlist=(),
    client_id="test-client-id",
    client_secret="test-client-secret",
):
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG,
        "auth",
        {
            "provider": "google",
            "allowlist": list(allowlist),
            "google_client_id": client_id,
            "google_client_secret": client_secret,
        },
    )


def _mock_google_flow(
    api_module,
    monkeypatch,
    *,
    email="a@example.com",
    email_verified=True,
    name="Alice",
    picture=None,
    raise_oauth_failed=False,
):
    async def fake_exchange(redirect_uri, code):
        if raise_oauth_failed:
            raise _routes_module().auth_google.OAuthFlowError("boom")
        return {"id_token": "fake-id-token", "access_token": "fake-access"}

    async def fake_verify(token, nonce):
        return {
            "email": email,
            "email_verified": email_verified,
            "name": name,
            "picture": picture,
            "sub": "google-123",
        }

    monkeypatch.setattr(_routes_module().auth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(_routes_module().auth_google, "verify_id_token", fake_verify)


def _mock_authorize_url(api_module, monkeypatch):
    async def fake_build(redirect_uri, state, nonce):
        return (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"redirect_uri={redirect_uri}&state={state}&nonce={nonce}"
        )

    monkeypatch.setattr(_routes_module().auth_google, "build_authorize_url", fake_build)


def _start_login(api_module, monkeypatch, client):
    """Run the login leg and return the state token from the authorize URL."""
    _mock_authorize_url(api_module, monkeypatch)
    client.headers["origin"] = str(client.base_url).rstrip("/")
    response = client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    query = parse_qs(urlparse(location).query)
    return query["state"][0]


# ---------------------------------------------------------------- state store


def test_state_cookie_roundtrip(api_module, monkeypatch):
    _enable_auth(monkeypatch)
    auth_google = _routes_module().auth_google
    data = {"nonce": "n1", "redirect_uri": "http://x/auth/google/callback"}
    cookie = auth_google.seal_oauth_state("s1", data)
    assert auth_google.open_oauth_state(cookie, "s1") == data


def test_state_cookie_rejects_forged_expired_and_mismatched(api_module, monkeypatch):
    _enable_auth(monkeypatch)
    auth_google = _routes_module().auth_google
    cookie = auth_google.seal_oauth_state("s1", {"nonce": "n"})
    payload, _, mac = cookie.partition(".")
    for value, state in (
        (cookie, "other"),
        (cookie, ""),
        (None, "s1"),
        ("garbage", "s1"),
        (f"{payload}.{'0' * len(mac)}", "s1"),
        (f"{payload}A.{mac}", "s1"),
        ("\u00e9\u00e9.\u00e9", "s1"),
    ):
        assert auth_google.open_oauth_state(value, state) is None
    # Another secret (another deployment) cannot mint or read this one's states.
    _enable_auth(monkeypatch, client_secret="rotated")
    assert auth_google.open_oauth_state(cookie, "s1") is None
    monkeypatch.setattr(auth_google, "STATE_TTL_SECONDS", -1)
    expired = auth_google.seal_oauth_state("s1", {"nonce": "n"})
    assert auth_google.open_oauth_state(expired, "s1") is None


# ------------------------------------------------------------- auth.google.*


def test_google_login_404_under_local_provider(api_module):
    client = TestClient(api_module.app)
    assert client.get("/auth/google/login").status_code == 404


def test_google_routes_404_under_local_provider(api_module):
    client = TestClient(api_module.app)
    assert client.get("/auth/google/callback?code=x&state=y").status_code == 404


def test_google_login_503_without_credentials(api_module, monkeypatch):
    _enable_auth(monkeypatch, client_id="", client_secret="")
    response = TestClient(api_module.app).get("/auth/google/login")
    assert response.status_code == 503


def test_google_login_redirects_to_google_and_stores_state(api_module, monkeypatch):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)
    assert state  # state was generated, embedded in the authorize URL


def _state_cookie_cleared(response) -> bool:
    """Exactly one Set-Cookie for the state cookie, and it deletes it."""
    headers = [
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith("tokenage_oauth_state=")
    ]
    return len(headers) == 1 and "Max-Age=0" in headers[0]


def test_google_callback_happy_path(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch, name="Alice")
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/"
    assert _state_cookie_cleared(callback)

    cookie = callback.cookies.get(SESSION_COOKIE)
    assert cookie and cookie.startswith("tokenage_web_")

    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["provider"] == "google"
    assert body["user"]["email"] == "a@example.com"
    assert body["user"]["name"] == "Alice"
    assert body["token"] == {"kind": "web", "device_name": "browser"}


def test_google_callback_refused_when_revoke_lands_before_the_mint(
    api_module, monkeypatch, fresh_db
):
    from src.auth import tokens

    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)
    original = _routes_module().mint_token

    def revoke_before_mint(email, *args, **kwargs):
        tokens.revoke_user_tokens(email)
        return original(email, *args, **kwargs)

    monkeypatch.setattr(_routes_module(), "mint_token", revoke_before_mint)
    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=oauth_failed"
    assert _state_cookie_cleared(callback)
    assert SESSION_COOKIE not in callback.cookies
    assert SESSION_COOKIE not in client.cookies
    assert not any(
        header.startswith(f"{SESSION_COOKIE}=")
        for header in callback.headers.get_list("set-cookie")
    )
    user = tokens.get_or_create_user("a@example.com")
    assert user.token_epoch == 1
    assert tokens.list_user_tokens(user.id) == []


@pytest.mark.parametrize(
    "invalid",
    [
        "http://example.com/a",
        "https:///missing",
        "https://example.com/" + "a" * 2048,
        123,
        None,
    ],
)
def test_google_picture_refresh_and_invalid_claim(
    api_module, monkeypatch, fresh_db, invalid
):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    for picture, expected in [
        ("https://example.com/first", "https://example.com/first"),
        ("https://example.com/second", "https://example.com/second"),
        (invalid, None),
    ]:
        _mock_google_flow(api_module, monkeypatch, picture=picture)
        state = _start_login(api_module, monkeypatch, client)
        response = client.get(
            f"/auth/google/callback?code=code&state={state}", follow_redirects=False
        )
        assert response.status_code == 302
        assert client.get("/auth/me").json()["user"]["picture"] == expected


def test_second_login_keeps_the_previous_web_session(api_module, monkeypatch, fresh_db):
    """Concurrent browser logins remain independent."""
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    first = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, first)
    first.get(f"/auth/google/callback?code=the-code&state={state}")
    assert first.get("/auth/me").status_code == 200

    second = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, second)
    second.get(f"/auth/google/callback?code=the-code&state={state}")
    assert second.get("/auth/me").status_code == 200

    assert first.get("/auth/me").json()["user"] is not None
    data = second.get("/auth/devices").json()
    assert data["devices"] == []
    assert len(data["web_sessions"]) == 2
    assert sum(row["current"] for row in data["web_sessions"]) == 1
    other = next(row for row in data["web_sessions"] if not row["current"])
    assert set(other) == {"id", "device_name", "created_at", "last_used_at", "current"}
    assert second.post(f"/auth/devices/{other['id']}/revoke").status_code == 204
    assert first.get("/auth/me").json()["user"] is None
    assert second.get("/auth/me").json()["user"] is not None


def test_google_callback_state_replay(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    first = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert first.status_code == 302
    assert first.headers["location"] == "/"

    replay = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert replay.status_code == 302
    assert replay.headers["location"] == "/?auth_error=invalid_state"

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        tokens = conn.execute(text("SELECT COUNT(*) FROM auth_tokens")).scalar_one()
        users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
    assert tokens == 1
    assert users == 1


def test_google_callback_expired_state(api_module, monkeypatch, fresh_db):

    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    auth_google = _routes_module().auth_google
    monkeypatch.setattr(auth_google, "STATE_TTL_SECONDS", -1)
    client.cookies.set(
        "tokenage_oauth_state",
        auth_google.seal_oauth_state(state, {"nonce": "n", "redirect_uri": "u"}),
    )

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=invalid_state"

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        tokens = conn.execute(text("SELECT COUNT(*) FROM auth_tokens")).scalar_one()
    assert tokens == 0


def test_google_callback_missing_state(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    callback = client.get("/auth/google/callback?code=the-code", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=invalid_state"


def test_google_callback_rejects_mismatched_state_cookie(
    api_module, monkeypatch, fresh_db
):
    """OAuth login CSRF: an attacker who completes their own login and
    forwards the resulting callback URL to a victim must not be able to log
    the victim's browser into the attacker's account. The victim's browser
    never received the state cookie the attacker's browser got, so the
    callback must reject it even though the state/code are otherwise valid."""
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    attacker = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, attacker)

    victim = TestClient(api_module.app)  # no oauth-state cookie set
    callback = victim.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=invalid_state"

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        tokens = conn.execute(text("SELECT COUNT(*) FROM auth_tokens")).scalar_one()
    assert tokens == 0


def test_google_callback_rejects_non_ascii_state_cookie(
    api_module, monkeypatch, fresh_db
):
    """A non-ASCII state cookie must redirect to invalid_state, not 500 —
    secrets.compare_digest raises TypeError on non-ASCII str input, so the
    comparison must happen on bytes. Headers are built as raw byte tuples
    (bypassing httpx's own str-header ASCII validation) since real HTTP
    clients aren't constrained to ASCII cookie values the way httpx's
    high-level API is."""
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}",
        follow_redirects=False,
        headers=[(b"cookie", b"tokenage_oauth_state=" + bytes([0xE9, 0xE9]))],
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=invalid_state"


def test_google_callback_email_unverified(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch, email_verified=False)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=email_unverified"
    assert _state_cookie_cleared(callback)

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
    assert users == 0


def test_google_callback_allowlist_rejects_without_user_row(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch, allowlist=["allowed@example.com"])
    _mock_google_flow(api_module, monkeypatch, email="stranger@example.com")
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=not_allowlisted"
    assert _state_cookie_cleared(callback)

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        emails = conn.execute(text("SELECT email FROM users")).scalars().all()
    assert emails == []


def test_google_callback_allowlist_is_case_insensitive(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch, allowlist=["Allowed@Example.com"])
    _mock_google_flow(api_module, monkeypatch, email="ALLOWED@example.com")
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/"

    me = client.get("/auth/me")
    assert me.json()["user"]["email"] == "allowed@example.com"


def test_google_callback_oauth_failed(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch, raise_oauth_failed=True)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=oauth_failed"
    assert _state_cookie_cleared(callback)

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        tokens = conn.execute(text("SELECT COUNT(*) FROM auth_tokens")).scalar_one()
    assert tokens == 0


def test_google_callback_user_error_param(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?error=access_denied&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=oauth_failed"


def test_google_callback_name_backfilled_once(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    _mock_google_flow(api_module, monkeypatch, name="Alice")
    client.get(f"/auth/google/callback?code=the-code&state={state}")
    state = _start_login(api_module, monkeypatch, client)

    _mock_google_flow(api_module, monkeypatch, name="Mallory")
    client.get(f"/auth/google/callback?code=the-code&state={state}")

    me = client.get("/auth/me")
    assert me.json()["user"]["name"] == "Alice"


# --------------------------------------------------- cookie / bearer identity


def test_cookie_and_bearer_resolve_identically(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token

    bearer_token, _ = mint_token(
        "cli@example.com",
        kind="cli",
        device_name="cli-laptop",
        db_path=fresh_db.db_path,
    )
    client = TestClient(api_module.app)

    bearer_me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {bearer_token}"}
    )
    assert bearer_me.status_code == 200
    assert bearer_me.json()["token"] == {"kind": "cli", "device_name": "cli-laptop"}

    cookie_me = client.get("/auth/me", cookies={SESSION_COOKIE: bearer_token})
    assert cookie_me.status_code == 200
    assert cookie_me.json() == bearer_me.json()


def test_logout_revokes_token_and_clears_cookie(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)
    client.get(f"/auth/google/callback?code=the-code&state={state}")
    assert client.get("/auth/me").status_code == 200

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    assert logout.cookies.get(SESSION_COOKIE) is None

    assert client.get("/auth/me").json() == {"provider": "google", "user": None}
    assert client.get("/usage").status_code == 401


def test_google_callback_redirects_to_login_origin(api_module, monkeypatch, fresh_db):
    """The post-login redirect preserves the origin the login started from."""
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    _mock_authorize_url(api_module, monkeypatch)
    login = client.get(
        "/auth/google/login",
        headers={"Origin": "http://localhost:5173"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "http://localhost:5173/"

    # Replaying a consumed state has no origin left to redirect to (the
    # origin lives inside the state entry), so it falls back to the root.
    replay = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert replay.headers["location"] == "/?auth_error=invalid_state"

    # A fresh login with a failing exchange keeps the origin on the error
    # redirect, so auth_error is visible in dev.
    _mock_google_flow(api_module, monkeypatch, raise_oauth_failed=True)
    login = client.get(
        "/auth/google/login",
        headers={"Origin": "http://localhost:5173"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    failed = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert failed.headers["location"] == (
        "http://localhost:5173/?auth_error=oauth_failed"
    )


def test_google_callback_ignores_non_http_origin(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    _mock_authorize_url(api_module, monkeypatch)
    login = client.get(
        "/auth/google/login",
        headers={"Origin": "javascript:alert(1)"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.headers["location"] == "/"


def test_google_callback_ignores_untrusted_origin(api_module, monkeypatch, fresh_db):
    """A client-supplied Origin outside the known dev frontend must not be
    reflected into the redirect target (would otherwise be an open redirect)."""
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    _mock_authorize_url(api_module, monkeypatch)
    login = client.get(
        "/auth/google/login",
        headers={"Origin": "http://evil.example.com"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.headers["location"] == "/"


# ---------------------------------------------------------------- route gate


def test_gate_401_without_session(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    response = client.get("/usage")
    assert response.status_code == 401
    assert response.json() == {"detail": "login required"}


def test_gate_allows_valid_cookie(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)
    client.get(f"/auth/google/callback?code=the-code&state={state}")

    response = client.get("/usage")
    assert response.status_code == 200


def test_gate_public_paths(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    assert client.get("/auth/me").json() == {"provider": "google", "user": None}
    assert client.get("/version").status_code == 200
    login = client.get("/auth/google/login", follow_redirects=False)
    assert login.status_code == 302
    assert login.headers["location"].startswith("https://accounts.google.com/")
    # Static frontend assets and the SPA index stay public. Only assert this
    # loosely — the StaticFiles mount is conditional on frontend/dist
    # existing, so a backend-only test run shouldn't depend on it being built.
    assert client.get("/").status_code != 401


def test_gate_lets_cors_preflight_through(api_module, monkeypatch, fresh_db):
    """OPTIONS preflights are unauthenticated by design (browsers never attach
    credentials to them), so the auth gate must not 401 them — otherwise the
    usage_read_cors middleware can never answer, breaking cross-origin usage
    reads from localhost dashboards whenever auth is enabled."""
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)

    preflight = client.options(
        "/usage",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert preflight.headers["access-control-allow-methods"] == "GET"

    # The actual cross-origin GET is still gated: no session -> 401.
    gated = client.get("/usage", headers={"Origin": "http://localhost:3000"})
    assert gated.status_code == 401


def test_gate_bearer_token_also_works(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token

    token, _ = mint_token("cli@example.com", kind="cli", db_path=fresh_db.db_path)
    client = TestClient(api_module.app)
    response = client.get("/usage", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_gate_lets_loopback_owner_in_under_local_provider(api_module, fresh_db):
    client = TestClient(api_module.app)
    assert client.get("/usage").status_code == 200


def test_gate_covers_docs_endpoints(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    assert client.get("/docs").status_code == 401
    assert client.get("/redoc").status_code == 401
    assert client.get("/openapi.json").status_code == 401


# ------------------------------------------------------------------ devices


def test_devices_list_marks_current(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token

    token_a, _ = mint_token(
        "a@example.com", kind="cli", device_name="old-laptop", db_path=fresh_db.db_path
    )
    mint_token(
        "a@example.com", kind="web", device_name="browser", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    devices = client.get(
        "/auth/devices", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert devices.status_code == 200
    body = devices.json()["devices"]
    assert len(devices.json()["web_sessions"]) == 1
    assert [device["device_name"] for device in body] == ["old-laptop"]
    assert body[0]["current"] is True
    assert body[0]["kind"] == "cli"
    assert body[0]["created_at"] > 0


def test_devices_revoke_own_token(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token, resolve_token

    token_a, _ = mint_token(
        "a@example.com", kind="cli", device_name="old-laptop", db_path=fresh_db.db_path
    )
    token_b, _ = mint_token(
        "a@example.com", kind="web", device_name="browser", db_path=fresh_db.db_path
    )
    _, token_a_row = resolve_token(token_a, db_path=fresh_db.db_path)
    client = TestClient(api_module.app)
    headers = {"Authorization": f"Bearer {token_b}"}

    revoked = client.post(f"/auth/devices/{token_a_row.id}/revoke", headers=headers)
    assert revoked.status_code == 204

    remaining = client.get("/auth/devices", headers=headers).json()["devices"]
    assert remaining == []

    assert resolve_token(token_a, db_path=fresh_db.db_path) is None


def test_devices_revoke_another_users_token_is_404(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token, resolve_token

    token_a, _ = mint_token("a@example.com", kind="cli", db_path=fresh_db.db_path)
    token_b, _ = mint_token(
        "b@example.com", kind="web", device_name="browser", db_path=fresh_db.db_path
    )
    _, token_a_row = resolve_token(token_a, db_path=fresh_db.db_path)
    client = TestClient(api_module.app)

    response = client.post(
        f"/auth/devices/{token_a_row.id}/revoke",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
    assert resolve_token(token_a, db_path=fresh_db.db_path) is not None


def test_devices_revoke_unknown_id_is_404(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_token

    token_b, _ = mint_token(
        "b@example.com", kind="web", device_name="browser", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    response = client.post(
        "/auth/devices/nope/revoke",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_device_self_revoke_without_origin_rejects_cli_and_ingest(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    from src.auth.tokens import mint_device_tokens, mint_token, resolve_token

    _, user = mint_token("a@example.com", kind="web", db_path=fresh_db.db_path)
    cli, ingest, device = mint_device_tokens(
        user.id, "logout-installation", "laptop", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    report = {
        "provider": "anthropic",
        "model": "test-model",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "endpoint": "/v1/messages",
        "client_source": "claude-code",
        "status": 200,
    }
    ingest_headers = {"x-tokenage-token": ingest}
    assert (
        client.post("/ingest/v1/usage", json=report, headers=ingest_headers).status_code
        == 204
    )
    assert (
        client.post(
            f"/auth/devices/{device.id}/revoke",
            headers={"Authorization": f"Bearer {cli}"},
        ).status_code
        == 204
    )
    assert resolve_token(cli, db_path=fresh_db.db_path) is None
    assert resolve_token(ingest, db_path=fresh_db.db_path) is None
    assert (
        client.get(
            "/auth/devices", headers={"Authorization": f"Bearer {cli}"}
        ).status_code
        == 401
    )
    assert (
        client.post("/ingest/v1/usage", json=report, headers=ingest_headers).status_code
        == 401
    )


# ---------------------------------------------------------------- database


def test_update_user_name_only_backfills_null(fresh_db):
    from src.auth.tokens import mint_token, update_user_name

    _, user = mint_token("a@example.com", db_path=fresh_db.db_path)
    update_user_name(user.id, "Alice", db_path=fresh_db.db_path)

    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        name = conn.execute(
            text("SELECT name FROM users WHERE id = :uid"), {"uid": user.id}
        ).scalar_one()
    assert name == "Alice"

    update_user_name(user.id, "Mallory", db_path=fresh_db.db_path)
    with engine.connect() as conn:
        name = conn.execute(
            text("SELECT name FROM users WHERE id = :uid"), {"uid": user.id}
        ).scalar_one()
    assert name == "Alice"


def test_revoke_token_scoped_to_owner(fresh_db):
    from src.auth.tokens import (
        list_user_tokens,
        mint_token,
        resolve_token,
        revoke_token,
    )

    token_a, _ = mint_token("a@example.com", db_path=fresh_db.db_path)
    token_b, _ = mint_token("b@example.com", db_path=fresh_db.db_path)
    _, row_a = resolve_token(token_a, db_path=fresh_db.db_path)
    _, row_b = resolve_token(token_b, db_path=fresh_db.db_path)

    assert revoke_token(row_a.id, row_b.user_id, db_path=fresh_db.db_path) is False
    assert revoke_token(row_a.id, row_a.user_id, db_path=fresh_db.db_path) is True
    assert revoke_token(row_a.id, row_a.user_id, db_path=fresh_db.db_path) is False
    assert resolve_token(token_a, db_path=fresh_db.db_path) is None

    user_b = list_user_tokens(row_b.user_id, db_path=fresh_db.db_path)
    assert [token.id for token in user_b] == [row_b.id]


# ------------------------------------------- real authlib with mocked transport


def test_authlib_exchange_and_verify_with_signed_id_token(api_module, monkeypatch):
    """Drive the real authlib code path: token POST + JWKS fetch + OIDC verify.

    Proves the seam wiring (redirect_uri in the exchange, nonce + issuer +
    audience validation) end to end against a locally generated key.
    """
    from joserfc.jwk import RSAKey
    from joserfc.jwt import encode as jwt_encode

    _enable_auth(monkeypatch)
    nonce = "test-nonce-123"
    now = int(time.time())
    rsa_key = RSAKey.generate_key(2048)
    public_jwk = rsa_key.as_dict(public=True)
    public_jwk["kid"] = "k1"
    id_token = jwt_encode(
        {"alg": "RS256", "kid": "k1"},
        {
            "iss": "https://accounts.google.com",
            "sub": "google-123",
            "aud": "test-client-id",
            "exp": now + 3600,
            "iat": now,
            "nonce": nonce,
            "email": "a@example.com",
            "email_verified": True,
            "name": "Alice",
        },
        rsa_key,
    )

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://oauth2.googleapis.com/token":
            assert request.method == "POST"
            body = {
                key: values[0]
                for key, values in parse_qs(request.content.decode("utf-8")).items()
            }
            assert body["code"] == "the-code"
            assert body["redirect_uri"] == "http://test/auth/google/callback"
            assert body["grant_type"] == "authorization_code"
            return httpx.Response(
                200,
                json={
                    "id_token": id_token,
                    "access_token": "at",
                    "token_type": "Bearer",
                },
            )
        if str(request.url) == "https://www.googleapis.com/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(500, text=f"unexpected: {request.url}")

    def fake_ensure_registered():
        _routes_module().auth_google.oauth.register(
            "google",
            client_id="test-client-id",
            client_secret="test-client-secret",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            access_token_url="https://oauth2.googleapis.com/token",
            client_kwargs={
                "scope": "openid email profile",
                "transport": httpx.MockTransport(transport_handler),
            },
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
            issuer="https://accounts.google.com",
        )

    monkeypatch.setattr(
        _routes_module().auth_google, "_ensure_registered", fake_ensure_registered
    )

    async def run():
        token = await _routes_module().auth_google.exchange_code(
            "http://test/auth/google/callback", "the-code"
        )
        claims = await _routes_module().auth_google.verify_id_token(token, nonce)
        return claims

    claims = asyncio.run(run())
    assert claims["email"] == "a@example.com"
    assert claims["email_verified"] is True
    assert claims["name"] == "Alice"


def test_authlib_verify_jwks_fetch_failure_redirects_not_500(
    api_module, monkeypatch, fresh_db
):
    """A failed JWKS fetch (network outage, rate limit, key rotation) must
    surface as auth_error=oauth_failed, not a raw 500.

    authlib raises httpx.HTTPStatusError from raise_for_status() on the certs
    fetch; verify_id_token must convert it to OAuthFlowError like it does for
    the exchange leg, or the callback leaks a 500 page in the browser.
    """
    from joserfc.jwk import RSAKey
    from joserfc.jwt import encode as jwt_encode

    _enable_auth(monkeypatch)
    now = int(time.time())
    rsa_key = RSAKey.generate_key(2048)
    id_token = jwt_encode(
        {"alg": "RS256", "kid": "k1"},
        {
            "iss": "https://accounts.google.com",
            "sub": "google-123",
            "aud": "test-client-id",
            "exp": now + 3600,
            "iat": now,
            "nonce": "test-nonce-123",
            "email": "a@example.com",
            "email_verified": True,
        },
        rsa_key,
    )

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://oauth2.googleapis.com/token":
            return httpx.Response(
                200, json={"id_token": id_token, "access_token": "at"}
            )
        if str(request.url) == "https://www.googleapis.com/oauth2/v3/certs":
            return httpx.Response(502, text="gateway error")
        return httpx.Response(500)

    def fake_ensure_registered():
        _routes_module().auth_google.oauth.register(
            "google",
            client_id="test-client-id",
            client_secret="test-client-secret",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            access_token_url="https://oauth2.googleapis.com/token",
            client_kwargs={
                "scope": "openid email profile",
                "transport": httpx.MockTransport(transport_handler),
            },
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
            issuer="https://accounts.google.com",
        )

    monkeypatch.setattr(
        _routes_module().auth_google, "_ensure_registered", fake_ensure_registered
    )

    _mock_authorize_url(api_module, monkeypatch)
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/?auth_error=oauth_failed"


def test_authlib_verify_rejects_wrong_nonce(api_module, monkeypatch):
    from joserfc.jwk import RSAKey
    from joserfc.jwt import encode as jwt_encode

    _enable_auth(monkeypatch)
    now = int(time.time())
    rsa_key = RSAKey.generate_key(2048)
    public_jwk = rsa_key.as_dict(public=True)
    public_jwk["kid"] = "k1"
    id_token = jwt_encode(
        {"alg": "RS256", "kid": "k1"},
        {
            "iss": "https://accounts.google.com",
            "sub": "google-123",
            "aud": "test-client-id",
            "exp": now + 3600,
            "iat": now,
            "nonce": "expected-nonce",
            "email": "a@example.com",
            "email_verified": True,
        },
        rsa_key,
    )

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://oauth2.googleapis.com/token":
            return httpx.Response(
                200, json={"id_token": id_token, "access_token": "at"}
            )
        if str(request.url) == "https://www.googleapis.com/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(500)

    def fake_ensure_registered():
        _routes_module().auth_google.oauth.register(
            "google",
            client_id="test-client-id",
            client_secret="test-client-secret",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            access_token_url="https://oauth2.googleapis.com/token",
            client_kwargs={
                "scope": "openid email profile",
                "transport": httpx.MockTransport(transport_handler),
            },
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
            issuer="https://accounts.google.com",
        )

    monkeypatch.setattr(
        _routes_module().auth_google, "_ensure_registered", fake_ensure_registered
    )

    async def run():
        token = await _routes_module().auth_google.exchange_code(
            "http://x/cb", "the-code"
        )
        with pytest.raises(_routes_module().auth_google.OAuthFlowError):
            await _routes_module().auth_google.verify_id_token(token, "wrong-nonce")

    asyncio.run(run())


def test_web_idle_expiry_and_activity(api_module, monkeypatch, fresh_db):
    from src.auth import tokens
    from src.database.engine import get_engine

    now = 10_000_000_000_000
    monkeypatch.setattr(tokens, "_now_micros", lambda: now)
    web, user = tokens.mint_token(
        "idle@example.com", kind="web", db_path=fresh_db.db_path
    )
    cli, _ = tokens.mint_token("idle@example.com", kind="cli", db_path=fresh_db.db_path)
    with get_engine(fresh_db.db_path).begin() as conn:
        conn.execute(
            text("UPDATE auth_tokens SET created_at = :old, last_used_at = NULL"),
            {"old": now - tokens.WEB_IDLE_TIMEOUT_MICROS},
        )
    assert tokens.resolve_token(web, fresh_db.db_path) is None
    assert tokens.resolve_token(cli, fresh_db.db_path) is not None
    assert all(
        row.kind != "web" for row in tokens.list_user_tokens(user.id, fresh_db.db_path)
    )
    with get_engine(fresh_db.db_path).begin() as conn:
        conn.execute(
            text("UPDATE auth_tokens SET last_used_at = :recent WHERE kind = 'web'"),
            {"recent": now - tokens.ACTIVITY_UPDATE_INTERVAL_MICROS - 1},
        )
    assert tokens.resolve_token(web, fresh_db.db_path) is not None
    with get_engine(fresh_db.db_path).connect() as conn:
        assert (
            conn.execute(
                text("SELECT last_used_at FROM auth_tokens WHERE kind = 'web'")
            ).scalar_one()
            == now
        )


def test_web_session_owner_scope_and_label(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    first = TestClient(
        api_module.app,
        headers={
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Chrome/120 Safari/537"
        },
    )
    state = _start_login(api_module, monkeypatch, first)
    first.get(f"/auth/google/callback?code=x&state={state}")
    session = first.get("/auth/devices").json()["web_sessions"][0]
    assert session["device_name"] == "Chrome on macOS"
    _mock_google_flow(api_module, monkeypatch, email="other@example.com")
    other = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, other)
    other.get(f"/auth/google/callback?code=x&state={state}")
    assert other.post(f"/auth/devices/{session['id']}/revoke").status_code == 404
    assert other.post("/auth/devices/missing/revoke").status_code == 404
    assert first.get("/auth/me").json()["user"] is not None


def test_only_ingest_activity_updates_machine_last_seen(api_module, fresh_db):
    from src.auth import tokens
    from src.database.engine import get_engine

    _, user = tokens.mint_token(
        "seen@example.com", kind="web", db_path=fresh_db.db_path
    )
    cli, ingest, device = tokens.mint_device_tokens(
        user.id, "seen-installation", "laptop", db_path=fresh_db.db_path
    )
    web, _ = tokens.mint_token("seen@example.com", kind="web", db_path=fresh_db.db_path)

    def last_seen():
        with get_engine(fresh_db.db_path).connect() as conn:
            return conn.execute(
                text("SELECT last_seen_at FROM devices WHERE id = :id"),
                {"id": device.id},
            ).scalar_one()

    with get_engine(fresh_db.db_path).begin() as conn:
        conn.execute(text("UPDATE devices SET last_seen_at = 1"))
    assert tokens.resolve_token(web, fresh_db.db_path) is not None
    assert tokens.resolve_token(cli, fresh_db.db_path) is not None
    assert last_seen() == 1
    assert tokens.resolve_token(ingest, fresh_db.db_path) is not None
    assert last_seen() > 1


def test_picture_write_failure_does_not_raise(api_module, monkeypatch, fresh_db):
    from sqlalchemy.exc import SQLAlchemyError

    from src.auth import tokens

    def fail(_db_path=None):
        raise SQLAlchemyError("down")

    monkeypatch.setattr(tokens, "get_engine", fail)
    tokens.update_user_picture("missing", "https://example.com/a.png")


def _limit_signin(monkeypatch, limit):
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG["rate_limits"], "signin_per_minute", limit
    )


@pytest.mark.parametrize(
    "path",
    [
        "/auth/google/login",
        "/auth/cli/start?code_challenge=" + "a" * 43,
        "/auth/local/login?code=invalid",
    ],
)
def test_anonymous_signin_starts_share_per_ip_limit(api_module, monkeypatch, path):
    if "/local/" not in path:
        _enable_auth(monkeypatch)
    _mock_authorize_url(api_module, monkeypatch)
    client = TestClient(api_module.app, client=("203.0.113.1", 1))
    # Off by default.
    for _ in range(3):
        assert client.get(path, follow_redirects=False).status_code != 429
    _limit_signin(monkeypatch, 2)
    for i in range(2):
        # A direct caller cannot rotate its budget with forged forwarding headers.
        response = client.get(
            path,
            headers={"x-forwarded-for": f"198.51.100.{i}"},
            follow_redirects=False,
        )
        assert response.status_code != 429
    limited = client.get(path, follow_redirects=False)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    other = TestClient(api_module.app, client=("203.0.113.2", 1))
    assert other.get(path, follow_redirects=False).status_code != 429


@pytest.mark.parametrize(
    "path", ["/auth/google/login", "/auth/cli/start?code_challenge=" + "a" * 43]
)
def test_signin_limit_runs_off_the_event_loop(api_module, monkeypatch, path):
    import asyncio

    import src.ratelimit

    _enable_auth(monkeypatch)
    _mock_authorize_url(api_module, monkeypatch)
    _limit_signin(monkeypatch, 5)
    on_loop = []
    original = src.ratelimit.rate_limit_hit

    def rate_limit_hit(key, limit_name):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return original(key, limit_name)

    monkeypatch.setattr(src.ratelimit, "rate_limit_hit", rate_limit_hit)
    client = TestClient(api_module.app, client=("203.0.113.1", 1))
    assert client.get(path, follow_redirects=False).status_code == 302
    assert on_loop == [False]


def test_signin_limit_keys_ipv6_by_64_and_mapped_ipv4_as_ipv4():
    from starlette.requests import Request

    from src.auth.signin import _client_key

    def key(host):
        return _client_key(Request({"type": "http", "client": (host, 1)}))

    assert key("2001:db8::1") == key("2001:db8::ffff") == "2001:db8::/64"
    assert key("2001:db8:0:1::1") != key("2001:db8::1")
    assert key("::ffff:203.0.113.1") == "203.0.113.1"
    assert key("::ffff:203.0.113.1") != key("::ffff:203.0.113.2")


def test_signed_in_cli_approval_spends_no_signin_budget(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    _limit_signin(monkeypatch, 1)
    from src.auth.tokens import mint_token

    token, _ = mint_token("a@example.com", kind="web")
    client = TestClient(api_module.app, client=("203.0.113.1", 1))
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["origin"] = str(client.base_url).rstrip("/")
    for _ in range(3):
        assert (
            client.get("/auth/cli/start", params={"code_challenge": "a" * 43})
        ).status_code == 200
        assert (
            client.post("/auth/cli/start", data={"code_challenge": "a" * 43})
        ).status_code == 200


def test_signin_flood_does_not_evict_another_users_pending_login(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    auth_google = _routes_module().auth_google
    # Even tiny store caps cannot be reached: anonymous starts store nothing.
    monkeypatch.setattr(auth_google, "MAX_PENDING_STATES", 1)
    monkeypatch.setattr(auth_google, "MAX_PENDING_CLI_CODES", 1)
    victim = TestClient(api_module.app, client=("203.0.113.1", 1))
    state = _start_login(api_module, monkeypatch, victim)
    flooder = TestClient(api_module.app, client=("203.0.113.1", 2))
    for i in range(10):
        path = "/auth/google/login" if i % 2 else "/auth/cli/start"
        response = flooder.get(
            path, params={"code_challenge": "a" * 43}, follow_redirects=False
        )
        assert response.status_code == 302
    callback = victim.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.headers["location"] == "/"
    assert SESSION_COOKIE in callback.cookies


def test_signin_uses_only_server_trusted_proxy_headers(api_module, monkeypatch):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    _enable_auth(monkeypatch)
    _mock_authorize_url(api_module, monkeypatch)
    _limit_signin(monkeypatch, 1)
    app = ProxyHeadersMiddleware(api_module.app, trusted_hosts=["10.0.0.2"])
    trusted = TestClient(app, client=("10.0.0.2", 1))
    for ip in ("203.0.113.1", "203.0.113.2"):
        assert (
            trusted.get(
                "/auth/google/login",
                headers={"x-forwarded-for": ip},
                follow_redirects=False,
            ).status_code
            == 302
        )
    untrusted = TestClient(app, client=("10.0.0.3", 1))
    for i, ip in enumerate(("203.0.113.3", "203.0.113.4")):
        assert untrusted.get(
            "/auth/google/login",
            headers={"x-forwarded-for": ip},
            follow_redirects=False,
        ).status_code == (302 if i == 0 else 429)


def test_google_callback_rejects_local_owner_identity(api_module, monkeypatch):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch, email="owner@localhost")
    client = TestClient(api_module.app)
    state = _start_login(api_module, monkeypatch, client)
    response = client.get(
        "/auth/google/callback",
        params={"state": state, "code": "x"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?auth_error=not_allowlisted"
