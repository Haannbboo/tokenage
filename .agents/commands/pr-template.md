# PR Template

Pick the smallest template that makes review easy. Delete sections that do not apply. Do not leave `N/A` filler.

Always be honest about validation. If you did not run a check, say why.

## Small PR

Use for docs-only, config-only, tiny low-risk fixes, or isolated cleanup.

```md
## Summary

-

## Testing

- `command`: pass/fail/not run — result or reason
- Test time (branch vs `origin/main`): serial `<s>` vs `<s>`, parallel `<s>` vs `<s>`; new slow tests: none / list

## Review

- Independent code review completed before commit: yes/no
- Must-fix review findings resolved: yes/no
```

## Standard PR

Use for most behavior changes.

```md
## Summary

-
-

## Why / Context


## How It Works


## Manual QA

- [ ]

## Testing

- `command`: pass/fail/not run — result or reason
- Test time (branch vs `origin/main`): serial `<s>` vs `<s>`, parallel `<s>` vs `<s>`; new slow tests: none / list

## Risk Areas

- Privacy/secrets: yes/no — notes
- Cost/token accounting: yes/no — notes
- Provider adapter/model normalization: yes/no — notes
- Streaming/tool calls: yes/no — notes
- Schema/migration/backfill: yes/no — notes
- Frontend/backend route parity: yes/no — notes

## Review

- Independent code review completed before commit: yes/no
- Must-fix review findings resolved: yes/no
- Standards checked against `AGENTS.md` and `.agents/commands/tokenage.md`: yes/no

## Known Limitations / Follow-ups

-
```

## High-risk PR

Use for schema/migrations, privacy/secrets, cost accounting, provider adapters, streaming/tool-call behavior, retry/timeout/idempotency, worker/DB loops, or broad frontend/backend route changes.

```md
## Summary

This PR changes high-risk area(s): <list>.

-
-

## Why / Context


## How It Works


## Design Decisions

- **Decision:**
  **Why:**
  **Trade-off:**

## Manual QA

- [ ] Happy path:
- [ ] Error/empty state:
- [ ] Edge case:

## Testing

- `command`: pass/fail/not run — result or reason
- Test time (branch vs `origin/main`): serial `<s>` vs `<s>`, parallel `<s>` vs `<s>`; new slow tests: none / list

## Risk / Rollout / Rollback

- **Risk:**
- **Rollout:**
- **Rollback:**

## Data / Privacy Impact

- Raw prompts/responses/request bodies captured by default: yes/no
- Secrets/auth headers/cookies touched: yes/no
- Logs/errors scrubbed: yes/no

## Cost / Provider / Schema Impact

- Cost/token accounting changed: yes/no
- Provider normalization changed: yes/no
- Streaming/tool-call behavior changed: yes/no
- Migration/backfill required: yes/no

## Review

- Independent code review completed before commit: yes/no
- Must-fix review findings resolved: yes/no
- Standards checked against `AGENTS.md`, `.agents/commands/tokenage.md`, and `.agents/commands/pre-pr.md`: yes/no

## Known Limitations / Follow-ups

-
```
