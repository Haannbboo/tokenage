# Open PR Workflow

Use this after implementation, verification, and independent review are complete. Do not push or open a PR without explicit approval.

## 1. Inspect current state

```bash
git status --short
git branch --show-current
git log --oneline -5
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

Stop if:

- current branch is `main`
- unrelated files are modified/staged
- private/local files are included
- independent review is missing
- must-fix review findings are unresolved

## 2. Standards review gate

This is a lightweight pre-flight gate, not a replacement for the independent review in `.agents/commands/review/SKILL.md`. The independent review should already be complete before this workflow pushes/opens a PR.

Before writing the PR body, compare the diff against:

- `AGENTS.md`
- `.agents/commands/tokenage.md`
- `.agents/commands/pre-pr.md`
- `.agents/commands/review/SKILL.md`

If the diff violates repo rules, stop and either fix the issue or list it honestly under `Known Limitations` if the user explicitly chooses to proceed.

## 3. Pick the smallest PR body that tells the truth

Use `.agents/commands/pr-template.md`.

Scale the body:

- **Small**: docs-only, config-only, trivial fix, low-risk isolated change.
- **Standard**: most behavior changes, multi-file changes, tests added/updated.
- **High-risk**: schema/migration, privacy/secrets, cost accounting, provider adapter, streaming/tool-call, retry/timeout/idempotency, worker/DB loop, broad frontend/backend route changes.

Never leave `N/A` filler. Delete sections that truly do not apply.

## 4. Commit if needed

Only after explicit approval:

```bash
git add <specific-files>
git commit -m "feat: short description"
```

Rules:

- stage specific files, not lazy `git add .`, unless the diff is tiny and already inspected
- one-line conventional commit by default
- no co-authorship
- no private/local files

## 5. Push branch

Only after explicit approval:

```bash
git push -u origin HEAD
```

## 6. Create PR

Use GitHub CLI:

```bash
gh pr create \
  --title "<title>" \
  --body-file /tmp/pr-body.md
```

Aim for a title of about 50 characters, conventional style; it becomes the squash commit title.

If a PR already exists for the current branch, update that PR instead of opening a duplicate:

```bash
gh pr view --json number,url,title
```

## 7. Output

Return:

```md
## PR Created

- URL: ...
- Branch: ...
- Title: ...

## Validation

- `command`: pass/fail/not run

## Review gate

- Independent review: completed/not completed
- Must-fix findings: resolved/not resolved

## Risks / follow-ups

- ...
```
