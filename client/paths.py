"""Where things live on disk, for both installation modes.

The client never imports the server and never reads its state. It knows the
server only by the URL it signed in to. ``$TOKENAGE_HOME/current`` is the client
source snapshot, if one is installed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Root of the source tree this client was loaded from. The agent-configuration
# scripts live in <root>/scripts, so the client needs the snapshot on disk, not
# just the client package.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
COMMIT_FILE = Path(__file__).resolve().parent / "COMMIT"

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def tracker_home() -> Path:
    return Path(os.environ.get("TOKENAGE_HOME", "~/.tokenage")).expanduser()


def credentials_path() -> Path:
    return tracker_home() / "credentials.json"


def installation_key_path() -> Path:
    """Machine-scoped installation secret; it survives logout and re-login."""
    return tracker_home() / "installation_key"


def read_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, or None when the file is absent. Unreadable is an error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"cannot read JSON object in {path}; repair or remove the file"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return data


def display_endpoint(raw: str | None) -> str | None:
    """Display collector locations without URL credentials or private paths."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "[redacted endpoint]"
    try:
        parsed = urlparse(raw)
        host, port = parsed.hostname, parsed.port
        if not host or parsed.scheme not in {"http", "https"}:
            return "[redacted endpoint]"
    except ValueError:
        return "[redacted endpoint]"
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    path = (
        parsed.path
        # The server's collector path, and the old standalone collector's (so a
        # stale agent wiring still reads as such).
        if parsed.path in {"/otlp/v1/logs", "/v1/logs"}
        else "/[redacted path]"
        if parsed.path not in {"", "/"}
        else ""
    )
    return f"{parsed.scheme}://{authority}{path}"


def client_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def client_commit() -> str | None:
    raw = os.environ.get("TOKENAGE_CLIENT_COMMIT")
    if raw is None and COMMIT_FILE.exists():
        raw = COMMIT_FILE.read_text(encoding="utf-8").strip()
    value = (raw or "").lower()
    return value if _COMMIT_RE.fullmatch(value) else None


def client_root() -> Path | None:
    """The source snapshot the ``current`` symlink points at, if installed."""
    link = tracker_home() / "current"
    try:
        resolved = link.resolve(strict=True)
    except OSError:
        return None
    return resolved if (resolved / "client").is_dir() else None


def client_config_path() -> Path:
    """The client's proxy/evaluation config; never the server's."""
    return tracker_home() / "client.yaml"
