# tokenage CLI: user-facing behavior

Status: implemented. One `tokenage` command is the entry point on every
machine; what it can do depends on which components are installed.

Every human-facing command prints the tokenage banner first — see
[Banner](#banner). The sample output below omits it.

| Command | Component | Effect |
| --- | --- | --- |
| `tokenage login` | client | Sign in to a server and wire detected agents. |
| `tokenage logout` | client | Remove this machine's credentials. |
| `tokenage setup` | client | Point detected agents at a collector. No auth, no service control. |
| `tokenage status` | either | Report installed components and whether they run. |
| `tokenage update` | either | Update whichever components are installed. |
| `tokenage <command> ...` | client | Run any command with usage tracking and print the run summary. |
| `tokenage server start` | server | Turn the services on. Nothing else. |
| `tokenage server stop` | server | Stop services. |
| `tokenage server restart` | server | Reload running services so new backend code takes effect. |
| `tokenage server bootstrap` | server | Install, build the dashboard, start, verify. |
| `tokenage server status` | server | Supervisord table, ports, port check. |
| `tokenage server token create` | server | Mint an operator token. |

`tokenage check-server --server URL` also exists and is internal: the
installer uses it to refuse a box whose wire protocol this client cannot speak.
It writes nothing.

Two components, one command:

- **Client** (`client/`) — the tracking wrapper, agent configuration, sign-in and
  the component report. Owns `$TOKENAGE_HOME/credentials.json`, mode `0600`.
  Depends only on `httpx` and `pyyaml`,
  and never imports `src`.
- **Server** (`src/`, `scripts/`) — the API, the OTLP collector, the
  dashboard, pricing, storage and the evaluation worker. Owns
  `~/.tokenage/config.yaml`, `supervisord.conf` and the database.

An all-in-one install has both. A remote machine installs only the client, and
every `tokenage server ...` command reports that the server component is not
installed instead of failing obscurely.

The launcher `scripts/tokenage` is the single entry point both installers
write. It resolves the two components separately: the client from
`$TOKENAGE_HOME/current` (a source snapshot with its own virtualenv) and the
server from `$TOKENAGE_ROOT` or `$TOKENAGE_HOME/src` (a git clone).
An explicit `$TOKENAGE_ROOT` selects that checkout's client; otherwise the
snapshot wins, followed by the server checkout. Client code, its interpreter,
and version metadata all come from the selected source. An incomplete snapshot
must be repaired by reinstalling it.

Server startup no longer edits user agent settings. The client owns agent
configuration in both installation modes, via `setup` and `login`.

---

## Banner

Every command starts by printing the banner, so a user always knows which tool is
talking to them — including on a client-only machine that has no server at all.
It is decoration, so it goes to stderr and disappears whenever the output is for
a machine.

```
$ tokenage status
  ── tokenage ──

  tokenage 0.1.0 (client 89075ce) · server 0.2.19 (self-hosted)
  ...
```

Rules:

- **Compact wordmark on every terminal.** `banner()` in
  `scripts/lib/terminal.sh` prints `── tokenage ──`.
- **Always on stderr, never stdout.** A tracked child command's stdout stays
  exactly what the child wrote, and the TTY test is on stderr because that is
  the stream the banner goes to. (The color test in `terminal.sh` is still on
  stdout.)
- **The launcher prints it, and the scripts it calls are told not to.**
  `TOKENAGE_SKIP_BANNER=1` is exported, and `bootstrap.sh` and `start.sh`
  honor it, so `tokenage server bootstrap` prints one banner rather than
  three. `restart.sh`, `status.sh` and `update.sh` print their own banner
  unconditionally, so those three commands show it twice.
- **Suppressed when the output is for a machine**: stderr is not a TTY, or the
  arguments contain `--json`, `--usage-only`, `--no-banner`, or
  `--summary-dest` with the value `file` or `stdout`. Structured output stays
  parseable.
- **`--no-banner` turns it off for one command.** It is accepted by every
  subcommand as well as the tracking wrapper. Without it, `tokenage claude`
  prints the compact banner before the interactive TUI on every launch.
- **Colors follow the existing rule**: colored on a TTY, plain when piped,
  plain under `NO_COLOR`. The banner itself still prints under `NO_COLOR` —
  that flag has always meant "no color", not "no banner".
- **`banner()` lives in `scripts/lib/terminal.sh`.** The client has no banner of
  its own. The launcher sources that file from the server clone, or from the
  client snapshot when there is no server component, and prints nothing when
  neither has it.

---

## `tokenage status`

Answers three questions in one screen: what is installed, is it running, and
where are my agents pointed. It never fails because something is *absent* — only
because something installed is broken. It is not an alias for anything; the old
service view is `tokenage server status`.

### Layout

Line 1 is versions. Every other line is `label` + value, label padded to 12
columns, in this order, each printed only when it applies:

1. versions
2. `services` — server component installed
3. `account` — always; either the signed-in identity or `not signed in`
4. `device` — signed in, and no server component on this machine
5. `agents` — always
6. `dashboard` — server component installed
7. `fix` — only when something is wrong, naming the command that repairs it

### All-in-one install, everything up

```
$ tokenage status
tokenage 0.1.0 (client 89075ce) · server 0.2.19 (self-hosted)
  services    api :4001 up · otlp :4002 up
  account     signed in as you@example.com → http://localhost:4001
  agents      codex, claude, opencode → http://localhost:4002/v1/logs
  dashboard   http://localhost:4001
```

The `agents` line lists only the detected agents that point where this install
intends, and the first one's endpoint stands for all of them. It reads
`none detected` when no tracked agent is on `PATH`, and `none wired` when
agents are detected but none points at the intended collector.

### All-in-one install, services stopped

```
$ tokenage status
tokenage 0.1.0 (client 89075ce) · server 0.2.19 (self-hosted)
  services    api :4001 down · otlp :4002 down
  account     signed in as you@example.com → http://localhost:4001
  agents      codex, claude, opencode → http://localhost:4002/v1/logs (not reachable)
  dashboard   http://localhost:4001
  fix         run tokenage server start
```

Exit code 1. `(not reachable)` appears when a service is down and there is
still something wired to show.

### Client-only install, signed in

```
$ tokenage status
tokenage 0.1.0 (client 89075ce)
  account     signed in as you@example.com → https://app.example.com
  device      hanbo-macbook
  agents      codex, claude → https://app.example.com/v1/logs
```

No `services` and no `dashboard` line: there is no server on this machine. The
`device` line is the mirror image of that — it is shown only on a client-only
install, where the machine identity is the useful fact.

### Client-only install, not signed in

```
$ tokenage status
tokenage 0.1.0 (client 89075ce)
  account     not signed in
  agents      none wired
  fix         run tokenage login --server <url>
```

Exit code 0. Not being signed in is a fact, not a fault.

### `fix` precedence

Only one `fix` line is printed, and the first of these that applies wins:

1. a detected agent is not wired → `run tokenage setup`
2. the server component is installed and a service is not up →
   `run tokenage server start`
3. no server component and not signed in →
   `run tokenage login --server <url>`

### `--json`

One compact line, same fields, for scripts:

```
$ tokenage status --json
{"client":{"version":"0.1.0","commit":"89075ce1f4b7d2e6a9c0b3d8e5f2a7c1b4d90e33"},"server":{"installed":true,"version":"0.2.19","mode":"self-hosted","services":[{"name":"api","port":4001,"state":"up","supervised":true},{"name":"otlp","port":4002,"state":"up","supervised":true}]},"account":{"signed_in":true,"email":"you@example.com","server_url":"http://localhost:4001","device_name":"hanbo-macbook"},"agents":[{"name":"codex","installed":true,"detected":true,"configured":true,"endpoint_matches":true,"endpoint":"http://localhost:4002/v1/logs"},{"name":"claude","installed":true,"detected":true,"configured":true,"endpoint_matches":true,"endpoint":"http://localhost:4002/v1/logs"},{"name":"opencode","installed":true,"detected":true,"configured":true,"endpoint_matches":true,"endpoint":"http://localhost:4002/v1/logs"},{"name":"kilo","installed":true,"detected":false,"configured":false,"endpoint_matches":false,"endpoint":null}],"dashboard":"http://localhost:4001"}
```

Field notes:

- `client.commit` is a 40-character git SHA or `null`; it is `null` for a
  snapshot with no `COMMIT` file and no `TOKENAGE_CLIENT_COMMIT`.
- `server` is `{"installed": false}` and nothing else when the server component
  is absent, and carries `version`, `mode` and `services` when it is present.
- `service.supervised` is `true`/`false` from `supervisorctl`, or `null` when
  supervisord cannot be asked at all (no `supervisord.conf`, no `supervisorctl`).
- `account.device_name` is present only when the credentials carry one; an
  absent account is `{"signed_in": false, "email": null, "server_url": null}`.
- `agents` is always all four agents — `codex`, `claude`, `opencode`, `kilo` —
  in that order, with `installed` (tokenage ships a configure script for it),
  `detected` (found on `PATH`), `configured` (its config file has the keys),
  `endpoint_matches` and `endpoint` (what it actually points at, `null` when
  unconfigured). `dashboard` is the local API URL, or `null` with no server
  component.

### Rules

- Reads state only. Creates no files, starts no services.
- Works with no `config.yaml`, no `supervisord.conf`, no `credentials.json` — the
  normal case on a fresh client-only machine.
- "up" for a service means supervisord reports `RUNNING` **and** the port
  accepts a connection. Supervisord alone is not enough.
- "wired" for an agent means the endpoint in the agent's own config file equals
  the endpoint this install intends — the same comparison
  `GET /local/setup-health` already makes, so `status` and the dashboard cannot
  disagree.

---

## Start, restart, bootstrap

The three service commands are separated by *what you changed*, not by which
script grew where. Each does one job, and running the wrong one is cheap.

| After you… | Run | Cost |
| --- | --- | --- |
| reboot the machine | `tokenage server start` | seconds |
| edit backend Python | `tokenage server restart` | instant |
| change `requirements.txt`, pull new code, edit the frontend | `tokenage server bootstrap` | minutes |
| install a new agent, or point agents at a different collector | `tokenage setup` | seconds |

`start` is what runs at boot, so it is deliberately the dullest: it turns
services on and nothing else. `restart` is the hammer for code edits, so it does
nothing but reload processes — no installs, no builds, no config writes. You can
run it fifty times an hour without thinking about it. `bootstrap` is the heavy
one: install, build, start, verify.

## `tokenage server start`

Turns the services on. Idempotent — safe to run when they are already up.

It creates `config.yaml` from `config.example.yaml` if absent, syncs missing
defaults into it, checks the configured ports (auto-assigning on a first run
only), applies schema migrations, and starts supervisord and the three
programs. If supervisord is already running it reloads rather than starting a
second one.

```
$ tokenage server start
  ✓ Dependencies up to date
  ✓ Config exists: /home/you/.tokenage/config.yaml
  ✓ Port check passed
  Applying schema migrations...
  ✓ Migrations applied
  ✓ Supervisord started
  ✓ tokenage-api: running
  ✓ tokenage-otlp: running

  ───────────────────────────────────────────
  🚀 tokenage is LIVE → http://localhost:4001
```

What it deliberately does **not** do:

- **No dependency install.** If `requirements.txt` changed since the last
  install, it refuses with the command to run:

  ```
  ✗ Dependencies are out of date (requirements.txt changed)
    run tokenage server bootstrap
  ```

  The check is the `requirements.txt` hash stamp already written to
  `.venv/.requirements.sha256`, so it costs one `sha256sum`.
- **No frontend build.** That is `bootstrap`.
- **No agent configuration.** That is `setup`, in both installation modes. Agent
  wiring is never edited by the server.
- **No API restart because `frontend/dist` exists.** Restarting the API to pick
  up a new dashboard build belongs to `bootstrap`, which is the only command
  that builds.

Flags: none. It takes no program names; it manages all three services as a set,
and reloading a single program is not something this command can express.

Fails with `Port check failed` and a table of conflicting ports when a configured
port is held by a process supervisord does not own.

## `tokenage server stop`

```
$ tokenage server stop
  Stopping all programs...
  ✓ All services stopped
```

With program names, stops only those:

```
$ tokenage server stop tokenage-otlp
  Stopping tokenage-otlp...
  ✓ tokenage-otlp: stopped
```

Valid names: `tokenage-api`, `tokenage-otlp`.
Prints `Not running.` and exits 0 when `supervisord.conf` is absent.

## `tokenage server restart`

Reloads the running services so new backend code takes effect. Nothing else.

It refuses when nothing is running, applies pending schema migrations, sends
`SIGHUP` to each *running* service, and leaves stopped services stopped, naming
them and pointing at `tokenage server start`. Migrations stay in here on
purpose: they are idempotent and take milliseconds, and dropping them would mean
`git pull` followed by `restart` serves code against an old schema.

```
$ tokenage server restart
  ▶ Pre-flight checks
  ...

  ▶ Applying schema migrations
  ✓ Migrations applied

  ▶ Reloading services
  Sending SIGHUP to tokenage-api...
  ✓ tokenage-api: reloaded
  Sending SIGHUP to tokenage-otlp...
  ✓ tokenage-otlp: reloaded

  ───────────────────────────────────────────
  🚀 tokenage is LIVE → http://localhost:4001
```

What it deliberately does **not** do — all of this is `start` or `bootstrap`
and does not belong in a code reload:

- No dependency install, no frontend build.
- No config sync and no port check.
- No agent configuration.
- It never turns a stopped service on.

`--otlp-port N` persists the new port in `config.yaml` and fully restarts the
collector instead of reloading it, because the port is baked into the process.
This is the only flag `restart` takes, and the only one that writes config, since
the value has to be persisted before the collector can be reloaded with it.

## `tokenage server status`

The old `tokenage status`: the supervisord table, the configured ports, and
the port check. For operators who want only the service view.

```
$ tokenage server status
tokenage-api      RUNNING   pid 21456, uptime 3:11:02
tokenage-otlp     RUNNING   pid 21457, uptime 3:11:02

  ▶ Port Information
    API:   127.0.0.1:4001
    OTLP:  127.0.0.1:4002

  ▶ Port Check
```

Accepts optional program names. Exit code 1 when no `supervisord.conf` exists.

## `tokenage server bootstrap`

Installs, builds, starts, verifies. The command for "I changed dependencies, the
frontend, or pulled new code".

It installs uv and the virtualenv, runs `uv pip install -r requirements.txt`,
builds the dashboard with npm, (re)creates the CLI symlink, then runs
`server start` and verifies the result. Exit code 0 when every check passes, 1
otherwise.

```
$ tokenage server bootstrap
  ▶ Installing dependencies & CLI
  Setting up tokenage environment...
  Creating venv...
  Installing dependencies...
  Building frontend (Node v22.14.0)...
  Frontend built: /home/you/.tokenage/src/frontend/dist
  Setting up CLI symlink...
  Installation complete! You can now use 'tokenage' (if in PATH) or 'scripts/start.sh'.

  ▶ Starting services
  ...

  ▶ Running post-start checks
  ✓ Config: /home/you/.tokenage/config.yaml
  ✓ CLI wrapper: scripts/tokenage
  ✓ CLI symlink: /home/you/.local/bin/tokenage
  ✓ API running: http://localhost:4001
  ✓ OTLP listening: http://localhost:4001
  ✓ Dashboard: http://localhost:4001

  ▶ Verifying agent tracking
  ✓ Claude: skipped
  ✓ Codex: ready
  ✓ OpenCode: ready
  ✓ Kilo: skipped
  ✓ Agents: 2 ready, 2 skipped, 0 failed

  ───────────────────────────────────────────
  🚀 tokenage is LIVE → http://localhost:4001
```

It ends with a restart of the API when `frontend/dist` exists, because a freshly
built bundle is not served by an already-running process: the mount happens at
import time in `src/api.py`, so a process that started before the first build has
no dashboard at all. `start` and `restart` never do this; only the command that
builds needs to.


An agent that is installed but not wired fails the check:

```
  ✗ Codex: OTLP not configured
  ✗ Agents: 0 ready, 3 skipped, 1 failed

  ⚠  tokenage started with 1 issue(s) → http://localhost:4001
```

`tokenage status` on the same machine names the repair:

```
  fix         run tokenage setup
```

Skipped with a warning, never failed, when the tool is missing: no `uv`
(downloaded), no Node ≥ 18 (frontend not built, no dashboard), no npm.

## `tokenage server token create`

Operator command, runs on the server box, talks directly to the database. Not
available to clients, and never over the network API.

```
$ tokenage server token create --email ops@example.com --kind ingest --name nas-01
Minted ingest token for ops@example.com (shown once):
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

`--email` is required, `--kind` is `cli` (default), `ingest` or `web`, and
`--name` labels the token with a device name. Exit 2 with the validation message
when the email is missing or the user cannot be created. This is the only
operator command left in `src/cli.py`.

## `tokenage login`

Authenticates this machine and wires detected agents at the server's OTLP
collector. Credentials are written to `~/.tokenage/credentials.json` with mode
`0600`, and the device receives its own CLI and ingestion tokens plus the
server's `otlp.logs_endpoint`, stored as `otlp_logs_endpoint`. That stored
endpoint is what later `setup` and `status` calls use as the intended collector,
so a client-only machine needs no local config at all.

```
$ tokenage login --server https://app.example.com
Open this URL in your browser to continue login:
  https://app.example.com/auth/cli/start?code_challenge=Ab3...&device_name=hanbo-macbook
Paste the code shown in your browser: A1B2-C3D4
Logged in as you@example.com (device: hanbo-macbook)
Credentials saved to /home/you/.tokenage/credentials.json
Dashboard: https://app.example.com
Wired agents: codex, claude, opencode
```

- Refuses non-HTTPS servers, except HTTP loopback origins (`localhost`, `127.0.0.1`, and `[::1]`),
  and reduces the URL to a bare origin. Without `--server` and without
  `$TOKENAGE_SERVER` or a previous sign-in, it exits 2 with
  `server URL required: use login --server URL`.
- Checks `GET /version` and the protocol generation first, and prints nothing
  when that succeeds; a client the server cannot talk to exits 1 before any
  credential is touched.
- Sends the one-time code and PKCE verifier to the existing server exchange.
  A failed exchange leaves the old credentials file in place; the server may
  already have rotated its tokens. Stable device IDs and client version
  reporting are deferred until the server implements those contracts.
- No browser is opened under `SSH_CONNECTION`/`SSH_TTY`, or with
  `--no-browser`. The URL is always printed.
- Agent wiring failures are warnings, not failures, and the exit code stays 0:

  ```
  warning: wiring kilo failed: npm install exited 1
  Logged in as you@example.com (device: hanbo-macbook)
  Wired agents: codex, claude
  ```

- With no tracked agent installed: `No tracked agents detected; nothing to wire.`

Flags: `--server URL`, `--device-name NAME`, `--no-browser`, `--no-banner`.

## `tokenage setup`

Points detected agents at a collector. Idempotent, no auth, no service control,
no dependency install. This is what replaced agent configuration inside
`server start` and `server restart`.

```
$ tokenage setup
  collector   https://app.example.com/v1/logs
  wired       codex, claude, opencode
```

Where the collector comes from, in order:

1. What `tokenage login` recorded in `credentials.json`.
2. A signed-in server that signed this machine in before it recorded one: `setup`
   reads `GET /version` once, takes its `otlp_logs_endpoint`, and stores it. The
   OTLP port is usually not the API port, so a remote server's collector cannot
   be derived locally.
3. The local collector, when this machine has the server component and is not
   signed in to anything else.

A machine signed in to a remote server never falls back to the local collector.
`tokenage status` reads only what is stored, so it stays offline and fast.

When the collector is unknown — a machine that signed in before its server
published one — `status` says `(target unknown)` and exits 0. Not knowing where
an agent points is not the same as it pointing somewhere wrong, and the
distinction shows in the exit code:

```
  agents      claude → https://app.example.com/v1/logs (target unknown)
  fix         run tokenage setup
```

`setup --disable` and `tokenage logout` use the recorded value, so a
hand-written collector is reported and left alone.

Re-run after moving the collector, changing its port, or installing a new agent.

There is no `--server` flag — the collector is discovered, never typed, because
the OTLP port is not the API port and typing it is how agents end up aimed at the
wrong server. With no collector to be found, the command prints one line and
exits 1:

```
No collector to wire agents to. Sign in with tokenage login, or install the server component.
```

With no tracked agent on `PATH` it prints
`No tracked agents detected; nothing to wire.` and exits 0. When agents are found
but none could be wired, it prints `no agents could be wired` and exits 1.
Login also exits 1 if agents were detected but none could be wired; credentials
remain saved and the diagnostic directs the user to run setup after repairing
the agent configuration.

Flags: `--disable`, `--no-banner`.

`--disable` takes tokenage's telemetry settings back off the agents it
manages:

```
$ tokenage setup --disable
  un-wired  codex, claude
```

or `No matching telemetry removed; agent settings left unchanged.` when there
was nothing to remove. Only settings for the known collector are touched;
plugin entries must match both their tracker path and collector endpoint.
If the collector is unknown, settings are left unchanged and setup exits 1.
Helper failures also cause exit 1, even if another agent was removed successfully.
Otherwise the agent is reported and nothing is changed:

```
  claude     points at another collector, left alone
```

## `tokenage logout`

Removes this machine's credentials and un-wires the agents. The previous server
collector is read out of the credentials *before* they are deleted, so the
`--keep-agents` warning can still name what this machine was pointed at.

```
$ tokenage logout
  removed   /home/you/.tokenage/credentials.json
  un-wired  codex, claude, opencode
Signed out. The device stays listed in Settings → Devices until you remove it.
```

Nothing is sent to the server: `logout` only removes the local credentials file
and the agent configuration.

If older credentials have no recorded collector, logout removes the credentials
and warns that agent settings were left unchanged. Cleanup failures cause exit 1
after sign-out, with a diagnostic naming the affected agents.

With `--keep-agents`, credentials are removed and the agent configs are left
alone; a warning on stderr says telemetry will be rejected, naming the server it
was pointed at:

```
  removed   /home/you/.tokenage/credentials.json
  warning   agents still point at the last configured collector (https://app.example.com/v1/logs) and will be rejected
```

With nothing to remove it prints `Not signed in; nothing to remove.` and exits
1. Flags: `--keep-agents`, `--no-banner`.

## `tokenage update`

Updates whichever components are installed, client first, stopping at the first
failure.

- **Server** — `scripts/update.sh`: fetch, fast-forward pull, then `bootstrap`,
  which installs, builds and restarts the services as its last steps. Refuses to
  run with a dirty worktree, detached HEAD, or no upstream.
- **Client** — the client is a source snapshot, so it updates by downloading the
  installer from the signed-in server's `GET /install.sh` and running it with
  `TOKENAGE_SERVER` and `TOKENAGE_BIN_DIR` set. That is the only way to
  install a new snapshot, so there is no separate release resolution here.
  The updater skips sign-in and runs `tokenage setup` with the existing
  credentials so agent configuration points at the updated snapshot. Without a
  signed-in server there is nothing to fetch the installer from and the command exits 1:
  `tokenage: cannot update the client without a server; run tokenage login --server <url> first.`

```
$ tokenage update
  ▶ Installed components
  Installed server  0.2.19  (/home/you/.tokenage/src)
  Installed client  0.1.0  (commit 89075ce1f4b7d2e6a9c0b3d8e5f2a7c1b4d90e33)

  ▶ Updating client
  ✓ client updated
  ✓ credentials preserved

  ▶ Updating server
  ...

  ✓ tokenage is up to date
```

Client-only install:

```
$ tokenage update
  ▶ Installed components
  Installed client  0.1.0  (commit 89075ce1f4b7d2e6a9c0b3d8e5f2a7c1b4d90e33)

  ▶ Updating client
  ...
```

With neither component installed it prints
`tokenage: nothing to update on this machine.` and exits 0.

Flags: `--check` runs the server's real update check when the server is
installed. The hosted installer does not publish a client version, so the
client's update availability cannot be checked and is reported as unknown;
`--check` does not claim the client is up to date. `--dry-run` prints the
planned commands, and `--scope all|client|server` limits the work (default
`all`).

```
$ tokenage update --dry-run
  ▶ Installed components
  Installed server  0.2.19  (/home/you/.tokenage/src)
  Installed client  0.1.0  (commit 89075ce1f4b7d2e6a9c0b3d8e5f2a7c1b4d90e33)

  Planned commands:
  bash /home/you/.tokenage/src/scripts/update.sh
  sh </install.sh from the signed-in server>
```

## `tokenage <command> ...`

Runs any command with usage tracking and prints the run summary to stderr, so
piping the child's stdout stays clean.

Tracking uses the services that are already running. The wrapper reads the usage
high-watermark from the API, runs the child, then fetches the summary for
everything recorded after that watermark. It starts nothing and stops nothing,
The API is the signed-in server when there is one, and
the local server otherwise.

```
$ tokenage codex exec "say hello in one sentence"
...child output...
tokenage usage summary (account window)
Concurrent runs are included; delayed events may fall outside this window.
requests: 1, total tokens: 12,431, cached: 11,904 (96%)
latency avg: 1.42s, ttft avg: 380ms, cost: $0.0187

sessions:
  01JQ8F2K9W3N  1 req  12,431 tok  96% cached  $0.0187
```

A run that recorded nothing:

```
No tokenage usage recorded after the starting watermark.
Concurrent runs are included; delayed events may fall outside this window.
```

**If the API is not reachable, the command still runs, untracked.** It never
starts a service on your behalf, and the child is not held up waiting:

```
$ tokenage codex exec "say hello"
...child output...
tokenage API unavailable before command start: [Errno 111] Connection refused
No summary could be produced.
```

The exit code is still the child's, so a wrapper script behaves the same either
way. Only the summary is missing. With `--no-summary` the same situation prints
just the first line.

Flags: `--json`, `--usage-only`, `--summary-dest stdout|stderr|file`,
`--summary-file`, `--wait-ms`, `--poll-ms`, `--no-summary`,
`--no-banner`. Use `--` before the child command when tokenage flags come
first. `--summary-dest file` without `--summary-file`, and `--usage-only`
together with `--no-summary`, are argument errors and exit 2.

This is an account usage window, not exclusive attribution to the child command.
Concurrent runs under the same account are included. Event timestamps bound the
query, so delayed events outside the watermark window can be omitted. Use the
dashboard's session views for individual sessions.

JSON summaries carry an `attribution` object identifying this account activity
window and its concurrency and delayed-event limitations.

## `tokenage --version` / `--help`

```
$ tokenage --version
tokenage 0.1.0 (client 89075ce)
```

With the server component installed, `--version` adds the server release:

```
$ tokenage --version
tokenage 0.1.0 (client 89075ce) · server 0.2.19
```

The two versions are independent. `client/VERSION` is the client snapshot's
version and root `VERSION` is the server release; the bump workflow raises the
patch of whichever of them the changed files belong to. The commit shown is the
client snapshot's `COMMIT`, or the server clone's `HEAD` on an all-in-one
install.

---

## Legacy aliases

Kept during migration, forwarding to the server commands. Each prints one notice
on stderr:

```
$ tokenage start
note: "tokenage start" is now "tokenage server start"
...
```

| Old | New |
| --- | --- |
| `tokenage bootstrap` | `tokenage server bootstrap` |
| `tokenage start` | `tokenage server start` |
| `tokenage stop` | `tokenage server stop` |
| `tokenage restart` | `tokenage server restart` |
| `tokenage token create` | `tokenage server token create` |

`status` is not an alias — it means the component report. The old service view is
`tokenage server status`, and it prints the same text as before.

The aliases are removed in the next major version of the server release.

## Removed commands

`tokenage summary` is gone. Evaluations run in the background through the
server's job API, and the result is on the dashboard. The spelling is refused
with exit 2 so a script that still calls it fails loudly instead of being run as
a tracked child command named `summary`:

```
$ tokenage summary 01JQ8F2K9W3N
tokenage: 'summary' is no longer a command. Evaluations now run in the background; see the dashboard.
$ echo $?
2
```

Isolated tracking is gone with it. There is no temporary database, no temporary
collector, no temporary proxy and no merge step: every run reports to the
collector that is already installed, in both installation modes.

## When a component is missing

```
$ tokenage server start
tokenage: the server component is not installed on this machine.
This machine has the client only, which reports to a remote server.
To run a local server, install the all-in-one release:
  curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | bash
```

Exit code 1. Every `tokenage server ...` command behaves this way, so the
failure names the missing component instead of surfacing a missing file or a
missing virtualenv.

The mirror image, for a launcher with no client to run:

```
$ tokenage status
tokenage: the client component is not installed on this machine.
Install it with:
  curl -fsSL https://YOUR_SERVER/install.sh | sh
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, or the command ran and reported what it found. `tokenage status` exits 0 when nothing installed is broken — not being signed in is not a fault. |
| 1 | Something installed is broken or a check failed: a stopped service, a detected agent pointing at another collector, an unwired-or-unwireable `setup`, a failed sign-in or update, or `logout` with no credentials. |
| 2 | Argument validation error, including a removed command and an unknown `server` subcommand. |
| 126 | The tracked child command is not executable. |
| 127 | The tracked child command was not found. |
| 128+N | The tracked child command was killed by signal N. |
| other | The tracked child command's own exit code. |
