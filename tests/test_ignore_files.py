"""The example config's neighbours must stay out of git and the image.

`src/config/config.example.yaml` is the tracked, secret-free template. A real
`config.yaml` or `*.local.yaml` next to it holds the operator's DB URL and
Google client secret, so neither git nor the Docker build context may take it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_git_ignores_the_local_config_next_to_the_example():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/src/config/config.yaml" in lines
    assert "/src/config/*.local.yaml" in lines
    # The template itself stays tracked.
    assert (ROOT / "src" / "config" / "config.example.yaml").is_file()


def test_docker_excludes_the_local_config_after_re_including_src():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    # Order matters: `!src/**` re-includes the whole tree, so the exclusion
    # has to come after it or the config would ship in the image.
    assert lines.index("src/config/config.yaml") > lines.index("!src/**")
    assert lines.index("src/config/*.local.yaml") > lines.index("!src/**")
