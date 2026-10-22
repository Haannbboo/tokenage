"""In-process queue consumer; blocking ingest and DB calls run on daemon threads.

Delivery is at least once: one transaction per partition pass, then commit,
flush correlation state, and acknowledge. A crash after commit and before ack
redelivers up to one pass (500 events); these rare duplicates are accepted.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.orm import Session

from .shared_state import (
    Event,
    QueueFull,
    SharedState,
    StoreUnavailable,
    defer_updates,
    get_shared_state,
)

log = logging.getLogger(__name__)
POLL_SECONDS = 0.25
# A pass holds the SQLite write lock (and Postgres row locks) until it commits;
# keep it short so API writers get gaps, well under the 10s lease.
PASS_SECONDS = 1
# Short SQLite passes and a yield trade some drain throughput for API write latency.
SQLITE_PASS_SECONDS = 0.05
SQLITE_PAUSE_SECONDS = 0.01


TRANSIENT_DB_ERRORS = (
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
    except QueueFull as exc:
        raise HTTPException(
            503, "collector is busy, retry later", headers={"Retry-After": "5"}
        ) from exc


def store_unavailable_response(request: Request, exc: Exception) -> JSONResponse:
    # Handled exceptions are otherwise not logged; omit sensitive error details.
    log.warning("Returning 503: %s", type(exc).__name__)
    # Clients retry 503, not 500, including DB failures before ingest is queued.
    return JSONResponse(
        status_code=503,
        content={"detail": "shared store unavailable, retry later"},
        headers={"Retry-After": "5"},
    )


class Consumer:
    def __init__(self, store: SharedState | None = None):
        self.store = store if store is not None else get_shared_state()
        self.owner = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.retry_delay = POLL_SECONDS

    def _process(self, event: Event, session: Session) -> None:
        payload = event.payload
        if payload["kind"] == "otlp":
            from .otlp import _parse_log_record

            _parse_log_record(
                *payload["record"],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
                session=session,
            )
        elif payload["kind"] == "usage":
            from .recorder import record_usage

            record_usage(
                **payload["fields"],
                user_id=payload["user_id"],
                device_id=payload["device_id"],
                session=session,
            )
        else:
            raise ValueError("unknown queue event kind")

    def drain(self) -> int:
        done = 0
        self.retry_delay = POLL_SECONDS
        for partition in sorted(s.partition for s in self.store.status() if s.depth):
            if self.stop_event.is_set():
                break
            if not self.store.claim(partition, self.owner):
                continue
            sqlite = False
            last = None
            processed = 0
            deadline = time.monotonic() + PASS_SECONDS
            try:
                from .database import get_engine

                events = self.store.read(partition)
                with defer_updates() as updates:
                    with Session(get_engine()) as session:
                        # sqlite3 legacy mode does not BEGIN for SAVEPOINT; without
                        # this, releasing the first savepoint commits that event.
                        # IMMEDIATE takes the write lock now: a read-first WAL
                        # transaction fails without waiting once another writer commits.
                        dialect = session.get_bind().dialect.name
                        if dialect == "sqlite":
                            sqlite = True
                            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                            # Start the budget after acquiring the write lock.
                            deadline = time.monotonic() + SQLITE_PASS_SECONDS
                        elif dialect == "postgresql":
                            # Bound row-lock waits (e.g. behind a reprice) below the
                            # lease; the timeout is transient, so the pass retries.
                            session.connection().exec_driver_sql(
                                "SET LOCAL lock_timeout = '5s'"
                            )
                        for event in events:
                            if self.stop_event.is_set() or time.monotonic() >= deadline:
                                break
                            before = updates.copy()
                            try:
                                with session.begin_nested():
                                    self._process(event, session)
                            except TRANSIENT_DB_ERRORS:
                                raise
                            except Exception as exc:
                                updates.clear()
                                updates.update(before)
                                # Exception text can contain record bodies or SQL values.
                                log.warning(
                                    "Skipping ingest event user=%s device=%s kind=%s error=%s message=processing-failed",
                                    event.payload.get("user_id"),
                                    event.payload.get("device_id"),
                                    event.payload.get("kind"),
                                    type(exc).__name__,
                                )
                            last = event.position
                            processed += 1
                        session.commit()
            except Exception as exc:
                self.retry_delay = 5
                log.warning(
                    "Ingest partition unavailable partition=%s error=%s",
                    partition,
                    type(exc).__name__,
                )
            else:
                # Exiting defer_updates flushes only after the database commit.
                # A store failure in the flush or ack lands in the except above:
                # the committed pass is redelivered (accepted duplicates).
                if last is not None:
                    self.store.ack(partition, last)
                done += processed
            finally:
                self.store.release(partition, self.owner)
                if sqlite:
                    # SQLite has one writer for every partition and API request.
                    # Yield between partitions so waiting API writers can acquire it.
                    self.stop_event.wait(SQLITE_PAUSE_SECONDS)
                    # A short pass may leave work queued. The fairness pause
                    # already lets API writers run; reserve the idle poll for
                    # an unproductive drain, preserving any failure backoff.
                    if processed and self.retry_delay == POLL_SECONDS:
                        self.retry_delay = 0
        return done

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.drain()
            except Exception as exc:
                self.retry_delay = 5
                log.warning("Ingest consumer unavailable error=%s", type(exc).__name__)
            self.stop_event.wait(self.retry_delay)

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
