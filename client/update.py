"""``tokenage update`` — update the client.

The client is a source snapshot under ``$TOKENAGE_HOME/versions``, so it updates
by asking its server for the installer and letting that install a new snapshot.
Updating the server clone is ``tokenage server update``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

from client.paths import client_commit, client_root, client_version

INSTALLER_PATH = "/install.sh"
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _installer_source(installer: Path) -> tuple[str, str]:
    """Read the GitHub repository and branch the installer installs from."""
    script = installer.read_text(encoding="utf-8")
    repository = re.search(
        r"^REPOSITORY=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", script, re.M
    )
    branch = re.search(r"^BRANCH=\$\{TOKENAGE_BRANCH:-([^}\s]+)\}$", script, re.M)
    if repository is None or branch is None:
        raise ValueError("invalid installer source")
    return repository[1], os.environ.get("TOKENAGE_BRANCH") or branch[1]


def _resolve_target(
    repository: str, branch: str, pin: str | None = None
) -> tuple[str, str]:
    """Find the commit and client version the installer would install."""
    sha = pin
    if sha is None:
        with urlopen(
            f"https://api.github.com/repos/{repository}/commits/{quote(branch, safe='')}",
            timeout=5,
        ) as response:
            sha = json.load(response)["sha"]
    if not isinstance(sha, str) or not _COMMIT_RE.fullmatch(sha):
        raise ValueError("invalid source commit")
    with urlopen(
        f"https://raw.githubusercontent.com/{repository}/{sha}/client/VERSION",
        timeout=5,
    ) as response:
        version = response.read(128).decode("utf-8").strip()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("invalid client version")
    return version, sha


def _failure_reason(exc: Exception) -> str:
    """Turn a resolve error into a short reason (offline, TLS, rate limit, 404)."""
    if isinstance(exc, HTTPError):
        if exc.code in {403, 429}:
            return f"GitHub rate limit (HTTP {exc.code})"
        if exc.code == 404:
            return "not found (HTTP 404)"
        return f"HTTP {exc.code}"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return "TLS certificate failure"
    if isinstance(exc, (OSError, URLError)):
        return "offline/DNS or network failure"
    return "invalid response"


def _bin_dir() -> Path:
    return Path(os.environ.get("TOKENAGE_BIN_DIR", "~/.local/bin")).expanduser()


def _run(command: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(command, env=env).returncode


def update_client(
    target_commit: str | None = None, *, check: bool = False, dry_run: bool = False
) -> int:
    """Install the newest client snapshot without starting a new login."""
    from client.auth import load_credentials, normalize_server_url

    raw = (load_credentials() or {}).get("server_url")
    try:
        server_url = normalize_server_url(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        server_url = None
    if not server_url:
        print(
            "tokenage: cannot update the client without a server; run "
            "tokenage login --server <url> first.",
            file=sys.stderr,
        )
        return 1
    with tempfile.TemporaryDirectory(prefix="tokenage-update-") as work:
        installer = Path(work) / "install.sh"
        curl = shutil.which("curl")
        if curl is None:
            print("tokenage: curl is required to update the client.", file=sys.stderr)
            return 1
        if _run([curl, "-fsSL", f"{server_url}{INSTALLER_PATH}", "-o", str(installer)]):
            print(
                f"tokenage: could not download the installer from {server_url}.",
                file=sys.stderr,
            )
            return 1
        return _update_from_installer(
            installer, str(server_url), target_commit, check, dry_run
        )


def _update_from_installer(
    installer: Path,
    server_url: str,
    target_commit: str | None,
    check: bool,
    dry_run: bool,
) -> int:
    """Show installed vs latest, then install exactly that commit unless already current."""
    user_pin = os.environ.get("TOKENAGE_INSTALL_COMMIT") or None
    if user_pin is not None:
        if not _COMMIT_RE.fullmatch(user_pin):
            print(
                "tokenage: invalid TOKENAGE_INSTALL_COMMIT; expected 40 lowercase hex characters.",
                file=sys.stderr,
            )
            return 1
        target_commit = user_pin
    branch = os.environ.get("TOKENAGE_BRANCH") or "the installer branch"
    target_version = "unknown"
    try:
        repository, branch = _installer_source(installer)
        target_version, target_commit = _resolve_target(
            repository, branch, target_commit
        )
    except (OSError, HTTPException, ValueError, KeyError, TypeError) as exc:
        print(
            f"  tokenage: could not resolve the latest version: {_failure_reason(exc)}.",
            file=sys.stderr,
        )
        if check:
            return 1
    else:
        print(f"  Latest client     {target_version}  (commit {target_commit[:7]})")
        installed_version = client_version()
        if _VERSION_RE.fullmatch(installed_version) and tuple(
            map(int, installed_version.split("."))
        ) > tuple(map(int, target_version.split("."))):
            print(f"  Installed client is newer than {branch}; not downgrading")
            return 0
        if client_commit() == target_commit:
            print("  Client already up to date.")
            return 0
        if check:
            print("  Client update available.")
            return 0
    if dry_run:
        sha = target_commit[:7] if target_commit else "unknown"
        print(
            f"  Would install client {target_version} (commit {sha}) using the installer from {server_url}"
        )
        return 0
    print("  ▶ Updating client")
    installer.chmod(0o700)
    env = os.environ.copy()
    env["TOKENAGE_SERVER"] = str(server_url)
    env["TOKENAGE_BIN_DIR"] = str(_bin_dir())
    env["TOKENAGE_SKIP_LOGIN"] = "1"
    if target_commit is not None and user_pin is None:
        env["TOKENAGE_INSTALL_COMMIT"] = target_commit
    if _run(["sh", str(installer)], env=env):
        print("tokenage: client update failed.", file=sys.stderr)
        return 1
    if target_commit is not None:
        root = client_root()
        try:
            actual = (
                (root / "client" / "COMMIT").read_text(encoding="utf-8").strip().lower()
                if root
                else "unknown"
            )
        except OSError:
            actual = "unknown"
        if actual != target_commit:
            print(
                f"  Warning: requested {target_commit[:7]}, installed {actual[:7]}; the server's installer ignored the pin; update the server.",
                file=sys.stderr,
            )
    print("  ✓ client updated")
    print("  ✓ credentials preserved")
    return 0


def run_update(*, check: bool, dry_run: bool) -> int:
    if client_root() is None:
        print(
            "tokenage: no client snapshot to update on this machine "
            "(the server updates with tokenage server update)."
        )
        return 0

    installed_commit = client_commit()
    print(
        f"  Installed client  {client_version()}  "
        f"(commit {installed_commit[:7] if installed_commit else 'unknown'})"
    )
    return update_client(check=check, dry_run=dry_run)
