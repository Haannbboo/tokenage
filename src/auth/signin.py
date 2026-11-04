"""Budgets for sign-in starts (per client IP) and CLI approvals (per user)."""

import ipaddress

from fastapi import Request

from src.config.app import CONFIG


def _client_key(request: Request) -> str:
    # Uvicorn applies forwarded headers only for its configured trusted peers
    # (FORWARDED_ALLOW_IPS). Never interpret X-Forwarded-For ourselves.
    host = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        # One host usually controls a whole /64.
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)


def _check(key: str, limit_name: str) -> None:
    if CONFIG["rate_limits"].get(limit_name, 0) <= 0:
        return
    # Lazy: src.ratelimit imports src.auth.
    from src.ratelimit import check_rate_limit

    check_rate_limit(key, limit_name)


def check_signin_rate_limit(request: Request) -> None:
    """429 once this client exceeds `rate_limits.signin_per_minute` (0 = off)."""
    _check(f"signin:{_client_key(request)}", "signin_per_minute")


def check_cli_approve_rate_limit(user_id: str) -> None:
    """429 once this account exceeds `rate_limits.cli_approve_per_minute`.

    Keyed by user, not IP, so it holds behind any proxy; it bounds the pending
    CLI codes one account can create (0 = off)."""
    _check(f"cli-approve:{user_id}", "cli_approve_per_minute")
