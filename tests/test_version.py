import os
import subprocess

import pytest

from src import _version


@pytest.fixture(autouse=True)
def clear_commit_cache(monkeypatch):
    # Git hooks export paths into the real checkout; isolate fixture repos.
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.delenv("TOKENAGE_GIT_COMMIT", raising=False)
    _version._git_commit.cache_clear()
    yield
    _version._git_commit.cache_clear()


def test_commit_reads_clone_head_once(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "test",
    )
    expected = git("rev-parse", "HEAD")
    monkeypatch.setattr(_version, "_VERSION_FILE", tmp_path / "VERSION")
    monkeypatch.setenv("TOKENAGE_GIT_COMMIT", "")
    assert _version.get_commit() == expected
    # The cached result survives loss of Git access after the first read.
    monkeypatch.setenv("PATH", "")
    assert _version.get_commit() == expected


def test_commit_prefers_image_stamp(monkeypatch):
    monkeypatch.setenv("TOKENAGE_GIT_COMMIT", "image-commit")
    monkeypatch.setenv("PATH", "")
    assert _version.get_commit() == "image-commit"
    assert _version._git_commit.cache_info().misses == 0


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("git", 1),
        subprocess.CalledProcessError(128, "git"),
        UnicodeError(),
    ],
)
def test_commit_failure_is_cached_unknown(monkeypatch, failure):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise failure

    monkeypatch.setattr(_version.subprocess, "run", fail)
    assert _version.get_commit() == "unknown"
    assert _version.get_commit() == "unknown"
    assert len(calls) == 1
    assert calls[0]["timeout"] == 1


def test_commit_does_not_report_parent_repository(tmp_path, monkeypatch):
    # Even when the checkout happens to live inside a Git repository, this
    # installation has no .git of its own and must not inherit another HEAD.
    nested = tmp_path / "installation"
    nested.mkdir()
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(_version, "_VERSION_FILE", nested / "VERSION")
    assert _version.get_commit() == "unknown"
    assert _version._git_commit.cache_info().misses == 1
