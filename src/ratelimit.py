"""Per-key sliding-window rate limiter and ingest-token authentication.

Windows are shared across server processes; invalid-token caching stays local.
"""

import asyncio
import concurrent.futures
import threading
import time
from collections import OrderedDict
from typing import NoReturn

from fastapi import HTTPException, Request

from src.config.app import CONFIG
from src.shared_state import StoreUnavailable, get_shared_state

from .auth import resolve_token
from .auth.tokens import hash_token

WINDOW_SECONDS = 60

_rate_limit_lock = threading.Lock()
_invalid_token_cache: OrderedDict[str, float] = OrderedDict()
# ponytail: bounded 4096-entry expiry scan; add an expiry index if profiling
# shows lock contention.
INVALID_TOKEN_CACHE_SIZE = 4096
INVALID_TOKEN_CACHE_TTL_SECONDS = 60
AUTH_LOOKUP_TIMEOUT_SECONDS = 10
_auth_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="tokenage-auth"
)


def rate_limit_hit(key: str, limit_name: str) -> int | None:
    """Count a hit on `key` against `rate_limits.<limit_name>`.

    Returns None when allowed, else the seconds until a slot frees up.
    """
    try:
        return get_shared_state().rate_limit(
            key, CONFIG["rate_limits"][limit_name], WINDOW_SECONDS
        )
    except StoreUnavailable as exc:
        raise HTTPException(
            503, "rate limiter unavailable", headers={"Retry-After": "5"}
        ) from exc


def check_rate_limit(key: str, limit_name: str) -> None:
    """Raise HTTPException(429) when `key` exceeds `rate_limits.<limit_name>`."""
    retry_after = rate_limit_hit(key, limit_name)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def _reject_invalid_token() -> NoReturn:
    raise HTTPException(status_code=401, detail="invalid token")


def _invalid_token_is_cached(token_id: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        # TTL is fixed, so insertion order is expiry order: pop from the front.
        while _invalid_token_cache:
            oldest = next(iter(_invalid_token_cache))
            if _invalid_token_cache[oldest] > now:
                break
            del _invalid_token_cache[oldest]
        return token_id in _invalid_token_cache


def _remember_invalid_token(token_id: str) -> None:
    with _rate_limit_lock:
        _invalid_token_cache[token_id] = time.time() + INVALID_TOKEN_CACHE_TTL_SECONDS
        _invalid_token_cache.move_to_end(token_id)
        while len(_invalid_token_cache) > INVALID_TOKEN_CACHE_SIZE:
            _invalid_token_cache.popitem(last=False)


def resolve_ingest_user(request: Request) -> tuple[str, str, str]:
    """Return (user_id, token_id, device_id) for the request.

    Requires an ingest token (x-tokenage-token); there is no tokenless path,
    loopback included. Raises HTTPException(401) otherwise. No IP is read: a
    bad token is a plain 401, and repeats skip the DB via the token-hash cache.
    """
    token = request.headers.get("x-tokenage-token")
    if not token:
        _reject_invalid_token()

    token_id = hash_token(token)
    if _invalid_token_is_cached(token_id):
        _reject_invalid_token()
    result = resolve_token(token)
    if result is None:
        _remember_invalid_token(token_id)
        _reject_invalid_token()

    user, auth_token = result
    # resolve_token already rejects device-less ingest tokens; check anyway so
    # an unattributed row can never be written.
    if auth_token.kind != "ingest" or auth_token.device_id is None:
        _remember_invalid_token(token_id)
        _reject_invalid_token()

    return user.id, token_id, auth_token.device_id


def resolve_ingest_request(request: Request) -> tuple[str, str]:
    """Auth + rate limit; returns (user_id, device_id)."""
    user_id, token_id, device_id = resolve_ingest_user(request)
    check_rate_limit(f"ingest:{token_id}", "ingest_per_minute")
    return user_id, device_id


async def resolve_ingest_request_async(request: Request) -> tuple[str, str]:
    """`resolve_ingest_request` with the DB lookup bounded and off the loop."""
    try:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(_auth_executor, resolve_ingest_request, request),
            timeout=AUTH_LOOKUP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="authentication unavailable"
        ) from exc
