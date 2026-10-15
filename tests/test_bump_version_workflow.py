import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "bump-version.yml"


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_bump_version_workflow_runs_for_prs_targeting_main():
    workflow = load_workflow()

    triggers = workflow["on"]

    assert "push" not in triggers
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]


def test_bump_version_workflow_skips_its_own_bump_commit():
    workflow = load_workflow()

    bump_job = workflow["jobs"]["bump"]

    assert bump_job["if"] == (
        "github.actor != 'github-actions[bot]' && "
        "github.event.pull_request.head.repo.full_name == github.repository"
    )


def test_bump_version_workflow_bumps_only_changed_packages_from_base_version():
    workflow = load_workflow()

    checkout_step = workflow["jobs"]["bump"]["steps"][0]
    bump_step = workflow["jobs"]["bump"]["steps"][1]

    assert checkout_step["with"]["ref"] == "${{ github.head_ref }}"
    assert checkout_step["with"]["fetch-depth"] == 0
    # The base ref goes through the environment: an inline ${{ }} would let a
    # branch name containing shell syntax run inside this script.
    assert bump_step["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert "github.base_ref" not in bump_step["run"]
    assert 'git fetch origin "$BASE_REF"' in bump_step["run"]
    assert 'git diff --name-only "origin/$BASE_REF...HEAD"' in bump_step["run"]
    assert "client/*|plugins/*)" in bump_step["run"]
    assert "server/*|src/*|frontend/*" in bump_step["run"]
    # The one installer ships in both components.
    assert "install.sh) BUMP_SERVER=true BUMP_CLIENT=true" in " ".join(
        bump_step["run"].split()
    )
    assert "protocol/*)" in bump_step["run"]
    assert 'git show "origin/$BASE_REF:$FILE"' in bump_step["run"]
    assert "bump_file VERSION" in bump_step["run"]
    assert "bump_file client/VERSION" in bump_step["run"]
    assert "BASE_PATCH + 1" in bump_step["run"]


def test_bump_version_workflow_never_lowers_an_existing_patch_version():
    workflow = load_workflow()

    bump_step = workflow["jobs"]["bump"]["steps"][1]

    assert "CURRENT_PATCH > BASE_PATCH + 1" in bump_step["run"]
    assert "CURRENT_PATCH" in bump_step["run"]


def test_bump_version_workflow_fails_when_path_detection_fails():
    """A failed git diff must fail the job, not silently bump nothing."""
    workflow = load_workflow()

    bump_step = workflow["jobs"]["bump"]["steps"][1]

    # The diff is captured and status-checked; the loop no longer reads from a
    # process substitution, which discards git's exit status.
    assert (
        'if ! CHANGED_FILES=$(git diff --name-only "origin/$BASE_REF...HEAD"); then'
        in bump_step["run"]
    )
    assert '<<< "$CHANGED_FILES"' in bump_step["run"]
    assert "< <(git diff" not in bump_step["run"]


def test_bump_version_workflow_handles_main_advancing_after_pr_branch(
    tmp_path, monkeypatch
):
    origin = tmp_path / "origin"
    checkout = tmp_path / "checkout"
    origin.mkdir()
    hook_index = tmp_path / "hook-index"
    hook_index.write_bytes(b"outer Git index")
    monkeypatch.setenv("GIT_INDEX_FILE", str(hook_index))
    env = {
        # Commit hooks export Git paths; fixture repos must not inherit them.
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
        "GIT_AUTHOR_NAME": "Workflow test",
        "GIT_AUTHOR_EMAIL": "workflow@example.com",
        "GIT_COMMITTER_NAME": "Workflow test",
        "GIT_COMMITTER_EMAIL": "workflow@example.com",
        "BASE_REF": "main",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=origin, env=env, check=True, capture_output=True
        )

    git("init", "--initial-branch=main")
    (origin / "VERSION").write_text("0.1.0\n")
    (origin / "client").mkdir()
    (origin / "client" / "VERSION").write_text("0.1.0\n")
    git("add", ".")
    git("commit", "-m", "initial versions")
    git("checkout", "-b", "pr")
    (origin / "client" / "feature.py").write_text("pass\n")
    git("add", ".")
    git("commit", "-m", "client feature")
    git("checkout", "main")
    (origin / "README.md").write_text("Main advanced after the PR branched.\n")
    git("add", ".")
    git("commit", "-m", "advance main")
    git("clone", "--branch=pr", origin.as_uri(), str(checkout))

    script = load_workflow()["jobs"]["bump"]["steps"][1]["run"]
    subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script], cwd=checkout, env=env, check=True
    )

    assert (checkout / "client" / "VERSION").read_text() == "0.1.1\n"
    assert (checkout / "VERSION").read_text() == "0.1.0\n"
    assert hook_index.read_bytes() == b"outer Git index"


def test_bump_version_workflow_includes_version_in_bump_commit_message():
    workflow = load_workflow()

    assert "pull-requests" not in workflow.get("permissions", {})
    auto_commit_step = next(
        step
        for step in workflow["jobs"]["bump"]["steps"]
        if step.get("uses") == "stefanzweifel/git-auto-commit-action@v5"
    )

    assert (
        auto_commit_step["with"]["commit_message"]
        == "chore: bump version to ${{ steps.bump.outputs.new_version }}"
    )
    assert auto_commit_step["if"] == "steps.bump.outputs.changed == 'true'"
    assert auto_commit_step["with"]["file_pattern"] == "VERSION client/VERSION"
    assert not any(
        step.get("name") == "Append version to PR title"
        for step in workflow["jobs"]["bump"]["steps"]
    )
