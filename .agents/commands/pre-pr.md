# Pre-PR Checklist

Run this before pushing/opening a PR.

## Git hygiene

- [ ] Not on `main`.
- [ ] Branch is based on current `main`.
- [ ] `git status --short` contains only intended files.
- [ ] No unrelated refactors or drive-by cleanup.
- [ ] No ignored/private/local files staged.
- [ ] No co-authorship in commit message.

## Privacy/security

- [ ] No API keys, tokens, cookies, auth headers, connection strings, or secrets.
- [ ] No raw private prompts/responses/request bodies logged by default.
- [ ] Error paths do not dump sensitive payloads.
- [ ] Security review (`.agents/commands/review/security.md`) ran on the final diff with no open High/Medium findings. Run it again before merge if the branch changed since.
- [ ] Local files such as `AGENTS.local.md`, `CLAUDE.local.md`, `.claude/*.local.md`, `.agents/private/` are not committed.

## tokenage risks

- [ ] Cost/token accounting tested if touched: `uv run python -m pytest tests/test_costs.py tests/test_pricing.py -q`.
- [ ] Provider/model normalization tested if touched: `uv run python -m pytest tests/test_provider_parser.py tests/test_config.py -q`.
- [ ] Streaming/tool-call partial chunk behavior tested if touched: `uv run python -m pytest tests/client/test_client_proxy.py tests/test_usage_ingest.py tests/test_recorder.py -q`.
- [ ] Schema migration/backward compatibility tested if touched: `uv run python -m pytest tests/test_database.py tests/test_api.py -q`.
- [ ] Worker/background job DB timeout behavior considered if touched.
- [ ] Non-trivial ingest-path change by the repository owner: suggest the benchmark in `.agents/commands/benchmark.md` (needs approval; never run it unasked or for other authors).
- [ ] Frontend-used API routes added to `frontend/vite-api-proxy.js` by editing `shouldProxyApiRequest()` explicit pathname/prefix checks, with `frontend/vite-api-proxy.test.js` updated.

## Verification

Backend touched:

- [ ] `uv run python -m pytest -q` or documented fallback to `./.venv/bin/python -m pytest -q`
- [ ] Time the suite on this branch and on `origin/main`: `uv run python -m pytest -q` and `uv run python -m pytest -q -m "not slow" -n 4`, plus `--durations=10`, or the documented `./.venv/bin/python -m pytest` fallback. Rerun a time that looks off (host load is noisy).

Scripts touched:

- [ ] `uv run python -m pytest tests/scripts/ -q` or documented fallback

Frontend touched:

- [ ] `cd frontend && npm test`
- [ ] `cd frontend && npm run build`

Broad check before commit/PR:

- [ ] `pre-commit run --all-files`

## PR body

- [ ] Small/Standard/High-risk template chosen from `.agents/commands/pr-template.md`.
- [ ] Summary explains what changed.
- [ ] Why/context included for non-trivial behavior changes.
- [ ] Manual QA included if behavior changed.
- [ ] Test plan lists exact commands and results.
- [ ] Risk areas called out honestly.
- [ ] Follow-ups separated from current scope.
