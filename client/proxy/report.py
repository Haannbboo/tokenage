"""Build and post the metadata-only usage report for one model call."""

from __future__ import annotations

import logging
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
RATE_LIMIT_RETRIES = 3
MAX_RETRY_DELAY_SECONDS = 60.0
# Bounds reports waiting out a 429 at once; beyond this they drop.
_retry_slots = threading.BoundedSemaphore(100)
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

    Only a 429 is retried (the server rejected it before recording, so a retry
    can't duplicate a row), after `Retry-After`, up to RATE_LIMIT_RETRIES times.
    """
    try:
        creds = load_credentials() or {}
        server_url, token = creds.get("server_url"), creds.get("ingest_token")
        if not server_url or not token:
            logger.warning("usage report dropped: not signed in")
            return
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = httpx.post(
                f"{server_url.rstrip('/')}/ingest/v1/usage",
                json=report.model_dump(mode="json"),
                headers={"x-tokenage-token": token},
                timeout=REPORT_TIMEOUT_SECONDS,
            )
            if response.status_code != 429:
                response.raise_for_status()
                return
            # ponytail: sleeping threads are the buffer, capped by _retry_slots;
            # a persistent queue only if drops under sustained limiting matter.
            if attempt == RATE_LIMIT_RETRIES or not _retry_slots.acquire(
                blocking=False
            ):
                break
            try:
                time.sleep(_retry_delay(response))
            finally:
                _retry_slots.release()
        logger.warning("usage report dropped: rate limited")
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
    """Fire and forget, off the request path.

    ponytail: one short-lived thread per call; a queue worker if call volume
    ever makes that matter.
    """
    threading.Thread(target=post_report, args=(report,), daemon=True).start()


ReportSink = Callable[[UsageReport], None]
