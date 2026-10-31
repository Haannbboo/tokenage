# Verdict Format

```markdown
<n> Important, <n> Nit, <n> Pre-existing. <what needs fixing, or "Nothing needs fixing.">

## Intent
<what the author is trying to achieve>

## Verdict: PASS | CONTESTED | REJECT
<one-line summary>

## Findings
<numbered list: Important first, then at most 3 Nits total>

For each finding:
- **[Important | Nit]** Description with file:line and a concrete failure scenario
- Lens: which reviewer raised it
- Recommendation: concrete action, not vague advice
- Same class: every other repo instance of the bug (`a.py:12, b.py:40`), or none found

## Pre-existing
<separate numbered list tagged [Pre-existing], with file:line, concrete failure
scenario and same-class instances for bugs; suggest an issue, not a must-fix for this PR>

## What Went Well
<1-3 things the reviewers found no issue with — acknowledge good work>

## Lead Judgment
<for each finding: accept or reject with a one-line rationale>
```

## Verdict Logic

- **PASS** — no unresolved Important findings; say plainly when nothing needs fixing
- **CONTESTED** — unresolved Important findings but reviewers disagree on them
- **REJECT** — unresolved Important findings with reviewer consensus

Important means must-fix; Nit is optional. Pre-existing findings do not block this PR.
Every finding needs code evidence and a concrete failure scenario, not guesses from names.
