import os

from dotenv import load_dotenv

from src.config.server_config import load_server_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Optional env overrides (Google OAuth credentials and anything else a
# deployment injects) without touching ~/.tokenage/config.yaml.
load_dotenv(os.path.join(ROOT, ".env"))

_server = load_server_config()
bind = f"{_server.host}:{_server.api_port}"
workers = _server.workers
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = os.path.join(ROOT, "logs/api.access.log")
errorlog = os.path.join(ROOT, "logs/api.error.log")
capture_output = True
graceful_timeout = 30
timeout = 300


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
