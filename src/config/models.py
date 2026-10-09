"""Shared data classes and utilities for tokenage home paths."""

from __future__ import annotations

import os

DEFAULT_TRACKER_HOME = "~/.tokenage"
TRACKER_HOME_ENV_VAR = "TOKENAGE_HOME"


def expand_path(path: str) -> str:
    return os.path.expanduser(path)


def get_tracker_home() -> str:
    return expand_path(os.environ.get(TRACKER_HOME_ENV_VAR, DEFAULT_TRACKER_HOME))
