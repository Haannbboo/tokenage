"""Optional per-client budget for anonymous sign-in starts."""

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


def check_signin_rate_limit(request: Request) -> None:
    """429 once this client exceeds `rate_limits.signin_per_minute` (0 = off)."""
    if CONFIG["rate_limits"].get("signin_per_minute", 0) <= 0:
        return
    # Lazy: src.ratelimit imports src.auth.
    from src.ratelimit import check_rate_limit

    check_rate_limit(f"signin:{_client_key(request)}", "signin_per_minute")
