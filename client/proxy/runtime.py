"""Run the proxy inside the client service process.

``start_proxy()`` never raises: every outcome is a ``state`` recorded under
the ``proxy`` key of ``run/client.json``::

    {"state": "running" | "failed" | "disabled",
     "port": int | None, "url": str | None, "error": str | None}
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import uvicorn

from client.proxy.app import create_app
from client.proxy.config import ProxyConfig, load_proxy_config

HOST = "127.0.0.1"
DEFAULT_PORT = 4000
SCAN_LIMIT = 100
STARTUP_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger("tokenage.client.proxy")


class PortError(Exception):
    pass


def _bind(port: int) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError:
        sock.close()
        return None
    return sock


def bind_proxy_socket(configured: int | None, recorded: int | None) -> socket.socket:
    """A bound (not yet listening) loopback socket, or PortError.

    Configured port: bind it or fail, never scan. Otherwise the recorded port,
    then 4000 scanning upward. Handing uvicorn the bound socket avoids a
    check-then-bind race.
    """
    if configured is not None:
        sock = _bind(configured)
        if sock is None:
            raise PortError(f"port {configured} (from client.yaml) is in use")
        return sock
    candidates = ([recorded] if recorded else []) + list(
        range(DEFAULT_PORT, DEFAULT_PORT + SCAN_LIMIT)
    )
    for port in candidates:
        sock = _bind(port)
        if sock is not None:
            return sock
    raise PortError(f"no free port in {DEFAULT_PORT}-{DEFAULT_PORT + SCAN_LIMIT - 1}")


def record_proxy_state(state: dict[str, Any]) -> None:
    """Merge ``state`` into ``run/client.json`` under ``proxy``, keeping the
    service's own keys."""
    from client.auth import save_private_object
    from client.service import _read_state, state_path

    current = _read_state()
    current["proxy"] = {**state, "pid": os.getpid()}
    save_private_object(state_path(), current)


def read_proxy_state() -> dict[str, Any] | None:
    from client.service import _read_state

    state = _read_state().get("proxy")
    return state if isinstance(state, dict) else None


def live_proxy_state() -> dict[str, Any] | None:
    """The recorded proxy state, or None when the process that wrote it is gone
    (a crashed or stopped service must not read as a running proxy)."""
    state = read_proxy_state()
    pid = state.get("pid") if state else None
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError:  # alive, owned by someone else
        pass
    return state


def proxy_line(state: dict[str, Any] | None) -> str | None:
    """One line for status output; None when there is nothing worth showing
    (no proxy configured, or no live service)."""
    if not state or state.get("state") == "disabled":
        return None
    if state.get("state") == "running":
        return str(state.get("url"))
    return f"failed: {state.get('error')}"


def _state(kind: str, port: int | None = None, error: str | None = None):
    return {
        "state": kind,
        "port": port,
        "url": f"http://{HOST}:{port}" if port and kind == "running" else None,
        "error": error,
    }


@dataclass
class ProxyHandle:
    state: dict[str, Any]
    _server: uvicorn.Server | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def start_proxy(config: ProxyConfig | None = None) -> ProxyHandle:
    """Start the proxy in a daemon thread and record its state.

    ``config`` defaults to client.yaml. Safe to call from the service's main
    thread; call ``handle.stop()`` on shutdown.
    """
    sock: socket.socket | None = None
    try:
        if config is None:
            config = load_proxy_config()
        if config is None:
            return _finish(ProxyHandle(_state("disabled")))
        previous = read_proxy_state() or {}
        recorded = previous.get("port")
        sock = bind_proxy_socket(
            config.port, recorded if isinstance(recorded, int) else None
        )
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(config), log_level="warning", lifespan="off")
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            server.should_exit = True
            raise PortError("proxy server did not start")
        return _finish(ProxyHandle(_state("running", port), server, thread))
    except Exception as exc:
        if sock is not None:
            sock.close()
        message = str(exc) if isinstance(exc, (PortError, ValueError)) else repr(exc)
        logger.warning("proxy failed: %s", message)
        return _finish(ProxyHandle(_state("failed", error=message)))


def _finish(handle: ProxyHandle) -> ProxyHandle:
    try:
        record_proxy_state(handle.state)
    except Exception:
        logger.exception("could not record proxy state")
    return handle
