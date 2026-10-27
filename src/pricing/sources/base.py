"""Price-source protocol, entry types, and shared cache helpers.

A source fetches pricing and returns ``SourceEntry`` rows keyed by a normalized
model key. The registry (``registry.py``) assigns each source a priority (its
position in ``pricing.sources``) and resolves entries into the runtime pricing
generation.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.config.models import get_tracker_home

from ..models import ModelCost

log = logging.getLogger(__name__)

CACHE_DIR_NAME = "pricing"
REQUEST_TIMEOUT = 10  # seconds

# Cache names come from config (`pricing.sources[].name`), so reject anything
# that could escape the pricing cache directory.
_CACHE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class SourceEntry:
    """One model's pricing from one source."""

    key: str
    cost: ModelCost


@dataclass(frozen=True)
class FetchedSource:
    """Everything one source returned, with its resolution priority.

    ``priority`` is the source's index in ``pricing.sources`` (lower = higher
    priority).
    """

    name: str
    priority: int
    entries: tuple[SourceEntry, ...]


class PriceSource(Protocol):
    name: str
    ttl_seconds: int

    def fetch(self) -> list[SourceEntry]: ...


def cache_path(name: str) -> Path:
    if not _CACHE_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid pricing cache name: {name!r}")
    return Path(get_tracker_home()) / CACHE_DIR_NAME / f"{name}.json"


def cache_is_fresh(name: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    try:
        age = time.time() - cache_path(name).stat().st_mtime
    except OSError:
        return False
    return age < ttl_seconds


def load_cache_json(name: str) -> Any | None:
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read pricing cache: %s", path)
        return None


def save_cache_json(name: str, data: Any) -> None:
    path = cache_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Each worker may refresh at startup. Readers must see a complete
        # generation, and concurrent writers must not share a temporary file.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            try:
                temporary.write(json.dumps(data, indent=2))
                temporary.close()
                os.replace(temporary.name, path)
            finally:
                Path(temporary.name).unlink(missing_ok=True)
    except OSError:
        log.warning("Failed to write pricing cache: %s", path)


def _create_ipv4_connection(address, timeout=REQUEST_TIMEOUT, source_address=None):
    """Same as socket.create_connection, but only tries AF_INET candidates."""
    host, port = address
    err = None
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host, port, socket.AF_INET, socket.SOCK_STREAM
    ):
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()
    if err is not None:
        raise err
    raise OSError("getaddrinfo returned no IPv4 candidates")


class _IPv4OnlyHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that resolves/connects over IPv4 only.

    `_create_connection` is read by `HTTPConnection.connect()` and is an
    instance attribute (stdlib sets it in `__init__` specifically so it can
    be swapped per-instance), so overriding it here only affects this one
    connection -- unlike patching `socket.getaddrinfo` globally.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_ipv4_connection


class _IPv4OnlyHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib opener handler that issues HTTPS requests via
    _IPv4OnlyHTTPSConnection instead of the default http.client.HTTPSConnection.
    """

    def https_open(self, req):
        return self.do_open(_IPv4OnlyHTTPSConnection, req)


def fetch_json(url: str, timeout: int = REQUEST_TIMEOUT) -> Any | None:
    """GET ``url`` and parse its JSON body, IPv4-only. None on any failure.

    ponytail: raw.githubusercontent.com's IPv6 candidates are unreachable on
    some networks, and the default resolver burns the timeout per candidate
    before falling back to IPv4. Force IPv4 via a dedicated connection class
    instead of monkeypatching socket.getaddrinfo globally -- this process also
    serves live proxy traffic concurrently, and a global patch would force
    those unrelated connections to IPv4 too.
    """
    opener = urllib.request.build_opener(_IPv4OnlyHTTPSHandler)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "tokenage"})
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        log.warning("Failed to fetch pricing from %s", url)
        return None
