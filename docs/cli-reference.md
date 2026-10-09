# tokenage CLI Reference

Reference for the CLI as it exists now. `tokenage summary` and the isolated
tracking mode are gone; this document describes what remains. Per-command
behavior — including exit codes, the banner and the removed commands — is in
[cli-refactor.md](cli-refactor.md).

## Overview

`tokenage` is one command with two components, and a machine can have either
or both. They are installed separately and share only the launcher; the client
never runs from the server clone, even on the same machine.

- **Client** — the tracking wrapper, agent configuration, sign-in, the component
  report. Reports to whichever collector it is pointed at.
- **Server** — the API, the OTLP collector, the proxy, the dashboard, the
  evaluation worker. Reached through `tokenage server ...`.

Anything that is not one of the subcommands below is run as a child command with
usage tracking: the wrapper records the usage high-watermark, runs the child,
then fetches the summary of everything recorded after that watermark. It uses
the collector that is already running — there is no temporary database,
collector or proxy per run, and nothing is started or stopped on your behalf. If
the API is not reachable the child still runs, untracked, and the exit code is
still the child's.

## Usage

```bash
tokenage [options] <command> [args...]
tokenage [options] -- <command> [args...]
tokenage <subcommand> [subcommand options]
tokenage server <command> [args...]
```

The `--` separator is optional for common agent commands. Use it after
tokenage flags when the child command or its first argument could be parsed
as a wrapper option.

## Commands

| Command | Component | Effect |
|---|---|---|
| `tokenage login [--server URL] [--device-name NAME] [--no-browser]` | client | Register this machine and sign in; wires detected agents. Persists an installation key (survives logout) so re-login rotates the machine's tokens instead of duplicating it. Stores the server's `otlp_logs_endpoint`. |
| `tokenage logout [--keep-agents]` | client | Delete this machine's credentials, then un-wire the agents. |
| `tokenage setup [--disable]` | client | Point detected agents at a collector, or take tokenage's telemetry keys back off. |
| `tokenage status [--json]` | client | Report this client: version, sign-in (server URL), detected agents and where they point, and the local proxy URL or failure when the service runs one. `--json` is the device status report the client service sends to the server, plus `account`. Exits 1 when a detected agent points at the wrong collector. The service view is `tokenage server status`. |
| `tokenage client start\|stop\|restart` | client | Install and start the client service under systemd `--user` (Linux) or launchd (macOS), so it also starts at login. `stop` stops it and disables start at login; `start` re-enables. The service runs the local proxy when `client.yaml` has a `proxy:` section; `start` prints its URL or failure, and editing `client.yaml` takes effect on `restart`. Elsewhere these exit 1: run `tokenage client run` under your own supervisor. |
| `tokenage client status [--json]` | client | Ask the OS service manager whether the client service runs (exit 0 running, 1 not), plus its last check and report and the proxy URL (or failure). |
| `tokenage client run` | client | Run the client service in the foreground; the unit/agent executes this. |
| `tokenage update [--check\|--dry-run]` | client | Update the client snapshot from the signed-in server; client availability is not published. The server clone updates with `tokenage server update`. |
| `tokenage check-server --server URL` | client | Internal: verify reachability and wire protocol. Writes nothing. |
| `tokenage <command> [args...]` | client | Run any command with usage tracking. |
| `tokenage server bootstrap` | server | Install, build the dashboard, start, verify, restart the API. |
| `tokenage server start` | server | Turn the services on. |
| `tokenage server stop [program...]` | server | Stop all services, or the named ones. |
| `tokenage server restart [--otlp-port N]` | server | Migrate, then reload the running services. |
| `tokenage server status [program...]` | server | Supervisord table, ports, port check. |
| `tokenage server update [--check\|--dry-run]` | server | Fast-forward the server clone, bootstrap, restart. |
| `tokenage server token create --email EMAIL [--kind cli\|ingest\|web] [--name NAME]` | server | Mint an operator token on the server box. |
| `tokenage server login-link` | server | Print a one-time, 5-minute browser sign-in link for the local owner (local provider). Needed only for browsers that are not on the server machine; a browser on the server machine is signed in automatically. |
| `tokenage --version` | either | Client version, plus the server release when it is installed. |
| `tokenage --help`, `tokenage server --help` | either | Usage for the client and the service commands. |


## Sign-in providers

`auth.provider` in the server's `config.yaml` is `local` (default) or `google`;
an older `auth.enabled: true` still means `google`. Under `local` the server has
one built-in owner (`owner@localhost`): a request that comes straight from the
server machine (loopback peer, no forwarding headers, `localhost`/`127.0.0.1`/`::1`
Host) is that owner with no token, so the dashboard and already-wired agents work
untouched. Anything else needs a token. `tokenage login` works on loopback with no
Google account; for a browser on another machine run `tokenage server login-link`
on the server and open the URL it prints (single use, 5 minutes).

## Examples

```bash
# Track an interactive Codex session
tokenage codex

# Track Claude Code
tokenage claude

# Track a single-shot Codex command
tokenage codex exec "say hello in one sentence"

# JSON summary
tokenage --json -- codex

# Machine-readable summary only (suppress child stdout/stderr)
tokenage --usage-only --json -- codex exec "hello"

# Write summary to a file
tokenage --summary-dest file --summary-file /tmp/llm-summary.json -- claude

# Longer wait for late-arriving telemetry
tokenage --wait-ms 5000 -- codex exec "hello"

# No summary at all
tokenage --no-summary -- codex exec "say hello"

# Sign-in and where agents point
tokenage status
tokenage status --json

# Wire the agents this machine has on PATH
tokenage setup
tokenage setup --disable
```

Status redacts credentials and private paths from displayed endpoints. When the
running API cannot provide collector bind metadata, the OTLP service has state
`unknown` and a null port in JSON; the text view shows `:? unknown` and asks you
to check the collector configuration.
An unknown address alone does not mark the service as down.

## Tracking Flags

All tracking flags go before the child command; use `--` when they do.

| Flag | Default | Description |
|---|---|---|
| `--json` | off | Write the usage summary as a single-line compact JSON object. |
| `--usage-only` | off | Write only the summary to stdout and suppress child stdout/stderr. Implies `--summary-dest stdout`. Cannot combine with `--no-summary`. |
| `--summary-dest` | `stderr` | Where to write the summary: `stdout`, `stderr`, or `file`. |
| `--summary-file` | (none) | Path for `--summary-dest file`. Required when using that mode. |
| `--wait-ms` | `3000` | Milliseconds to poll for usage data after the child exits. |
| `--poll-ms` | `250` | Milliseconds between poll attempts. |
| `--no-summary` | off | Skip the summary; just run the command and return its exit code. |
| `--no-banner` | off | Do not print the tokenage banner. Accepted by every subcommand too. |
| `--version` | — | Print the client version, and the server release when the server component is installed. |

## Tracking Model

One path, in both installation modes:

1. Read the usage high-watermark from the signed-in server
   (`GET /usage/high-watermark`).
2. Run the child command. Nothing is started.
3. Poll `GET /usage/run-summary?after_ts=…` until `--wait-ms` expires. If
   nothing arrived, re-anchor on the current watermark and ask once more, so a
   run that recorded usage just after the deadline is not reported as empty.
4. Print the summary, or the JSON, to `--summary-dest`.

The summary covers all usage visible to the account after the starting
watermark. Concurrent commands and devices are included, and delayed events
outside the timestamp window can be omitted. It does not attribute cost
exclusively to the child command; use dashboard session views for that.
JSON summaries include an `attribution` object with scope
`account_activity_window`, `exclusive_to_command: false`,
`concurrent_runs_included: true`, and `delayed_events_may_be_omitted: true`.

The API being unreachable is not an error: the child runs, the exit code is the
child's, and the wrapper says so on stderr before the child starts and once more
after it.

## Installing

One installer, `install.sh`, installs the components you name:

```bash
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash                # both (default)
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash -s -- --server # server only
curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash -s -- --client # client only (set TOKENAGE_SERVER=URL)
curl -fsSL https://YOUR_SERVER/install.sh | sh                                                         # client for that server
```

`GET /install.sh` on a server serves the same script preset to that server, so
it installs the client. The server install is a clone in `~/.tokenage/src` plus
`src/scripts/bootstrap.sh`, which never starts or configures a client. The client
install is a snapshot and virtualenv under `~/.tokenage/versions` with a
`current` link. Both = server, then client pointed at
`http://127.0.0.1:<api_port>`: interactive sign-in (otherwise the command to run
is printed), then `tokenage client start`, which may fail without being fatal.
Either install may place the launcher first; the client install replaces the
server's symlink with its own copy. A machine whose client used to run from the
server clone has no snapshot: `tokenage` prints the one command that installs it.

## Service Management

```bash
tokenage server status                              # supervisord table, ports, port check
tokenage server status tokenage-api              # one program
tokenage server start                               # services on; refuses if src/pyproject.toml changed
tokenage server stop                                # all services
tokenage server stop tokenage-proxy              # one program
tokenage server restart                             # migrate, then SIGHUP the running services
tokenage server restart --otlp-port 5002            # persist a new OTLP port, then restart the collector
tokenage server bootstrap                           # install, build, start, verify, restart the API
tokenage server token create --email ops@example.com
tokenage server login-link                          # open the printed URL in a remote browser
```

Valid program names: `tokenage-proxy`, `tokenage-api`, `tokenage-otlp`.
`tokenage server <command>` runs `python -m src.cli <command>` (`src/ops.py`);
`bootstrap` goes through `src/scripts/bootstrap.sh` first, because it must run
before the virtualenv exists. `restart`, `stop` and `status` accept program names.

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success, or the command ran and reported what it found. `tokenage status` exits 0 when nothing installed is broken; not being signed in is not a fault. |
| 1 | Something installed is broken, or a check failed: a stopped service, a detected agent pointing at another collector, `setup` with no collector or nothing wireable, a failed sign-in or update, `logout` with no credentials. |
| 2 | Argument validation error, a removed command (`summary`), or an unknown `server` subcommand. |
| 126 | Child command is not executable. |
| 127 | Child command not found. |
| 128+N | Child killed by signal N. |
| Other | The child command's own exit code. |

## Configuration

Server config lives at `~/.tokenage/config.yaml` (override the path with
`TOKENAGE_CONFIG`). A template is provided at `config.example.yaml`. The
client reads only the `server:` section, so a config written by a different
version cannot break it.

```yaml
models:
  gpt-5.4:
    cost:
      input: 2.5        # USD per million input tokens
      output: 15.0       # USD per million output tokens
      cacheRead: 0.25    # USD per million cached input tokens

providers:
  my-provider:
    base_url: https://api.example.com/v1
    models:
      gpt-5.4: {}

server:
  host: 127.0.0.1
  port: 4000        # Proxy port
  api_port: 4001    # API port
  otlp_port: 4002   # OTLP collector port

db:
  path: ~/.tokenage/usage.db   # SQLite (default)
  # url: postgresql+psycopg://user:pass@host:5432/db
```

`tokenage server start` merges missing defaults from `config.example.yaml`
into the user config without overwriting existing values. `tokenage server
restart` does not touch config at all, except to persist `--otlp-port`.

Client state, all under `$TOKENAGE_HOME` (default `~/.tokenage`):

| Path | Owner | Contents |
|---|---|---|
| `current` | client | Symlink to the active client source snapshot. |
| `versions/` | client | Previous client snapshots. |
| `credentials.json` | client | Signed-in identity, CLI and ingest tokens, `otlp_logs_endpoint`. Mode `0600`. |
| `config.yaml` | server | Server config. |
| `supervisord.conf` | server | Written by `server start`. |
| `run/` | server | supervisord pid and socket. |

## Environment Variables

| Variable | Description |
|---|---|
| `TOKENAGE_HOME` | Override the tracker home directory (default `~/.tokenage`). |
| `TOKENAGE_ROOT` | Path to the server checkout. Overrides `$TOKENAGE_HOME/src`; used by worktrees and tests. |
| `TOKENAGE_CONFIG` | Override the config file path. |
| `TOKENAGE_BIN_DIR` | Where the launcher is installed (default `~/.local/bin`). |
| `TOKENAGE_CLIENT_COMMIT` | Commit to report for the client, when the snapshot has no `COMMIT` file. |
| `TOKENAGE_SKIP_BANNER` | Set by the launcher so a script it calls does not print a second banner. `bootstrap.sh` and `start.sh` honor it; `restart.sh`, `status.sh` and `update.sh` do not. |
| `TOKENAGE_SKIP_INSTALL` | `1` makes `bootstrap` skip dependency installation and record the requirements stamp anyway. |
| `TOKENAGE_SERVER` | Fallback server URL for `tokenage login` when `--server` is absent; passed by `tokenage update` to `install.sh`, which reads it instead of its baked-in server URL. |
| `TOKENAGE_DB_URL` | Override the database URL at runtime (server side). |
| `TOKENAGE_API_URL` | Frontend-only: override the API base URL used by the Vite dev server. |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | The agent telemetry endpoint. `tokenage setup` strips any pre-existing value before writing its own. |
| `NO_COLOR` | No color. The banner still prints. |

## API Endpoints

The API service runs at `http://127.0.0.1:4001` by default, and at the
signed-in server's origin on a client-only machine.

```bash
curl http://127.0.0.1:4001/usage?limit=20
curl http://127.0.0.1:4001/usage/summary
curl http://127.0.0.1:4001/usage/daily
curl http://127.0.0.1:4001/usage/high-watermark
curl http://127.0.0.1:4001/usage/run-summary?after_ts=0
curl http://127.0.0.1:4001/config
curl http://127.0.0.1:4001/version
```

Device status and agent wiring come from the client service, not the API:

```bash
tokenage status --json
```

`/version` is public, and carries the wire protocol range,
the server release, and `otlp_logs_endpoint` — the collector clients point agents
at. Its `collector_bind` host/port fields describe server-local listener
addresses for status diagnostics; they are not hosted client wiring targets.
No URL credentials or paths are included in those bind fields. Authenticated
ingestion still requires its token. A client that signed in before the endpoint
was published reads it once from here and stores it in `credentials.json` for
later agent endpoint checks. Status also queries the local API for bind metadata
when the server component is installed.

Query params for `/usage`: `limit`, `offset`, `provider`, `model`, `since`, `until`.

Query params for `/usage/daily`: `since`, `until`, `provider`, `model`, `granularity`, `tz_offset`.

Query params for `/usage/run-summary`: `after_ts`, `until_ts`, `since`, `until`,
`client_source`, `session_id`, `provider`, `model`, `include_rows`. The wrapper
sends only `after_ts`, plus `until_ts` on the re-anchor pass.

## Helper Commands

| Command | Purpose |
|---|---|
| `python -m src.cli sync-config` | Merge missing defaults from `config.example.yaml` into the user config. Run by `server start` and the Docker entrypoint. |
| `python -m src.cli migrate` | Apply database schema migrations (`TOKENAGE_DB_URL` overrides the target). Run by `server start`, `server restart`, Docker and the dev scripts. |
| `src/scripts/check-otlp-ready.py` | Pre-commit hook: the collector answers and detected agents are wired. |
