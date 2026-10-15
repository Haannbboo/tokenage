# tokenage Agent Guide

LLM usage tracker: OTLP collector + server, with a local proxy in the client. See [README.md](README.md) for architecture, setup, and commands.

This file is public repo guidance. Keep personal memory, local paths, private planning, account details, and machine-specific secrets out of it.

## Local private instructions

If `AGENTS.local.md` exists, read it before starting work.

`AGENTS.local.md` is private, gitignored, and must never be committed, quoted, summarized into public files, or included in PRs.

If `CLAUDE.local.md` or `.claude/*.local.md` exists, treat it the same way: private local context only.

## Default workflow

Assume the worktree/branch has already been created for you. Do not create branches or worktrees unless explicitly asked.

Normal feature work is handled by the agent's planning/testing skills:

```txt
request → design/spec → approval → implementation → testing
```

This repo adds only the final landing gate:

1. Before commit, run an independent code review with fresh opposite-tool reviewers (Orca orchestration workers when available, otherwise CLI processes).
2. Fix must-fix review findings.
3. Commit only after approval.
4. Push the current branch only after approval.
5. Open a PR using `.agents/commands/pr-template.md`.
6. Do not merge without approval.

Workflow details: `.agents/commands/feature-pr.md`.
Verification workflow: `.agents/commands/verify.md`.
Independent review: `.agents/commands/review/SKILL.md`.
Open PR workflow: `.agents/commands/open-pr.md`.
Pre-PR checklist: `.agents/commands/pre-pr.md`.
Project rules: `.agents/commands/tokenage.md`.

When handling PR comments, CI failures, or CodeRabbit feedback, read `.agents/commands/pr-follow-up.md` before acting. For review comments only, read `.agents/commands/respond-to-pr-comments.md`. These are not optional — they cover reply style, fix scoping, verification, and resolving GitHub conversations.

## Git and PR rules

- `main` must stay runnable.
- Do not work directly on `main` unless explicitly asked.
- Do not create branches/worktrees unless explicitly asked.
- Do not commit until explicitly approved.
- Do not push, open PRs, or merge until explicitly approved.
- Prefer squash merge for feature branches.
- Ask whether to squash when merging.
- Never add merge commits when asked to merge.
- Commit messages: one-line conventional style, no co-authorship.

Example commit style:

```txt
feat: add OpenCode plugin usage tracking
fix: handle missing cache_read token pricing
docs: add provider adapter workflow
```

## Version management

Version format: `MAJOR.MINOR.PATCH` (e.g. `0.1.180`).

- `VERSION` at repo root is the server release.
- `client/VERSION` is the client release, tracked separately so a hosted client
  snapshot can move without a server release.

- `MAJOR`: bumped manually for breaking changes — edit first field in the file.
- `MINOR`: bumped manually for feature releases — edit second field in the file.
- `PATCH`: auto-incremented on PR branches targeting `main` by `.github/workflows/bump-version.yml`; the bump commit becomes part of the PR before squash merge, so `main` gets a single squashed commit. The workflow raises only the files the diff touched: `client/` and `plugins/` bump the client, `install.sh` bumps both, `src/`, `frontend/`, `VERSION` and friends bump the server, and `protocol/` bumps both.

`tokenage --version` prints the client version and commit, and appends `· server <VERSION>` when the server component is installed.

The version is exposed via `GET /version` (the API app) and displayed in Settings → Services in the frontend.

Any new API route consumed by the frontend must be added to `frontend/vite-api-proxy.js` and its proxy route tests.

## Code style

- Python: use ruff formatting. Pre-commit enforces ruff-format.
- TypeScript: use `Record<string, any>` for dynamic objects such as config and API results. `Record<string, unknown>` causes excessive casting in JSX here.
- Avoid unrelated refactors. If you see unrelated crap, note it separately instead of mixing it into the current diff.

## Test and verification commands

Backend:

```bash
uv run python -m pytest -q
```

If `uv` is unavailable but the bootstrap-created virtualenv exists, use `./.venv/bin/python -m pytest -q` as a fallback.

Script-specific changes:

```bash
uv run python -m pytest tests/scripts/ -q
```

Client changes:

```bash
uv run python -m pytest tests/client/ -q
```

Frontend:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Pre-commit:

```bash
pre-commit run --all-files
```

Use targeted tests during iteration, but before commit/PR run the relevant full checks for touched areas.

## Bootstrap architecture

There is one installed command: `client/bin/tokenage`. Whichever component installs first places it, and the `# tokenage launcher` marker line is how an install recognises a launcher it previously wrote. The server install symlinks it from the clone into `~/.local/bin` unless one exists; the client install replaces it with a copy from its snapshot. It resolves the two components out of `$TOKENAGE_HOME` — `current` for the client snapshot, `src` for the server clone — and routes to whichever one the arguments name. The client runs only from `current` (or `TOKENAGE_ROOT`), never from the server clone, even on one machine.

- `client/` — the client: tracking wrapper, agent configuration, sign-in, component report, and the local proxy (`client/proxy/`, run by the client service, configured in `$TOKENAGE_HOME/client.yaml`). It must never import `src`.
- `src/` — the server component: API, OTLP collector, usage ingest, dashboard, evaluation worker; its operations are `src/ops.py` (start/stop/restart/status/update, the post-venv half of bootstrap), dispatched by `src/cli.py`; the only shell it needs is `src/scripts/bootstrap.sh`, which runs before the venv exists. There is no top-level `scripts/`.

Command surface:

```bash
tokenage status            # this client: sign-in, agents, wiring
tokenage setup             # agent configuration
tokenage update            # update the client
tokenage client start      # install + start the OS-supervised client service (systemd --user / launchd)
tokenage client status     # whether the OS manager runs it, last report
tokenage server start      # turn the services on
tokenage server restart    # reload running code
tokenage server update     # update the server clone
tokenage server bootstrap  # install, build the dashboard, start, verify
tokenage server status     # the service view
```

Server commands exist only under `tokenage server`; there are no top-level aliases.

### Testing local changes: set `TOKENAGE_ROOT`

`tokenage server ...` runs `python -m src.cli` (and `bootstrap.sh`) from `$TOKENAGE_HOME/src` — the deployed clone — not the checkout you are editing. So prefix every command in a checkout:

```bash
TOKENAGE_ROOT="$PWD" tokenage server restart    # reload THIS checkout's code
TOKENAGE_ROOT="$PWD" tokenage server bootstrap  # build THIS checkout's dashboard
```

It also makes the client import this checkout instead of a snapshot. Repair agent settings with `TOKENAGE_ROOT="$PWD" tokenage setup`.

There is one installer, `install.sh` at the repo root (POSIX sh, also served by `GET /install.sh` with the server URL and commit placeholders filled). Components are chosen by flag: `--server`, `--client`, or neither/both. With no flag it installs both, unless it was served by a server (preset URL), in which case it installs the client. Any other argument is rejected.

```txt
server: install.sh → clone to ~/.tokenage/src → src/scripts/bootstrap.sh → python -m src.cli bootstrap (src/ops.py)
client: install.sh → snapshot + venv under ~/.tokenage/versions/<commit>, `current` link, launcher copy
both:   server, then client pointed at http://127.0.0.1:<api_port>: sign-in, `client start` (not fatal)
```

- `install.sh` — the server step checks git/bash, clones/updates `~/.tokenage/src`, runs `src/scripts/bootstrap.sh`. The client step needs `TOKENAGE_SERVER` or the served URL. `TOKENAGE_SKIP_LOGIN=1` runs `setup` instead of signing in; `tokenage update` uses it.
- `src/scripts/bootstrap.sh` — the only shell: uv, `.venv`, `uv pip install -r src/pyproject.toml --extra dev`, launcher symlink if none exists, then `exec python -m src.cli bootstrap`. `tokenage server update` re-runs it after pulling because dependencies may change.
- `src/ops.py` — everything else, one module. `bootstrap` records the `src/pyproject.toml` hash stamp, builds the dashboard, runs `start`, verifies ports and the dashboard, then restarts the API so the new `frontend/dist` is served (only the command that builds restarts the API; the mount happens at import time). It never starts, signs in or configures a client.
- `start` — config create/sync, port check (first run auto-assigns free ports), schema migrations, supervisord start or reload. Refuses with "run tokenage server bootstrap" when `src/pyproject.toml` changed. Never touches agent settings.
- `restart [program...] [--otlp-port N]` — migrations, then `SIGHUP` to the running programs; `--otlp-port` persists the port and restarts the collector. `stop`, `status` take program names; `update [--check|--dry-run]` is fetch, ff-only pull, bootstrap.sh, restart. `python -m src.cli migrate` and `sync-config` serve Docker and the dev scripts.

Quick backend iteration: `TOKENAGE_ROOT="$PWD" tokenage server restart` (see `src/ops.py`) reloads the supervisord-managed services to pick up backend changes locally.

The human views frontend changes through the built version, not Vite dev — after frontend edits, run `TOKENAGE_ROOT="$PWD" tokenage server bootstrap` to rebuild `frontend/dist` (`npm install && npm run build`) so the changes show up.

Frontend dev:

```bash
cd frontend && npm run dev
```

Vite dev uses port `5173`. `tokenage server bootstrap` builds and serves the frontend through FastAPI.

## Worktree dev environment

Create feature worktrees in `../tokenage-worktrees/`, next to the main `tokenage` clone. After creating one, symlink the main clone's virtualenv into it (`ln -s /path/to/main/clone/.venv .venv`) — the launcher and tests need it; without it they fall back to system `python3` and fail on missing deps.

`src/scripts/dev/dev-start.sh` launches an isolated dev environment for worktree work. It is fully independent from the main production server:

| | Main API server | Dev API server |
|---|---|---|
| **Manager** | supervisord (`~/.tokenage/supervisord.conf`) | standalone uvicorn |
| **Working dir** | `~/Documents/tokenage/` | worktree dir |
| **Port** | from `~/.tokenage/config.yaml` | random free port |
| **DB** | `~/.tokenage/usage.db` | ephemeral copy in `/tmp/` |
| **Auto-reload** | no | yes (`--reload`) |

Key behaviors:

- **Ephemeral DB**: copies `~/.tokenage/usage.db` into a temp dir; the main DB is never modified. Temp dir is deleted on stop.
- **Free ports**: API and Vite ports are allocated dynamically; no conflicts with main server.
- **Auto-reload**: the dev uvicorn server runs with `--reload`, so Python file changes restart it automatically.
- **Safe restart**: `src/scripts/dev/dev-stop.sh` + `src/scripts/dev/dev-start.sh` restarts only the dev server. The main supervisord-managed server is unaffected.

```bash
# Start isolated dev environment
./src/scripts/dev/dev-start.sh

# Stop it
./src/scripts/dev/dev-stop.sh
```

## Durable repo notes

- Three Python packages, each declaring its dependencies in its own `pyproject.toml` (there are no `requirements.txt` files): `client/`, `src/` (server; `--extra dev` adds test and lint tools), `protocol/` (shared pydantic wire schemas). Installers run `uv pip install -r <package>/pyproject.toml`. Import rules: client ↛ src, src ↛ client, protocol ↛ client/src. `plugins/` belongs to the client component but stays at the root.
- `client/` must never import `src`. A client-only install has no server clone, so the dependency would break the whole client; `.importlinter` (run by pre-commit and CI via `lint-imports`) enforces it.
- One auth model: every request resolves to a user. `auth.provider` is `local` (default; direct loopback requests are the built-in owner, others need a token or `tokenage server login-link`) or `google`. There is no auth-off mode; don't add `user is None` branches.
- Runtime API port is config-driven. Do not assume `4001`; read `~/.tokenage/config.yaml`. This repo has recently run the API on `4004`.
- Service control uses `~/.tokenage/supervisord.conf`.
- The configured DB may be remote Postgres/Supabase, not local SQLite. Worker and session-selector changes must tolerate slow or hung DB calls.
- For stuck evaluations, inspect `evaluation_jobs` plus `/evaluation-jobs/active`. A queued auto job can be normal buffer behavior; if no running job exists and it survives a worker interval, suspect the worker loop.
- Frontend-used API routes must be added to `frontend/vite-api-proxy.js` and its proxy route tests, or Vite dev mode may fail while production works.

## High-risk areas

Treat these as review-sensitive:

- API key, token, auth header, and secret handling.
- Raw prompt, raw response, request body, trace payload, and privacy-sensitive logs.
- Cost calculation, token accounting, cache read/write pricing, provider multipliers.
- Provider adapters and model-name normalization.
- Streaming/tool-call partial chunks and retry/timeout/idempotency behavior.
- Schema migrations and backward compatibility.
- Worker loops, background jobs, and DB calls that can hang.
- Frontend/backend route parity, especially Vite proxy behavior.

More details: `.agents/commands/tokenage.md`.

## Agent skills

### Issue tracker

This repo currently uses GitHub for source control. If issues are needed, prefer GitHub Issues unless local markdown is explicitly requested. Future detailed config can live in `docs/agents/issue-tracker.md`.

### Triage labels

Default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Future detailed config can live in `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo. Use root README plus `.agents/commands/tokenage.md`; ADRs may live under `docs/adr/` only for durable architecture decisions.
For the hosted client/server split design, read `docs/client-server-split-handoff.md`.
