"""Google OAuth web login flow (docs/design/specs/pr2-google-oauth-web-login.md).

authlib handles the OAuth2/OIDC handshake (authorization URL construction,
code exchange, ID-token verification). A pending OAuth ``state`` and its data
travel in a signed, expiring HttpOnly cookie, so an anonymous sign-in start
allocates nothing server-side that another start could evict.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError

from src.config.app import CONFIG
from src.shared_state import get_shared_state

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 5 * 60
MAX_PENDING_STATES = 512

# One-time CLI and browser login codes share the same store and TTL.
CLI_CODE_NAMESPACE = "cli-codes"
CLI_CODE_TTL_SECONDS = 300
# Codes are minted only by signed-in approvals (rate_limits.cli_approve_per_minute
# per user) and completed Google sign-ins. Keep the cap small: the SQLite store
# counts the namespace inside its write lock on every insert.
MAX_PENDING_CLI_CODES = 4096
LOGIN_CODE_NAMESPACE = "login-codes"

# Google endpoints are pinned (no discovery-document fetch at runtime); the
# only outbound calls are the token exchange and the JWKS fetch.
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUER = "https://accounts.google.com"

oauth = OAuth()


class OAuthFlowError(Exception):
    """The Google handshake failed (misconfiguration, exchange, or verify)."""


def google_credentials() -> tuple[str, str] | None:
    """Return (client_id, client_secret) from env-backed config, or None."""
    auth = CONFIG.get("auth", {})
    client_id = str(auth.get("google_client_id", "") or "")
    client_secret = str(auth.get("google_client_secret", "") or "")
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def _ensure_registered() -> None:
    credentials = google_credentials()
    if credentials is None:
        raise OAuthFlowError("google oauth is not configured")
    client_id, client_secret = credentials
    oauth.register(
        "google",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url=GOOGLE_AUTHORIZE_URL,
        access_token_url=GOOGLE_TOKEN_URL,
        client_kwargs={"scope": "openid email profile"},
        jwks_uri=GOOGLE_JWKS_URI,
        issuer=GOOGLE_ISSUER,
    )


async def build_authorize_url(redirect_uri: str, state: str, nonce: str) -> str:
    """Build the Google consent-screen URL for this state/nonce."""
    _ensure_registered()
    result = await oauth.google.create_authorization_url(
        redirect_uri, state=state, nonce=nonce
    )
    return str(result["url"])


async def exchange_code(redirect_uri: str, code: str) -> dict[str, Any]:
    """Exchange an authorization code for tokens (authlib/httpx)."""
    _ensure_registered()
    try:
        token = await oauth.google.fetch_access_token(
            redirect_uri=redirect_uri, code=code
        )
    except (OAuthError, httpx.HTTPError) as exc:
        logger.warning("Google token exchange failed: %s", exc)
        raise OAuthFlowError("token exchange failed") from exc
    return dict(token)


async def verify_id_token(token: dict[str, Any], nonce: str) -> dict[str, Any]:
    """Verify the ID token (signature, nonce, issuer, audience) and return claims."""
    _ensure_registered()
    try:
        claims = await oauth.google.parse_id_token(token, nonce=nonce)
    except (OAuthError, JoseError, httpx.HTTPError) as exc:
        # httpx.HTTPError covers the JWKS fetch: authlib's fetch_jwk_set calls
        # raise_for_status(), which raises HTTPStatusError on a bad response
        # and transport errors on connectivity failures.
        logger.warning("Google ID-token verification failed: %s", exc)
        raise OAuthFlowError("id token verification failed") from exc
    return dict(claims)


def _state_mac(payload: str) -> str:
    credentials = google_credentials()
    if credentials is None:
        raise OAuthFlowError("google oauth is not configured")
    # The client secret is shared by every node and never leaves the server.
    key = hashlib.sha256(b"tokenage-oauth-state\0" + credentials[1].encode()).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def seal_oauth_state(state: str, data: dict[str, Any]) -> str:
    """Return the state cookie value: `data` signed and valid for five minutes."""
    body = {**data, "state": state, "exp": time.time() + STATE_TTL_SECONDS}
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    payload = payload.rstrip("=")
    return f"{payload}.{_state_mac(payload)}"


def open_oauth_state(cookie: str | None, state: str) -> dict[str, Any] | None:
    """Return the data sealed for `state`, or None if forged, expired or another
    browser's. Replays are harmless: Google's authorization code is single-use."""
    payload, _, mac = (cookie or "").partition(".")
    try:
        if not state or not hmac.compare_digest(
            mac.encode(), _state_mac(payload).encode()
        ):
            return None
        body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (OAuthFlowError, ValueError):
        return None
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("exp"), (int, float))
        or body.pop("exp") <= time.time()
        or not hmac.compare_digest(str(body.pop("state", "")).encode(), state.encode())
    ):
        return None
    return body


def store_cli_code(code: str, data: dict[str, Any]) -> None:
    _store_entry(
        CLI_CODE_NAMESPACE, code, data, CLI_CODE_TTL_SECONDS, MAX_PENDING_CLI_CODES
    )


def pop_cli_code(code: str) -> dict[str, Any] | None:
    return _pop_entry(CLI_CODE_NAMESPACE, code)


def store_login_code(code: str, token_epoch: int = 0) -> None:
    """Store a one-time login link, tagged with the owner's token epoch.

    The epoch makes a later revoke kill the link (checked in auth_local_login).
    """
    _store_entry(
        LOGIN_CODE_NAMESPACE,
        code,
        {"token_epoch": token_epoch},
        CLI_CODE_TTL_SECONDS,
        MAX_PENDING_STATES,
    )


def pop_login_code(code: str) -> dict[str, Any] | None:
    return _pop_entry(LOGIN_CODE_NAMESPACE, code)


def _store_entry(
    namespace: str, key: str, data: dict[str, Any], ttl_seconds: float, max_pending: int
) -> None:
    get_shared_state().set(
        namespace,
        key,
        data,
        ttl_seconds,
        cap=max_pending,
    )


def _pop_entry(namespace: str, key: str) -> dict[str, Any] | None:
    return get_shared_state().pop(namespace, key) if key else None
