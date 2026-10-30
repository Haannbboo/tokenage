import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src.config.runtime_ports import format_bind
from src.config.server_config import load_server_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Optional env overrides (Google OAuth credentials and anything else a
# deployment injects) without touching ~/.tokenage/config.yaml.
load_dotenv(os.path.join(ROOT, ".env"))

_server = load_server_config()


bind = [format_bind(_server.host, _server.api_port)]
if _server.metrics.enabled:
    assert _server.metrics.port is not None
    bind.append(format_bind(_server.metrics.host, _server.metrics.port))
workers = _server.workers
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = os.path.join(ROOT, "logs/api.access.log")
errorlog = os.path.join(ROOT, "logs/api.error.log")
capture_output = True
graceful_timeout = 30
timeout = 300


def on_starting(server):
    from src.metrics import cleanup_multiprocess

    cleanup_multiprocess()
    if _server.metrics.enabled:
        if server.cfg.preload_app:
            raise RuntimeError("metrics requires Gunicorn preload_app=false")


def when_ready(server):
    from src.config.models import get_tracker_home

    run = Path(get_tracker_home()) / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "api-binds.json").write_text(json.dumps({"pid": os.getpid(), "bind": bind}))
    if _server.metrics.enabled:
        from src.metrics import start_multiprocess

        server.tokenage_metrics_dir = start_multiprocess()


def child_exit(server, worker):
    directory = getattr(server, "tokenage_metrics_dir", None)
    if directory:
        from prometheus_client import multiprocess

        # Ponytail: counters/histograms must survive reloads. Files grow by up
        # to two per retired worker (counter and histogram; live gauge removed
        # here): at most 2 * server.workers files per reload. A full master
        # restart clears them. There is no per-master
        # reload ceiling, so long-lived deployments should periodically stop/start.
        multiprocess.mark_process_dead(worker.pid, path=directory)


def on_exit(server):
    directory = getattr(server, "tokenage_metrics_dir", None)
    if directory:
        from src.metrics import stop_multiprocess

        stop_multiprocess(directory)


def post_fork(server, worker):
    # UvicornWorker.__init__ copies gunicorn's access log handlers onto uvicorn.access,
    # using gunicorn's plain %(message)s formatter. Replace it with one that adds timestamps.
    import logging

    from uvicorn.logging import AccessFormatter

    formatter = AccessFormatter(
        fmt='%(asctime)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        datefmt="%Y-%m-%d %H:%M:%S",
        use_colors=False,
    )
    for handler in logging.getLogger("uvicorn.access").handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_drop_query)


def _drop_query(record):
    # Query strings can carry one-time sign-in codes; log the path only.
    if not isinstance(record.args, tuple) or len(record.args) < 3:
        return True
    client, method, path, *rest = record.args
    record.args = (client, method, path.split("?", 1)[0], *rest)
    return True
