"""Release tagging is exercised against disposable local Git repositories."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/release_tags.py"


def test_release_boundary_and_changed_component_tags(tmp_path, monkeypatch):
    # Commit hooks export GIT_INDEX_FILE (and sometimes GIT_DIR). Never let
    # disposable repositories stage files in the caller's index.
    git_env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    monkeypatch.setenv(
        "GIT_INDEX_FILE", str(tmp_path / "parent-index-must-not-be-used")
    )

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "parent-git-dir"))

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, text=True, env=git_env
        ).strip()

    def commit(message):
        git("add", ".")
        git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        )
        return git("rev-parse", "HEAD")

    def run(*args):
        return subprocess.check_output(
            [sys.executable, str(SCRIPT), "--ref", "HEAD", *args],
            cwd=tmp_path,
            text=True,
            env=os.environ.copy(),
        )

    git("init", "-q")
    (tmp_path / "client").mkdir()
    (tmp_path / "VERSION").write_text("0.2.1\n")
    (tmp_path / "client/VERSION").write_text("0.1.1\n")
    commit("old initial versions")
    (tmp_path / "client/VERSION").write_text("0.1.27\n")
    (tmp_path / "VERSION").write_text("0.2.91\n")
    commit("old releases")
    assert run() == ""  # No tags before the workflow exists.
    workflow = tmp_path / ".github/workflows/release-tags.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: release-tags\n")
    (tmp_path / "client/VERSION").write_text("0.1.28\n")
    first = commit("enable release tagging")
    (tmp_path / "client/VERSION").write_text("0.1.29\n")
    second = commit("client release")
    output = run()
    assert f"Would tag {first} client-v0.1.28" in output
    # The server version at the boundary is included even though it did not change.
    assert f"Would tag {first} v0.2.91" in output
    assert "client-v0.1.29" in output
    assert "client-v0.1.1" not in output and "client-v0.1.27" not in output
    assert "v0.2.1" not in output
    assert git("tag") == ""
    # Simulate a missed run: only one release has already been tagged.
    git("tag", "client-v0.1.29", second)
    assert git("rev-parse", "client-v0.1.29") == second
    dry = run()
    assert f"Would tag {first} client-v0.1.28" in dry
    assert f"Would tag {first} v0.2.91" in dry
    assert "client-v0.1.29" not in dry
    assert git("tag") == "client-v0.1.29"
    run("--apply")
    assert set(git("tag").splitlines()) == {
        "client-v0.1.28",
        "client-v0.1.29",
        "v0.2.91",
    }
    assert git("rev-parse", "v0.2.91") == first
    assert run("--apply") == ""
    # A later push must repair a missing tag even without a VERSION change.
    git("tag", "-d", "client-v0.1.28")
    (tmp_path / "unrelated").write_text("next push")
    git("add", "unrelated")
    commit("unrelated push")
    assert f"Tag {first} client-v0.1.28" in run("--apply")
    assert run("--apply") == ""
    # Force-pushed history has no reachable BEFORE; --ref alone still works.
    git("checkout", "--orphan", "rewritten-main")
    (tmp_path / "client/VERSION").write_text("0.1.3\n")
    rewritten = commit("rewritten release history")
    assert f"Tag {rewritten} client-v0.1.3" in run("--apply")
    assert not (tmp_path / "parent-index-must-not-be-used").exists()


def test_workflow_tags_main_push_with_write_permission():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-tags.yml").read_text())
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "paths" not in workflow["on"]["push"]
    assert workflow["permissions"]["contents"] == "write"
    steps = workflow["jobs"]["tag"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert '--ref "$AFTER" --apply --push' in steps[1]["run"]
