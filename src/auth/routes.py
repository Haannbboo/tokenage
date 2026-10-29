import asyncio
import base64
import hashlib
import html
import logging
import re
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from src.config.app import CONFIG
from src.config.server_config import resolve_server_urls

from ..database import AuthToken, User
from . import google as auth_google
from .tokens import (
    LOCAL_OWNER_EMAIL,
    get_local_owner,
    get_or_create_user,
    get_user_by_id,
    list_user_devices,
    list_user_tokens,
    mint_device_tokens,
    mint_token,
    resolve_token,
    revoke_device,
    revoke_token,
    update_user_name,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "tokenage_session"
OAUTH_STATE_COOKIE_NAME = "tokenage_oauth_state"
_NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter()


def auth_provider() -> str:
    """The configured sign-in provider: "local" (default) or "google"."""
    return CONFIG.get("auth", {}).get("provider", "local")


def _require_local_owner() -> None:
    """Admin-only routes: the local provider's owner is the only user, so any
    authenticated caller is the admin; denied outright under google."""
    if auth_provider() != "local":
        raise HTTPException(status_code=404)


def _require_google() -> None:
    if auth_provider() != "google":
        raise HTTPException(status_code=404, detail="not found")


_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def _hostname(netloc: str) -> str | None:
    try:
        return urlparse(f"//{netloc}").hostname
    except ValueError:
        return None


def is_direct_loopback(request: Request) -> bool:
    """True only for a request that provably never left this machine.

    Peer is loopback, no forwarding headers (a local reverse proxy adds them),
    and the Host header names loopback (DNS-rebinding guard: a rebound public
    name resolves to 127.0.0.1 but keeps its own Host). A state-changing
    request that carries a non-loopback Origin is a cross-site browser request
    and is refused too, so a website can't drive the local owner's API.
    """
    if request.client is None or request.client.host not in _LOOPBACK_PEERS:
        return False
    if any(header in request.headers for header in _FORWARDING_HEADERS):
        return False
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK_HOSTNAMES:
        return False
    origin = request.headers.get("origin")
    if origin is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
        if _hostname(origin.partition("://")[2]) not in _LOOPBACK_HOSTNAMES:
            return False
    return True


def local_owner_allowed(request: Request) -> bool:
    """Tokenless access: local provider and a direct loopback request."""
    return auth_provider() == "local" and is_direct_loopback(request)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def _resolve_request_user(
    request: Request,
) -> tuple[User, AuthToken | None] | None:
    """Resolve the request to (user, auth_token), or None.

    A valid bearer/cookie token wins. A bearer token that doesn't resolve is
    None (no owner fallback). Otherwise a direct loopback request under the
    local provider is the owner (auth_token None); a stale cookie falls back
    here so it can't lock the owner's browser out. Anything else is None.
    """
    bearer = _bearer_token(request)
    token = bearer or request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        resolved = resolve_token(token)
        if resolved is not None and resolved[1].kind != "ingest":
            user, auth_token = resolved
            request.state.user = user
            request.state.auth_token = auth_token
            return user, auth_token
        if bearer:
            return None
    if local_owner_allowed(request):
        user = get_local_owner()
        request.state.user = user
        request.state.auth_token = None
        return user, None
    return None


def get_current_user(request: Request) -> User:
    """Resolve the request to a user; never None.

    Raises 401 for anything unresolved (one message for all cases — callers
    must not learn which). DB outages return a retryable 503: fail closed.
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    resolved = _resolve_request_user(request)
    if resolved is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return resolved[0]


def _cookie_is_secure() -> bool:
    """`Secure` on the session cookie iff the public API URL is HTTPS.

    Derived from server.base_url (the same source of truth as the OAuth
    redirect URI) — not from the incoming request's scheme, which is wrong
    behind a TLS-terminating reverse proxy.
    """
    return resolve_server_urls(CONFIG)["api_url"].startswith("https://")


def _google_redirect_uri() -> str:
    return resolve_server_urls(CONFIG)["api_url"] + "/auth/google/callback"


def _frontend_redirect(
    origin: str | None = None, auth_error: str | None = None
) -> RedirectResponse:
    """Redirect back to the frontend, preserving its origin when known.

    The origin is captured from the login request, so in Vite dev the browser
    lands back on the dev server; production (frontend served by this app)
    gets an identical same-origin redirect.
    """
    base = f"{origin}/" if origin else "/"
    location = f"{base}?auth_error={auth_error}" if auth_error else base
    response = RedirectResponse(location, status_code=302)
    # Single-use: every callback exit (success or failure) clears the state
    # cookie set by auth_google_login.
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
    return response


_DEV_FRONTEND_HOSTS = ("localhost", "127.0.0.1")
_DEV_FRONTEND_PORT = 5173


def _request_frontend_origin(request: Request) -> str | None:
    """The Vite dev server's origin, when the login request came from it.

    Production serves the frontend from this same app (mounted at "/"), so a
    relative "/" redirect is already correct there. The only case worth
    trusting here is the well-known Vite dev port — never an arbitrary
    client-supplied origin, which would make this an open redirect.
    """
    raw = request.headers.get("origin") or request.headers.get("referer")
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _DEV_FRONTEND_HOSTS
        or parsed.port != _DEV_FRONTEND_PORT
    ):
        return None
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


@router.get("/auth/me")
def auth_me(request: Request):
    """Provider plus the caller's user; user is null (200) when unresolved so
    the frontend can tell "sign in" from "ask the operator for a login link"."""
    resolved = _resolve_request_user(request)
    if resolved is None:
        return {"provider": auth_provider(), "user": None}
    user, auth_token = resolved
    return {
        "provider": auth_provider(),
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "created_at": user.created_at,
        },
        "token": (
            {"kind": auth_token.kind, "device_name": auth_token.device_name}
            if auth_token
            else None
        ),
    }


@router.get("/auth/local/login")
def auth_local_login(request: Request, code: str = ""):
    """Redeem a one-time link from `tokenage server login-link` for a session."""
    if auth_provider() != "local":
        raise HTTPException(status_code=404, detail="not found")
    if auth_google.pop_login_code(code) is None:
        raise HTTPException(status_code=400, detail="invalid or expired link")
    # One live browser session: the new login retires the old cookie.
    _retire_web_sessions(get_local_owner().id)
    plaintext_token, _ = mint_token(
        LOCAL_OWNER_EMAIL, kind="web", device_name="browser"
    )
    response = _frontend_redirect(_request_frontend_origin(request))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        plaintext_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(),
    )
    return response


@router.get("/auth/google/login")
async def auth_google_login(request: Request):
    """Start the Google authorization-code flow (public when auth enabled)."""
    _require_google()
    if auth_google.google_credentials() is None:
        raise HTTPException(status_code=503, detail="google oauth not configured")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    redirect_uri = _google_redirect_uri()
    origin = _request_frontend_origin(request)
    try:
        url = await auth_google.build_authorize_url(redirect_uri, state, nonce)
    except auth_google.OAuthFlowError:
        logger.exception("Failed to build Google authorize URL")
        raise HTTPException(status_code=503, detail="google oauth not configured")
    await asyncio.to_thread(
        auth_google.store_oauth_state,
        state,
        {"nonce": nonce, "redirect_uri": redirect_uri, "origin": origin},
    )
    response = RedirectResponse(url, status_code=302)
    # Ties the callback to this browser (see auth_google_callback) so a
    # state/code pair captured by one browser can't be completed by another.
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(),
        max_age=auth_google.STATE_TTL_SECONDS,
    )
    return response


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    """Complete the Google flow: verify state, exchange the code, set the cookie."""
    _require_google()
    state = request.query_params.get("state", "")
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    data = await asyncio.to_thread(auth_google.pop_oauth_state, state)
    # The state cookie ties this callback to the browser that started the
    # login. Without it, an attacker who completes their own Google login and
    # forwards the resulting callback URL to a victim could log the victim's
    # browser into the attacker's account (OAuth login CSRF) — the stored
    # state alone doesn't prove which browser is calling back.
    if (
        data is None
        or not cookie_state
        # compare_digest on str requires ASCII-only input (raises TypeError
        # otherwise); encode first so a non-ASCII cookie/query value can't
        # turn this pre-auth endpoint into an unhandled 500.
        or not secrets.compare_digest(cookie_state.encode(), state.encode())
    ):
        return _frontend_redirect(auth_error="invalid_state")
    origin = data.get("origin")
    if request.query_params.get("error") or not request.query_params.get("code"):
        return _frontend_redirect(origin, auth_error="oauth_failed")
    code = request.query_params["code"]
    try:
        token = await auth_google.exchange_code(data["redirect_uri"], code)
        claims = await auth_google.verify_id_token(token, data["nonce"])
    except auth_google.OAuthFlowError:
        logger.exception("Google OAuth callback failed")
        return _frontend_redirect(origin, auth_error="oauth_failed")
    if claims.get("email_verified") is not True:
        return _frontend_redirect(origin, auth_error="email_unverified")
    email = str(claims.get("email", "")).strip().lower()
    if not email:
        return _frontend_redirect(origin, auth_error="email_unverified")
    allowlist = CONFIG.get("auth", {}).get("allowlist") or []
    normalized_allowlist = [str(entry).strip().lower() for entry in allowlist if entry]
    if normalized_allowlist and email not in normalized_allowlist:
        # No user/token row is created for a rejected email.
        return _frontend_redirect(origin, auth_error="not_allowlisted")
    cli_flow = data.get("cli")
    if isinstance(cli_flow, dict):
        # CLI paste-back flow: no web session is minted — the code page
        # renders the one-time code and the CLI redeems it at exchange.
        user = get_or_create_user(email)
        update_user_name(user.id, str(claims.get("name") or "").strip() or None)
        device = _sanitize_device_name(str(cli_flow.get("device_name") or ""))
        code = await asyncio.to_thread(
            _mint_cli_code, user, device, str(cli_flow.get("code_challenge") or "")
        )
        code_response = HTMLResponse(_code_page(code, device), headers=_NO_STORE)
        # Single-use: the state was popped above; clear the login-CSRF cookie
        # like the web path does.
        code_response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
        return code_response
    # One live browser session per user: the new login retires the old cookie.
    user = get_or_create_user(email)
    _retire_web_sessions(user.id)
    plaintext_token, user = mint_token(email, kind="web", device_name="browser")
    update_user_name(user.id, str(claims.get("name") or "").strip() or None)
    response = _frontend_redirect(origin)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        plaintext_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(),
    )
    return response


@router.post("/auth/logout")
def auth_logout(request: Request, user: User = Depends(get_current_user)):
    """Revoke the authenticating token and clear the session cookie."""
    auth_token = getattr(request.state, "auth_token", None)
    if auth_token is not None:
        revoke_token(auth_token.id, user.id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/auth/devices")
def auth_devices(
    request: Request,
    include_revoked: bool = False,
    user: User = Depends(get_current_user),
):
    """List installed machines and the caller's unlinked tokens.

    ``include_revoked`` also returns revoked machines (for the usage device
    filter, which must still name them).

    Browser sessions are deliberately absent: signing in again retires the
    previous one, so a stale browser entry is never left to manage here.
    """
    auth_token = getattr(request.state, "auth_token", None)
    current_id = auth_token.id if auth_token is not None else None
    current_device_id = auth_token.device_id if auth_token is not None else None
    return {
        "devices": [
            {
                "id": device.id,
                "kind": "client",
                "device_name": device.device_name,
                "created_at": device.created_at,
                "last_used_at": device.last_seen_at,
                "client_version": device.client_version,
                "client_commit": device.client_commit,
                "current": device.id == current_device_id,
                "revoked_at": device.revoked_at,
            }
            for device in list_user_devices(user.id, include_revoked=include_revoked)
        ]
        + [
            {
                "id": device.id,
                "kind": device.kind,
                "device_name": device.device_name,
                "created_at": device.created_at,
                "last_used_at": device.last_used_at,
                "client_version": None,
                "client_commit": None,
                "current": device.id == current_id,
            }
            for device in list_user_tokens(user.id)
            if device.device_id is None and device.kind != "web"
        ]
    }


@router.post("/auth/devices/{device_id}/revoke")
def auth_devices_revoke(
    device_id: str,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Revoke a machine or legacy token owned by the caller."""
    if not revoke_device(device_id, user.id) and not revoke_token(device_id, user.id):
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=204)


# ------------------------------------------------------- CLI login (PR 3)
#
# Paste-back flow: the browser renders a short human-typeable one-time code
# and the user pastes it into the terminal — one flow for local, SSH, and
# cross-device (phone-browser) logins. The code is PKCE S256-bound to the
# CLI's code_challenge and redeemed at /auth/cli/exchange with the
# verifier. A browser that already has a web session approves on a confirm
# page instead of redoing Google.

_CODE_CHALLENGE_RE = re.compile(r"[A-Za-z0-9\-_]{43,128}")
_DEFAULT_DEVICE_NAME = "cli-device"
# RFC 8628 §6.1 unambiguous alphabet; 3 groups of 4 (~52 bits).
_CLI_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


class CliExchangeRequest(BaseModel):
    # Capped: this is a public (auth-gated) endpoint taking unbounded strings.
    # installation_key stays optional here so pydantic's 422 never echoes the
    # secret in its error body; the handler checks it and returns a static 422.
    code: str | None = Field(default=None, max_length=256)
    code_verifier: str | None = Field(default=None, max_length=256)
    installation_key: str | None = Field(default=None, max_length=128)
    ingest_token: str | None = Field(default=None, max_length=256, repr=False)
    client_version: str | None = Field(default=None, max_length=64)
    client_commit: str | None = Field(default=None, max_length=40)

    @model_validator(mode="wrap")
    @classmethod
    def _validate_without_echoing_secrets(cls, data, handler):
        try:
            return handler(data)
        except ValidationError:
            # Pydantic includes rejected input in type/length errors, including
            # secrets on malformed requests. Match the handler's static 422s.
            raise HTTPException(
                status_code=422, detail="invalid exchange request"
            ) from None


def _sanitize_device_name(raw: str | None) -> str:
    cleaned = "".join(c for c in (raw or "").strip() if c.isprintable())
    return cleaned[:64] or _DEFAULT_DEVICE_NAME


def _retire_web_sessions(user_id: str) -> None:
    """Retire the user's existing browser sessions so a new login is the only one.

    Keeps the session list from growing one indistinguishable entry per login,
    and makes a sign-in from a new browser take over the old one. Called before
    minting, so the fresh token is never a candidate.
    """
    # ponytail: one UPDATE per stale row; a single UPDATE if that ever matters.
    for stale in list_user_tokens(user_id):
        if stale.kind == "web":
            revoke_token(stale.id, user_id)


def _normalize_cli_code(raw: str) -> str:
    """Uppercase, strip whitespace and hyphens (what a human may retype)."""
    return "".join(raw.split()).upper().replace("-", "")


def _validate_cli_start_params(
    code_challenge: str | None, device_name: str | None
) -> tuple[str, str]:
    if not code_challenge or not _CODE_CHALLENGE_RE.fullmatch(code_challenge):
        raise HTTPException(status_code=422, detail="invalid code_challenge")
    return code_challenge, _sanitize_device_name(device_name)


def _mint_cli_code(user: User, device_name: str, code_challenge: str) -> str:
    code = "-".join(
        "".join(secrets.choice(_CLI_CODE_ALPHABET) for _ in range(4)) for _ in range(3)
    )
    # Storage key is the normalized form (what the exchange looks up).
    auth_google.store_cli_code(
        _normalize_cli_code(code),
        {
            "user_id": user.id,
            "device_name": device_name,
            "code_challenge": code_challenge,
        },
    )
    return code


def _confirm_page(user: User, device_name: str, code_challenge: str) -> str:
    return f"""<!doctype html>
<html><head><title>tokenage CLI login</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto;">
<h2>CLI login request</h2>
<p>A CLI on device <b>{html.escape(device_name)}</b> is requesting access to
tokenage as <b>{html.escape(user.email)}</b>.</p>
<p><b>Only approve logins you started in a terminal you are looking at.</b></p>
<form method="post" action="/auth/cli/start">
<input type="hidden" name="code_challenge" value="{html.escape(code_challenge, quote=True)}">
<input type="hidden" name="device_name" value="{html.escape(device_name, quote=True)}">
<button type="submit">Approve</button>
<a href="/" style="margin-left: 1rem;">Cancel</a>
</form>
</body></html>"""


def _code_page(code: str, device_name: str) -> str:
    return f"""<!doctype html>
<html><head><title>tokenage CLI login</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto;">
<h2>CLI login code</h2>
<p>Paste this code into your terminal
(device <b>{html.escape(device_name)}</b>):</p>
<code id="cli-code" style="font-size: 2rem; letter-spacing: 0.15em;">{html.escape(code)}</code>
<p><button type="button" onclick="copyCode()">Copy</button></p>
<p>If you did not start this login in a terminal, close this page.</p>
<script>
function copyCode() {{
  var el = document.getElementById('cli-code');
  var text = el.textContent;
  if (navigator.clipboard) {{
    navigator.clipboard.writeText(text);
  }} else {{
    var range = document.createRange();
    range.selectNode(el);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }}
}}
</script>
</body></html>"""


@router.get("/auth/cli/start")
async def auth_cli_start(
    request: Request,
    code_challenge: str | None = None,
    device_name: str | None = None,
):
    """Start the CLI login: confirm page for a resolved user (session token or
    local loopback owner); Google for anyone else under the google provider."""
    challenge, device = _validate_cli_start_params(code_challenge, device_name)
    resolved = _resolve_request_user(request)
    if resolved is not None:
        return HTMLResponse(
            _confirm_page(resolved[0], device, challenge), headers=_NO_STORE
        )
    if auth_provider() != "google":
        raise HTTPException(
            status_code=401,
            detail="login required: run `tokenage server login-link` on the server",
        )
    if auth_google.google_credentials() is None:
        raise HTTPException(status_code=503, detail="google oauth not configured")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    redirect_uri = _google_redirect_uri()
    try:
        url = await auth_google.build_authorize_url(redirect_uri, state, nonce)
    except auth_google.OAuthFlowError:
        logger.exception("Failed to build Google authorize URL")
        raise HTTPException(status_code=503, detail="google oauth not configured")
    await asyncio.to_thread(
        auth_google.store_oauth_state,
        state,
        {
            "nonce": nonce,
            "redirect_uri": redirect_uri,
            "origin": None,
            "cli": {
                "code_challenge": challenge,
                "device_name": device,
            },
        },
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(),
        max_age=auth_google.STATE_TTL_SECONDS,
    )
    return response


@router.post("/auth/cli/start")
def auth_cli_start_approve(
    request: Request,
    code_challenge: str | None = Form(default=None),
    device_name: str | None = Form(default=None),
):
    """Approve the CLI login from the confirm page (requires a resolved user).

    The SameSite=Lax cookie is withheld on cross-site POSTs, and the loopback
    owner rule refuses a cross-site Origin (see is_direct_loopback), so this is
    unreachable from another site — no separate CSRF token (same argument as
    POST /auth/logout).
    """
    challenge, device = _validate_cli_start_params(code_challenge, device_name)
    resolved = _resolve_request_user(request)
    if resolved is None:
        raise HTTPException(status_code=401, detail="login required")
    code = _mint_cli_code(resolved[0], device, challenge)
    return HTMLResponse(_code_page(code, device), headers=_NO_STORE)


@router.post("/auth/cli/exchange")
def auth_cli_exchange(body: CliExchangeRequest):
    """Redeem a one-time code with its PKCE verifier for device tokens."""
    if not body.code or not body.code_verifier:
        # One message for all failures — no oracle for which half was wrong.
        raise HTTPException(status_code=400, detail="invalid code")
    # Format-check the client fields before the code is consumed: a malformed
    # client_version/client_commit would otherwise burn a one-time code the
    # user then has to re-request. 422 (not 400) so the CLI's "wrong code,
    # paste again" retry loop stops instead of re-sending an identical body.
    if not body.installation_key or not re.fullmatch(
        r"[A-Za-z0-9_-]{43,128}", body.installation_key
    ):
        raise HTTPException(status_code=422, detail="invalid installation_key")
    if body.client_version is not None and not re.fullmatch(
        r"\d+\.\d+\.\d+", body.client_version
    ):
        raise HTTPException(status_code=422, detail="invalid client_version")
    if body.client_commit is not None and not re.fullmatch(
        r"[0-9a-fA-F]{40}", body.client_commit
    ):
        raise HTTPException(status_code=422, detail="invalid client_commit")
    entry = auth_google.pop_cli_code(_normalize_cli_code(body.code))
    if entry is None:
        raise HTTPException(status_code=400, detail="invalid code")
    challenge = str(entry.get("code_challenge") or "")
    verifier_digest = hashlib.sha256(body.code_verifier.encode("utf-8")).digest()
    derived = base64.urlsafe_b64encode(verifier_digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(derived, challenge):
        raise HTTPException(status_code=400, detail="invalid code")
    user = get_user_by_id(str(entry.get("user_id") or ""))
    if user is None:
        raise HTTPException(status_code=400, detail="invalid code")
    device_name = _sanitize_device_name(str(entry.get("device_name") or ""))
    cli_token, ingest_token, device = mint_device_tokens(
        user.id,
        body.installation_key,
        device_name,
        client_version=body.client_version,
        client_commit=body.client_commit,
        keep_ingest_token=body.ingest_token,
    )
    urls = resolve_server_urls(CONFIG)
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "device_name": device_name,
        "device_id": device.id,
        "cli_token": cli_token,
        "ingest_token": ingest_token,
        "otlp": {
            "endpoint": urls["otlp_url"],
            "logs_endpoint": f"{urls['otlp_url']}/v1/logs",
        },
    }
