"""Shared transient state, with a durable partitioned queue.

Queue consumers write before acknowledging; acks remove all events through a
finished position. Correlation mappings defer updates until record writes succeed.
"""

from __future__ import annotations

import hashlib
import json
import logging
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
from typing import Any, Protocol, cast

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

log = logging.getLogger(__name__)

PARTITIONS = 16
LEASE_SECONDS = 10
MAX_PENDING_EVENTS = 50_000
RATE_LIMIT_WINDOW_SECONDS = 60


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
    def read(self, partition: int) -> list[Event]: ...
    def ack(self, partition: int, position: str) -> None: ...
    def claim(self, partition: int, owner: str) -> bool: ...
    def release(self, partition: int, owner: str) -> None: ...
    def status(self) -> list[PartitionStatus]: ...
    def get(self, namespace: str, key: str) -> Any: ...
    def set(
        self, namespace: str, key: str, value: Any, ttl: float, cap: int | None = None
    ) -> None: ...
    def pop(self, namespace: str, key: str) -> Any: ...
    def rate_limit(self, key: str, limit: int) -> int | None: ...


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
            # Switching a new file to WAL may return SQLITE_BUSY immediately
            # despite busy_timeout when another worker is opening it too.
            deadline = time.monotonic() + 5
            while True:
                try:
                    mode = self._anchor.execute("PRAGMA journal_mode").fetchone()[0]
                    if mode != "wal":
                        self._anchor.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            self._anchor.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.OperationalError as exc:
            if getattr(self, "_anchor", None) is not None:
                self._anchor.close()
            raise StoreUnavailable() from exc
        with self._connection() as conn:
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

    def read(self, partition: int) -> list[Event]:
        with self._connection() as conn:
            return [
                Event(str(row[0]), json.loads(row[1]))
                for row in conn.execute(
                    "SELECT id,payload FROM events WHERE partition=? ORDER BY id LIMIT 500",
                    (partition,),
                )
            ]

    def ack(self, partition: int, position: str) -> None:
        with self._connection(True) as conn:
            conn.execute(
                "DELETE FROM events WHERE partition=? AND id<=?",
                (partition, int(position)),
            )

    def claim(self, partition: int, owner: str) -> bool:
        now = time.time()
        with self._connection(True) as conn:
            return (
                conn.execute(
                    "INSERT INTO leases VALUES (?,?,?) ON CONFLICT(partition) DO UPDATE SET owner=excluded.owner, expires=excluded.expires WHERE leases.expires<=?",
                    (partition, owner, now + LEASE_SECONDS, now),
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

    def rate_limit(self, key: str, limit: int) -> int | None:
        with self._connection(True) as conn:
            # Read the clock after taking the write lock: a stale `now` from a
            # waiting caller would put a later entry in its past (Retry-After 61).
            now = time.time()
            conn.execute("DELETE FROM limits WHERE expires<?", (now,))
            count, oldest = conn.execute(
                "SELECT count(*),min(ts) FROM limits WHERE key=?", (key,)
            ).fetchone()
            if count >= limit:
                return max(
                    1, math.ceil((oldest or now) + RATE_LIMIT_WINDOW_SECONDS - now)
                )
            conn.execute(
                "INSERT INTO limits VALUES (?,?,?)",
                (key, now, now + RATE_LIMIT_WINDOW_SECONDS),
            )
            return None


class RedisSharedState:
    """Redis 6.2+ store; atomic scripts serialize competing hosts.

    Streams contain only pending events. Like SQLite, acknowledgement removes
    events through the finished position, after the usage transaction commits.
    """

    def __init__(self, url: str, *, prefix: str = "tokenage:"):
        self.prefix = prefix
        try:
            pool = redis.ConnectionPool.from_url(url)
            # URL query parameters must not disable bounded calls or enable
            # retries of writes whose response was lost (notably publish/pop).
            pool.connection_kwargs.update(
                socket_timeout=5,
                socket_connect_timeout=5,
                decode_responses=True,
                # Load-bearing: never retry a write whose reply was lost.
                retry=Retry(NoBackoff(), 0),
            )
            self._client = redis.Redis(connection_pool=pool)
        except (ValueError, redis.RedisError) as exc:
            log.warning("shared store unavailable error=%s", type(exc).__name__)
            raise StoreUnavailable() from None

    @contextmanager
    def _available(self):
        try:
            yield
        except (ValueError, redis.RedisError) as exc:
            # Redis exceptions may contain credentials or payload fragments.
            log.warning("shared store unavailable error=%s", type(exc).__name__)
            raise StoreUnavailable() from None

    def _key(self, kind: str, *parts: Any) -> str:
        return self.prefix + kind + ":" + json.dumps(parts, separators=(",", ":"))

    def publish(self, user_id: str, payloads: list[dict[str, Any]]) -> None:
        rows = [json.dumps(payload) for payload in payloads]
        with self._available():
            accepted = self._client.eval(
                """
                if redis.call('XLEN', KEYS[1]) + #ARGV - 1 > tonumber(ARGV[1]) then
                    return 0
                end
                for i = 2, #ARGV do
                    redis.call('XADD', KEYS[1], '*', 'payload', ARGV[i])
                end
                return 1
                """,
                1,
                self._key("events", partition_for(user_id)),
                MAX_PENDING_EVENTS,
                *rows,
            )
            if not accepted:
                raise QueueFull()

    def read(self, partition: int) -> list[Event]:
        with self._available():
            rows = cast(
                list[tuple[str, dict[str, str]]],
                self._client.xrange(self._key("events", partition), count=500),
            )
            return [
                Event(position, json.loads(fields["payload"]))
                for position, fields in rows
            ]

    def ack(self, partition: int, position: str) -> None:
        # MINID is exclusive. Increment the sequence to include position itself;
        # exact trimming preserves every later event, including the same ms.
        with self._available():
            milliseconds, sequence = map(int, position.split("-"))
            self._client.xtrim(
                self._key("events", partition),
                minid=f"{milliseconds}-{sequence + 1}",
                approximate=False,
            )

    def claim(self, partition: int, owner: str) -> bool:
        with self._available():
            return bool(
                self._client.set(
                    self._key("lease", partition),
                    owner,
                    nx=True,
                    px=max(1, math.ceil(LEASE_SECONDS * 1000)),
                )
            )

    def release(self, partition: int, owner: str) -> None:
        with self._available():
            self._client.eval(
                """
                if redis.call('GET', KEYS[1]) == ARGV[1] then
                    return redis.call('DEL', KEYS[1])
                end
                return 0
                """,
                1,
                self._key("lease", partition),
                owner,
            )

    def status(self) -> list[PartitionStatus]:
        with self._available():
            keys = [
                self._key(kind, partition)
                for partition in range(PARTITIONS)
                for kind in ("events", "lease")
            ]
            clock, values = self._client.eval(
                """
                local values = {}
                for i = 1, #KEYS, 2 do
                    local first = redis.call('XRANGE', KEYS[i], '-', '+', 'COUNT', 1)
                    values[#values + 1] = {
                        redis.call('XLEN', KEYS[i]),
                        first[1] and first[1][1] or false,
                        redis.call('GET', KEYS[i + 1])
                    }
                end
                return {redis.call('TIME'), values}
                """,
                len(keys),
                *keys,
            )
            redis_now = int(clock[0]) + int(clock[1]) / 1_000_000
            local_now = time.time()
            return [
                PartitionStatus(
                    p,
                    depth,
                    local_now - (redis_now - int(first.split("-")[0]) / 1000)
                    if first
                    else None,
                    owner,
                )
                for p, (depth, first, owner) in enumerate(values)
            ]

    def get(self, namespace: str, key: str) -> Any:
        with self._available():
            value = self._client.get(self._key("kv", namespace, key))
            return json.loads(value) if value is not None else None

    def set(
        self, namespace: str, key: str, value: Any, ttl: float, cap: int | None = None
    ) -> None:
        with self._available():
            self._client.eval(
                """
                local t = redis.call('TIME')
                local now = t[1] * 1000 + t[2] / 1000
                redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
                redis.call('ZREM', KEYS[2], KEYS[1])
                if ARGV[3] ~= '' then
                    local n = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[3]) + 1
                    if n > 0 then
                        local victims = redis.call('ZRANGE', KEYS[2], 0, n - 1)
                        for _, key in ipairs(victims) do
                            redis.call('DEL', key)
                            redis.call('ZREM', KEYS[2], key)
                        end
                    end
                end
                if tonumber(ARGV[2]) > 0 then
                    redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
                    redis.call('ZADD', KEYS[2], now + tonumber(ARGV[2]), KEYS[1])
                else
                    redis.call('DEL', KEYS[1])
                end
                local last = redis.call('ZRANGE', KEYS[2], -1, -1, 'WITHSCORES')
                if #last > 0 then
                    redis.call('PEXPIREAT', KEYS[2], math.ceil(tonumber(last[2])))
                else
                    redis.call('DEL', KEYS[2])
                end
                """,
                2,
                self._key("kv", namespace, key),
                self._key("kv-index", namespace),
                json.dumps(value),
                math.ceil(ttl * 1000),
                cap if cap is not None else "",
            )

    def pop(self, namespace: str, key: str) -> Any:
        with self._available():
            value = self._client.eval(
                """
                local value = redis.call('GET', KEYS[1])
                redis.call('DEL', KEYS[1])
                redis.call('ZREM', KEYS[2], KEYS[1])
                return value
                """,
                2,
                self._key("kv", namespace, key),
                self._key("kv-index", namespace),
            )
            return json.loads(value) if value is not None else None

    def rate_limit(self, key: str, limit: int) -> int | None:
        # A fixed INCR/EXPIRE window would permit bursts SQLite rejects. A
        # sorted set keeps the same sliding window, using the server's clock
        # so independently clocked hosts agree. Rejected attempts never count.
        with self._available():
            retry = self._client.eval(
                """
                local t = redis.call('TIME')
                local now = t[1] * 1000000 + t[2]
                local window = tonumber(ARGV[3]) * 1000000
                redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', '(' .. (now - window))
                if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
                    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
                    if #oldest == 0 then return math.max(1, math.ceil(tonumber(ARGV[3]))) end
                    return math.max(1, math.ceil((oldest[2] + window - now) / 1000000))
                end
                redis.call('ZADD', KEYS[1], now, ARGV[2])
                redis.call('PEXPIRE', KEYS[1], math.ceil(window / 1000) + 1000)
                return 0
                """,
                1,
                self._key("limit", key),
                limit,
                os.urandom(16).hex(),
                RATE_LIMIT_WINDOW_SECONDS,
            )
            return retry or None


_store_lock = threading.Lock()
_store_url: str | None = None
_store_prefix: str | None = None
_store: SharedState | None = None


def get_shared_state() -> SharedState:
    """Resolve the deployment store. The environment is a raw URL override."""
    from src.config.app import CONFIG
    from src.config.models import get_tracker_home

    url = os.environ.get("TOKENAGE_QUEUE_URL") or (CONFIG.get("queue") or {}).get("url")
    url = url or f"sqlite:///{Path(get_tracker_home()) / 'state.db'}"
    if not isinstance(url, str) or not url.startswith(
        ("sqlite:///", "redis://", "rediss://")
    ):
        # Do not include a URL: it may contain credentials.
        raise ValueError(
            "Unsupported queue URL scheme; use sqlite:///, redis:// or rediss://"
        )
    prefix = (CONFIG.get("queue") or {}).get("prefix", "tokenage:")
    global _store_url, _store_prefix, _store
    with _store_lock:
        if url != _store_url or prefix != _store_prefix or _store is None:
            _store = (
                SQLiteSharedState(os.path.expanduser(url[10:]))
                if url.startswith("sqlite:///")
                else RedisSharedState(url, prefix=prefix)
            )
            _store_url = url
            _store_prefix = prefix
        return _store


# Keep a consumer pass's state transitions private until its database commit.
# A partial state flush can still leave a retry with changed correlation inputs
# and duplicate usage.
_pending: ContextVar[dict | None] = ContextVar("correlation_updates", default=None)
_DELETED = object()
CORRELATION_TTL_SECONDS = 600


@contextmanager
def defer_updates():
    updates = {}
    token = _pending.set(updates)
    try:
        yield updates
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
