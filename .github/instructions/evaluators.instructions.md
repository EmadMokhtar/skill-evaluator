---
applyTo: "src/skill_eval/evaluators/**"
---

# Reviewing evaluator code

An evaluator turns a `RunResult` into a pass/fail verdict with a score and human-readable
detail. Its *score* is about the skill; a low score is never used to report a harness
problem, and a harness problem is never reported as a low score.

- **A check that could not be performed is not a pass.** `BudgetEvaluator` *skips* an
  unpriceable cost limit rather than passing it, so a case whose only budget check is an
  unpriced cost limit fails — nothing was verified. `JudgeEvaluator` follows the same rule:
  an unjudged rubric, and a check that passes without citing evidence, are not passes. Flag
  any "if we can't check it, assume it's fine" branch.
- **`errored` is for infra, and it must set `passed=False`.** An evaluator sets
  `EvalScore.errored` when the harness broke — a judge endpoint returning 500, a verdict
  that does not match its rubric, an offered case on a runner that cannot report triggering
  — never when the skill scored badly. The orchestrator turns any errored score into an
  errored case. A model validator makes `errored=True, passed=True` unconstructable.
- **No agent framework in this directory.** `JudgeEvaluator` reaches a model only through
  the injected `Judge` protocol. Flag any direct client or framework import.
- **Eval-side spend goes on `EvalScore.cost_usd`, never on `RunResult`.** `budget:` measures
  the skill's efficiency; what we spent grading it is the harness's cost and is reported
  separately.
- **Authoring errors propagate; they never become a failing score.** An unknown assertion
  kind, a malformed regex, or a tool name in a `trajectory` block that the case never
  declared is a mistake in the user's files. Scoring it as a failure would be a lie about
  the skill. These raise and `cli.py` exits 2.
- **New assertion kinds go in `_CHECKS`** in `assertion.py` and must be documented in
  `docs/eval-files.md`. `tests/test_docs.py` fails until both happen.
- `detail` is read by a human staring at a red CI run. It should name what failed and with
  what value, not just that something did.
- Every assertion in a case must hold for the case to pass. A case with no assertions passes.
