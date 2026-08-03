---
applyTo: "src/skill_eval/evaluators/**"
---

# Reviewing evaluator code

An evaluator turns a `RunResult` into a pass/fail verdict with a score and human-readable
detail. It scores the skill — it never reports on the harness.

- **A check that could not be performed is not a pass.** `BudgetEvaluator` *skips* an
  unpriceable cost limit rather than passing it, so a case whose only budget check is an
  unpriced cost limit fails — nothing was verified. Flag any "if we can't check it, assume
  it's fine" branch.
- **Authoring errors propagate; they never become a failing score.** An unknown assertion
  kind, a malformed regex, or a tool name in a `trajectory` block that the case never
  declared is a mistake in the user's files. Scoring it as a failure would be a lie about
  the skill. These raise and `cli.py` exits 2.
- **New assertion kinds go in `_CHECKS`** in `assertion.py` and must be documented in
  `docs/eval-files.md`. `tests/test_docs.py` fails until both happen.
- `detail` is read by a human staring at a red CI run. It should name what failed and with
  what value, not just that something did.
- Every assertion in a case must hold for the case to pass. A case with no assertions passes.
