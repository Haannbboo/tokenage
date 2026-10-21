"""In-process queue consumer; blocking ingest and DB calls run on daemon threads.

Delivery is at least once: write each event, then acknowledge it in the store.
A crash mid-pass or a stalled owner resuming after takeover can redeliver up
to one pass (500 events). PR 1c will group writes into one transaction per batch, then ack.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from .shared_state import (
    LEASE_SECONDS,
    Event,
    QueueFull,
    SharedState,
    StoreUnavailable,
    get_shared_state,
)

log = logging.getLogger(__name__)
POLL_SECONDS = 0.25


TRANSIENT_DB_ERRORS = (
    sqlite3.OperationalError,
    StoreUnavailable,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    DatabaseTimeout,
)


def publish(user_id: str, payloads: list[dict]) -> None:
    try:
        get_shared_state().publish(
            user_id, [{**payload, "user_id": user_id} for payload in payloads]
        )
    except (QueueFull, StoreUnavailable) as exc:
        raise HTTPException(
            503, "collector is busy, retry later", headers={"Retry-After": "5"}
        ) from exc


class Consumer:
    def __init__(self, store: SharedState | None = None):
        self.store = store if store is not None else get_shared_state()
        self.owner = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    def _process(self, event: Event) -> None:
        payload = event.payload
        if payload["kind"] == "otlp":
            from .otlp import _parse_log_record

            _parse_log_record(
                *payload["record"],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
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

    def drain(self) -> int:
        done = 0
        for partition in sorted(s.partition for s in self.store.status() if s.depth):
            if self.stop_event.is_set():
                break
            if not self.store.claim(partition, self.owner):
                continue
            last = None
            renew_at = time.monotonic() + LEASE_SECONDS / 2
            try:
                for event in self.store.read(partition):
                    if self.stop_event.is_set():
                        break
                    now = time.monotonic()
                    if now >= renew_at:
                        if not self.store.renew(partition, self.owner):
                            break
                        renew_at = now + LEASE_SECONDS / 2
                    try:
                        self._process(event)
                    except TRANSIENT_DB_ERRORS as exc:
                        log.warning(
                            "Ingest partition unavailable partition=%s error=%s",
                            partition,
                            type(exc).__name__,
                        )
                        break
                    except Exception as exc:
                        # Exception text can contain record bodies or SQL values.
                        log.warning(
                            "Skipping ingest event user=%s device=%s kind=%s error=%s message=processing-failed",
                            event.payload.get("user_id"),
                            event.payload.get("device_id"),
                            event.payload.get("kind"),
                            type(exc).__name__,
                        )
                    last = event.position
                    done += 1
            finally:
                try:
                    if last is not None:
                        self.store.ack(partition, last)
                finally:
                    self.store.release(partition, self.owner)
        return done

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.drain()
            except Exception as exc:
                log.warning("Ingest consumer unavailable error=%s", type(exc).__name__)
            self.stop_event.wait(POLL_SECONDS)

    def start(self) -> None:
        self.worker = threading.Thread(
            target=self._run, name="tokenage-ingest", daemon=True
        )
        self.worker.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(3)


@asynccontextmanager
async def consumer_lifespan():
    consumer = Consumer()
    consumer.start()
    try:
        yield consumer
    finally:
        await asyncio.to_thread(consumer.close)
