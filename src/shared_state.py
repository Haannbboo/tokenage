"""Shared transient state, with a durable partitioned queue.

Queue consumers write before acknowledging; acks remove all events through a
finished position. Correlation mappings defer updates until record writes succeed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PARTITIONS = 16
LEASE_SECONDS = 10
MAX_PENDING_EVENTS = 50_000


def partition_for(user_id: str) -> int:
    return (
        int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big")
        % PARTITIONS
    )


class StoreUnavailable(Exception):
    """The shared store is temporarily unavailable."""


class QueueFull(Exception):
    """The user's partition is over its pending-event limit."""


@dataclass(frozen=True)
class Event:
    position: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PartitionStatus:
    partition: int
    depth: int
    oldest_pending: float | None
    owner: str | None


class SharedState(Protocol):
    def publish(self, user_id: str, payloads: list[dict[str, Any]]) -> None: ...
    def read(self, partition: int, limit: int = 500) -> list[Event]: ...
    def ack(self, partition: int, position: str) -> None: ...
    def claim(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool: ...
    def renew(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool: ...
    def release(self, partition: int, owner: str) -> None: ...
    def status(self) -> list[PartitionStatus]: ...
    def get(self, namespace: str, key: str) -> Any: ...
    def set(
        self, namespace: str, key: str, value: Any, ttl: float, cap: int | None = None
    ) -> None: ...
    def pop(self, namespace: str, key: str) -> Any: ...
    def rate_limit(self, key: str, limit: int, window: float = 60) -> int | None: ...


class SQLiteSharedState:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Auth codes and queued telemetry must not be readable by other users.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        # Keep WAL open across short operation connections. Closing the last
        # connection otherwise checkpoints/deletes WAL on every operation.
        try:
            self._anchor = sqlite3.connect(
                self.path, timeout=5, check_same_thread=False
            )
            self._anchor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            raise StoreUnavailable() from exc
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, partition INTEGER NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_partition ON events(partition, id);
                CREATE TABLE IF NOT EXISTS leases (
                    partition INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS kv (
                    namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    expires REAL NOT NULL, PRIMARY KEY(namespace, key));
                CREATE INDEX IF NOT EXISTS kv_expiry ON kv(expires);
                CREATE TABLE IF NOT EXISTS limits (
                    key TEXT NOT NULL, ts REAL NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS limits_key ON limits(key, ts);
                CREATE INDEX IF NOT EXISTS limits_expiry ON limits(expires);
            """)

    @contextmanager
    def _connection(self, write: bool = False):
        conn = None
        try:
            conn = sqlite3.connect(self.path, timeout=5)
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            with conn:
                yield conn
        except sqlite3.OperationalError as exc:
            raise StoreUnavailable() from exc
        finally:
            if conn is not None:
                conn.close()

    def publish(self, user_id: str, payloads: list[dict[str, Any]]) -> None:
        partition = partition_for(user_id)
        rows = [(partition, json.dumps(payload), time.time()) for payload in payloads]
        with self._connection(True) as conn:
            pending = conn.execute(
                "SELECT count(*) FROM events WHERE partition=?", (partition,)
            ).fetchone()[0]
            if pending + len(rows) > MAX_PENDING_EVENTS:
                raise QueueFull()
            conn.executemany(
                "INSERT INTO events(partition,payload,created) VALUES (?,?,?)", rows
            )

    def read(self, partition: int, limit: int = 500) -> list[Event]:
        with self._connection() as conn:
            return [
                Event(str(row[0]), json.loads(row[1]))
                for row in conn.execute(
                    "SELECT id,payload FROM events WHERE partition=? ORDER BY id LIMIT ?",
                    (partition, limit),
                )
            ]

    def ack(self, partition: int, position: str) -> None:
        with self._connection(True) as conn:
            conn.execute(
                "DELETE FROM events WHERE partition=? AND id<=?",
                (partition, int(position)),
            )

    def claim(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool:
        now = time.time()
        with self._connection(True) as conn:
            return (
                conn.execute(
                    "INSERT INTO leases VALUES (?,?,?) ON CONFLICT(partition) DO UPDATE SET owner=excluded.owner, expires=excluded.expires WHERE leases.expires<=?",
                    (partition, owner, now + ttl, now),
                ).rowcount
                == 1
            )

    def renew(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool:
        now = time.time()
        with self._connection(True) as conn:
            return (
                conn.execute(
                    "UPDATE leases SET expires=? WHERE partition=? AND owner=? AND expires>?",
                    (now + ttl, partition, owner, now),
                ).rowcount
                == 1
            )

    def release(self, partition: int, owner: str) -> None:
        with self._connection(True) as conn:
            conn.execute(
                "DELETE FROM leases WHERE partition=? AND owner=?", (partition, owner)
            )

    def status(self) -> list[PartitionStatus]:
        with self._connection() as conn:
            pending = {
                r[0]: (r[1], r[2])
                for r in conn.execute(
                    "SELECT partition,count(*),min(created) FROM events GROUP BY partition"
                )
            }
            owners = dict(
                conn.execute(
                    "SELECT partition,owner FROM leases WHERE expires>?", (time.time(),)
                )
            )
            return [
                PartitionStatus(p, *pending.get(p, (0, None)), owners.get(p))
                for p in range(PARTITIONS)
            ]

    def get(self, namespace: str, key: str) -> Any:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value FROM kv WHERE namespace=? AND key=? AND expires>?",
                (namespace, key, time.time()),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def set(
        self, namespace: str, key: str, value: Any, ttl: float, cap: int | None = None
    ) -> None:
        with self._connection(True) as conn:
            conn.execute("DELETE FROM kv WHERE expires<=?", (time.time(),))
            if cap is not None:
                count = conn.execute(
                    "SELECT count(*) FROM kv WHERE namespace=? AND key<>?",
                    (namespace, key),
                ).fetchone()[0]
                conn.execute(
                    "DELETE FROM kv WHERE namespace=? AND key IN (SELECT key FROM kv WHERE namespace=? AND key<>? ORDER BY expires LIMIT ?)",
                    (namespace, namespace, key, max(0, count - cap + 1)),
                )
            conn.execute(
                "INSERT INTO kv VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
                (namespace, key, json.dumps(value), time.time() + ttl),
            )

    def pop(self, namespace: str, key: str) -> Any:
        with self._connection(True) as conn:
            row = conn.execute(
                "SELECT value,expires FROM kv WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            conn.execute("DELETE FROM kv WHERE namespace=? AND key=?", (namespace, key))
            return json.loads(row[0]) if row and row[1] > time.time() else None

    def rate_limit(self, key: str, limit: int, window: float = 60) -> int | None:
        now = time.time()
        with self._connection(True) as conn:
            conn.execute("DELETE FROM limits WHERE expires<?", (now,))
            count, oldest = conn.execute(
                "SELECT count(*),min(ts) FROM limits WHERE key=?", (key,)
            ).fetchone()
            if count >= limit:
                return max(1, math.ceil((oldest or now) + window - now))
            conn.execute("INSERT INTO limits VALUES (?,?,?)", (key, now, now + window))
            return None


_store_lock = threading.Lock()
_store_url: str | None = None
_store: SharedState | None = None


def get_shared_state() -> SharedState:
    """Resolve the deployment store. The environment is a raw URL override."""
    from src.config.app import CONFIG
    from src.config.models import get_tracker_home

    url = os.environ.get("TOKENAGE_QUEUE_URL") or (CONFIG.get("queue") or {}).get("url")
    url = url or f"sqlite:///{Path(get_tracker_home()) / 'state.db'}"
    if not isinstance(url, str) or not url.startswith("sqlite:///"):
        # Do not include a URL: it may contain credentials.
        raise ValueError(
            "Unsupported queue URL scheme; this release supports sqlite:///"
        )
    global _store_url, _store
    with _store_lock:
        if url != _store_url or _store is None:
            _store = SQLiteSharedState(os.path.expanduser(url[10:]))
            _store_url = url
        return _store


# One consumer thread processes a record at a time. Keep its state transitions
# private until the record's database writes succeed, so a retry sees the same
# correlation inputs (including tools from previously acknowledged events).
_pending: ContextVar[dict | None] = ContextVar("correlation_updates", default=None)
_DELETED = object()
CORRELATION_TTL_SECONDS = 600


@contextmanager
def defer_updates():
    updates = {}
    token = _pending.set(updates)
    try:
        yield
        store = get_shared_state()
        for (namespace, key), value in updates.items():
            if value is _DELETED:
                store.pop(namespace, key)
            else:
                store.set(namespace, key, value, CORRELATION_TTL_SECONDS)
    finally:
        _pending.reset(token)


class SharedMapping(MutableMapping[str, Any]):
    def __init__(self, namespace: str):
        self.namespace = namespace

    def __getitem__(self, key: str) -> Any:
        pending = _pending.get()
        state_key = (self.namespace, key)
        value = (
            pending[state_key]
            if pending is not None and state_key in pending
            else get_shared_state().get(self.namespace, key)
        )
        if value is None or value is _DELETED:
            raise KeyError(key)
        return deepcopy(value)

    def __setitem__(self, key: str, value: Any) -> None:
        pending = _pending.get()
        if pending is not None:
            pending[self.namespace, key] = deepcopy(value)
        else:
            get_shared_state().set(self.namespace, key, value, CORRELATION_TTL_SECONDS)

    def __delitem__(self, key: str) -> None:
        pending = _pending.get()
        if pending is not None:
            pending[self.namespace, key] = _DELETED
        else:
            get_shared_state().pop(self.namespace, key)

    def __iter__(self) -> Iterator[str]:
        # Correlation maps are accessed by key, never scanned.
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError
