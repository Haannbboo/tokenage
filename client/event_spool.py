"""Private bounded metadata spool and the service's independent delivery loop."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import time
import uuid
from pathlib import Path

import httpx

from client.auth import load_credentials
from client.paths import tracker_home
from protocol.events import SpoolReport

MAX_FILES = 1000
MAX_BYTES = 4096
BATCH_SIZE = 100
RETENTION_SECONDS = 86400


def _identity() -> tuple[str, str, str] | None:
    credentials = load_credentials() or {}
    server, token = credentials.get("server_url"), credentials.get("ingest_token")
    if (
        not isinstance(server, str)
        or not isinstance(token, str)
        or not server
        or not token
    ):
        return None
    # Bind files to the signed-in server/device credential without spooling a
    # token. Old files cannot be attributed to a later login on this machine.
    binding = hashlib.sha256(f"{server}\n{token}".encode()).hexdigest()
    return server, token, binding


def spool_dir() -> Path:
    return tracker_home() / "spool"


def write_report(report: SpoolReport) -> None:
    identity = _identity()
    if identity is None:
        return
    directory = spool_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        return
    directory.chmod(0o700)
    if sum(1 for _ in directory.glob("*.json")) >= MAX_FILES:
        return
    name = f"{uuid.uuid4()}.{identity[2]}"
    temporary = directory / f".{name}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(report.model_dump_json())
        temporary.replace(directory / f"{name}.json")
    finally:
        temporary.unlink(missing_ok=True)


def clear_spool() -> None:
    directory = spool_dir()
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.iterdir():
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)


def _read_report(path: Path) -> SpoolReport:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("invalid spool file")
        return SpoolReport.model_validate_json(stream.read(MAX_BYTES + 1))


def drain_once() -> bool:
    """One bounded request; True on success, False requests backoff."""
    identity = _identity()
    directory = spool_dir()
    if identity is None or not directory.is_dir() or directory.is_symlink():
        return True
    server, token, binding = identity
    reports = []
    for path in directory.glob("*.tmp"):
        try:
            if path.lstat().st_mtime < time.time() - 60:
                path.unlink(missing_ok=True)
        except OSError:
            pass
    for path in list(directory.glob("*.json"))[:MAX_FILES]:
        try:
            report = _read_report(path)
            if (
                path.name.split(".")[-2] != binding
                or report.ts < (time.time() - RETENTION_SECONDS) * 1_000_000
            ):
                path.unlink(missing_ok=True)
                continue
            reports.append((report.ts, path, report))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    batch = sorted(reports, key=lambda item: (item[0], item[1].name))[:BATCH_SIZE]
    if not batch:
        return True
    try:
        response = httpx.post(
            f"{server.rstrip('/')}/ingest/v1/events",
            headers={"x-tokenage-token": token},
            json={"events": [r.event().model_dump(mode="json") for _, _, r in batch]},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return False
    # Auth, timeouts, rate limits and unavailable service remain retryable.
    if 200 <= response.status_code < 300 or (
        400 <= response.status_code < 500
        and response.status_code not in {401, 403, 408, 429}
    ):
        for _, path, _ in batch:
            path.unlink(missing_ok=True)
        return True
    return False


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

    threading.Thread(target=run, name="tokenage-events", daemon=True).start()
    return stop
