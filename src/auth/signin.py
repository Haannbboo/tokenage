"""Small, bounded admission limit for public sign-in routes."""

import math
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

SIGNIN_PER_MINUTE = 10
MAX_SIGNIN_IPS = 4096
_WINDOW_SECONDS = 60
_hits: OrderedDict[str, deque[float]] = OrderedDict()
_lock = threading.Lock()


def check_signin_rate_limit(request: Request) -> None:
    # Uvicorn applies forwarded headers only for its configured trusted peers
    # (FORWARDED_ALLOW_IPS). Never interpret X-Forwarded-For ourselves.
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    # ponytail: in-process only; multiple workers/nodes multiply this allowance.
    # Default two workers on each of two nodes stay below the 512-state/5min cap.
    with _lock:
        while _hits and next(iter(_hits.values()))[-1] <= cutoff:
            _hits.popitem(last=False)
        hits = _hits.get(key)
        if hits is None:
            # Refuse new buckets when full instead of evicting active budgets.
            if len(_hits) >= MAX_SIGNIN_IPS:
                retry_after = _WINDOW_SECONDS
            else:
                hits = deque()
                _hits[key] = hits
                retry_after = None
        else:
            while hits and hits[0] <= cutoff:
                hits.popleft()
            retry_after = (
                max(1, math.ceil(hits[0] + _WINDOW_SECONDS - now))
                if len(hits) >= SIGNIN_PER_MINUTE
                else None
            )
        if retry_after is None:
            assert hits is not None
            hits.append(now)
            _hits.move_to_end(key)
            return
    raise HTTPException(
        status_code=429,
        detail="rate limit exceeded",
        headers={"Retry-After": str(retry_after)},
    )
