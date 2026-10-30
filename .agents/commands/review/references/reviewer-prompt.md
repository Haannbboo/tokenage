# Reviewer Prompt Template

Each reviewer gets a single prompt containing:

1. The stated intent (from Step 2)
2. Their assigned lens (full text from references/reviewer-lenses.md)
3. The project standards relevant to their lens (from AGENTS.md and .agents/commands/tokenage.md)
4. The code or diff to review
5. Instructions: "You are an adversarial reviewer. Your job is to find real problems, not
   validate the work. Tag every finding Important (must fix), Nit (optional), or
   Pre-existing. Cite file:line and a concrete failure scenario for each; trace the
   code rather than guessing from names. Report at most 3 nits. List Pre-existing
   findings separately; suggest an issue, not a must-fix for this PR.
   For each bug, search the repo for the same pattern and list every other instance
   under the finding: `Same class: a.py:12, b.py:40` (or `Same class: none found`).
   Start the summary with counts of Important, Nit and Pre-existing findings; say
   plainly when nothing needs fixing. Write findings as a numbered markdown list
   to your output file, following .agents/commands/review/references/verdict-format.md."

Spawn all reviewers in parallel.
