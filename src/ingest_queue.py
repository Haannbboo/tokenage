"""In-process queue consumer; blocking ingest and DB calls run on daemon threads.

Delivery is at least once: write each event, then acknowledge it in the store.
A crash before ack or a stalled owner resuming after takeover can duplicate an
event. PR 1c will group writes into one transaction per batch, then ack.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from .shared_state import LEASE_SECONDS, Event, QueueFull, SharedState, get_shared_state

log = logging.getLogger(__name__)
POLL_SECONDS = 0.25
RENEW_SECONDS = 2.0


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


class LeaseLost(Exception):
    pass


class Consumer:
    def __init__(self, store: SharedState | None = None):
        self.store = store if store is not None else get_shared_state()
        self.owner = uuid.uuid4().hex
        self.partitions: set[int] = set()
        self.records_completed: dict[str, int] = {}
        self.events: dict[int, str] = {}
        self.worker: threading.Thread | None = None
        self.renew_at: dict[int, float] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.active_partition: int | None = None
        self.processing_since: float | None = None

    def _renew_if_due(self, partition: int) -> None:
        now = time.monotonic()
        with self.lock:
            due = self.renew_at.get(partition, 0)
        if now < due:
            return
        if not self.store.renew(partition, self.owner):
            raise LeaseLost()
        with self.lock:
            self.renew_at[partition] = now + LEASE_SECONDS / 2

    def _progress(self) -> None:
        partition = self.active_partition
        with self.lock:
            if partition not in self.partitions:
                raise LeaseLost()
        assert partition is not None
        self._renew_if_due(partition)

    def _process(self, event: Event) -> None:
        payload = event.payload
        if payload["kind"] == "otlp":
            from .otlp import _ingest_records

            partition = self.active_partition
            assert partition is not None
            start = self.records_completed.get(event.position, 0)

            def record_completed() -> None:
                with self.lock:
                    if partition not in self.partitions:
                        raise LeaseLost()
                    self.records_completed[event.position] = (
                        self.records_completed.get(event.position, start) + 1
                    )

            _ingest_records(
                payload["records"][start:],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
                progress=self._progress,
                record_completed=record_completed,
                partition=partition,
                event_id=event.position,
                record_start=start,
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
            if self.stop_event.is_set():
                break
            if partition not in self.partitions:
                if not self.store.claim(partition, self.owner):
                    continue
                with self.lock:
                    self.partitions.add(partition)
                    self.renew_at[partition] = time.monotonic() + LEASE_SECONDS / 2
            for event in self.store.read(partition, limit=limit):
                self.processing_since = time.monotonic()
                try:
                    if self.stop_event.is_set():
                        raise LeaseLost()
                    self._renew_if_due(partition)
                    try:
                        self.active_partition = partition
                        self.events[partition] = event.position
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
                    self._progress()
                    if not self.store.ack(partition, event.position, self.owner):
                        raise LeaseLost()
                    self.records_completed.pop(event.position, None)
                    self.events.pop(partition, None)
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
            if partition in self.partitions and (
                self.stop_event.is_set() or not self.store.read(partition, limit=1)
            ):
                self._drop(partition)
        return processed

    def _drop(self, partition: int) -> None:
        with self.lock:
            self.partitions.discard(partition)
            event_id = self.events.pop(partition, None)
            if event_id is not None:
                self.records_completed.pop(event_id, None)
            self.renew_at.pop(partition, None)
        self.store.release(partition, self.owner)

    def _run(self) -> None:
        # ponytail: a hung DB call blocks this process's consumer; bound it with
        # a DB statement timeout if it bites. Renewal pauses for stalled work
        # so another worker can take over after lease expiry. If a stale call
        # returns after takeover, its writes may duplicate the event.
        while not self.stop_event.is_set():
            try:
                self.drain()
            except Exception as exc:
                log.warning("Ingest consumer unavailable error=%s", type(exc).__name__)
            self.stop_event.wait(POLL_SECONDS)

    def _renew(self) -> None:
        while not self.stop_event.wait(RENEW_SECONDS):
            since = self.processing_since
            if since is not None and time.monotonic() - since > LEASE_SECONDS / 4:
                continue
            with self.lock:
                partitions = list(self.partitions)
            for partition in partitions:
                try:
                    self._renew_if_due(partition)
                except LeaseLost:
                    self._drop(partition)
                except Exception as exc:
                    log.warning(
                        "Ingest lease renewal failed error=%s", type(exc).__name__
                    )

    def start(self) -> None:
        self.worker = threading.Thread(
            target=self._run, name="tokenage-ingest", daemon=True
        )
        self.worker.start()
        threading.Thread(
            target=self._renew, name="tokenage-leases", daemon=True
        ).start()

    def close(self) -> None:
        self.stop_event.set()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=3)
        for partition in list(self.partitions):
            # close may be called from inside an event in synchronous drains.
            # That event releases its lease after finishing and acknowledging.
            if self.active_partition == partition and (
                self.worker is None or self.worker is threading.current_thread()
            ):
                continue
            self._drop(partition)


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
