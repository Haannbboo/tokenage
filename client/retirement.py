"""Best-effort removal of retired client state, independently of tracking."""

from __future__ import annotations

import errno
import logging
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path

import yaml

from client.paths import tracker_home

logger = logging.getLogger(__name__)
RETRY_SECONDS = 10.0


MAX_ATTEMPTS = 3


class TerminalCleanupError(Exception):
    """Invalid config cannot be repaired by retrying."""


def _clean_config(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise TerminalCleanupError("client.yaml is not a regular file")
    # O_NOFOLLOW also closes the lstat/open symlink race. Native transcripts
    # are never opened, even if somebody links client.yaml to one.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TerminalCleanupError("client.yaml is a symlink") from exc
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise TerminalCleanupError("client.yaml is not a regular file")
        mode = opened.st_mode
        try:
            text = stream.read()
            data = yaml.safe_load(text)
            root = yaml.compose(text)
        except (yaml.YAMLError, UnicodeError) as exc:
            raise TerminalCleanupError("invalid client.yaml") from exc
    if not isinstance(data, dict) or not isinstance(root, yaml.MappingNode):
        raise TerminalCleanupError("client.yaml is not a mapping")
    if "evaluation" not in data:
        return
    expected = dict(data)
    del expected["evaluation"]
    entries = [(key, value) for key, value in root.value if key.value == "evaluation"]
    if len(entries) != 1:
        raise TerminalCleanupError("ambiguous evaluation key")
    key, value = entries[0]
    lines = text.splitlines(keepends=True)
    start = key.start_mark.line
    end = value.end_mark.line + bool(value.end_mark.column)
    edited = "".join(lines[:start] + lines[end:])
    try:
        if yaml.safe_load(edited) != expected:
            raise TerminalCleanupError("unsafe evaluation edit")
    except yaml.YAMLError as exc:
        raise TerminalCleanupError("unsafe evaluation edit") from exc
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), stat.S_IMODE(mode))
            stream.write(edited)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("client.yaml changed during cleanup")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cleanup_once() -> bool | None:
    """Retryable, signed-out-safe cleanup. Failure must never stop tracking."""
    try:
        path = tracker_home() / "evaluations"
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISDIR(mode):
                shutil.rmtree(path)
            else:
                path.unlink()  # Includes symlinks: never traverse their target.
        _clean_config(tracker_home() / "client.yaml")
        return True
    except TerminalCleanupError as exc:
        logger.warning("retired client state cleanup stopped: %s", exc)
        return None
    except OSError:
        logger.warning("retired client state cleanup deferred")
        return False


def start_cleanup() -> threading.Event:
    """Start after the manager has stopped the predecessor; never block startup."""
    # ponytail: A hand-started old service cannot be restarted by the installer
    # and may recreate evaluations; the next new-client start cleans it up.
    stop = threading.Event()

    def run() -> None:
        for attempt in range(MAX_ATTEMPTS):
            if stop.is_set() or cleanup_once() is not False:
                return
            if attempt + 1 < MAX_ATTEMPTS:
                stop.wait(RETRY_SECONDS)

    threading.Thread(target=run, name="tokenage-state-cleanup", daemon=True).start()
    return stop
