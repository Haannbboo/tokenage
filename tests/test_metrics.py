"""Metrics listener isolation, bounded scraping and committed instrumentation."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.types import ASGIApp

# Keep prometheus_client imports inside tests: loading its default collectors
# during collection adds GC pressure to every xdist worker.
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def collect_metrics_cycles():
    """Keep collector/request cycles from accumulating in unrelated tests."""
    import gc

    yield
    gc.collect()


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"enabled": True},
        {"port": True},
        {"port": 0},
        {"port": 65536},
        {"port": "9108"},
        {"enabled": True, "port": 4001},
        {"allowed_hosts": "host"},
        {"allowed_hosts": ["http://host"]},
        {"allowed_hosts": ["host:9108"]},
        {"host": "127.0.0.1:9108"},
        {"host": ""},
        {"host": "host\nname"},
        {"host": None},
        [],
        None,
    ],
)
def test_metrics_config_validation(config_module, monkeypatch, tmp_path, settings):
    from src.config.server_config import load_server_config

    config = tmp_path / "metrics.yaml"
    config.write_text(yaml.safe_dump({"metrics": settings}))
    monkeypatch.setenv("TOKENAGE_CONFIG", str(config))
    for loader in (config_module.load_config, load_server_config):
        with pytest.raises(ValueError, match="metrics"):
            loader()


def test_metrics_config_defaults_and_binds(config_module, monkeypatch, tmp_path):
    from src.config.server_config import load_server_config

    config = tmp_path / "metrics.yaml"
    config.write_text("{}")
    monkeypatch.setenv("TOKENAGE_CONFIG", str(config))
    assert config_module.load_config()["metrics"] == {
        "enabled": False,
        "host": "127.0.0.1",
        "port": None,
        "allowed_hosts": [],
    }
    assert not load_server_config().metrics.enabled
    assert runpy.run_path(str(ROOT / "src/config/api.conf.py"))["bind"] == [
        "127.0.0.1:4001"
    ]
    config.write_text(
        yaml.safe_dump({"metrics": {"enabled": True, "host": "[::1]", "port": 9108}})
    )
    assert load_server_config().metrics.host == "::1"
    assert runpy.run_path(str(ROOT / "src/config/api.conf.py"))["bind"] == [
        "127.0.0.1:4001",
        "[::1]:9108",
    ]
    # Docker's API binding overrides must preserve the metrics listener and
    # validate against the actual API socket, even with a custom config port.
    monkeypatch.setenv("TOKENAGE_API_HOST", "0.0.0.0")
    monkeypatch.setenv("TOKENAGE_API_PORT", "4001")
    assert runpy.run_path(str(ROOT / "src/config/api.conf.py"))["bind"] == [
        "0.0.0.0:4001",
        "[::1]:9108",
    ]
    monkeypatch.setenv("TOKENAGE_API_PORT", "9108")
    for loader in (config_module.load_config, load_server_config):
        with pytest.raises(ValueError, match="differ"):
            loader()


class LocalPort:
    """Set the transport's local socket independently of Host/forwarded headers."""

    def __init__(self, app: ASGIApp, port: int):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        scope = {**scope, "server": ("127.0.0.1", self.port)}
        await self.app(scope, receive, send)


@pytest.fixture
def measured_app(fresh_db, monkeypatch):
    import src.api as api
    import src.metrics as metrics

    # Reuse the API's route schemas instead of rebuilding them for every test
    # and leaving their cycles for a later test's garbage collection.
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    measured = metrics.Metrics()
    monkeypatch.setattr(metrics, "_metrics", measured)
    for middleware in api.app.user_middleware:
        if middleware.cls is metrics.MetricsMiddleware:
            monkeypatch.setitem(middleware.kwargs, "enabled", True)
            monkeypatch.setitem(middleware.kwargs, "port", 9108)
    monkeypatch.setattr(api.app, "middleware_stack", None)
    yield api.app, measured


def test_port_gate_ignores_forwarded_headers(measured_app):
    app, _ = measured_app
    api = TestClient(LocalPort(app, 4001))
    private = TestClient(LocalPort(app, 9108), client=("198.51.100.1", 50000))
    assert (
        api.get(
            "/metrics", headers={"host": "localhost:9108", "x-forwarded-port": "9108"}
        ).status_code
        == 404
    )
    assert api.get("/metrics/").status_code == 404
    assert (
        private.get("/metrics", headers={"host": "evil.example:9108"}).status_code
        == 404
    )
    response = private.get("/metrics", headers={"host": "localhost:4001"})
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "tokenage_build_info{" in response.text
    for route in (
        "/version",
        "/otlp/v1/logs",
        "/ingest/v1/usage",
        "/",
        "/docs",
        "/metrics/",
    ):
        for method in ("get", "post", "options"):
            assert getattr(private, method)(route).status_code == 404
    assert private.post("/metrics").status_code == 404
    assert api.get("/version").status_code == 200


def test_metrics_disabled_is_404(fresh_db):
    import src.api as api
    from src.metrics import get_metrics

    assert get_metrics() is None
    for port in (4001, 9108):
        assert TestClient(LocalPort(api.app, port)).get("/metrics").status_code == 404


def test_ingest_counters_and_committed_rows(measured_app, fresh_db, monkeypatch):
    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from src.shared_state import get_shared_state
    from tests.test_usage_ingest import _report

    app, metrics = measured_app
    token, _ = mint_token(
        "metrics@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(LocalPort(app, 4001))
    headers = {"x-tokenage-token": token}
    assert client.post("/otlp/v1/logs", json={}).status_code == 401
    assert client.post("/otlp/v1/logs", json={}, headers=headers).status_code == 200
    assert (
        client.post(
            "/ingest/v1/usage",
            json=_report(client_source="private-arbitrary-value"),
            headers=headers,
        ).status_code
        == 204
    )
    registry = metrics.registry
    assert (
        registry.get_sample_value(
            "tokenage_ingest_requests_total", {"route": "otlp_logs", "code": "401"}
        )
        == 1
    )
    assert (
        registry.get_sample_value(
            "tokenage_ingest_requests_total", {"route": "otlp_logs", "code": "200"}
        )
        == 1
    )
    assert (
        registry.get_sample_value(
            "tokenage_ingest_requests_total", {"route": "proxy_usage", "code": "204"}
        )
        == 1
    )
    assert (
        registry.get_sample_value("tokenage_ingest_errors_total", {"kind": "auth"}) == 1
    )
    assert (
        registry.get_sample_value(
            "tokenage_ingest_duration_seconds_count", {"route": "otlp_logs"}
        )
        == 2
    )
    assert (
        registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "other"}
        )
        is None
    )
    consumer = Consumer(get_shared_state())
    assert consumer.drain() == 1
    assert (
        registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "other"}
        )
        == 1
    )
    assert registry.get_sample_value("tokenage_consumer_rows_total") == 1
    assert registry.get_sample_value("tokenage_consumer_batch_seconds_count") == 1
    assert "private-arbitrary-value" not in metrics.render().decode()


def test_usage_counter_does_not_count_rolled_back_insert(
    measured_app, fresh_db, monkeypatch
):
    from sqlalchemy.orm import Session

    from src.auth.tokens import mint_token
    from src.ingest_queue import Consumer
    from tests.test_usage_ingest import _report

    app, metrics = measured_app
    token, _ = mint_token(
        "rollback@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(LocalPort(app, 4001))
    client.post("/ingest/v1/usage", json=_report(), headers={"x-tokenage-token": token})

    def fail(session):
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail)
        assert Consumer().drain() == 0
    assert (
        metrics.registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "claude-code"}
        )
        is None
    )
    assert Consumer().drain() == 1
    assert (
        metrics.registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "claude-code"}
        )
        == 1
    )


@pytest.mark.parametrize(
    "code,kind",
    [(413, "too_large"), (422, "bad_request"), (503, "store_unavailable"), (503, "db")],
)
def test_ingest_error_kinds(measured_app, fresh_db, monkeypatch, code, kind):
    from sqlalchemy.exc import OperationalError

    import src.api
    from src.auth.tokens import mint_token
    from src.shared_state import StoreUnavailable
    from tests.test_usage_ingest import _report

    app, metrics = measured_app
    token, _ = mint_token("errors@example.com", kind="ingest", db_path=fresh_db.db_path)
    client = TestClient(LocalPort(app, 4001))
    headers = {"x-tokenage-token": token}
    if code == 413:
        response = client.post(
            "/otlp/v1/logs",
            content=b"{}",
            headers={**headers, "content-length": "99999999"},
        )
    else:
        if code == 503:

            def fail(*args):
                if kind == "store_unavailable":
                    raise StoreUnavailable()
                raise OperationalError("", {}, Exception())

            monkeypatch.setattr(src.api, "publish", fail)
        response = client.post(
            "/ingest/v1/usage", json=_report() if code == 503 else {}, headers=headers
        )
    assert response.status_code == code
    assert (
        metrics.registry.get_sample_value(
            "tokenage_ingest_errors_total", {"kind": kind}
        )
        == 1
    )


def test_queue_depth_timeout_and_error_omit_sample(monkeypatch):
    from prometheus_client import CollectorRegistry, generate_latest

    import src.metrics as metrics
    import src.shared_state

    release = threading.Event()
    calls = []

    def stuck():
        calls.append(1)
        release.wait(2)
        return [SimpleNamespace(depth=99)]

    monkeypatch.setattr(
        src.shared_state, "get_shared_state", lambda: SimpleNamespace(status=stuck)
    )
    monkeypatch.setattr(metrics, "QUEUE_DEPTH_TIMEOUT", 0.02)
    collector = metrics.QueueDepthCollector()
    registry = CollectorRegistry()
    registry.register(collector)
    try:
        start = time.monotonic()
        assert b"tokenage_queue_depth" not in generate_latest(registry)
        assert time.monotonic() - start < 0.5
        assert b"tokenage_queue_depth" not in generate_latest(registry)
        assert len(calls) == 1
    finally:
        release.set()
        collector._reader.join(2)

    def fail():
        raise RuntimeError("private store URL")

    monkeypatch.setattr(src.shared_state, "get_shared_state", fail)
    assert b"tokenage_queue_depth" not in generate_latest(registry)
    monkeypatch.setattr(
        src.shared_state,
        "get_shared_state",
        lambda: SimpleNamespace(
            status=lambda: [SimpleNamespace(depth=2), SimpleNamespace(depth=3)]
        ),
    )
    monkeypatch.setattr(metrics, "QUEUE_DEPTH_TIMEOUT", 1)
    assert b"tokenage_queue_depth 5.0" in generate_latest(registry)


def test_multiprocess_directory_lifecycle(monkeypatch, tmp_path):
    from src.metrics import cleanup_multiprocess, start_multiprocess, stop_multiprocess

    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path))
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    (unowned / "keep").write_text("preserve")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(unowned))
    first = start_multiprocess()
    Path(first, "old.db").write_text("stale")
    second = start_multiprocess()
    assert first != second
    assert [p.name for p in Path(second).iterdir()] == ["master.pid"]
    cleanup_multiprocess()
    assert Path(first).exists()  # Another live master must be preserved.
    Path(first, "master.pid").write_text("invalid")
    cleanup_multiprocess()
    assert not Path(first).exists()
    assert (unowned / "keep").read_text() == "preserve"
    assert Path(second).stat().st_mode & 0o777 == 0o700
    assert os.environ["PROMETHEUS_MULTIPROC_DIR"] == second
    stop_multiprocess(first)
    stop_multiprocess(second)
    assert not Path(first).exists() and not Path(second).exists()


def test_disabled_import_does_not_load_prometheus(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("metrics: {enabled: false}\npricing: {auto_fetch: false}\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.metrics; assert 'src.config.app' not in sys.modules; "
            "import src.api; assert 'prometheus_client' not in sys.modules",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "TOKENAGE_CONFIG": str(config),
            "TOKENAGE_HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
def test_gunicorn_two_workers_aggregate_and_isolate_ports(tmp_path):
    """Use real sockets/workers: scope.server must come from the local listener."""
    import signal
    import socket

    import httpx

    with socket.socket() as api_socket, socket.socket() as metrics_socket:
        api_socket.bind(("127.0.0.1", 0))
        metrics_socket.bind(("127.0.0.1", 0))
        api_port = api_socket.getsockname()[1]
        metrics_port = metrics_socket.getsockname()[1]
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "server": {"host": "127.0.0.1", "api_port": api_port, "workers": 2},
                "metrics": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": metrics_port,
                },
                "db": {"path": str(tmp_path / "usage.db")},
                "pricing": {"auto_fetch": False},
            }
        )
    )
    env = {
        **os.environ,
        "TOKENAGE_HOME": str(tmp_path),
        "TOKENAGE_CONFIG": str(config),
        "TOKENAGE_GIT_COMMIT": "metrics-test",
        # Recent Gunicorn versions create a control socket under this path.
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    for name in (
        "PROMETHEUS_MULTIPROC_DIR",
        "TOKENAGE_QUEUE_URL",
        "TOKENAGE_API_HOST",
        "TOKENAGE_API_PORT",
    ):
        env.pop(name, None)
    # Initialize usage and queue schemas before the workers race to start.
    # This listener test does not exercise the store's cold-start WAL switch.
    initialized = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.database import init_db; from src.shared_state import get_shared_state; init_db(); get_shared_state()",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert initialized.returncode == 0, initialized.stderr
    log = tmp_path / "gunicorn.log"
    api_url = f"http://127.0.0.1:{api_port}"
    metrics_url = f"http://127.0.0.1:{metrics_port}"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "src.api:app",
                "-c",
                "src/config/api.conf.py",
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 20
        directory = None
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                assert process.poll() is None, log.read_text()
                directories = list(tmp_path.glob("metrics-*"))
                if directories:
                    directory = directories[0]
                    if len(list(directory.glob("gauge_livemax_*.db"))) == 2:
                        try:
                            if client.get(api_url + "/version").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                time.sleep(0.05)
            else:
                pytest.fail(log.read_text())
            assert directory is not None
            assert (
                client.get(
                    api_url + "/metrics", headers={"host": f"localhost:{metrics_port}"}
                ).status_code
                == 404
            )
            assert client.get(metrics_url + "/version").status_code == 404
            response = client.get(metrics_url + "/metrics")
            assert response.status_code == 200
            assert 'commit="metrics-test"' in response.text
            assert "pid=" not in response.text
            assert "instance=" not in response.text
            assert "provider=" not in response.text

            # Stop one worker at a time so each must record a known counter.
            # This proves aggregation independently of accept() scheduling.
            pids = [
                int(path.stem.rsplit("_", 1)[1])
                for path in directory.glob("gauge_livemax_*.db")
            ]
            for stopped in pids:
                os.kill(stopped, signal.SIGSTOP)
                try:
                    # Fresh connections avoid keep-alives to the paused worker.
                    with httpx.Client(
                        timeout=2, trust_env=False, headers={"connection": "close"}
                    ) as fresh:
                        assert (
                            fresh.post(api_url + "/otlp/v1/logs", json={}).status_code
                            == 401
                        )
                finally:
                    os.kill(stopped, signal.SIGCONT)
            response = client.get(metrics_url + "/metrics")
            assert (
                'tokenage_ingest_requests_total{code="401",route="otlp_logs"} 2.0'
                in response.text
            )
            assert 'tokenage_ingest_errors_total{kind="auth"} 2.0' in response.text
            assert (
                'tokenage_ingest_duration_seconds_count{route="otlp_logs"} 2.0'
                in response.text
            )
            assert response.text.count("tokenage_build_info{") == 1
            # Worker exit must remove its live gauge while retaining counters.
            victim = pids[0]
            os.kill(victim, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while (directory / f"gauge_livemax_{victim}.db").exists():
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.05)
            assert (
                'tokenage_ingest_requests_total{code="401",route="otlp_logs"} 2.0'
                in client.get(metrics_url + "/metrics").text
            )
            # A normal reload replaces every worker, keeps both sockets alive,
            # and retains the old workers' counters in the same master directory.
            before_reload = set(directory.glob("gauge_livemax_*.db"))
            os.kill(process.pid, signal.SIGHUP)
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, log.read_text()
                gauges = set(directory.glob("gauge_livemax_*.db"))
                if len(gauges) == 2 and not gauges.intersection(before_reload):
                    break
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.05)
            assert client.get(api_url + "/version").status_code == 200
            response = client.get(metrics_url + "/metrics")
            assert response.status_code == 200
            assert (
                'tokenage_ingest_requests_total{code="401",route="otlp_logs"} 2.0'
                in response.text
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode == 0, log.read_text()
    assert not list(tmp_path.glob("metrics-*"))


@pytest.mark.parametrize(
    "bind_host,allowed,request_host,code",
    [
        ("127.0.0.1", [], "evil.example:9108", 404),
        ("127.0.0.1", ["evil.example"], "evil.example", 404),
        ("127.0.0.1", [], "localhost:9108", 200),
        ("127.0.0.1", [], "127.0.0.1:9108", 200),
        ("::1", [], "[::1]:9108", 200),
        ("localhost", [], "LOCALHOST:9108", 200),
        ("0.0.0.0", [], "tokenage:9108", 404),
        ("0.0.0.0", ["tokenage"], "tokenage:9108", 200),
        ("100.64.0.1", ["node.tailnet.ts.net"], "100.64.0.1:9108", 200),
        ("100.64.0.1", ["node.tailnet.ts.net"], "node.tailnet.ts.net:9108", 200),
        ("fd00::1", [], "[fd00::1]:9108", 200),
        ("fd00::1", [], "evil.example:9108", 404),
        ("0.0.0.0", ["tokenage"], "[broken", 404),
    ],
)
def test_metrics_host_validation(measured_app, bind_host, allowed, request_host, code):
    from src.metrics import MetricsMiddleware

    app, _ = measured_app
    gate = MetricsMiddleware(
        app, enabled=True, port=9108, host=bind_host, allowed_hosts=allowed
    )
    client = TestClient(LocalPort(gate, 9108))
    assert (
        client.get(
            "/metrics",
            headers={"host": request_host, "x-forwarded-host": "localhost:9108"},
        ).status_code
        == code
    )


def test_disabled_conflicting_port_is_inert_and_api_override_is_consistent(
    config_module, monkeypatch, tmp_path
):
    from src.config.server_config import load_server_config

    config = tmp_path / "metrics.yaml"
    monkeypatch.setenv("TOKENAGE_CONFIG", str(config))
    config.write_text("metrics: {enabled: false, port: 4001}\n")
    assert config_module.load_config()["metrics"]["port"] == 4001
    assert load_server_config().api_port == 4001
    config.write_text(
        "server: {api_port: 9108}\nmetrics: {enabled: true, port: 9108}\n"
    )
    monkeypatch.setenv("TOKENAGE_API_PORT", "4001")
    assert (
        config_module.load_config()["server"]["api_port"]
        == load_server_config().api_port
        == 4001
    )


@pytest.mark.parametrize("source", ["claude", "gemini", "proxy", "cursor"])
def test_usage_client_source_labels_follow_producers(measured_app, source):
    from collections import Counter

    from src.database.usage import _CLIENT_SOURCE_FAMILIES
    from src.metrics import committed_usage, stage_usage

    _, metrics = measured_app
    assert metrics.client_sources == frozenset(_CLIENT_SOURCE_FAMILIES) | {"proxy"}
    counts = Counter()
    stage_usage(SimpleNamespace(info={"tokenage_usage_counts": counts}), source)
    committed_usage(counts)
    label = "other" if source == "cursor" else source
    assert (
        metrics.registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": label}
        )
        == 1
    )


def test_per_event_rollback_preserves_only_successful_usage(
    measured_app, fresh_db, monkeypatch
):
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from src.auth.tokens import mint_token
    from src.database import Usage, get_engine
    from src.ingest_queue import Consumer
    from tests.test_usage_ingest import _report

    app, metrics = measured_app
    token, _ = mint_token(
        "event-rollback@example.com", kind="ingest", db_path=fresh_db.db_path
    )
    client = TestClient(LocalPort(app, 4001))
    for source in ("claude", "gemini"):
        assert (
            client.post(
                "/ingest/v1/usage",
                json=_report(client_source=source),
                headers={"x-tokenage-token": token},
            ).status_code
            == 204
        )
    original = Consumer._process

    def process(self, event, session):
        original(self, event, session)
        if event.payload["fields"]["client_source"] == "gemini":
            raise ValueError("failure after staging the insert")

    monkeypatch.setattr(Consumer, "_process", process)
    monkeypatch.setattr("src.ingest_queue.SQLITE_PASS_SECONDS", 5)
    assert Consumer().drain() == 2
    with Session(get_engine()) as session:
        assert session.scalar(select(func.count()).select_from(Usage)) == 1
    assert (
        metrics.registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "claude"}
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "tokenage_usage_rows_total", {"client_source": "gemini"}
        )
        is None
    )
    assert (
        metrics.registry.get_sample_value(
            "tokenage_ingest_errors_total", {"kind": "processing"}
        )
        == 1
    )


@pytest.mark.parametrize("route", ["/ingest/v1/usage", "/otlp/v1/logs"])
def test_queue_full_has_distinct_error_kind(measured_app, fresh_db, monkeypatch, route):
    from src.auth.tokens import mint_token
    from src.shared_state import QueueFull
    from tests.test_usage_ingest import _report

    app, metrics = measured_app
    token, _ = mint_token(
        "queue-full@example.com", kind="ingest", db_path=fresh_db.db_path
    )

    def fail(*args, **kwargs):
        raise QueueFull()

    monkeypatch.setattr(
        "src.ingest_queue.get_shared_state", lambda: SimpleNamespace(publish=fail)
    )
    payload = (
        _report()
        if route == "/ingest/v1/usage"
        else {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {
                            "scope": {"name": "claude-code"},
                            "logRecords": [{"body": {"stringValue": "test"}}],
                        }
                    ]
                }
            ]
        }
    )
    response = TestClient(LocalPort(app, 4001)).post(
        route, json=payload, headers={"x-tokenage-token": token}
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert (
        metrics.registry.get_sample_value(
            "tokenage_ingest_errors_total", {"kind": "queue_full"}
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "tokenage_ingest_errors_total", {"kind": "store_unavailable"}
        )
        is None
    )


@pytest.mark.slow
def test_failed_bind_does_not_create_metrics_directory(tmp_path):
    import socket

    config = tmp_path / "config.yaml"
    with socket.socket() as occupied, socket.socket() as api_socket:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        api_socket.bind(("127.0.0.1", 0))
        api_port = api_socket.getsockname()[1]
        api_socket.close()
        config.write_text(
            yaml.safe_dump(
                {
                    "server": {"host": "127.0.0.1", "api_port": api_port},
                    "metrics": {"enabled": True, "port": occupied.getsockname()[1]},
                    "pricing": {"auto_fetch": False},
                }
            )
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("TOKENAGE_")}
        env.update(
            TOKENAGE_CONFIG=str(config),
            TOKENAGE_HOME=str(tmp_path),
            XDG_RUNTIME_DIR=str(tmp_path),
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "src.api:app",
                "-c",
                "src/config/api.conf.py",
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
            ],
            env=env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode != 0, result.stderr
    assert "Connection in use" in result.stderr, result.stderr
    assert not list(tmp_path.glob("metrics-*"))
