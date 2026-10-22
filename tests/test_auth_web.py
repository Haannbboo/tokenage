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
    response = client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    query = parse_qs(urlparse(location).query)
    return query["state"][0]


# ---------------------------------------------------------------- state store


def test_state_store_roundtrip(api_module, isolated_home):
    auth_google = _routes_module().auth_google
    auth_google.store_oauth_state(
        "s1", {"nonce": "n1", "redirect_uri": "http://x/auth/google/callback"}
    )
    entry = auth_google.pop_oauth_state("s1")
    assert entry == {
        "nonce": "n1",
        "redirect_uri": "http://x/auth/google/callback",
    }
    # Single-use: a second pop of the same state is a miss.
    assert auth_google.pop_oauth_state("s1") is None


def test_state_store_rejects_expired_and_missing(api_module, isolated_home):
    auth_google = _routes_module().auth_google
    auth_google.store_oauth_state("old", {"nonce": "n", "redirect_uri": "u"})
    from src.shared_state import get_shared_state

    get_shared_state().set(auth_google.OAUTH_STATE_NAMESPACE, "old", {}, -1)

    assert auth_google.pop_oauth_state("old") is None
    assert auth_google.pop_oauth_state("never-seen") is None


def test_state_store_prunes_expired_on_insert(api_module, isolated_home):
    auth_google = _routes_module().auth_google
    auth_google.store_oauth_state("a", {"nonce": "n", "redirect_uri": "u"})
    from src.shared_state import get_shared_state

    store = get_shared_state()
    store.set(auth_google.OAUTH_STATE_NAMESPACE, "a", {}, -1)
    auth_google.store_oauth_state("b", {"nonce": "n", "redirect_uri": "u"})
    assert store.get(auth_google.OAUTH_STATE_NAMESPACE, "a") is None
    assert store.get(auth_google.OAUTH_STATE_NAMESPACE, "b") is not None


def test_state_store_caps_pending_entries(api_module, isolated_home, monkeypatch):
    auth_google = _routes_module().auth_google
    monkeypatch.setattr(auth_google, "MAX_PENDING_STATES", 3)
    for i in range(4):
        auth_google.store_oauth_state(f"s{i}", {"nonce": "n", "redirect_uri": "u"})

    assert auth_google.pop_oauth_state("s0") is None
    for i in range(1, 4):
        assert auth_google.pop_oauth_state(f"s{i}") is not None


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

    cookie = callback.cookies.get(SESSION_COOKIE)
    assert cookie and cookie.startswith("tokenage_web_")

    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["provider"] == "google"
    assert body["user"]["email"] == "a@example.com"
    assert body["user"]["name"] == "Alice"
    assert body["token"] == {"kind": "web", "device_name": "browser"}


def test_second_login_retires_the_previous_web_session(
    api_module, monkeypatch, fresh_db
):
    """One live browser session per user: the new login revokes the old cookie."""
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

    assert first.get("/auth/me").json()["user"] is None

    # The surviving session is a cookie, not a listed device.
    assert second.get("/auth/devices").json()["devices"] == []


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

    from src.shared_state import get_shared_state

    get_shared_state().set(
        _routes_module().auth_google.OAUTH_STATE_NAMESPACE, state, {}, -1
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
    # The browser session is not a device: a new login replaces it.
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
