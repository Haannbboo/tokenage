# tokenage Agent Guide

LLM usage tracker: OTLP collector + server, with a local proxy in the client.
Architecture, setup and commands: [README.md](README.md) and `tokenage --help`.

This file is public. Keep personal memory, local paths, private planning,
account details and secrets out of it.

## Local private instructions

Read `AGENTS.local.md` first if it exists. It, `CLAUDE.local.md` and
`.claude/*.local.md` are private and gitignored: never commit, quote or
summarize them into public files or PRs.

## Workflow

The worktree/branch is already created; don't create branches or worktrees
unless asked. Before commit, run the landing gate (opposite-tool review →
fixes → approval → commit → push → PR): [.agents/commands/review-workflow.md](.agents/commands/review-workflow.md).
PR comments, CI failures or CodeRabbit feedback: read
[.agents/commands/pr-follow-up.md](.agents/commands/pr-follow-up.md) first.
Project rules: [.agents/commands/tokenage.md](.agents/commands/tokenage.md).

## Repo layout and import rules

- `client/` — tracking wrapper, agent setup, sign-in, local proxy. Never
  imports `src`.
- `src/` — server: API, OTLP collector, ingest, dashboard; operations in
  `src/ops.py`, dispatched by `src/cli.py`.
- `protocol/` — shared pydantic wire schemas; imports neither.
- `plugins/` — client-side agent plugins.
- `frontend/` — dashboard.

Each Python package has its own `pyproject.toml`. Import rules
(client ↛ src, src ↛ client, protocol ↛ both) are enforced by `lint-imports`.

## Tests

```bash
uv run python -m pytest -q             # backend (fallback: ./.venv/bin/python)
uv run python -m pytest tests/client/ -q
uv run python -m pytest tests/scripts/ -q
cd frontend && npm test && npm run build
pre-commit run --all-files
```

Targeted tests while iterating; full checks for touched areas before commit.
Details: [.agents/commands/verify.md](.agents/commands/verify.md).

## Running a branch locally

In a new worktree, symlink the main clone's `.venv`. Run the branch with
`src/scripts/dev/dev-start.sh` (stop: `dev-stop.sh`): an isolated server on an
ephemeral copy of the DB and free ports, with auto-reload; the main server is
untouched.

`TOKENAGE_ROOT="$PWD" tokenage server restart` runs this checkout's migrations
against the configured DB and reloads running services, but they keep running
the install that last ran `start` (it does not switch code).
`TOKENAGE_ROOT="$PWD" tokenage server bootstrap` rebuilds the dashboard,
repoints the services at this checkout and runs its migrations. Never use
either against a server that serves real traffic.

## Git and PR rules

- `main` stays runnable; don't work on it unless asked.
- Commit, push, open PRs and merge only after explicit approval.
- Squash merge (ask first); never add merge commits.
- One-line conventional commits (`feat:`, `fix:`, `docs:`), no co-authorship,
  titles ~50 chars. The PR title becomes the squash commit.

## Version management

`VERSION` is the server release, `client/VERSION` the client release
(`MAJOR.MINOR.PATCH`). MAJOR/MINOR are bumped by hand; PATCH is bumped on PR
branches by `.github/workflows/bump-version.yml` for the components the diff
touches.

## Code style

- Python: ruff format (pre-commit enforces it).
- TypeScript: `Record<string, any>` for dynamic objects (config, API results).
- No unrelated refactors; note them separately.
- New frontend-used API routes go in `frontend/vite-api-proxy.js` and its tests.

## Architecture and privacy

- The same code runs whether server and client share a machine or not (local,
  self-hosted, hosted). No deployment-specific branches (`if hosted`,
  `if local`); the client always talks to a server URL.
- tokenage collects usage metadata only (tokens, cost, model, timing, tool
  names), not prompts, responses or request bodies. Don't capture content
  without an explicit opt-in, and never log it.

## Gotchas

- One auth model: every request resolves to a user. No `user is None` branches.
- The API port is config-driven; read it from config, don't assume one.
- The DB may be remote Postgres; worker and DB code must tolerate slow or hung
  calls.

## High-risk areas

Secrets and auth headers; raw prompts/responses and privacy-sensitive logs;
cost and token accounting; provider adapters and model-name normalization;
streaming/tool-call chunks, retries and idempotency; schema migrations; worker
loops; frontend/backend route parity. Details:
[.agents/commands/tokenage.md](.agents/commands/tokenage.md).
