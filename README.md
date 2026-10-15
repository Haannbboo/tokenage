[English](README.md) | [中文](README_CN.md)

# tokenage

**Local-first observability for command-line LLM agents.**

`tokenage` shows what your coding agents are doing: requests, token usage, cost estimates, latency, TTFT, models, sources, and session IDs across **Claude Code**, **Codex**, **OpenCode**, **Kilo Code**, and OpenAI/Anthropic-compatible traffic.

It is built for people who run multiple LLM agents locally and want one place to answer:

- Which agent/model is spending the most?
- Did my last command actually get tracked?
- How much did this coding session cost?
- Which requests were slow, streamed, cached, or reasoning-heavy?

The default setup is local: config in `~/.tokenage/config.yaml`, usage data in SQLite at `~/.tokenage/usage.db`, and services bound to loopback ports.

## What it does

- **Tracks popular coding agents**: Claude Code, Codex, OpenCode, and Kilo Code through local OTLP telemetry.
- **Tracks OpenAI/Anthropic-compatible clients**: route clients through the optional local proxy that runs in the client service.
- **Shows a dashboard**: usage, cost, latency, models, sources, request logs, setup health, and first-event onboarding.
- **Prints command summaries**: run an agent through `tokenage` and get usage for that run.
- **Keeps setup inspectable**: plain YAML config, SQLite by default, logs in `logs/`, no hosted backend required.
- **Supports SQL databases**: keep SQLite locally or point `db.url` at PostgreSQL/MySQL through SQLAlchemy.

## How collection works

`tokenage` collects usage in two complementary ways:

```text
Claude Code / Codex / OpenCode / Kilo Code
        │
        │ OTLP telemetry
        ▼
tokenage OTLP collector ──► database ──► dashboard / API / summaries
```

```text
OpenAI-compatible or Anthropic-compatible client
        │
        │ HTTP
        ▼
tokenage client proxy (127.0.0.1) ──► upstream provider
        │
        │ usage report (metadata only)
        ▼
tokenage server ──► database
```

Agent telemetry is best for agent-specific fields such as sessions and tool/reasoning metadata. The proxy is useful for clients that support custom base URLs. It runs on your machine, keeps your provider keys there, and sends the server only token counts and timings.

## Quick start

### Prerequisites

- macOS or Linux shell environment
- Python 3.13, or `uv` so the installer can create it
- Node.js 18+ if you want the dashboard built and served
- Optional: `claude`, `codex`, `opencode`, or `kilo` installed locally

### 1. Install

Pick the components this machine needs. With no flag the installer does both:

```bash
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash                # server + client
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash -s -- --server # server only
curl -fsSL https://YOUR_SERVER/install.sh | sh                                                         # client only, for that server
```

The server is a git clone in `~/.tokenage/src`. The client is a separate snapshot
and virtualenv under `~/.tokenage/versions`, even on the machine that runs the
server; the two talk only over HTTP. Installing both is the server install
followed by the client install pointed at `http://127.0.0.1:<api_port>`. The
`tokenage` launcher in `~/.local/bin` is shared. `tokenage update` updates the
client and `tokenage server update` the server.

From a checkout, `bash src/scripts/bootstrap.sh` is the server install alone. It:

1. installs Python dependencies into `.venv`
2. builds the dashboard when Node/npm are available
3. creates a launcher symlink at `~/.local/bin/tokenage` if there is none (steps 1 and 3 are shell, the rest `src/ops.py`)
4. creates `~/.tokenage/config.yaml` if needed
5. starts the API and OTLP services with Supervisor
6. verifies service ports and the dashboard
7. restarts the API so the freshly built dashboard is served

If `~/.local/bin` is not on your `PATH`, the installer prints the shell command to add it.

### 2. Open the dashboard

```bash
open http://localhost:4001
```

If you prefer the Vite dev server while developing the frontend:

```bash
cd frontend
npm install
npm run dev
```

Then open [http://localhost:5173](http://localhost:5173).

### 3. Point your agents at the collector

The server install never configures agents. That is the client's job (the
both-install runs the sign-in for you):

```bash
tokenage setup
```

It points the agents installed on this machine at the collector of the server
the client signed in to (`tokenage login --server URL`), and
leaves every setting it does not own alone. `tokenage setup --disable` takes
them back off again. `tokenage status` shows this client's sign-in and where the
agents point.

### 4. Generate your first tracked event

After bootstrap, run one of the commands shown by the dashboard, or use one of these directly:

```bash
tokenage codex exec "hello"
tokenage claude
```

Repo-local fallback, useful before the symlink is on your `PATH`:

```bash
TOKENAGE_ROOT="$PWD" ./client/bin/tokenage codex exec "hello"
TOKENAGE_ROOT="$PWD" ./client/bin/tokenage claude
```

The empty dashboard automatically checks for your first event. No fake demo data, no manual seeding.

## Docker deployment

Run the API and OTLP servers in a single container for NAS or remote server deployment. See [docker/README.md](docker/README.md).

## CLI examples

The wrapper runs a child command, captures usage while it runs, then prints a summary. It reports to the collector that is already running: it never starts a service, never spins up a temporary database or collector, and never merges anything afterwards. If the collector is not reachable the child still runs, untracked, and the exit code is still the child's.

```bash
# Interactive agents
tokenage codex
tokenage claude

# One-shot commands
tokenage codex exec "say hello in one sentence"

# Installed CLI
tokenage codex exec "say hello in one sentence"
```

The same command also covers everything that is not a tracked run:

```bash
# Client sign-in and agents
tokenage status
tokenage setup
tokenage update --check

# The local server needs no Google account: its dashboard and `tokenage login`
# work from the server machine; for a browser on another machine, run
# `tokenage server login-link` on the server and open the URL it prints.
# A remote server instead of a local one
tokenage login --server https://app.example.com
tokenage logout
```

Use `--` when passing flags to `tokenage` itself:

```bash
tokenage --json -- codex
tokenage --usage-only -- codex exec "say hello in one sentence"
tokenage --wait-ms 5000 -- codex exec "say hello in one sentence"
tokenage --summary-dest file --summary-file /tmp/llm-summary.json -- claude
tokenage --no-summary -- codex exec "say hello"
```

See [docs/cli-reference.md](docs/cli-reference.md) for all flags, the tracking model, exit codes, service commands, API endpoints, and environment variables. Per-command behavior is in [docs/cli-refactor.md](docs/cli-refactor.md).

## Dashboard

The dashboard gives you:

- first-event onboarding when no data exists yet
- usage and cost overview
- model/source breakdowns
- latency and TTFT trends
- request logs
- detected agents and setup health

By default, the backend API serves the built dashboard at `http://localhost:4001`. The frontend dev server resolves the API URL in this order:

1. `TOKENAGE_API_URL`
2. `TOKENAGE_BACKEND_URL`
3. `~/.tokenage/config.yaml` using `server.host` and `server.api_port`
4. `http://localhost:4001`

Frontend-specific notes live in [frontend/README.md](frontend/README.md).

## Default local services

| Service | Default URL | Purpose |
| --- | --- | --- |
| API + dashboard | `http://127.0.0.1:4001` | REST API and built dashboard |
| OTLP collector | `http://127.0.0.1:4002` | Local agent telemetry ingestion |

Service commands:

```bash
tokenage server status
tokenage server restart
tokenage server stop
```

`tokenage server start` turns the services on, and `tokenage server
bootstrap` reinstalls, rebuilds the dashboard and restarts the API so the new
bundle is served. `tokenage server update` updates the server clone. `tokenage status` is a
different command: it reports this client, not the services.

Runtime files live under `~/.tokenage/run/`. Logs are written to `logs/`.

## Configuration

Main config:

```text
~/.tokenage/config.yaml
```

Minimal server and database config:

```yaml
models:
  gpt-5.4:
    cost:
      input: 2.5
      output: 15.0
      cacheRead: 0.25

server:
  host: 127.0.0.1
  api_port: 4001
  otlp_port: 4002

db:
  path: ~/.tokenage/usage.db
```

To use PostgreSQL or MySQL instead of SQLite, set `db.url`:

```yaml
db:
  url: postgresql+psycopg://user:password@db-host:5432/tokenage?sslmode=require
```

`tokenage server start` merges missing defaults from `config.example.yaml` into your user config without overwriting existing values.

## Point clients at the proxy

The proxy runs in the client service (`tokenage client start`), not in the server. It binds to `127.0.0.1` only and is configured in `~/.tokenage/client.yaml` (kept mode `0600`; keys are literal). Without a `proxy:` section there is no proxy.

```yaml
proxy:
  port: 4000              # optional
  providers:
    my-provider:
      base_url: https://api.example.com/v1
      api_key: sk-...
      models: [gpt-5.4]
    anthropic:
      base_url: https://api.anthropic.com/v1
      api_key: sk-ant-...
      auth_scheme: x-api-key   # default is bearer
      enabled: true            # default
      models: [claude-sonnet-4-6]
```

After editing, run `tokenage client restart`. `tokenage status`, `tokenage client status` and `tokenage client start` print the proxy URL, or `proxy failed: <reason>`. An explicit `port` is bound or fails; without one the proxy reuses its last port, else tries 4000 and scans upward. The default is `http://127.0.0.1:4000`, but read the real URL from `tokenage status`.

For OpenAI-compatible clients:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:4000/v1
```

For Anthropic-compatible clients:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
```

The client's own API key is ignored: the proxy injects the provider's `api_key` upstream as `Authorization: Bearer <key>`, or `x-api-key: <key>` with `auth_scheme: x-api-key` (e.g. `api.anthropic.com`). The client must still send its own `anthropic-version` header (real Anthropic clients like Claude Code always do; the proxy passes it through unchanged and does not set a default).

For each model call the client posts a metadata-only usage report (model, token counts, timings; no prompts, responses or keys) to the server's `POST /ingest/v1/usage` with this device's ingest token. It makes a single attempt and drops the report on failure; the server prices it. This works the same whether the server is on this machine or elsewhere.

Supported proxy paths include:

- `/v1/chat/completions`
- `/v1/responses`
- `/v1/messages`

For streamed responses, the proxy records TTFT as time until the first upstream chunk arrives.

## Automatic session evaluation

The client service evaluates local Claude Code, Codex, OpenCode, and Kilo sessions
after ten minutes without activity, oldest last activity first. It uses your
installed evaluator's account and credits. Setup chooses the first installed
evaluator in this order: **Claude Code → Codex → OpenCode**. If none is installed,
evaluation stays idle. An explicit choice is preserved across updates.

Configure it in `~/.tokenage/client.yaml`, alongside any `proxy:` settings:

```yaml
evaluation:
  auto_enabled: true
  evaluator: claude        # claude, codex, or opencode
  quiet_delay_seconds: 600
```

Set `auto_enabled: false` to turn it off, then run `tokenage client restart`.
The client selects and runs evaluations locally; the dashboard shows current
results and run history and lets you annotate outcomes manually. A resumed
session is evaluated again after it becomes quiet. Manual annotations take
precedence over reported results.

If the server rejects a start report with HTTP 404, the client skips evaluation
and saves a local rejection marker. That revision stays covered until the local
transcript changes, avoiding evaluator charges for sessions the server cannot store.

Full transcripts stay on the machine and are passed to your configured evaluator;
tokenage receives only bounded, secret-scrubbed derived results and run metadata.
The client retains one latest result per session under
`$TOKENAGE_HOME/evaluations/`, including whether submission succeeded. Unsent
results are retried without repeating the evaluation. Permanent rejections
(HTTP 400, 404, 409, or 422) remain local with `submission_error` and are not
retried automatically. The server keeps run
metadata history and the latest session evaluation, not historical result copies.

The client reports to `POST /evaluation-jobs/started` and
`POST /evaluation-jobs/completed` with its device-bound CLI bearer token, including
on loopback. A successful completion response means the server committed the
report; retrying the same job ID is safe. User-scoped run history is available at
`GET /sessions/{session_id}/evaluation-jobs`. Manual annotations use
`GET`, `PUT`, and `DELETE /sessions/{session_id}/evaluation`.

## Tracking coverage

| Metric | Claude Code | Codex | OpenCode / Kilo Code | Direct proxy |
| --- | --- | --- | --- | --- |
| Input tokens | OTLP | OTLP | Plugin OTLP | Response usage |
| Output tokens | OTLP | OTLP | Plugin OTLP | Response usage |
| Cached tokens read | OTLP | OTLP | Plugin OTLP | Response usage |
| Cached tokens write | OTLP | Not available | Plugin OTLP | Not available |
| Reasoning tokens | Not available | OTLP | Plugin OTLP | Response usage |
| Tool tokens | Not available | OTLP | Not available | Not available |
| Prompt length | OTLP | OTLP | Plugin OTLP | Not available |
| Latency | OTLP | OTLP | Plugin OTLP | Proxy timing |
| TTFT | Not available | OTLP | Plugin OTLP | Streaming only |
| Session ID | OTLP | OTLP | Plugin OTLP | Not available |

TTFT is an operational signal, not a billing-grade metric. Each agent exposes different timing data.

OpenCode and Kilo Code tracking is provided by local plugins (`plugins/opencode` and `plugins/kilo`) that emit one OTLP log record for each completed assistant message. `tokenage setup` registers each built plugin (when `opencode` or `kilo` is installed) with the local OTLP logs endpoint, using `client/agents/`.

The OpenCode plugin serves both OpenCode 1 and OpenCode 2 from one build: the default export carries a v1 `server()` (message events) and a v2 `setup()` (the `session.step.*` and `session.tool.*` event stream). It is registered as the built directory (`plugins/opencode/dist`) under `plugin`, which both versions load; OpenCode 2 rejects a file path there. Re-running `tokenage setup` migrates an older `dist/index.js` entry.

## API

Useful local endpoints:

```bash
curl http://127.0.0.1:4001/usage?limit=20
curl http://127.0.0.1:4001/usage/summary
curl http://127.0.0.1:4001/usage/daily
curl http://127.0.0.1:4001/usage/high-watermark
curl http://127.0.0.1:4001/config
```

Device status and agent wiring live on the client, not the API:

```bash
tokenage status --json
```

Query params for `/usage`: `limit`, `offset`, `provider`, `model`, `since`, `until`.

Query params for `/usage/daily`: `since`, `until`, `provider`, `model`, `granularity`, `tz_offset`.

## Development

Install/start backend services:

```bash
TOKENAGE_ROOT="$PWD" tokenage server start
```

Run backend tests:

```bash
uv run python -m pytest -q
```

If `uv` is unavailable but the bootstrap-created virtualenv exists, use `./.venv/bin/python -m pytest -q` as a fallback.

Run frontend tests and build:

```bash
cd frontend
npm test
npm run build
```

Maintainer-only bootstrap smoke test:

```bash
bash src/scripts/dev/smoke-bootstrap-container.sh
```

That check runs `src/scripts/bootstrap.sh` in a fresh Docker or Apple `container` environment. It is not part of normal user setup.

## Privacy and security notes

- `tokenage` is intended to run locally.
- Usage is stored in `~/.tokenage/usage.db` by default.
- If you configure `db.url`, usage data is written to that database instead.
- The proxy listens on loopback only and replaces the client's credentials with the provider key from `client.yaml`; the client's own credential headers are never forwarded upstream. Provider keys stay on your machine; only usage metadata goes to the server.
- OTLP payloads are emitted by the agents themselves; review agent telemetry settings if you need strict metadata control.

## Contributing

Issues and PRs are welcome. Good contributions usually include:

- a clear bug report or product problem
- a small, testable change
- backend tests with `pytest` when changing Python behavior
- frontend tests under `frontend/tests/` when changing dashboard behavior
- updated docs when commands, setup, or behavior changes

Please keep examples consistent: plain agent invocations should use `tokenage codex` or `tokenage claude`; reserve `--` for cases where `tokenage` flags are present.

## License

MIT. See [LICENSE](LICENSE).
