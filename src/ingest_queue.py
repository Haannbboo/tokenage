"""In-process queue consumer; blocking ingest and DB calls run on daemon threads.

PR 1b commits a fenced position immediately after each event's existing writes.
Those writes still own their sessions. PR 1c will commit them and the position
in one transaction; a crash between them can currently redeliver an event.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from .shared_state import Event, QueueFull, SharedState, get_shared_state

log = logging.getLogger(__name__)
POLL_SECONDS = 0.25
RENEW_SECONDS = 2.0
MAX_PROCESS_SECONDS = 30


TRANSIENT_DB_ERRORS = (
    sqlite3.OperationalError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    DatabaseTimeout,
)


def publish(user_id: str, payload: dict) -> None:
    try:
        get_shared_state().publish(user_id, {**payload, "user_id": user_id})
    except (QueueFull, sqlite3.OperationalError) as exc:
        raise HTTPException(
            503, "collector is busy, retry later", headers={"Retry-After": "5"}
        ) from exc


def _offset(position: str) -> str:
    return position.split(":", 1)[0].split("/", 1)[0]


class LeaseLost(Exception):
    pass


class Consumer:
    def __init__(self, store: SharedState | None = None):
        self.store = store if store is not None else get_shared_state()
        self.owner = uuid.uuid4().hex
        self.positions: dict[int, str] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.abandoned = threading.Event()
        self.processing_since: float | None = None
        self.active_partition: int | None = None

    def _claim_position(self, partition: int) -> str:
        from .database import get_engine

        # Include the claim ID in the opaque position so an owner that lost a
        # lease is fenced even if its successor has not advanced the offset.
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO queue_positions(partition,position) VALUES (:p, :v) ON CONFLICT(partition) DO NOTHING"
                ),
                {"p": partition, "v": "0"},
            )
            old: str = conn.execute(
                text("SELECT position FROM queue_positions WHERE partition=:p"),
                {"p": partition},
            ).scalar_one()
            parts = old.split(":")
            offset = (
                old.split(":", 1)[0]
                if len(parts) < 3 or parts[1] == self.store.identity
                else "0"
            )
            claimed = f"{offset}:{self.store.identity}:{self.owner}"
            changed = conn.execute(
                text(
                    "UPDATE queue_positions SET position=:new WHERE partition=:p AND position=:old"
                ),
                {"new": claimed, "p": partition, "old": old},
            )
            if changed.rowcount != 1:
                raise LeaseLost()
        return claimed

    def commit_position(self, partition: int, expected: str, position: str) -> str:
        from .database import get_engine

        if self.abandoned.is_set() or not self.store.renew(partition, self.owner):
            raise LeaseLost()
        value = f"{position}:{self.store.identity}:{self.owner}"
        with get_engine().begin() as conn:
            changed = conn.execute(
                text(
                    "UPDATE queue_positions SET position=:new WHERE partition=:p AND position=:old"
                ),
                {"new": value, "p": partition, "old": expected},
            )
            if changed.rowcount != 1:
                raise LeaseLost()
        return value

    def _progress(self) -> None:
        partition = self.active_partition
        if (
            self.abandoned.is_set()
            or partition is None
            or not self.store.renew(partition, self.owner)
        ):
            raise LeaseLost()
        # The watchdog measures stalled work, not the duration of a healthy
        # multi-record OTLP export over a slow remote database.
        self.processing_since = time.monotonic()

    def _process(self, event: Event) -> None:
        payload = event.payload
        if payload["kind"] == "otlp":
            from .otlp import _ingest_records

            partition = self.active_partition
            assert partition is not None
            checkpoint = self.positions[partition].split(":", 1)[0].split("/")
            start = (
                int(checkpoint[2])
                if len(checkpoint) == 3 and checkpoint[1] == event.position
                else 0
            )
            completed = start

            def record_completed() -> None:
                nonlocal completed
                completed += 1
                expected = self.positions[partition]
                value = self.commit_position(
                    partition,
                    expected,
                    f"{_offset(expected)}/{event.position}/{completed}",
                )
                with self.lock:
                    self.positions[partition] = value

            _ingest_records(
                payload["records"][start:],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
                progress=self._progress,
                record_completed=record_completed,
            )
        elif payload["kind"] == "usage":
            from .recorder import record_usage

            record_usage(
                **payload["fields"],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
            )
        else:
            raise ValueError("unknown queue event kind")

    def drain(self, *, limit: int = 500) -> int:
        """Synchronously process available events; tests call this without sleeps."""
        processed = 0
        pending = {status.partition for status in self.store.status() if status.depth}
        for partition in sorted(pending):
            if self.stop_event.is_set() or self.abandoned.is_set():
                break
            if partition not in self.positions:
                if not self.store.claim(partition, self.owner):
                    continue
                with self.lock:
                    self.positions[partition] = "0"
                try:
                    self.processing_since = time.monotonic()
                    value = self._claim_position(partition)
                    if not self.store.renew(partition, self.owner):
                        raise LeaseLost()
                    with self.lock:
                        self.positions[partition] = value
                except LeaseLost:
                    self._drop(partition)
                    continue
                except TRANSIENT_DB_ERRORS as exc:
                    self._drop(partition)
                    log.warning(
                        "Ingest partition unavailable partition=%s error=%s",
                        partition,
                        type(exc).__name__,
                    )
                    continue
                except Exception:
                    self._drop(partition)
                    raise
                finally:
                    self.processing_since = None
            position = self.positions[partition]
            # Trims redeliveries already committed before a crash during ack.
            if not self.store.ack(partition, _offset(position), self.owner):
                self._drop(partition)
                continue
            for event in self.store.read(partition, _offset(position), limit):
                self.processing_since = time.monotonic()
                try:
                    if (
                        self.abandoned.is_set()
                        or self.stop_event.is_set()
                        or not self.store.renew(partition, self.owner)
                    ):
                        raise LeaseLost()
                    try:
                        self.active_partition = partition
                        self._process(event)
                    except (LeaseLost, *TRANSIENT_DB_ERRORS):
                        raise
                    except Exception as exc:
                        # Never interpolate exception text: parsers and drivers
                        # may include raw attributes or SQL parameter values.
                        log.warning(
                            "Skipping ingest event user=%s device=%s kind=%s error=%s message=processing-failed",
                            event.payload.get("user_id"),
                            event.payload.get("device_id"),
                            event.payload.get("kind"),
                            type(exc).__name__,
                        )
                    position = self.commit_position(
                        partition, self.positions[partition], event.position
                    )
                    with self.lock:
                        self.positions[partition] = position
                    if not self.store.ack(partition, event.position, self.owner):
                        raise LeaseLost()
                    processed += 1
                except LeaseLost:
                    self._drop(partition)
                    break
                except TRANSIENT_DB_ERRORS as exc:
                    log.warning(
                        "Ingest partition unavailable partition=%s error=%s",
                        partition,
                        type(exc).__name__,
                    )
                    break
                finally:
                    self.active_partition = None
                    self.processing_since = None
            # Once drained, let a newly started consumer claim future work.
            # A publish racing this check is harmless: either this owner keeps
            # the lease for its next poll or a successor claims it after release.
            if partition in self.positions and not self.store.read(
                partition, _offset(self.positions[partition]), 1
            ):
                self._drop(partition)
        return processed

    def _drop(self, partition: int) -> None:
        with self.lock:
            self.positions.pop(partition, None)
        self.store.release(partition, self.owner)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.drain()
            except Exception as exc:
                log.warning("Ingest consumer unavailable error=%s", type(exc).__name__)
            if self.abandoned.is_set():
                for partition in list(self.positions):
                    self._drop(partition)
                self.owner = uuid.uuid4().hex
                self.abandoned.clear()
            self.stop_event.wait(POLL_SECONDS)

    def _renew(self) -> None:
        while not self.stop_event.wait(RENEW_SECONDS):
            # A hung DB must not keep leases forever. Abandon this consumer
            # after the deadline; resume with fresh leases if the call returns.
            since = self.processing_since
            if since is not None and time.monotonic() - since > MAX_PROCESS_SECONDS:
                self.abandoned.set()
            if self.abandoned.is_set():
                continue
            with self.lock:
                partitions = list(self.positions)
            for partition in partitions:
                try:
                    if not self.store.renew(partition, self.owner):
                        self._drop(partition)
                except Exception as exc:
                    log.warning(
                        "Ingest lease renewal failed error=%s", type(exc).__name__
                    )

    def start(self) -> None:
        threading.Thread(target=self._run, name="tokenage-ingest", daemon=True).start()
        threading.Thread(
            target=self._renew, name="tokenage-leases", daemon=True
        ).start()

    def close(self) -> None:
        # Do not release leases while a DB call might still be writing. Stop
        # renewal and let them expire; a returned call fails its position fence.
        self.stop_event.set()


@asynccontextmanager
async def consumer_lifespan():
    consumer = Consumer()
    consumer.start()
    try:
        yield consumer
    finally:
        consumer.close()


def drain() -> int:
    """Test helper: drain without launching lifespan/background threads."""
    consumer = Consumer()
    try:
        total = 0
        while count := consumer.drain():
            total += count
        return total
    finally:
        consumer.close()
        for partition in list(consumer.positions):
            consumer._drop(partition)
