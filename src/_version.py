import os
import subprocess
from functools import lru_cache
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
_version_string: str | None = None


def get_version() -> str:
    global _version_string
    if _version_string is None:
        _version_string = _VERSION_FILE.read_text().strip()
    return _version_string


@lru_cache(maxsize=1)
def _git_commit() -> str:
    """Read the installed clone once; images need neither Git nor a checkout."""
    try:
        if not (_VERSION_FILE.parent / ".git").exists():
            return "unknown"
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=_VERSION_FILE.parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=1,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return "unknown"


def get_commit() -> str:
    return os.environ.get("TOKENAGE_GIT_COMMIT") or _git_commit()
