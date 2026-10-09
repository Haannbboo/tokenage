# Feature PR Workflow

Use this for non-trivial features, bug fixes, refactors, provider integrations, schema changes, pricing changes, and UI changes that touch behavior.

This workflow assumes the branch/worktree already exists. Do not create branches or worktrees unless explicitly asked.

## 0. Safety check

Before touching code:

```bash
git status --short
git branch --show-current
git fetch origin
```

Rules:

- Do not work on `main` unless only inspecting or explicitly asked for a direct local edit.
- Do not overwrite or delete user changes.
- If the working tree has unrelated changes, stop and explain what exists before modifying those files.
- Never commit, push, open PRs, or merge without explicit approval.

## 1. Normal implementation flow

Use the agent's standard planning and testing skills for:

```txt
request → design/spec → approval → implementation → testing
```

Keep scope tight:

- Read relevant code before designing. Do not rely on memory when files are available.
- Implement only the approved scope.
- Avoid opportunistic cleanup.
- Preserve existing public APIs unless the spec approved changing them.
- For API routes used by frontend, update `frontend/vite-api-proxy.js` by editing `shouldProxyApiRequest()` explicit `pathname === ...` / prefix checks, then update `frontend/vite-api-proxy.test.js`.
- For DB changes, add migration/backward compatibility tests covering `src/schema_migrations.py` and affected `src/database/` models.
- For provider/pricing changes, add tests covering provider-specific names, cache read/write or reasoning token fields if relevant, and missing pricing fallbacks.
- For streaming/tool calls, test partial chunks and malformed/incomplete events.

Typical code areas:

- Backend API: `src/api.py`
- Proxy/collector: `client/proxy/`, OTLP code, trace and usage ingestion code
- Database/schema: `src/database/`, `src/schema_migrations.py`
- Pricing: pricing config, LiteLLM/OpenRouter fetch/cache code
- Frontend: `frontend/src/`, `frontend/vite-api-proxy.js`
- Tests: `tests/`, `frontend/tests/`

## 2. Verify

Use `.agents/commands/verify.md`.

Run focused checks first, then relevant full checks. Do not duplicate verification commands here; `verify.md` is the source of truth so this workflow does not rot into contradictory crap.

If a check fails:

1. Read the failure.
2. Fix the root cause, not the symptom.
3. Re-run the failing check.
4. Report unrelated pre-existing failures separately.

## 3. Independent review before commit

Before commit, run `.agents/commands/review/SKILL.md` in a fresh opposite-tool CLI process, not through a subagent or internal delegation call.

Review the branch against `main`:

```bash
git diff --stat main
git diff main
git ls-files --others --exclude-standard
```

The reviewer must be independent: separate process, no implementation history, and no assumptions beyond the diff and repo guidance. Read and include the contents of intended untracked files because `git diff` does not show them.

Fix all must-fix findings before committing.

## 4. Commit

Only after explicit approval:

```bash
git add <files>
git commit -m "feat: short description"
```

Rules:

- No co-authorship.
- Prefer one-line conventional commit messages.
- Do not commit ignored/private/local files.

## 5. Push and PR

Use `.agents/commands/open-pr.md`.

Only after explicit approval:

```bash
git push -u origin HEAD
gh pr create --title "..." --body-file /tmp/pr-body.md
```

Use `.agents/commands/pr-template.md` for `/tmp/pr-body.md`.

If a PR already exists for the branch, update it instead of opening a duplicate.

Wait for CI and review feedback. Use `.agents/commands/pr-follow-up.md` for CI failures, CodeRabbit, or human/bot review comments. Fix must-fix issues on the branch. Do not merge without approval.

## 6. Merge

Only after explicit approval:

```bash
gh pr merge --squash --delete-branch
```

Ask whether to squash if unclear.
