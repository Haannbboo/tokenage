#!/usr/bin/env python3
"""Tag releases since the release workflow was introduced (dry run by default).

Run with --apply to create local tags; only CI should also pass --push.
Existing tags are never moved. Every run repairs missing tags from the commit
that added .github/workflows/release-tags.yml through --ref, including the
versions at that starting commit. Older releases are never backfilled.
"""

import argparse
import os
import re
import subprocess

GIT_ENV = {
    key: value for key, value in os.environ.items() if not key.startswith("GIT_")
}


def git(*args):
    return subprocess.check_output(["git", *args], text=True, env=GIT_ENV).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--apply", action="store_true", help="create local tags")
    parser.add_argument(
        "--push", action="store_true", help="push newly created tags (requires --apply)"
    )
    args = parser.parse_args()
    if args.push and not args.apply:
        parser.error("--push requires --apply")
    starts = git(
        "log",
        "--first-parent",
        "--reverse",
        "--diff-filter=A",
        "--format=%H",
        args.ref,
        "--",
        ".github/workflows/release-tags.yml",
    ).splitlines()
    if not starts:
        return  # Release tagging was not enabled yet at this ref.
    start = starts[0]
    commits = [
        start,
        *git(
            "log",
            "--first-parent",
            "--reverse",
            "--format=%H",
            f"{start}..{args.ref}",
            "--",
            "client/VERSION",
            "VERSION",
        ).splitlines(),
    ]
    seen = set()
    for commit in commits:
        changed = git(
            "diff-tree",
            "--root",
            "-m",
            "--first-parent",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).splitlines()
        for path, prefix in (("client/VERSION", "client-v"), ("VERSION", "v")):
            if commit != start and path not in changed:
                continue
            try:
                version = git("show", f"{commit}:{path}")
            except subprocess.CalledProcessError:
                continue  # A version file may not exist yet, or was deleted.
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                raise ValueError(f"invalid {path} at {commit}")
            tag = prefix + version
            if tag in seen:
                continue
            seen.add(tag)
            exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
                env=GIT_ENV,
            ).returncode
            if exists == 0:
                continue
            if exists != 1:
                raise RuntimeError(f"could not check tag {tag}")
            print(f"{'Tag' if args.apply else 'Would tag'} {commit} {tag}", flush=True)
            if args.apply:
                subprocess.run(["git", "tag", tag, commit], check=True, env=GIT_ENV)
                if args.push:
                    subprocess.run(
                        ["git", "push", "origin", f"refs/tags/{tag}"],
                        check=True,
                        env=GIT_ENV,
                    )


if __name__ == "__main__":
    main()
