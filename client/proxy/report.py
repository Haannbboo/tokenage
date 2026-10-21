"""Build and post the metadata-only usage report for one model call."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from client.auth import load_credentials
from client.proxy.usage import extract_usage
from protocol.usage_report import ToolCallReport, UsageReport

REPORT_TIMEOUT_SECONDS = 10.0
RATE_LIMIT_RETRIES = 5
MAX_RETRY_DELAY_SECONDS = 60.0
# Cap threads/memory when the server is unreachable, including initial requests.
_report_slots = threading.BoundedSemaphore(100)
logger = logging.getLogger("tokenage.client.proxy")


def build_report(
    *,
    provider: str,
    model: str,
    endpoint: str,
    base_url: str,
    client_source: str | None,
    raw_usage: dict[str, Any],
    tool_calls: list[dict[str, str]],
    prompt_length: int,
    latency_ms: int,
    ttft_ms: int | None,
    status: int,
) -> UsageReport:
    """Raises pydantic.ValidationError if upstream sent unusable values."""
    return UsageReport(
        ts=int(time.time() * 1_000_000),
        provider=provider,
        model=model,
        endpoint=endpoint,
        client_source=client_source[:64] if client_source else None,
        prompt_length=prompt_length,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        status=status,
        base_url=base_url,
        tool_calls=[
            ToolCallReport(
                tool_use_id=tc["tool_use_id"][:256],
                tool_name=(tc["tool_name"] or "unknown")[:256],
            )
            for tc in tool_calls
        ],
        **extract_usage(raw_usage),
    )


def post_report(report: UsageReport) -> None:
    """POST with the device's ingest token; log and drop on failure.

    Retry backpressure and transport failures up to RATE_LIMIT_RETRIES times.
    """
    try:
        creds = load_credentials() or {}
        server_url, token = creds.get("server_url"), creds.get("ingest_token")
        if not server_url or not token:
            logger.warning("usage report dropped: not signed in")
            return
        payload = report.model_dump(mode="json")
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = None
            try:
                response = httpx.post(
                    f"{server_url.rstrip('/')}/ingest/v1/usage",
                    json=payload,
                    headers={"x-tokenage-token": token},
                    timeout=REPORT_TIMEOUT_SECONDS,
                )
                if response.status_code not in (429, 503):
                    response.raise_for_status()
                    return
            except httpx.TransportError:
                # Slow/unreachable servers are retryable; ambiguous timeout
                # duplicates are acceptable.
                pass
            if attempt == RATE_LIMIT_RETRIES:
                break
            # Upward jitter avoids retry storms without undercutting Retry-After.
            delay = min(
                MAX_RETRY_DELAY_SECONDS,
                (_retry_delay(response) if response is not None else 5.0) * 2**attempt,
            )
            time.sleep(min(MAX_RETRY_DELAY_SECONDS, random.uniform(delay, delay * 1.5)))
        logger.warning("usage report dropped: rate limited or unavailable")
    except Exception as exc:
        # The message never includes headers, so the token stays out of logs.
        logger.warning("usage report dropped: %s", type(exc).__name__)


def _retry_delay(response: httpx.Response) -> float:
    value = response.headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        try:  # HTTP-date form
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            seconds = 5.0
    return min(max(seconds, 1.0), MAX_RETRY_DELAY_SECONDS)


def send_in_background(report: UsageReport) -> None:
    """Fire and forget, off the request path."""
    if not _report_slots.acquire(blocking=False):
        logger.warning("usage report dropped: too many pending")
        return

    def send() -> None:
        try:
            post_report(report)
        finally:
            _report_slots.release()

    try:
        threading.Thread(target=send, daemon=True).start()
    except Exception:
        _report_slots.release()
        logger.warning("usage report dropped: thread start failed")


ReportSink = Callable[[UsageReport], None]
