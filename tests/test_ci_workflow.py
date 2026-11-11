import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load_workflow(name="ci") -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "output"),
    [
        ("backend", "backend"),
        ("postgres", "backend"),
        ("hosted-client", "backend"),
        ("frontend", "frontend"),
    ],
)
def test_ci_jobs_depend_on_changes(name, output):
    job = load_workflow()["jobs"][name]

    assert job["needs"] == "changes"
    assert job["if"] == f"needs.changes.outputs.{output} == 'true'"


def test_ci_prs_always_run_but_docs_only_pushes_skip():
    triggers = load_workflow()["on"]

    assert "pull_request" in triggers
    assert "paths-ignore" not in (triggers["pull_request"] or {})
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["paths-ignore"] == ["**.md", "docs/**"]


@pytest.mark.parametrize("name", ["reviewdog", "bump-version"])
def test_optional_workflows_keep_docs_filter(name):
    assert load_workflow(name)["on"]["pull_request"]["paths-ignore"] == [
        "**.md",
        "docs/**",
    ]


def test_changes_outputs_and_checkout():
    job = load_workflow()["jobs"]["changes"]

    assert job["outputs"] == {
        "backend": "${{ steps.diff.outputs.backend }}",
        "frontend": "${{ steps.diff.outputs.frontend }}",
    }
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    step = next(step for step in job["steps"] if step.get("id") == "diff")
    assert step["env"] == {
        "EVENT": "${{ github.event_name }}",
        "BASE_REF": "${{ github.base_ref }}",
    }
    assert "github.base_ref" not in step["run"]


@pytest.mark.parametrize(
    ("paths", "event", "base_ref", "rename", "backend", "frontend"),
    [
        (["frontend/app.ts"], "pull_request", "main", False, False, True),
        (["src/api.py"], "pull_request", "main", False, True, False),
        (
            ["frontend/app.ts", "protocol/schema.py"],
            "pull_request",
            "main",
            False,
            True,
            True,
        ),
        (
            ["frontend/app.ts", "README.md", "docs/guide.txt"],
            "pull_request",
            "main",
            False,
            False,
            True,
        ),
        (
            ["README.md", "docs/guide.txt", "src/notes.md"],
            "pull_request",
            "main",
            False,
            False,
            False,
        ),
        (["frontend/README.md"], "pull_request", "main", False, False, True),
        (["client/install.sh"], "pull_request", "main", False, True, False),
        (["plugins/opencode/index.ts"], "pull_request", "main", False, True, False),
        ([".github/workflows/ci.yml"], "pull_request", "main", False, True, True),
        (["README.md"], "push", "", False, True, True),
        (["README.md"], "pull_request", "missing", False, True, True),
        (["docs/x.md"], "pull_request", "main", True, True, False),
        (
            [f"src/long_changed_filename_{i:04d}.py" for i in range(5000)],
            "pull_request",
            "main",
            False,
            True,
            False,
        ),
    ],
    ids=[
        "frontend",
        "backend",
        "both",
        "frontend-and-docs",
        "docs",
        "frontend-markdown",
        "client-installer",
        "plugins",
        "ci-workflow",
        "push",
        "undiffable",
        "rename-to-docs",
        "large-diff",
    ],
)
def test_changes_script(
    tmp_path, monkeypatch, paths, event, base_ref, rename, backend, frontend
):
    repo = tmp_path / "repo"
    repo.mkdir()
    outer_index = tmp_path / "outer-index"
    outer_index.write_bytes(b"outer Git index")
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer_index))
    env = {
        # Hooks export Git paths; never let fixture commands use the outer repo.
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
        "GIT_AUTHOR_NAME": "Workflow test",
        "GIT_AUTHOR_EMAIL": "workflow@example.com",
        "GIT_COMMITTER_NAME": "Workflow test",
        "GIT_COMMITTER_EMAIL": "workflow@example.com",
        "EVENT": event,
        "BASE_REF": base_ref,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
        )

    git("init", "--initial-branch=main")
    source = repo / "src" / "x.py"
    source.parent.mkdir()
    source.write_text("original source\n")
    git("add", ".")
    git("commit", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    if rename:
        target = repo / paths[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    else:
        for name in paths:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("changed\n")
    git("add", "-A")
    git("commit", "-m", "changes")

    script = next(
        step["run"]
        for step in load_workflow()["jobs"]["changes"]["steps"]
        if step.get("id") == "diff"
    )
    subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )

    assert (tmp_path / "output").read_text() == (
        f"backend={str(backend).lower()}\nfrontend={str(frontend).lower()}\n"
    )
    assert outer_index.read_bytes() == b"outer Git index"
