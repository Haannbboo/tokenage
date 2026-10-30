"""Opt-in application metrics; prometheus_client is imported only when enabled.

The Gunicorn master sets the multiprocess directory before importing the app.
No identifying request data is used as a label.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from starlette.responses import Response

INGEST_ROUTES = {
    "/otlp/v1/logs": "otlp_logs",
    "/otlp/v1/metrics": "otlp_metrics",
    "/otlp/v1/traces": "otlp_traces",
    "/ingest/v1/usage": "proxy_usage",
}
QUEUE_DEPTH_TIMEOUT = 0.2
_metrics: Metrics | None = None


def cleanup_multiprocess() -> None:
    """Remove only stale directories under our own prefix, never an env path."""
    from .config.models import get_tracker_home

    home = Path(get_tracker_home())
    for directory in home.glob("metrics-*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            pid = int((directory / "master.pid").read_text())
            if pid <= 0:
                raise ValueError("invalid pid")
            os.kill(pid, 0)
        except (FileNotFoundError, ProcessLookupError, ValueError):
            shutil.rmtree(directory, ignore_errors=True)
        except PermissionError:
            pass  # An inaccessible live master is not ours to clean.


def start_multiprocess() -> str:
    """Create a fresh, private directory after bind, before workers import the app."""
    from .config.models import get_tracker_home

    home = Path(get_tracker_home())
    home.mkdir(parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix="metrics-", dir=home)
    try:
        Path(directory, "master.pid").write_text(str(os.getpid()))
    except OSError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = directory
    return directory


def stop_multiprocess(directory: str) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def get_metrics() -> Metrics | None:
    return _metrics


def configure(enabled: bool) -> Metrics | None:
    global _metrics
    _metrics = Metrics() if enabled else None
    return _metrics


class QueueDepthCollector:
    """Bound the entire store read, including initialization, to 200 ms.

    At most one daemon read per worker can be outstanding. A hung store cannot
    accumulate threads, and late results are never reported as fresh samples.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        with self._lock:
            if self._reader is not None and self._reader.is_alive():
                return
            result: list[int] = []

            def read():
                from .shared_state import get_shared_state

                try:
                    result.append(sum(s.depth for s in get_shared_state().status()))
                except Exception:
                    # No exception text: store URLs may contain credentials.
                    pass

            reader = threading.Thread(target=read, name="metrics-depth", daemon=True)
            self._reader = reader
            reader.start()
        reader.join(QUEUE_DEPTH_TIMEOUT)
        if reader.is_alive() or not result:
            return
        metric = GaugeMetricFamily(
            "tokenage_queue_depth", "Pending shared queue events"
        )
        metric.add_metric([], result[0])
        yield metric


class Metrics:
    def __init__(self):
        from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

        from ._version import get_commit, get_version
        from .database.usage import _CLIENT_SOURCE_FAMILIES

        # Import the canonical map only in workers. Lifecycle hooks import this
        # module in the master, which must not preload database/config modules:
        # replacement workers need to read the current configuration on SIGHUP.
        self.client_sources = frozenset(_CLIENT_SOURCE_FAMILIES) | {"proxy"}
        self.registry = CollectorRegistry()
        self.build = Gauge(
            "tokenage_build_info",
            "Server build",
            ["version", "commit"],
            multiprocess_mode="livemax",
            registry=self.registry,
        )
        self.build.labels(get_version(), get_commit()).set(1)
        self.requests = Counter(
            "tokenage_ingest_requests_total",
            "Ingest HTTP responses",
            ["route", "code"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "tokenage_ingest_duration_seconds",
            "Time to accept or reject ingest",
            ["route"],
            registry=self.registry,
        )
        self.errors = Counter(
            "tokenage_ingest_errors_total",
            "Ingest failures",
            ["kind"],
            registry=self.registry,
        )
        self.usage = Counter(
            "tokenage_usage_rows_total",
            "New committed usage rows",
            ["client_source"],
            registry=self.registry,
        )
        self.consumer_rows = Counter(
            "tokenage_consumer_rows_total",
            "Queue events committed and acknowledged (includes skipped events)",
            registry=self.registry,
        )
        self.consumer_batch = Histogram(
            "tokenage_consumer_batch_seconds",
            "Consumer partition pass duration (including failures)",
            registry=self.registry,
        )
        self.depth = QueueDepthCollector()

    def render(self) -> bytes:
        from prometheus_client import CollectorRegistry, generate_latest, multiprocess

        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        else:
            registry = CollectorRegistry()
            registry.register(self.registry)
        # Queue depth is a shared-store read, separate from process aggregation.
        registry.register(self.depth)
        return generate_latest(registry)


def stage_usage(session, client_source: str | None) -> None:
    """Called after insertion; the consumer publishes counts only after commit."""
    if _metrics is None:
        return
    counts = session.info.get("tokenage_usage_counts")
    if counts is not None:
        counts[
            client_source if client_source in _metrics.client_sources else "other"
        ] += 1


def committed_usage(counts: Counter) -> None:
    if _metrics is not None:
        for source, count in counts.items():
            _metrics.usage.labels(source).inc(count)


class MetricsMiddleware:
    """Port firewall outside auth, SPA handling and mounted apps."""

    def __init__(
        self,
        app,
        *,
        enabled: bool,
        port: int | None,
        host: str = "127.0.0.1",
        allowed_hosts=(),
    ):
        from .auth.routes import _LOOPBACK_HOSTNAMES

        self.app = app
        self.enabled = enabled
        self.port = port
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        self.allowed_hosts = _LOOPBACK_HOSTNAMES if loopback else {host, *allowed_hosts}

    def _valid_host(self, scope) -> bool:
        from .auth.routes import _hostname

        hosts = [
            value.decode("latin-1") for key, value in scope["headers"] if key == b"host"
        ]
        return len(hosts) == 1 and _hostname(hosts[0]) in self.allowed_hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        server = scope.get("server")
        metrics_port = self.enabled and server is not None and server[1] == self.port
        if metrics_port or path.rstrip("/") == "/metrics":
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            metrics = get_metrics()
            if (
                metrics_port
                and path == "/metrics"
                and scope["method"] == "GET"
                and self._valid_host(scope)
                and metrics
            ):
                from prometheus_client import CONTENT_TYPE_LATEST

                body = await asyncio.to_thread(metrics.render)
                response = Response(body, headers={"content-type": CONTENT_TYPE_LATEST})
            else:
                response = Response(status_code=404)
            return await response(scope, receive, send)

        route = INGEST_ROUTES.get(path.rstrip("/"))
        if route is None or scope["type"] != "http":
            return await self.app(scope, receive, send)
        metrics = get_metrics() if self.enabled else None
        if metrics is None:
            return await self.app(scope, receive, send)
        started = time.monotonic()
        code = 500

        async def counted_send(message):
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, counted_send)
        finally:
            metrics.requests.labels(route, str(code)).inc()
            metrics.duration.labels(route).observe(time.monotonic() - started)
            kind = scope.get("tokenage_ingest_error") or {
                401: "auth",
                403: "auth",
                413: "too_large",
                400: "bad_request",
                422: "bad_request",
                503: "store_unavailable",
            }.get(code)
            if kind:
                metrics.errors.labels(kind).inc()
