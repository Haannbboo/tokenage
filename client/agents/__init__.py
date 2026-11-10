"""Per-agent configuration: each module owns one agent's settings file format.

Every module exposes ``configure(target, logs_endpoint, token) -> int`` and
``disable(target, expected_endpoint) -> int`` returning ``DONE`` (configured or
removed), ``SKIPPED`` (nothing to do or not ours) or ``FAILED``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from client.prompts import is_terminal

DONE, FAILED, SKIPPED = 0, 1, 2

_GRAY = "\033[38;2;102;102;102m" if is_terminal(sys.stdout) else ""
_RESET = "\033[0m" if is_terminal(sys.stdout) else ""


def info(msg: str) -> None:
    print(f"  {_GRAY}{msg}{_RESET}")


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
