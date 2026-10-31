# Respond to PR Comments Workflow

Use this lightweight workflow for PR comments only. For the full opened-PR follow-up loop that includes CI, GitHub Actions logs, CodeRabbit, fixing, verification, push, and public reply rules, use `.agents/commands/pr-follow-up.md`.

Use this to handle CodeRabbit, human reviewer, or CI bot comments on the current branch's PR.

Do not push fixes or post replies without explicit approval.

## 1. Find the PR

```bash
gh pr view --json number,title,url,state,headRefName,baseRefName,reviews,comments
```

Stop if no PR exists, or if it is closed/merged.

Fetch inline review comments:

```bash
PR_NUMBER=$(gh pr view --json number --jq .number)
gh api "repos/{owner}/{repo}/pulls/$PR_NUMBER/comments"
```

Also inspect top-level comments and checks when relevant:

```bash
gh pr checks
gh pr view --comments
```

## 2. List and classify comments

For each comment, record:

- reviewer/bot
- file and line, if inline
- exact comment/request
- category

Categories:

- **BLOCKER** — required change, bug, security/privacy issue, failing CI, broken behavior.
- **QUESTION** — asks for clarification or design rationale.
- **SUGGESTION** — optional improvement, useful but not blocking.
- **NITPICK** — style/readability detail, cheap to fix or safe to ignore.
- **FALSE POSITIVE** — reviewer is wrong; requires evidence if replying.

Handle order: BLOCKER → QUESTION → SUGGESTION → NITPICK.

## 3. Address each item

For each comment:

1. Read the surrounding code, not just the diff hunk.
2. Decide: fix code, draft reply, or mark false positive with evidence.
3. For BLOCKERs, present the intended fix before editing unless the fix is trivial and safe.
4. Before fixing a bug, grep the repo for all instances of the same pattern. Fix them
   together, or explain in the reply why the other instances are unaffected.
5. After code changes, run focused verification from `.agents/commands/verify.md`.
6. Update the PR body if the behavior/risk/testing changed.

## 4. Reply style

Keep replies short and specific:

```md
Fixed in <commit>: <what changed>. Added/ran `<test command>`.
```

For false positives:

```md
I don't think this applies because <specific evidence from code/test>. No change made.
```

Do not argue with bots like a clown. Either fix, prove, or ignore with reason.

## 5. Output

```md
## PR Comment Response Summary

| Category | Count |
| --- | ---: |
| BLOCKER | 0 |
| QUESTION | 0 |
| SUGGESTION | 0 |
| NITPICK | 0 |
| FALSE POSITIVE | 0 |

## Actions

- [file:line] comment summary → fixed/replied/deferred/false positive

## Verification

- `command`: pass/fail/not run

## Needs approval before external action

- Push fixes: yes/no
- Post replies: yes/no
```
