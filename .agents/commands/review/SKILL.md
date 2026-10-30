# Adversarial Review

Spawn reviewers on the **opposite tool** to challenge work. Reviewers attack from distinct
lenses grounded in project standards. The deliverable is a synthesized verdict — do NOT make
changes.

**Hard constraint:** Reviewers MUST run via the opposite tool's CLI (`claude -p` or
`opencode -p`). Do NOT use subagents, the Agent tool, or any internal delegation mechanism as
reviewers — those run on *your own* model, which defeats the purpose.

## Step 1 — Load Context

Read `AGENTS.md` and `.agents/commands/tokenage.md`. These govern reviewer judgments.

## Step 2 — Determine Scope and Intent

Identify what to review from context (recent diffs, referenced plans, user message).

```bash
git status --short
git branch --show-current
git diff --stat main
git diff main
git ls-files --others --exclude-standard
```

If not on a feature branch, say so. If the diff contains unrelated changes, flag them.

`git diff main` includes committed branch changes plus staged and unstaged tracked changes. `git ls-files --others --exclude-standard` lists untracked paths; read and include the contents of every intended untracked file because Git diff omits them.

Determine the **intent** — what the author is trying to achieve. This is critical: reviewers
challenge whether the work *achieves the intent well*, not whether the intent is correct.
State the intent explicitly before proceeding.

Choose a tier by risk, not size: a 20-line auth change is Deep.

| Tier | Risk | Reviewers |
|------|------|-----------|
| Quick | Docs, comments, tests-only, non-security config | Skeptic; no security reviewer by default |
| Standard | Ordinary code: frontend, CLI output, dashboards, refactors | Skeptic + Minimalist + security |
| Deep | Auth/tokens/sessions, ingest path, cost/token accounting, schema/migrations, installer/update, CI workflows, new network listeners or anything exposed, privacy-sensitive data | Skeptic + Architect + Minimalist + security |

When unsure between tiers, pick the higher one and say why in the review intent.
Deep also requires a live functional test (GCP or the test lane), with results in the
PR body. Deep's reviewer list is extensible; add further reviewers there as needed.

Read `references/reviewer-lenses.md` for lens definitions.

Add one **security** reviewer for Standard and Deep; for Quick, only if the diff
touches secrets, auth, CI or installer files. Use the same tool and path as the
lens reviewers, prompt `security.md` in this directory plus the scope and intent, report
`$REVIEW_DIR/security.md`. Its High and Medium findings are must-fix unless the lead
judgment shows they are false positives.

## Step 3 — Detect Tool and Spawn Reviewers

Both paths below write reports to one directory inside the worktree. Create it first;
a path outside the worktree makes opencode stop on an "Access external directory" prompt:

```sh
REVIEW_DIR=$(mktemp -d "$PWD/.adversarial-review.XXXXXX")
export REVIEW_DIR
```

**Preferred: Orca orchestration.** If the `orchestration` skill is available and
`orca status --json` reports a running runtime, start reviewers as supervised Orca
workers. `worker-start --agent` launches the same opposite-tool CLI (`claude` or
`opencode`) under Orca supervision; it is a supervised way to run that CLI, not a
different reviewer. Load the skill and its version-matched guide first
(`orca skills get orchestration`), and resolve the executable as that skill says.
Then:

```text
orca orchestration run-create --objective "Adversarial review of <branch>" --json
orca orchestration worker-start --spec "<reviewer prompt>" --task-title "Review: skeptic" --worktree current --agent opencode --json
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

- Use the opposite tool as `--agent` (`opencode` when you are Claude Code, `claude`
  when you are opencode). One worker per lens, all started before waiting.
- Each reviewer prompt (Step 3 template) must say review-only (no edits, staging or
  commits), name a report file `$REVIEW_DIR/<lens>.md`, and end with `worker_done`
  carrying `--report-path` to it.
- Process every delivery, acknowledge it, and `worker-release` each settled reviewer.
  A `worker_done` with `--outcome failed`, an escalation, or a missing or empty
  report counts as a failed reviewer in Step 4.
- Permission mode follows the user's Orca setting for new agent tabs; there is no flag.
  If a wait times out with no messages, check the worker terminals for a
  pending permission prompt before assuming the reviewer is still working.

Fall back to the CLI flow below only when Orca is unavailable.

**Fallback: CLI processes.**

Retain every background process handle:

```sh
REVIEW_TIMEOUT_SECONDS=300
review_pids=()
```

Run all reviewer spawn commands and the wait loop within one shell invocation. PIDs and arrays are shell-local; if the host tool cannot preserve shell state, use its native process handles or a single script that performs both steps.

Determine which tool you are, then spawn reviewers on the opposite:

**If you are opencode** — spawn claude code reviewers via `claude` CLI:

```sh
timeout "$REVIEW_TIMEOUT_SECONDS" claude -p "prompt" \
  > "$REVIEW_DIR/skeptic.md" 2> "$REVIEW_DIR/skeptic.stderr" &
review_pids+=("$!")
```

Start each reviewer this way and retain its PID. Use the corresponding `opencode` command when that is the opposite tool.

**If you are claude code** — spawn opencode reviewers via `opencode` CLI:

```sh
timeout "$REVIEW_TIMEOUT_SECONDS" opencode -p "prompt" \
  > "$REVIEW_DIR/skeptic.md" 2> "$REVIEW_DIR/skeptic.stderr" &
review_pids+=("$!")
```

Use the same timeout and PID handling for each reviewer.

Name each output file after the lens: `skeptic.md`, `architect.md`, `minimalist.md`, `security.md`.

Wait for every retained PID before reading any output:

```sh
review_status=()
for pid in "${review_pids[@]}"; do
  if wait "$pid"; then
    status=0
  else
    status=$?
  fi
  review_status+=("$status")
done
```

Record timeout exit status 124 and every other non-zero exit as an incomplete or failed reviewer; do not treat missing, empty, or partial output as a completed review.

Build each reviewer's prompt using the template in `references/reviewer-prompt.md`.

## Step 4 — Verify and Synthesize Verdict

Before reading reviewer output, confirm that every reviewer settled (Orca `worker_done` for each Dispatch, or the CLI wait loop completed), log which path and agent were used, and confirm the output files exist:

```sh
echo "review_path=orca reviewer_agent=opencode"  # the path (orca|cli) and agent actually used
ls "$REVIEW_DIR"/*.md
```

If any reviewer timed out, exited non-zero, or produced missing or empty output, note the failure
in the verdict — do not silently skip a reviewer.

Read each reviewer's output file from `$REVIEW_DIR/`. Deduplicate overlapping findings.
Produce a single verdict using the format in `references/verdict-format.md`.

## Step 5 — Render Judgment

After synthesizing the reviewers, apply your own judgment. Using the stated intent and
project standards as your frame, state which findings you would accept and which you would
reject — and why. Reviewers are adversarial by design; not every finding warrants action. Call
out false positives, overreach, and findings that mistake style for substance.

Append the Lead Judgment section to the verdict (see `references/verdict-format.md`).

## Rules

- Do not rewrite code during review unless explicitly asked for fixes.
- Do not approve if secrets/privacy/cost/schema risks are unresolved.
- Do not nitpick formatting that tools handle.
- Prefer concrete file/function feedback over vague vibes.
