"""Tests for PR 3 CLI login flow (docs/design/specs/pr3-cli-login-flow.md).

The Google handshake is mocked at the same seam functions as
test_auth_web.py; the one-time code + PKCE exchange run for real.
"""

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import drain

SESSION_COOKIE = "tokenage_session"


def _routes_module():
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


def _mock_google_flow(api_module, monkeypatch, *, email="a@example.com"):
    async def fake_exchange(redirect_uri, code):
        return {"id_token": "fake-id-token", "access_token": "fake-access"}

    async def fake_verify(token, nonce):
        return {
            "email": email,
            "email_verified": True,
            "name": "Alice",
            "sub": "google-123",
        }

    async def fake_build(redirect_uri, state, nonce):
        return (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"redirect_uri={redirect_uri}&state={state}&nonce={nonce}"
        )

    monkeypatch.setattr(_routes_module().auth_google, "exchange_code", fake_exchange)
    monkeypatch.setattr(_routes_module().auth_google, "verify_id_token", fake_verify)
    monkeypatch.setattr(_routes_module().auth_google, "build_authorize_url", fake_build)


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _start_params(device="myhost"):
    _, challenge = _pkce_pair()
    return {"code_challenge": challenge, "device_name": device}


def _web_login(api_module, monkeypatch, client, email="a@example.com"):
    """Run the mocked web flow and leave the session cookie on `client`."""
    _mock_google_flow(api_module, monkeypatch, email=email)
    response = client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 302
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert client.cookies.get(SESSION_COOKIE)


def _exchange(client, code, verifier, **extra):
    extra.setdefault("installation_key", secrets.token_urlsafe(32))
    return client.post(
        "/auth/cli/exchange",
        json={"code": code, "code_verifier": verifier, **extra},
    )


def _approve_and_exchange(client, device_name, **extra):
    verifier, challenge = _pkce_pair()
    approved = client.post(
        "/auth/cli/start",
        data={"code_challenge": challenge, "device_name": device_name},
    )
    assert approved.status_code == 200
    return _exchange(client, _code_from_page(approved.text), verifier, **extra)


def _code_from_page(text: str) -> str:
    match = re.search(r'id="cli-code"[^>]*>([^<]+)<', text)
    assert match, "code page did not contain the cli-code element"
    return match.group(1)


def _token_rows(fresh_db):
    engine = fresh_db.database_module.get_engine(fresh_db.db_path)
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT kind, device_name, device_id IS NOT NULL, revoked_at"
                " FROM auth_tokens"
            )
        ).all()


# ---------------------------------------------------------------- disabled


def test_cli_start_without_a_user_is_401_under_local_provider(api_module):
    remote = TestClient(
        api_module.app, client=("203.0.113.9", 5), base_url="http://tracker.example"
    )
    response = remote.get("/auth/cli/start", params=_start_params())
    assert response.status_code == 401
    assert "login-link" in response.json()["detail"]
    assert remote.post("/auth/cli/start", data=_start_params()).status_code == 401


# ---------------------------------------------------------- session shortcut


def test_session_shortcut_confirm_and_exchange(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    verifier, challenge = _pkce_pair()
    params = {
        "code_challenge": challenge,
        "device_name": "<evil>host",
    }

    page = client.get("/auth/cli/start", params=params)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert page.headers["cache-control"] == "no-store"
    # Attacker-influenced device name is escaped in the reflected page.
    assert "&lt;evil&gt;host" in page.text
    assert "a@example.com" in page.text
    assert "Only approve logins you started" in page.text
    # The approve form posts back with the flow parameters as hidden inputs.
    assert 'method="post"' in page.text and 'name="code_challenge"' in page.text
    assert 'name="device_name"' in page.text

    # A browser submits the hidden inputs as form-encoded body fields.
    approved = client.post("/auth/cli/start", data=params)
    assert approved.status_code == 200
    assert "text/html" in approved.headers["content-type"]
    assert approved.headers["cache-control"] == "no-store"
    # The code is delivered in the response body, never in a URL.
    assert "location" not in approved.headers
    code = _code_from_page(approved.text)
    assert re.fullmatch(
        r"[BCDFGHJKLMNPQRSTVWXZ]{4}(-[BCDFGHJKLMNPQRSTVWXZ]{4}){2}", code
    )

    exchanged = _exchange(client, code, verifier)
    assert exchanged.status_code == 200
    body = exchanged.json()
    assert body["user"]["email"] == "a@example.com"
    assert body["device_name"] == "<evil>host"
    assert body["device_id"]
    assert body["cli_token"].startswith("tokenage_cli_")
    assert body["ingest_token"].startswith("tokenage_ingest_")
    assert body["otlp"]["endpoint"].endswith("/otlp")
    assert body["otlp"]["logs_endpoint"] == body["otlp"]["endpoint"] + "/v1/logs"

    kinds = sorted(
        (kind, device, linked, revoked is None)
        for kind, device, linked, revoked in _token_rows(fresh_db)
    )
    # 1 web (from _web_login, unlinked) + 1 linked cli + 1 linked ingest.
    assert kinds == [
        ("cli", "<evil>host", True, True),
        ("ingest", "<evil>host", True, True),
        ("web", "browser", False, True),
    ]


def test_post_without_session_is_401(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    response = client.post("/auth/cli/start", data=_start_params())
    assert response.status_code == 401


# ------------------------------------------------------------ Google path


def test_google_path_renders_code_page_without_web_session(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    verifier, challenge = _pkce_pair()
    params = {
        "code_challenge": challenge,
        "device_name": "myhost",
    }

    start = client.get("/auth/cli/start", params=params, follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"].startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    callback = client.get(
        f"/auth/google/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 200
    assert "location" not in callback.headers
    assert callback.headers["cache-control"] == "no-store"
    # CLI flow mints no web session.
    assert callback.cookies.get(SESSION_COOKIE) is None
    code = _code_from_page(callback.text)

    exchanged = _exchange(client, code, verifier)
    assert exchanged.status_code == 200
    assert exchanged.json()["device_name"] == "myhost"


def test_exchange_normalizes_code(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    verifier, challenge = _pkce_pair()
    params = {"code_challenge": challenge, "device_name": "d"}
    approved = client.post("/auth/cli/start", data=params)
    code = _code_from_page(approved.text)

    # Lowercased, hyphen-stripped, whitespace-padded input still exchanges.
    mangled = " " + code.lower().replace("-", " ") + " "
    assert _exchange(client, mangled, verifier).status_code == 200


def test_exchange_replay_fails(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    verifier, challenge = _pkce_pair()
    params = {"code_challenge": challenge, "device_name": "d"}
    approved = client.post("/auth/cli/start", data=params)
    code = _code_from_page(approved.text)

    assert _exchange(client, code, verifier).status_code == 200
    replay = _exchange(client, code, verifier)
    assert replay.status_code == 400
    assert replay.json()["detail"] == "invalid code"


def test_exchange_wrong_verifier_fails_and_mints_nothing(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    _, challenge = _pkce_pair()
    params = {"code_challenge": challenge, "device_name": "d"}
    approved = client.post("/auth/cli/start", data=params)
    code = _code_from_page(approved.text)

    before = len(_token_rows(fresh_db))
    response = _exchange(client, code, secrets.token_urlsafe(48))
    assert response.status_code == 400
    assert len(_token_rows(fresh_db)) == before  # no rows on failed exchange


def test_exchange_expired_code_fails(api_module, monkeypatch, fresh_db, isolated_home):

    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    _, challenge = _pkce_pair()
    params = {"code_challenge": challenge, "device_name": "d"}
    approved = client.post("/auth/cli/start", data=params)
    code = _code_from_page(approved.text)

    from src.shared_state import get_shared_state

    get_shared_state().set(
        _routes_module().auth_google.CLI_CODE_NAMESPACE, code.replace("-", ""), {}, -1
    )

    assert _exchange(client, code, "anything").status_code == 400


def test_exchange_unknown_code_fails(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    assert _exchange(client, "no-such-code", "v").status_code == 400


def test_exchange_missing_fields_rejected(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    # installation_key is required, and the rejection never echoes the secret.
    missing = client.post(
        "/auth/cli/exchange",
        json={"code": "ABC", "code_verifier": "xyz"},
    )
    assert missing.status_code == 422
    assert "installation_key" in missing.text
    assert "ABC" not in missing.text
    # Present but empty code fields are the handler's 400.
    assert (
        client.post(
            "/auth/cli/exchange",
            json={"code": "", "code_verifier": "", "installation_key": "a" * 64},
        ).status_code
        == 400
    )


def test_malformed_client_fields_422_and_leave_the_code_unusable(
    api_module, monkeypatch, fresh_db
):
    # A rejected client_version must not consume the one-time code, and must
    # not answer 400 — the CLI retries 400 with an identical body three times.
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    verifier, challenge = _pkce_pair()
    approved = client.post(
        "/auth/cli/start",
        data={"code_challenge": challenge, "device_name": "d"},
    )
    code = _code_from_page(approved.text)

    rejected = _exchange(
        client,
        code,
        verifier,
        installation_key="a" * 64,
        client_version="0.1.0-rc1",
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "invalid client_version"

    # Code survives the rejection: a corrected retry still redeems it.
    assert (
        _exchange(
            client,
            code,
            verifier,
            installation_key="a" * 64,
            client_version="0.1.180",
        ).status_code
        == 200
    )


# -------------------------------------------------------- revoke-and-remint


def test_installed_machine_relogin_rotates_both_tokens_and_revoke_stops_ingest(
    api_module, otlp_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    installation_key = "a" * 64
    first = _approve_and_exchange(
        client,
        "laptop",
        installation_key=installation_key,
        client_version="0.1.180",
        client_commit="b" * 40,
    )
    assert first.status_code == 200
    first_tokens = first.json()
    device_id = first_tokens["device_id"]
    assert device_id

    second = _approve_and_exchange(
        client,
        "renamed-laptop",
        installation_key=installation_key,
        device_id=device_id,
        client_version="0.1.181",
        client_commit="c" * 40,
    )
    assert second.status_code == 200
    second_tokens = second.json()
    assert second_tokens["device_id"] == device_id
    from src.auth.tokens import resolve_token

    assert resolve_token(first_tokens["cli_token"]) is None
    assert resolve_token(first_tokens["ingest_token"]) is None
    assert resolve_token(second_tokens["cli_token"]) is not None
    assert resolve_token(second_tokens["ingest_token"]) is not None

    devices = client.get("/auth/devices").json()["devices"]
    machine = [row for row in devices if row["kind"] == "client"]
    assert len(machine) == 1
    assert machine[0]["id"] == device_id
    assert machine[0]["device_name"] == "renamed-laptop"
    assert machine[0]["client_version"] == "0.1.181"
    assert machine[0]["client_commit"] == "c" * 40

    assert client.post(f"/auth/devices/{device_id}/revoke").status_code == 204
    assert resolve_token(second_tokens["cli_token"]) is None
    assert resolve_token(second_tokens["ingest_token"]) is None
    otlp_client = TestClient(otlp_module.app)
    rejected = otlp_client.post(
        "/v1/logs",
        json={},
        headers={"x-tokenage-token": second_tokens["ingest_token"]},
    )
    drain()
    assert rejected.status_code == 401


def test_same_hostname_distinct_installations(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)
    from src.auth.tokens import resolve_token

    first = _approve_and_exchange(client, "laptop", installation_key="a" * 64)
    second = _approve_and_exchange(client, "laptop", installation_key="b" * 64)
    assert first.status_code == second.status_code == 200
    assert first.json()["device_id"] != second.json()["device_id"]
    # A second installation never revokes the first one's credentials.
    assert resolve_token(first.json()["cli_token"]) is not None
    assert resolve_token(first.json()["ingest_token"]) is not None
    machines = [
        row
        for row in client.get("/auth/devices").json()["devices"]
        if row["kind"] == "client"
    ]
    assert len(machines) == 2
    assert (
        client.post(f"/auth/devices/{first.json()['device_id']}/revoke").status_code
        == 204
    )
    assert resolve_token(second.json()["ingest_token"]) is not None
    assert resolve_token(first.json()["ingest_token"]) is None


def test_installation_key_is_scoped_to_user_and_cross_user_revoke_is_404(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    first_client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, first_client, email="a@example.com")
    first = _approve_and_exchange(
        first_client, "shared-hostname", installation_key="a" * 64
    )
    assert first.status_code == 200

    second_client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, second_client, email="b@example.com")
    second = _approve_and_exchange(
        second_client,
        "shared-hostname",
        installation_key="a" * 64,
        device_id=first.json()["device_id"],
    )
    assert second.status_code == 200
    assert second.json()["device_id"] != first.json()["device_id"]
    assert (
        second_client.post(
            f"/auth/devices/{first.json()['device_id']}/revoke"
        ).status_code
        == 404
    )
    from src.auth.tokens import resolve_token

    assert resolve_token(first.json()["ingest_token"]) is not None


def test_exchange_creates_device_pair_and_leaves_unlinked_tokens_alone(
    api_module, monkeypatch, fresh_db
):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _web_login(api_module, monkeypatch, client)

    tokens = _routes_module().mint_token
    tokens("a@example.com", kind="cli", device_name="myhost")
    tokens("a@example.com", kind="ingest", device_name="myhost")
    tokens("a@example.com", kind="cli", device_name="otherhost")

    verifier, challenge = _pkce_pair()
    params = {
        "code_challenge": challenge,
        "device_name": "myhost",
    }
    approved = client.post("/auth/cli/start", data=params)
    code = _code_from_page(approved.text)
    assert _exchange(client, code, verifier).status_code == 200

    rows = {
        (kind, device, linked): revoked is None
        for kind, device, linked, revoked in _token_rows(fresh_db)
    }
    # The exchange registered a device: linked cli + ingest pair.
    assert rows[("cli", "myhost", True)]
    assert rows[("ingest", "myhost", True)]
    # Operator-minted cli tokens are untouched by client logins (minted ingest
    # tokens are device-bound, so they are not "unlinked").
    assert rows[("cli", "myhost", False)]
    assert rows[("cli", "otherhost", False)]
    assert rows[("web", "browser", False)]


# ------------------------------------------------------------- validation


def test_start_validation_rejects_bad_params(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    _, challenge = _pkce_pair()
    ok = {"code_challenge": challenge, "device_name": "d"}

    short_challenge = {**ok, "code_challenge": "tooshort"}
    assert client.get("/auth/cli/start", params=short_challenge).status_code == 422
    missing = {k: v for k, v in ok.items() if k != "code_challenge"}
    assert client.get("/auth/cli/start", params=missing).status_code == 422


def test_start_google_path_503_without_credentials(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch, client_id="", client_secret="")
    client = TestClient(api_module.app)
    response = client.get("/auth/cli/start", params=_start_params())
    assert response.status_code == 503


def test_gate_allows_unauthenticated_cli_start(api_module, monkeypatch, fresh_db):
    _enable_auth(monkeypatch)
    _mock_google_flow(api_module, monkeypatch)
    client = TestClient(api_module.app)
    # No cookie: the gate must let the request reach the route (302 to
    # Google, not 401 login-required).
    response = client.get(
        "/auth/cli/start", params=_start_params(), follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")


# ---------------------------------------------------------- code store unit


def test_cli_code_store_roundtrip_and_single_use(api_module, isolated_home):
    auth_google = _routes_module().auth_google
    auth_google.store_cli_code("c1", {"user_id": "u", "code_challenge": "x"})
    entry = auth_google.pop_cli_code("c1")
    assert entry is not None
    assert entry["user_id"] == "u"
    assert auth_google.pop_cli_code("c1") is None


def test_cli_code_store_rejects_expired(api_module, isolated_home):

    auth_google = _routes_module().auth_google
    auth_google.store_cli_code("old", {"user_id": "u"})
    from src.shared_state import get_shared_state

    get_shared_state().set(auth_google.CLI_CODE_NAMESPACE, "old", {}, -1)
    assert auth_google.pop_cli_code("old") is None


def test_cli_code_store_caps_pending_entries(api_module, isolated_home, monkeypatch):
    auth_google = _routes_module().auth_google
    monkeypatch.setattr(auth_google, "MAX_PENDING_STATES", 3)
    for i in range(4):
        auth_google.store_cli_code(f"c{i}", {"user_id": "u"})

    assert auth_google.pop_cli_code("c0") is None
    for i in range(1, 4):
        assert auth_google.pop_cli_code(f"c{i}") is not None
