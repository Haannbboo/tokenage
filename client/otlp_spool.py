"""Private bounded OTLP spool and the service's delivery loop for it.

Hooks that must not touch the network (``client.agy_hooks``) write whole OTLP
log payloads here; the client service posts them to the collector. The rules
mirror ``client.event_spool``.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path

import httpx

from client.agy_hooks import OTLP_MAX_BYTES as MAX_BYTES
from client.agy_hooks import OTLP_MAX_FILES as MAX_FILES
from client.agy_hooks import binding_of
from client.agy_hooks import otlp_dir as spool_dir
from client.auth import load_credentials

BATCH_SIZE = 20  # requests per pass: one spool file is one request
RETENTION_SECONDS = 7 * 86400  # Usage spooled offline survives a long weekend.


def _identity() -> tuple[str, str, str] | None:
    # One read of the credentials: the endpoint, the token and the binding
    # that picks which files to send must all describe the same login.
    try:
        credentials = load_credentials() or {}
    except ValueError:
        return None
    bound = binding_of(credentials)
    if bound is None:
        return None
    endpoint = credentials.get("otlp_logs_endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        endpoint = f"{credentials['server_url'].rstrip('/')}/otlp/v1/logs"
    return endpoint, credentials["ingest_token"], bound


def clear_spool() -> None:
    directory = spool_dir()
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.iterdir():
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)


def _read_payload(path: Path) -> tuple[float, bytes]:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("invalid spool file")
        body = stream.read(MAX_BYTES + 1)
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("resourceLogs"), list):
        raise ValueError("invalid spool file")
    return info.st_mtime, body


def drain_once() -> bool:
    """One bounded pass; True on success, False requests backoff."""
    identity = _identity()
    directory = spool_dir()
    if identity is None or not directory.is_dir() or directory.is_symlink():
        return True
    endpoint, token, bound = identity
    for path in directory.glob("*.tmp"):
        try:
            if path.lstat().st_mtime < time.time() - 60:
                path.unlink(missing_ok=True)
        except OSError:
            pass
    payloads = []
    cutoff = time.time() - RETENTION_SECONDS
    for path in list(directory.glob("*.json"))[:MAX_FILES]:
        try:
            if path.lstat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                continue
            if path.name.split(".")[-2] != bound:
                continue
            written, body = _read_payload(path)
            payloads.append((written, path.name, path, body))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    for _, _, path, body in sorted(payloads)[:BATCH_SIZE]:
        try:
            response = httpx.post(
                endpoint,
                headers={
                    "x-tokenage-token": token,
                    "content-type": "application/json",
                },
                content=body,
                timeout=5.0,
            )
        except httpx.HTTPError:
            return False
        # Auth, timeouts, rate limits and unavailable service remain retryable.
        if not (
            200 <= response.status_code < 300
            or (
                400 <= response.status_code < 500
                and response.status_code not in {401, 403, 408, 429}
            )
        ):
            return False
        path.unlink(missing_ok=True)
    return True


def start_drain() -> threading.Event:
    stop = threading.Event()

    def run():
        delay = 1.0
        while not stop.is_set():
            try:
                success = drain_once()
            except Exception:
                success = False  # Never log payloads, credentials or paths.
            delay = 1.0 if success else min(delay * 2, 60.0)
            stop.wait(delay)

    threading.Thread(target=run, name="tokenage-otlp", daemon=True).start()
    return stop
