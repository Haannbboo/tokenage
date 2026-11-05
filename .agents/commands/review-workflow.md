# Landing gate: review, commit, push, PR

Normal feature work (request → design/spec → approval → implementation →
testing) is handled by the agent's own planning and testing skills. This repo
adds only the landing gate below.

## Steps

1. **Independent review before commit.** Run fresh reviewers on the *opposite*
   tool from the one that wrote the change: Orca orchestration workers when
   available, otherwise the other tool's CLI (`claude -p` / `opencode run`).
   Never use subagents of your own model as reviewers. Pick the tier by risk,
   not size, from [review/SKILL.md](review/SKILL.md):
   - **Quick** (docs, comments, tests-only, non-security config): no security
     reviewer, unless the diff touches secrets, auth, CI or installer files.
   - **Standard** and **Deep**: a security reviewer is required
     ([review/security.md](review/security.md)).
2. **Fix must-fix findings.** Re-review if the fix is non-trivial.
3. **Commit only after approval.** One-line conventional message, no
   co-authorship.
4. **Push the current branch only after approval.**
5. **Open the PR** with [pr-template.md](pr-template.md); see
   [open-pr.md](open-pr.md) and [pre-pr.md](pre-pr.md).
6. **Do not merge without approval.** Prefer squash; ask before squashing;
   never add merge commits.

## Related

- Feature flow: [feature-pr.md](feature-pr.md)
- Verification: [verify.md](verify.md)
- PR comments, CI failures, CodeRabbit: [pr-follow-up.md](pr-follow-up.md),
  [respond-to-pr-comments.md](respond-to-pr-comments.md)
- Ingest benchmark (maintainer PRs, with approval): [benchmark.md](benchmark.md)
