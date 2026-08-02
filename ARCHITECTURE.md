# Architecture

How `skill-eval` is built, and why it is built this way. For how to *use* it, see the
[documentation site](https://emadmokhtar.github.io/skill-evaluator/).

## Scope and non-goals

`skill-eval` runs evaluations on Anthropic-style Agent Skills — directories containing a
`SKILL.md` file. It is a CLI and a library, designed to run as a CI gate where the exit
code is the contract, or on demand during development.

Skills under test and their eval cases are **inputs**. Nothing about a skill under test is
vendored here. That is the central constraint: any skill repository can adopt `skill-eval`
without embedding it, and `skill-eval` can be released independently of anything it evaluates.

Non-goals: authoring skills, running skills in production, and hosting a results dashboard.

## The two protocols

The design rests on two protocols. Everything else is plumbing around them.

```python
class Runner(Protocol):
    name: str
    def run(self, skill: Skill, case: EvalCase) -> RunResult: ...
```

```python
class Evaluator(Protocol):
    name: str
    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore: ...
```

`Runner` is the seam every agent framework plugs into. `Evaluator` is the seam every
scoring strategy plugs into. Adding a framework or a scoring rule means adding one
implementation of one protocol — no change to the orchestrator, the reporters, or the gate.

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | Every Pydantic model in the project. No other module defines a data shape. |
| `cli.py` | Typer entry point. Wires config → loaders → runner → orchestrator → reporters → gate, and owns the exit-code contract. |
| `orchestrator.py` | Builds and runs the skill × case × runner matrix, applying every evaluator to each result. |
| `gating.py` | Turns a `RunReport` into a pass/fail decision plus reasons and an exit code. |
| `config.py` | Loads `skill-eval.toml` by explicit path or upward discovery. Never reads secrets. |
| `yaml_loading.py` | A YAML loader that does not treat bare `yes`/`no`/`on`/`off` as booleans. |
| `skills/loader.py` | Walks a path for `SKILL.md` files and parses them into `Skill` models. |
| `cases/loader.py` | Finds and parses eval YAML for a skill into `EvalCase` models. |
| `runners/base.py` | The `Runner` protocol. |
| `runners/fake.py` | A deterministic, offline, scripted runner. The default, and the backbone of the zero-cost test tier. |
| `runners/pydantic_ai.py` | The PydanticAI adapter. **The only module that imports an agent framework.** |
| `runners/tools.py` | Builds framework-neutral `MockTool`s (name + JSON schema + callable) from a case's `tools:` block. |
| `runners/preflight.py` | Verifies the provider API key is present before any spend. |
| `runners/pricing.py` | Turns provider usage into USD. Degrades rather than raising. |
| `evaluators/base.py` | The `Evaluator` protocol. |
| `evaluators/assertion.py` | Rule-based scoring of the final output text. |
| `evaluators/trajectory.py` | Scoring which tools were called, in what order, and how many times. |
| `evaluators/budget.py` | Scoring efficiency: tokens, cost, latency. |
| `reporters/console.py` | Human-readable run summary. |
| `reporters/json_reporter.py` | Machine-readable run report. |

## Data flow

```
path
  └─ skills/loader (walk for SKILL.md) ──────────────► [Skill]
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml) ──► [EvalCase]

matrix: for each (skill × case × runner)
    Runner.run ──► RunResult ──► each Evaluator ──► [EvalScore]
                                                       └─► CaseOutcome

aggregate ──► RunReport ──► reporters/  ──► console + JSON
                        └─► gating      ──► exit code
```

## Core data models

All live in `models.py`.

| Model | Carries |
| --- | --- |
| `Skill` | name, description, instructions, path |
| `EvalCase` | name, task, `tools`, `assertions`, `trajectory`, `budget`, `tags` |
| `RunResult` | output, tool calls, transcript, token split, latency, cost, `cost_note`, model, `error` |
| `EvalScore` | one evaluator's `passed` / `score` / `detail` |
| `CaseOutcome` | one (skill, case, runner) triple: status plus its scores and result |
| `RunReport` | every outcome, plus skipped and tag-filtered skills |

Two fields are **derived, not stored**: `RunResult.tokens` (the input/output split summed)
and `RunResult.errored` (`error is not None`). Aggregates on `RunReport` — `total`,
`passed`, `failed`, `errored`, `pass_rate` — are likewise computed from `outcomes`.

## Invariants, and why

These are decided behaviors, not accidents. Several were bugs caught in review. Each has a
test asserting it.

**`errored` is not `failed`.** `failed` means the case ran and scored below the bar — an
*eval* signal about the skill. `errored` means the runner itself blew up — an *infra*
signal about the harness. Conflating them makes a broken API key look like a bad skill.
Runners therefore **never raise** for provider failures; they set `RunResult.error`.
Errored cases fail the gate by default so CI never goes green on a run that did not
actually happen.

**A run executing zero cases fails the gate.** "Nothing ran" is a broken run, not a pass —
otherwise a mistyped path reports success forever. `gating.evaluate_gate` distinguishes the
causes: no skills found, all skills skipped for having no cases, or every case filtered out
by `--tag`.

**Authoring errors abort the run; they never score as failures.** An unknown assertion
`kind`, a malformed regex, an undeclared tool name in a `trajectory` block, or an unknown
YAML key is a mistake in the user's files — it says nothing about the skill. Scoring it as
a failure would be a lie about the skill's quality. `orchestrator.run_evals` lets these
propagate; `cli.py` catches them via `_AUTHORING_ERRORS` and exits 2.

**Exit codes are the CI contract.** Gate passed `0`, gate failed `1`, user or authoring
error `2`. In `cli.py`, a JSON-write failure escalates to 2 only when the gate itself
passed — a write problem must never mask an already-failing gate.

**`extra="forbid"` on every user-authored model.** `EvalCase`, `AssertionSpec`, `ToolSpec`,
`TrajectorySpec`, `BudgetSpec`, `Config`. Without it, a typo like `assertion:` yields a
case that passes vacuously — the worst possible failure mode for an eval tool. It is also
on `RunResult`, where it makes writing the derived `tokens` field a loud error rather than
a total that silently disagrees with the split it was priced from.

**All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error
(`SkillParseError`, `CaseParseError`, `ConfigError`) naming the file and the field.

**YAML goes through `yaml_loading.safe_load`.** PyYAML's `SafeLoader` implements YAML 1.1,
which turns bare `yes`/`no`/`on`/`off` into booleans. An assertion `value: yes` is meant as
the string.

**Secrets come from environment variables only** — never from `skill-eval.toml`. A config
file is committed; a key must not be.

**No agent-framework type appears outside `runners/pydantic_ai.py`.** `runners/tools.py`
builds framework-neutral mock tools and the adapter wraps them. A test asserts the string
`pydantic_ai` does not appear in `tools.py`. This is what keeps the `Runner` seam real
rather than nominal.

**Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
`cost_note`. Pricing is reporting metadata; it must never be why a run errors. In
`BudgetEvaluator`, an unpriceable cost limit is *skipped* — not counted as passed — so a
case whose only budget check is an unpriced cost limit fails, because nothing was verified.

**Mock tools accept any arguments.** A model hallucinating an argument is an eval signal
about the skill; raising would surface it as an infra error instead.

**Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act.
A missing cassette skips; a mismatched request fails rather than reaching the network.

**`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is
`skill-eval` everywhere: command, config file, distribution.

**`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.

## Extension points

**Adding a runner.** Implement `Runner` in a new module under `runners/`, register it in
`cli._RUNNERS`, and put every framework import inside that module. Set
`needs_api_key = True` if it spends money — `cli.py` then runs the preflight key check
before constructing it. Never raise for a provider failure; return a `RunResult` with
`error` set.

**Adding an evaluator.** Implement `Evaluator` in a new module under `evaluators/` and add
it to the evaluator list in `orchestrator.py`. Return `passed=False` for a real failure;
never treat a check you could not perform as passed.

**Adding a reporter.** Add a module under `reporters/` taking `report` and an optional
`gate` keyword argument (default `None`), and returning a string. `cli.py` decides when to
call it.

**Adding an assertion kind.** Add an entry to `_CHECKS` in `evaluators/assertion.py` and
document it in `docs/eval-files.md`. `tests/test_docs.py` fails until you do both.

## Testing tiers

| Tier | Marker | Cost | Selected by default |
| --- | --- | --- | --- |
| Pipeline | none | free, offline, deterministic (`FakeRunner`) | yes |
| Cassette | `cassette` | free — replays recorded provider traffic | yes |
| Live | `integration` | real API spend, needs a key | no |

`pytest` runs with `--block-network`, so an accidental network call in the default tiers
fails loudly rather than silently costing money. Development is test-driven: the failing
test comes first.
