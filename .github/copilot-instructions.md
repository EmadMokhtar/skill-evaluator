# Copilot instructions for skill-eval

`skill-eval` is a standalone CLI and library that runs evaluations on Agent Skills
(`SKILL.md` files). Skills under test and their eval cases are **inputs** — nothing about a
skill under test is vendored here. The tool runs as a CI gate where the exit code is the
contract. Full design: [`ARCHITECTURE.md`](../ARCHITECTURE.md).

## The two seams

- `Runner.run(skill, case) -> RunResult` — where every agent framework plugs in.
- `Evaluator.evaluate(case, result) -> EvalScore` — where every scoring strategy plugs in.

`models.py` holds every Pydantic model; no other module defines a data shape.

## Invariants — flag any change that breaks one

These are decided behaviors with tests asserting them, not accidents.

1. **`errored` is not `failed`.** `failed` = the case ran and scored below the bar (an eval
   signal). `errored` = the runner itself blew up (an infra signal). Runners must **never
   raise** for provider failures — they set `RunResult.error`. Errored cases fail the gate.
2. **A run executing zero cases fails the gate.** "Nothing ran" is a broken run.
3. **Authoring errors abort the run; they never score as failures.** An unknown assertion
   kind, a bad regex, or an unknown YAML key is a mistake in the user's files and says
   nothing about the skill. They propagate out of the orchestrator and `cli.py` exits 2.
4. **Exit codes are the CI contract:** gate passed `0`, gate failed `1`, user or authoring
   error `2`. A JSON-write failure escalates to 2 only when the gate already passed.
5. **`extra="forbid"`** on every user-authored model. Without it a typo like `assertion:`
   yields a case that passes vacuously.
6. **All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error naming the
   file and field.
7. **YAML goes through `skill_eval.yaml_loading.safe_load`**, never `yaml.safe_load`.
8. **Secrets come from environment variables only** — never from `skill-eval.toml`.
9. **No agent-framework type appears outside `runners/pydantic_ai.py`.**
10. **Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` and a
    `cost_note`. An unpriceable budget check is *skipped*, not passed.
11. **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name
    is `skill-eval` everywhere.

## Do not suggest

- Importing an agent framework anywhere outside `runners/pydantic_ai.py`.
- Replacing `yaml_loading.safe_load` with `yaml.safe_load`.
- Raising from a runner when a provider call fails.
- Removing `extra="forbid"` to make a model more permissive.
- Making a mock tool reject unexpected arguments — a hallucinated argument is an eval
  signal, and raising would surface it as an infra error.
- Treating a check that could not be performed as passed.

## Conventions

- **Test-driven.** The failing test comes first. The default test tier must stay zero-cost,
  offline and deterministic — `pytest` runs with `--block-network`.
- **Conventional Commits are enforced, not stylistic.** `cz bump` derives the version and
  changelog from history. PRs are squash-merged, so **the PR title becomes the commit on
  `main`** and must be conventional too. Flag a non-conforming PR title in review.
- **Documentation ships with the change.** A change to a flag, config key, `EvalCase` field
  or assertion kind must update the matching page under `docs/`; a change to a protocol or
  an invariant must update `ARCHITECTURE.md`.
- Line length 100, `ruff` for lint and format.
