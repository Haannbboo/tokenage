# Verify Workflow

Run this before commit/PR. The goal is boring: prove the changed areas work, then summarize exactly what passed or failed.

## 1. Inspect scope

```bash
git status --short
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

Use the changed files to choose checks. Do not run random expensive crap if the touched area is obvious.

## 2. Python runner

Prefer `uv run python -m pytest ...` for repo tests because `.pre-commit-config.yaml` uses `uv run python -m pytest`. If `uv` is unavailable in a local environment, fall back to `./.venv/bin/python -m pytest ...` and report the fallback.

## 3. Run checks

Run relevant checks in parallel when tooling allows. Report exact commands and results.

### Backend touched

```bash
uv run python -m pytest -q
```

### Scripts touched

```bash
uv run python -m pytest tests/scripts/ -q
```

### Cost/pricing/provider touched

```bash
uv run python -m pytest tests/test_costs.py tests/test_pricing.py tests/test_provider_parser.py tests/test_config.py -q
```

Also include API/recorder coverage if usage recording or endpoints changed:

```bash
uv run python -m pytest tests/test_api.py tests/test_recorder.py -q
```

### Schema/database/migration touched

```bash
uv run python -m pytest tests/test_database.py tests/test_api.py -q
```

If `src/database/models.py` or `src/schema_migrations.py` changed, explicitly inspect backward compatibility and migration behavior.

### Proxy/collector/streaming touched

```bash
uv run python -m pytest tests/client/test_client_proxy.py tests/test_usage_ingest.py tests/test_recorder.py tests/test_cli.py -q
```

### Frontend touched

```bash
cd frontend && npm test
cd frontend && npm run build
```

For frontend-used API routes, also check the Vite dev proxy:

```bash
cd frontend && npm test -- vite-api-proxy
```

`frontend/vite-api-proxy.js` uses explicit `pathname === ...` / prefix checks inside `shouldProxyApiRequest()`. Do not look for a route config object like a lost raccoon.

### Broad pre-commit check

```bash
pre-commit run --all-files
```

## 4. Failure handling

If a check fails:

1. Read the real failure output.
2. Fix the root cause, not the symptom.
3. Re-run the failing check.
4. If the failure is unrelated/pre-existing, prove that with evidence and call it out separately.

Warnings count as failures if CI would fail on them. Don't hand-wave warnings away like a gremlin.

## 5. Output

```md
## Verify Summary

| Check | Status | Notes |
| --- | --- | --- |
| `command` | pass/fail/not run | reason/result |

## Changed areas

- Backend: yes/no
- Frontend: yes/no
- Scripts: yes/no
- Schema/migration: yes/no
- Provider/cost/streaming/privacy risk: yes/no

## Failures or skips

- ...
```
