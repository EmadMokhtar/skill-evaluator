# Auditing an existing suite

Work the checklist, then report findings with the file and case named. Do not rewrite the
user's suite unasked.

## Checklist

- [ ] **Vacuous cases.** Any `loaded` case with no `assertions`, no `trajectory`, no
      `judge`? It passes without checking anything.
- [ ] **Missing negative control.** Any `mode: offered` positive with no negative
      counterpart? The suite cannot distinguish a well-targeted skill from one that fires
      on everything.
- [ ] **Claims with no case.** List the skill's "always/never/must" statements and find
      the case for each. Name the ones with none.
- [ ] **One-sided edges.** A policy case that only proves the refusal, never the approval.
- [ ] **Over-tight assertions.** `equals` or a `regex` pinning phrasing the skill never
      promised; an assertion that would fail on a legitimately different good answer.
- [ ] **Fixture assertions.** An assertion whose value comes from a mock tool's `returns`
      rather than from the skill's behavior.
- [ ] **Unevidenceable rubric entries.** Anything you could not prove by quoting the
      output ("is helpful", "is well structured"), or compound entries hiding which half
      failed.
- [ ] **Budgets that never bind, or bind too tightly.** A ceiling far above any plausible
      run checks nothing; one at the current spend turns every prompt change red.
- [ ] **A cost limit as the only budget check** on a model with no pricing entry — the
      check is skipped, so the case fails for having verified nothing.
- [ ] **Leftover placeholders.** `TODO(skill-eval)` anywhere.
- [ ] **Tags.** Is there a `smoke` subset a fast CI job could run?
- [ ] **Cases that error under the configured runner or judge.** An `errored` case never
      ran to a verdict; it says nothing about the skill. Check whether the harness is
      simply unconfigured (default fake runner, default fake judge) or the case declares
      something this runner cannot report (`mode: offered` under a runner with no
      triggering support, for instance).

## Reporting

For each finding: the file and case, what is wrong, and the smallest change that fixes it.
Rank by what would let a broken skill through, not by what is easiest to fix.
