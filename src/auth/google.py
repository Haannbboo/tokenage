"""Google OAuth web login flow (docs/design/specs/pr2-google-oauth-web-login.md).

authlib handles the OAuth2/OIDC handshake (authorization URL construction,
code exchange, ID-token verification). OAuth ``state`` values are tracked
server-side in the shared-state store —
there is no client-side signed session.
"""

from __future__ import annotations

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

STATE_FILE_NAME = "oauth_state.json"
STATE_TTL_SECONDS = 5 * 60
MAX_PENDING_STATES = 512

# One-time CLI and browser login codes share the same store and TTL.
CLI_CODE_FILE_NAME = "cli_codes.json"
CLI_CODE_TTL_SECONDS = 300
LOGIN_CODE_FILE_NAME = "login_codes.json"

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


def store_oauth_state(state: str, data: dict[str, Any]) -> None:
    """Persist a single-use OAuth state for five minutes."""
    _store_entry(STATE_FILE_NAME, state, data, STATE_TTL_SECONDS, MAX_PENDING_STATES)


def pop_oauth_state(state: str) -> dict[str, Any] | None:
    return _pop_entry(STATE_FILE_NAME, state)


def store_cli_code(code: str, data: dict[str, Any]) -> None:
    _store_entry(
        CLI_CODE_FILE_NAME, code, data, CLI_CODE_TTL_SECONDS, MAX_PENDING_STATES
    )


def pop_cli_code(code: str) -> dict[str, Any] | None:
    return _pop_entry(CLI_CODE_FILE_NAME, code)


def store_login_code(code: str) -> None:
    _store_entry(
        LOGIN_CODE_FILE_NAME, code, {}, CLI_CODE_TTL_SECONDS, MAX_PENDING_STATES
    )


def pop_login_code(code: str) -> dict[str, Any] | None:
    return _pop_entry(LOGIN_CODE_FILE_NAME, code)


def _store_entry(
    namespace: str, key: str, data: dict[str, Any], ttl_seconds: float, max_pending: int
) -> None:
    get_shared_state().set(
        namespace,
        key,
        {**data, "exp": time.time() + ttl_seconds},
        ttl_seconds,
        cap=max_pending,
    )


def _pop_entry(namespace: str, key: str) -> dict[str, Any] | None:
    return get_shared_state().pop(namespace, key) if key else None
