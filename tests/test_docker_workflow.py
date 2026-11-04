from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docker.yml"
)
CLEANUP = "dataaxiom/ghcr-cleanup-action"


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_top_level_permissions_are_empty():
    assert load_workflow()["permissions"] == {}


def test_push_prunes_ghcr_inside_the_publish_job():
    publish = load_workflow()["jobs"]["publish"]

    assert publish["if"] == "github.event_name == 'push'"
    assert publish["permissions"]["packages"] == "write"
    steps = publish["steps"]
    names = [s.get("name") or s.get("uses", "") for s in steps]
    push = next(
        i for i, n in enumerate(names) if n.startswith("docker/build-push-action")
    )
    prune = steps[push + 1]
    # The prune follows the image push in the same job, under the publish lock,
    # and runs only when the push succeeded.
    assert prune is steps[-1]
    assert prune["uses"].startswith(CLEANUP)
    assert "if" not in prune
    assert "continue-on-error" not in steps[push]
    assert not any("always()" in s.get("if", "") for s in steps)
    assert "Validate retention count" not in names
    assert prune["with"]["owner"] == "haannbboo"
    assert prune["with"]["package"] == "tokenage"
    assert prune["with"]["keep-n-tagged"] == 20


def test_schedule_and_dispatch_prune_without_publishing():
    jobs = load_workflow()["jobs"]
    cleanup = jobs["cleanup"]

    assert cleanup["if"] == (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    )
    # Never queued behind or alongside a publish on push.
    assert "needs" not in cleanup
    assert cleanup["permissions"] == {"packages": "write"}
    assert cleanup["concurrency"] == jobs["publish"]["concurrency"]
    assert cleanup["steps"][0]["name"] == "Validate retention count"
    prune = cleanup["steps"][-1]
    assert prune["uses"].startswith(CLEANUP)
    assert prune["with"]["owner"] == "haannbboo"
    assert prune["with"]["package"] == "tokenage"
    assert prune["with"]["keep-n-tagged"] == "${{ inputs.keep_versions || 20 }}"
