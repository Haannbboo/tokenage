# tokenage Project Rules

This project tracks LLM usage via an OTLP collector and a loopback proxy in the client. Its core risk is obvious: it sits near prompts, responses, auth headers, token counts, cost data, and provider-specific weirdness. Don't be sloppy.

## Non-negotiables

- Never commit secrets: API keys, provider tokens, auth headers, cookies, passwords, connection strings.
- Never store or log raw prompts/responses/request bodies by default unless explicitly configured and approved.
- Never expose private local agent memory in public repo files.
- Never assume runtime ports. Read `~/.tokenage/config.yaml`.
- Never assume local SQLite. DB may be remote Postgres/Supabase.
- Never change schema/cost/provider behavior without tests.

## Privacy and trace data

Default posture: collect the minimum useful metadata.

Sensitive by default:

- Raw prompt text
- Raw completion text
- Full request/response bodies
- Tool arguments/results
- Auth headers and cookies
- Provider API keys and organization IDs
- User-local file paths when not needed for debugging

If a feature needs any of this:

1. Make it opt-in.
2. Document the setting.
3. Add tests proving default behavior does not capture it.
4. Scrub logs and errors.

## Provider adapters

Provider-specific behavior belongs behind adapters or narrow normalization functions.

Watch for:

- Different model name formats.
- Streaming chunks with partial content.
- Tool-call chunks arriving across multiple events.
- Missing usage fields.
- Reasoning tokens vs output tokens.
- Cache read/write token fields.
- Provider aliases that map to the same underlying model but different pricing.

When adding/changing a provider:

- Add focused tests for normalized model/provider names.
- Add tests for missing or malformed usage payloads.
- Keep fallback behavior explicit.

## Cost accounting

Cost bugs are product bugs. Treat them like money bugs, because they basically are.

Rules:

- Input/output/reasoning/cache tokens must not be collapsed unless the provider truly reports only total usage.
- Cache read and cache write pricing must be separate where supported.
- Missing prices should produce visible unknown/fallback behavior, not fake precision.
- Historical/backfill changes must be called out explicitly.
- LiteLLM/OpenRouter data must not silently override user-configured pricing without clear precedence.

Test cases should cover:

- Known priced model.
- Unknown model fallback.
- Cache read/write tokens.
- Reasoning tokens if supported.
- Provider alias/model normalization.

## Streaming and tool calls

Streaming paths need tests. Happy-path full responses are not enough.

Cover:

- Empty chunks.
- Partial JSON/tool-call chunks.
- Interrupted streams.
- Final usage metadata arriving after content.
- Duplicate final events.
- Retry after partial failure.

## Database, workers, and evaluations

The DB can be slow or remote. Don't write worker loops that hang forever.

Rules:

- Use timeouts for external/DB-ish calls where practical.
- Keep worker loops resilient to one bad job.
- Avoid unbounded scans as data grows.
- For stuck evaluations, inspect `evaluation_jobs` and `/evaluation-jobs/active`.
- Make migration compatibility explicit.

### Schema and migration workflow

When changing `src/database/models.py`, DB access code, or `src/schema_migrations.py`:

1. Identify whether existing SQLite/Postgres installs need a migration or only fresh schema changes.
2. Update `src/schema_migrations.py` when existing installs need compatibility.
3. Add or update tests that prove old schemas upgrade safely and new schemas still initialize.
4. Run:

```bash
uv run python -m pytest tests/test_database.py tests/test_api.py -q
```

If `uv` is unavailable, use `./.venv/bin/python -m pytest ...` and say so.

## Frontend/backend route parity

If the frontend calls a backend route, Vite dev mode must know it.

When adding API routes used by frontend:

- Add backend route.
- Add frontend API call/types.
- Add route to `frontend/vite-api-proxy.js` by editing `shouldProxyApiRequest()` explicit `pathname === ...` or prefix checks; this file is not a declarative route config.
- Add/update proxy route tests.
- Test both dev assumptions and production build when relevant.

## Bootstrap and service control

Do not casually rewrite bootstrap scripts. They are the user-facing install path.

Architecture:

```txt
install.sh (root; --client / --server / both) → bootstrap.sh → python -m src.cli bootstrap (src/ops.py)   (server)
                                             → client snapshot + venv                                  (client)
```

Quick backend restart:

```bash
~/.venv/bin/supervisorctl -c ~/.tokenage/supervisord.conf restart tokenage-api
```

## Documentation policy

- Do not add design docs unless the maintainer asked for them or approved the design/spec step.
- ADRs belong in `docs/adr/` only for durable architecture decisions.
- Keep public docs free of private memory, local-only preferences, and credentials.
