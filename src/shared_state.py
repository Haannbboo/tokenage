"""Shared transient state, with a durable partitioned queue.

Delivery is at least once; consumers must fence committed positions before acking.
Positions are opaque strings at the interface boundary (Redis stream IDs can
implement the same contract). SQLite uses monotonically increasing row IDs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PARTITIONS = 16
LEASE_SECONDS = 10
MAX_PENDING_EVENTS = 10_000


def partition_for(user_id: str) -> int:
    return (
        int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big")
        % PARTITIONS
    )


class QueueFull(Exception):
    """The user's partition is over its pending-event limit."""


@dataclass(frozen=True)
class Event:
    position: str
    payload: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class PartitionStatus:
    partition: int
    depth: int
    oldest_pending: float | None
    owner: str | None


class SharedState(Protocol):
    identity: str

    def publish(self, user_id: str, payload: dict[str, Any]) -> str: ...
    def read(
        self, partition: int, after: str = "0", limit: int = 500
    ) -> list[Event]: ...
    def ack(self, partition: int, position: str, owner: str) -> bool: ...
    def claim(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool: ...
    def renew(self, partition: int, owner: str, ttl: float = LEASE_SECONDS) -> bool: ...
    def release(self, partition: int, owner: str) -> None: ...
    def status(self) -> list[PartitionStatus]: ...
    def keys(self, namespace: str) -> list[str]: ...
    def get(self, namespace: str, key: str) -> Any: ...
    def set(
        self, namespace: str, key: str, value: Any, ttl: float, cap: int | None = None
    ) -> None: ...
    def pop(self, namespace: str, key: str) -> Any: ...
    def delete(self, namespace: str, key: str) -> None: ...
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
        self._anchor = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._anchor.execute("PRAGMA journal_mode=WAL")
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS store_meta (id INTEGER PRIMARY KEY, identity TEXT NOT NULL);
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

        with self._connection(True) as conn:
            conn.execute(
                "INSERT INTO store_meta VALUES (1, ?) ON CONFLICT(id) DO NOTHING",
                (uuid.uuid4().hex,),
            )
            self.identity = conn.execute(
                "SELECT identity FROM store_meta WHERE id=1"
            ).fetchone()[0]

    @contextmanager
    def _connection(self, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            with conn:
                yield conn
        finally:
            conn.close()

    def publish(self, user_id: str, payload: dict[str, Any]) -> str:
        partition = partition_for(user_id)
        encoded = json.dumps(payload)
        with self._connection(True) as conn:
            if (
                conn.execute(
                    "SELECT count(*) FROM events WHERE partition=?", (partition,)
                ).fetchone()[0]
                > MAX_PENDING_EVENTS
            ):
                raise QueueFull()
            row = conn.execute(
                "INSERT INTO events(partition,payload,created) VALUES (?,?,?)",
                (partition, encoded, time.time()),
            )
            return str(row.lastrowid)

    def read(self, partition: int, after: str = "0", limit: int = 500) -> list[Event]:
        with self._connection() as conn:
            return [
                Event(str(row[0]), json.loads(row[1]), row[2])
                for row in conn.execute(
                    "SELECT id,payload,created FROM events WHERE partition=? AND id>? ORDER BY id LIMIT ?",
                    (partition, int(after), limit),
                )
            ]

    def ack(self, partition: int, position: str, owner: str) -> bool:
        with self._connection(True) as conn:
            if not conn.execute(
                "SELECT 1 FROM leases WHERE partition=? AND owner=? AND expires>?",
                (partition, owner, time.time()),
            ).fetchone():
                return False
            conn.execute(
                "DELETE FROM events WHERE partition=? AND id<=?",
                (partition, int(position)),
            )
            return True

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

    def keys(self, namespace: str) -> list[str]:
        with self._connection() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT key FROM kv WHERE namespace=? AND expires>?",
                    (namespace, time.time()),
                )
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

    def delete(self, namespace: str, key: str) -> None:
        self.pop(namespace, key)

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
