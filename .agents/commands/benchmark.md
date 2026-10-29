# Ingest Benchmark

Finds the highest request rate the server sustains for OTLP and proxy ingest on fixed small hardware, branch vs `main`. Ingest only: the dashboard/read API is out of scope.

## Who

Maintainer only. Before suggesting or running anything here, check that the PR author is the repository owner:

```bash
owner=$(gh repo view --json owner -q .owner.login)
[ "$(gh api user -q .login)" = "$owner" ]                            # before the PR exists
[ "$(gh pr view <pr> --json author -q .author.login)" = "$owner" ]   # for an open PR
```

For anyone else, skip this file, even if they have their own GCP setup, and do not ask them to run it. The maintainer benchmarks after merge when needed.

## When

Once per PR, before opening it, and only when the diff changes how much work ingest does per request or per queued row, or the capacity around it:

- a new or changed query, transaction, lock or index on the write path: `src/otlp.py`, `src/ingest_queue.py`, `src/shared_state.py`, `src/recorder.py`, `src/database/` write helpers;
- ingest auth, consumer batching or queue partitioning;
- the DB engine or pool, or server worker, process or thread config.

Skip trivial changes, even in those files: input validation, log or error messages, renames, comments, types, tests, docs, frontend and client-only changes. For example, #134 (a duplicate lookup per OTLP event) needs it; #135 (a token character check) and #126 (a timestamp fallback) do not. When unsure, say which way you lean and why, and let the human decide.

- Never per commit.
- Run load and stress tests only on GCP VMs, never on the dev machine. Local runs are limited to unit tests and short single-process profiles.
- **Suggest it; do not run it.** It creates billable GCP VMs. Tell the human what you'd run and roughly how long (~1 h), and start only after explicit approval. Delete everything afterwards.

## What is measured

`200`/`204` means *queued*; a consumer thread writes to the DB afterwards.

- **Sustained QPS** = highest tested step with all responses 2xx, p95 < 1 s and a flat queue throughout the step.
- **Processing rate** (rows/s): immediately after the highest step, stop sending and sample the drain: Δusage_rows / Δseconds over intervals with a nonempty queue. Queue depth counts events (3 per OTLP turn, 1 per proxy report), not usage rows. If there is no measurable backlog, report only a lower bound from the accepted rate.

## Setup

The maintainer's GCP project and zone (from private local instructions), label `purpose=tokenage-bench`, Debian 12:

| VM | Type | Role |
|---|---|---|
| `bench-pg` | e2-small | PostgreSQL 15 (listen on internal IP, `pg_hba` 10.128.0.0/9, `max_connections=300`) |
| `bench-server` | e2-standard-2 (2 sustained vCPU) | tokenage server, `server.workers` at its default |
| `bench-load` | e2-small | load generator; never on the server VM |

Never compare numbers across different VM types; the e2-micro baseline below is superseded. Keep bench-load at e2-small for the 80 req/s ladder; check generator CPU and scheduling before attributing a limit to the server, and use e2-standard-2 if the generator cannot hold the offered rate.

- Install `main` with `install.sh --server`, then the branch with `TOKENAGE_BRANCH=<branch> sh install.sh --server` (same VM; fresh DB and `$TOKENAGE_HOME/state.db` per run). VMs have no Node: copy a locally built `frontend/dist`. Use `/version` as the health check; verify the commit with `git -C ~/.tokenage/src rev-parse HEAD` on bench-server (versions may match across commits).
- Config: `server.host: 0.0.0.0`, `rate_limits.ingest_per_minute: 10000000` and `rate_limits.api_per_minute: 10000000`, `db.url` → a fresh lowercase database name on `bench-pg` per pass (e.g. `bench_main_1`). Keep the product default fd limit; fd exhaustion, an accept-error log storm and failure to recover are overload signatures.
- Users: 8 ingest tokens (`tokenage server token create --email bench$i@example.test --kind ingest`), one per line in `tokens.txt`. Users map to queue partitions, so one user would test a single partition.

## Load

`.agents/benchmark/loadgen.py` sends an open-loop mix at fixed request rates (it keeps the schedule even when the server slows, so overload shows as latency/errors, not a lower send rate):

- 70% OTLP Claude turns: one request = `tool_decision` + `api_request` + `tool_result` records of one prompt (1 usage row, 1 tool call, 3 queue events).
- 30% proxy usage reports to `/ingest/v1/usage` (1 usage row with 1 tool call).
- Random user per request, random models/token counts, current timestamps, a new session now and then.

For each commit and DB, run **two fresh passes** (first-run noise was ±30%). Use a 20, 40, 60, 80 req/s ladder, 60 s per step; one core locally reached ~40 req/s before auth timeouts (issue #117). Invoke one rate at a time so queue growth can stop the ladder too:

```bash
# bench-server, using its venv; TOKENAGE_HOME and DB_URL refer to this disposable run
python .agents/benchmark/sampler.py --db-url "$DB_URL" > samples.txt
# bench-load, in another terminal; repeat at 40, 60, 80 only while passing
uv run --no-project --with httpx python .agents/benchmark/loadgen.py --url http://<server-ip>:<api_port> --tokens tokens.txt --rates 20 --step 60 --timeout 10
```

Sampler output every 2 s (`--interval`; drains after a passing step can last only seconds) is `unix_ts queue_depth usage_rows`; it reads queue depth directly from `$TOKENAGE_HOME/state.db`. For SQLite pass `sqlite:////absolute/path/to/usage.db` as the DB URL.

Stop at the first failing step (any non-2xx, p95 ≥ 1 s, or growing queue). The loadgen reports `ok` (2xx total) and aborts the remaining schedule once >20% of completed requests have failed (`aborted: true`); in-flight requests still finish subject to `--timeout`. Its multi-rate mode also stops on HTTP/latency failure, but cannot assess queue growth.

After each passing step, sample its immediate drain before advancing. Use the highest passing step's drain for the processing rate; if all four pass, report ≥80 QPS rather than an unmeasured maximum. After **any failed step**, stop the server, preserve logs/samples, replace both the usage DB and `state.db`, then start again; do not reuse a collapsed server or try higher rates. A fresh usage DB also needs fresh users/tokens; restart the sampler after each reset.

Run Postgres, then SQLite (`db.path`, with `db.url` unset). Run main and branch back to back on the same VMs, nothing else running. After a clean drain, usage rows added must equal summed `ok`; preserve mismatches from failed steps as failures rather than claiming successful ingestion.

## Report

In the PR body (Manual QA), main vs branch:

| Metric | DB | main | branch |
|---|---|---|---|
| Sustained QPS | Postgres | | |
| Processing rate (rows/s) | Postgres | | |
| p95 at sustained QPS | Postgres | | |
| Sustained QPS | SQLite | | |

Report both passes and their range; do not infer an improvement within the observed noise. Also say what was the limit (consumer, HTTP side, server CPU, DB CPU). When a PR changes the numbers, update the baseline below in the same PR.

## Baseline

Current e2-standard-2 results (two fresh passes per commit and DB; processing is the drain after the highest passing step). `main` was not run on SQLite at this size; #118 is its closest SQLite baseline.

| Date | Commit | DB | Sustained QPS | Processing rows/s | Limit |
|---|---|---|---|---|---|
| 2026-10-23 | f60fe3a (main) | Postgres | 20 / 20 | ≥20 (no measurable drain) / ≥20 (no measurable drain) | R=40: consumer backlog / R=40: consumer backlog |
| 2026-10-23 | 31589d7 (#121) | Postgres | 20 / 20 | ≥20 (no measurable drain) / ≥20 (no measurable drain) | R=40: consumer backlog / R=40: consumer backlog |
| 2026-10-23 | 38e8d72 (#122) | Postgres | 40 / 40 | ≥40 / ≥40 (no measurable drain) | R=60 consumer backlog; one ReadError in pass 2; PG CPU idle |
| 2026-10-23 | 104f827 (#121+#122) | Postgres | 40 / 40 | ≥40 (no measurable drain) / ≥40 (no measurable drain) | R=60: consumer backlog / R=60: consumer backlog |
| 2026-10-23 | f60fe3a (main) | SQLite | 40 / 40 | ≥40 (no measurable drain) / ≥40 (no measurable drain) | R=60: consumer backlog / R=60: consumer backlog |
| 2026-10-23 | 31589d7 (#121) | SQLite | 40 / 40 | ≥40 (no measurable drain) / ≥40 (no measurable drain) | R=60: consumer backlog / R=60: consumer backlog |
| 2026-10-23 | 38e8d72 (#122) | SQLite | 40 / 40 | ≥40 / ≥40 (no measurable drain) | R=60 consumer backlog; one protocol error in pass 1; backlog write p95 130 / 458 ms |
| 2026-10-23 | 104f827 (#121+#122) | SQLite | 40 / 40 | ≥40 / ≥40 (no measurable drain) | R=60 consumer backlog, concurrent processing 57.10 / 57.35 rows/s; backlog write p95 841 / 445 ms |
| 2026-10-22 | eebf2fc (main) | Postgres | 20 / 20 | 27.9 / 27.4 | consumer backlog at R=40; worker process ~1 core (93%), PG idle, all 2xx |
| 2026-10-22 | 9f036fc (#118) | Postgres | 20 / 20 | 27.5 / 29.6 | consumer backlog at R=40; worker ~1 core, PG idle, all 2xx |
| 2026-10-22 | 5ed82bd (PR 1c) | Postgres | 20 / 20 | 33.1 / 33.7 | consumer backlog at R=40; worker ~1 core, PG idle, all 2xx |
| 2026-10-22 | 9f036fc (#118) | SQLite | 40 / 40 | 74.9 / 77.0 | consumer backlog at R=60 under concurrent ingest, all 2xx |
| 2026-10-22 | 5b5d3d9 (PR 1c) | SQLite | 40 / 40 | 51.5 / 61.3 | consumer backlog at R=60 under concurrent ingest, all 2xx; dashboard write p95 0.11 s |

Older rows below used e2-micro and are **superseded** by the e2-standard-2 procedure; retain them as historical results only.

| Date | Commit | DB | Sustained QPS | Processing rows/s | Limit |
|---|---|---|---|---|---|
| 2026-10-22 | eebf2fc (main) | Postgres | 8-10 (noisy: 10 clean in run 1, p95 2.4 s at 8 in repeat) | >=10 (queue flat at R=10); ~5-6 draining after overload | HTTP side / server CPU (e2-micro, one worker at ~100%); DB idle |
| 2026-10-22 | eba105c (feat/batched-ingest-writes) | Postgres | <8 to 8 (503s at 8 in run 1, p95 1.4 s at 8 in repeat) | ~5-6 draining after overload | HTTP side / server CPU (e2-micro); DB idle |
| 2026-10-22 | eebf2fc (main) | SQLite | 10 | >=10 (queue flat) | HTTP side / server CPU; R=12 fails with 503 |
| 2026-10-22 | eba105c (feat/batched-ingest-writes) | SQLite | 10 | >=10 (queue flat) | HTTP side / server CPU; R=12 fails with 503 |
