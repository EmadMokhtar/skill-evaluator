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

## The three protocols

The design rests on three protocols. Everything else is plumbing around them.

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

```python
class Judge(Protocol):
    name: str

    def judge(self, request: JudgeRequest) -> JudgeVerdict: ...
```

`Runner` is the seam every agent framework plugs into. `Evaluator` is the seam every
scoring strategy plugs into. `Judge` is the seam every LLM-as-judge implementation plugs
into — it exists so `JudgeEvaluator` can grade a rubric with a real model without any
agent-framework type entering `evaluators/`. Adding a framework, a scoring rule, or a judge
means adding one implementation of one protocol — no change to the orchestrator, the
reporters, or the gate.

`Runner` and `Judge` share a rule: **neither raises for provider failures.** They report
through `RunResult.error` and `JudgeVerdict.error`, so the orchestrator can tell an infra
problem (errored) from a low score (failed).

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | Every Pydantic model in the project. No other module defines a data shape. |
| `cli.py` | Typer entry point. Wires config → loaders → runner → orchestrator → reporters → gate, and owns the exit-code contract. |
| `orchestrator.py` | Builds and runs the skill × case × runner matrix, applying every evaluator to each result. |
| `gating.py` | Turns a `RunReport` into a pass/fail decision plus reasons and an exit code. |
| `config.py` | Loads `skill-eval.toml` by explicit path or upward discovery. Never reads secrets. |
| `yaml_loading.py` | A YAML loader that does not treat bare `yes`/`no`/`on`/`off` as booleans. |
| `skills/loader.py` | Walks a path for `SKILL.md` files and parses them into `Skill` models, via `parse_skill_text` — the shared core both `parse_skill_file` and `skills/baseline.py` parse through, so a blob from git and a file on disk go through one code path. |
| `skills/baseline.py` | Resolves a skill's previous version from git history for `--baseline previous`. Shells out to `git`, never raises for an environmental failure, imports no agent framework. |
| `cases/loader.py` | Finds and parses eval YAML for a skill into `EvalCase` models. |
| `scaffold.py` | Renders the starter eval suite `skill-eval init` writes. Pure: a `Skill` in, the file text out, with the IO left to `cli.py`. |
| `runners/base.py` | The `Runner` protocol. |
| `runners/fake.py` | A deterministic, offline, scripted runner. The default, and the backbone of the zero-cost test tier. |
| `runners/pydantic_ai.py` | The PydanticAI runner adapter. **One of only two modules that import an agent framework.** |
| `runners/tools.py` | Builds framework-neutral `MockTool`s (name + JSON schema + callable) from a case's `tools:` block. |
| `runners/preflight.py` | Verifies the provider API key is present before any spend. |
| `runners/pricing.py` | Turns provider usage into USD. Degrades rather than raising. |
| `evaluators/base.py` | The `Evaluator` protocol. |
| `evaluators/assertion.py` | Rule-based scoring of the final output text. |
| `evaluators/trajectory.py` | Scoring which tools were called, in what order, and how many times. |
| `evaluators/budget.py` | Scoring efficiency: tokens, cost, latency. |
| `evaluators/judge.py` | Rubric scoring. Holds no framework code; takes a `Judge` by injection. |
| `comparison.py` | Turns a two-armed `RunReport` into a `Delta`: pairing, sign conventions, low-signal checks, high-variance cases. Pure — no IO, no provider calls. |
| `judges/base.py` | The `Judge` protocol. |
| `judges/prompt.py` | Renders a `JudgeRequest` into prompt text. Pure, deterministic, no IO. |
| `judges/fake.py` | A scripted, offline judge. The default — and unscripted it *errors* rather than passing, so an unjudged rubric is never a quiet green. |
| `judges/pydantic_ai.py` | The PydanticAI judge adapter. **The other module that imports an agent framework.** |
| `reporters/console.py` | Human-readable run summary. |
| `reporters/json_reporter.py` | Machine-readable run report. |

## Data flow

```
path
  └─ skills/loader (walk for SKILL.md) ──────────────► [Skill]
        └─ per skill: skills/baseline (once, if --baseline) ──► baseline Skill | note
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml) ──► [EvalCase]

matrix: for each (skill × case × arm × repeat × runner)
    Runner.run ──► RunResult ──► each Evaluator ──► [EvalScore]
                                                       └─► CaseOutcome (arm, repeat_index)

aggregate ──► RunReport ──► comparison.build_delta ──► Delta | None
                        └─► reporters/  ──► console + JSON
                        └─► gating      ──► exit code
```

`arm` is `"candidate"` for the skill under test and `"baseline"` for the comparison skill;
absent `--baseline` every outcome is `"candidate"` and `build_delta` returns `None`, so the
matrix, the aggregates and the reporters all degrade to exactly the pre-M4 shape.

## Core data models

All live in `models.py`.

| Model | Carries |
| --- | --- |
| `Skill` | name, description, instructions, `version` (declared frontmatter version, `""` if absent), path, `variant` (`"candidate"` or `"baseline"`) |
| `EvalCase` | name, task, `tools`, `assertions`, `trajectory`, `budget`, `tags` |
| `RunResult` | output, tool calls, transcript, token split, latency, cost, `cost_note`, model, `error` |
| `CheckResult` | one check's `id`, `passed`, `evidence` — emitted by the judge and, since M4, by assertion/trajectory/budget too |
| `EvalScore` | one evaluator's `passed` / `score` / `detail`, plus its `checks: list[CheckResult]` |
| `BaselineNote` | why a skill or case has no baseline arm: `kind` (`"unavailable"` or `"skipped"`) plus a reason |
| `CaseOutcome` | one (skill, case, runner, arm, repetition) combination: status plus its scores and result |
| `RunReport` | every outcome, skipped and tag-filtered skills, `baseline_kind`, `repeat`, `baseline_notes` |

Two fields are **derived, not stored**: `RunResult.tokens` (the input/output split summed)
and `RunResult.errored` (`error is not None`). Aggregates on `RunReport` — `total`,
`passed`, `failed`, `errored`, `pass_rate` — read `candidate_outcomes` only (Decision: baseline
outcomes never count toward the gate); `baseline_outcomes` and `baseline_errored` surface the
comparison side apart from them. `pass_rate_by_skill` is likewise candidate-only.

`comparison.py` adds a second layer of models — `ArmStats`, `CaseStats`, `LowSignalCheck`,
`CaseRef` and `Delta` — that are computed from a `RunReport`, never stored on it. `Delta` is
`None` whenever no baseline arm ran, which is the signal reporters use to fall back to the
pre-M4 single-arm rendering.

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

**An unfilled scaffold is an authoring error, not a failure.** `skill-eval init` writes
`TODO(skill-eval)` into every field the author must supply, and `cases/loader.py`
rejects any case still containing it — before schema validation, so the message names
the field rather than its type. Enforcing this in the loader rather than the generator
makes it unconditional: hand-written stubs get it too, and no CI configuration can opt
out of it.

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

**Agent-framework imports appear in exactly two modules** — `runners/pydantic_ai.py` and
`judges/pydantic_ai.py`. `runners/tools.py` builds framework-neutral mock tools and the
adapter wraps them. `tests/test_framework_isolation.py` scans the whole package for
top-level framework imports and allows only those two files; it matches import *forms*, so
`cli.py` importing our own `skill_eval.runners.pydantic_ai` is not a false positive. This is
what keeps the `Runner` and `Judge` seams real rather than nominal.

**Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
`cost_note`. Pricing is reporting metadata; it must never be why a run errors. In
`BudgetEvaluator`, an unpriceable `max_cost_usd` limit is *skipped* — not counted as passed —
and that skip is recorded as a failing `CheckResult`. `passed` requires every declared limit
to hold, so **any** budget block that declares an unpriceable `max_cost_usd` fails the case,
whether or not it is the only check declared: a case whose `max_tokens` and `max_latency_ms`
both hold still fails if `max_cost_usd` could not be priced, because that one check was never
verified. `score`, by contrast, is the fraction of *evaluated* limits that held — the unpriced
limit is excluded from that divisor entirely, so it neither inflates nor deflates the score
the way a false pass would. A repository running an unpriced model with a `budget:` block that
mixes a priced limit with `max_cost_usd` will see those cases turn red on an upgrade to this
behavior; the fix is to drop `max_cost_usd` for that provider, not to treat the skip as a pass.

**Nothing scores a vacuous pass.** The rule that an unpriceable budget limit fails rather
than passing generalises: a rubric with no configured judge is *errored*, and a judge check
that passes without citing evidence is recorded as a *failure*. An unsupported PASS is an
LLM judge's characteristic failure mode, so it gets a mechanical defence rather than a
prompt asking nicely.

**Judge spend never enters `RunResult`.** It lives on `EvalScore.cost_usd` and is reported
as judge overhead. `budget:` measures the skill's efficiency, not the harness's.

**Mock tools accept any arguments.** A model hallucinating an argument is an eval signal
about the skill; raising would surface it as an infra error instead.

**Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act.
A missing cassette skips; a mismatched request fails rather than reaching the network.

**`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is
`skill-eval` everywhere: command, config file, distribution.

**`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.

### Comparative evals (M4)

**Absent `--baseline`, what runs is identical to the single-arm run that predates M4.** One
arm, no delta block, the same one-line-per-outcome layout, and JSON that keeps every prior key
and value with additive ones alongside (`arm`, `repeat_index`, a null `delta`,
`baseline_notes`). Console output is *not* byte-identical: a failing case now prints one
indented line per failed check, because M4 made the assertion, trajectory and budget
evaluators emit per-check evidence where only the judge did before. That is strictly more
information, not a change in what runs; the Comparative evals page covers it in full.
`none` names a *kind* of baseline — the flag being unset, not `--baseline none`, is what turns
comparison off. Upgrading must never silently double a bill.

**Baseline outcomes never count toward the gate**, and never toward `errored`.
`RunReport.total` / `passed` / `failed` / `errored` / `pass_rate` / `pass_rate_by_skill` all
read `candidate_outcomes`; `baseline_outcomes` and `baseline_errored` exist so the comparison
side is visible without ever feeding the numbers the gate reads. A strong baseline means the
skill was unnecessary, not that CI should go red.

**The baseline arm never receives the skill's name, description or instructions** under
`--baseline none`. `_system_prompt` emits a neutral `BASELINE_PREAMBLE` instead of the normal
`# {name}` header whenever both `description` and `instructions` are empty. The rule keys on
emptiness, not on `variant`, so no runner can — or has to — branch on which arm it is serving;
a runner that could branch on the arm could cheat the comparison.

**A baseline that cannot be resolved is reported, never assumed to be "no change".**
`resolve_previous` returns a `BaselineUnavailable` rather than treating silence as evidence.
Without `--min-delta` it is a note; with `--min-delta` it fails the gate, because treating "we
couldn't check" as "nothing changed" would let a repository pass forever by deleting its git
history.

**The delta is paired.** `comparison.build_delta` excludes a case from *both* halves of the
delta the moment either arm cannot be honestly compared — a skipped baseline, an unresolvable
one, or every repetition of an arm erroring. Keeping the surviving half would bias the
aggregate with data that has no partner to be measured against.

**`--min-delta` without `--baseline` is a user error (exit 2).** A delta gate that checks
nothing must never report a pass — the same vacuous-pass rejection every other gate rule in
this project applies. Gating on a delta with no comparable case fails for the same reason,
mirroring "a run executing zero cases fails the gate".

**Low-signal and high-variance flags never change the exit code.** They are diagnostics about
the *eval suite* — a weak assertion, an unstable case — not verdicts on the skill. A flag that
could block a merge trains people to ignore flags, and a flaky provider would be
indistinguishable from a genuinely bad skill.

**`resolve_previous` never raises for environmental failures** — no `git`, no repository, an
untracked `SKILL.md`, an exhausted history window. Each comes back as a `BaselineUnavailable`
with a reason, the same discipline runners and judges follow for provider failures.
Subprocesses run without a shell, decode as UTF-8, and carry a timeout, so a hung `git` cannot
hang CI.

**Deterministic evaluators emit per-check verdicts**, ids derived from the case (never the
result) so the same id names the same check in both arms: `{kind}[{index}]` for assertions,
`called:{tool}` / `forbidden:{tool}` / `order` / `max_calls` / `skill_triggered` for
trajectory, `max_tokens` / `max_cost_usd` / `max_latency_ms` for budget. This is what lets
`comparison.py` name a specific low-signal check rather than only flag a whole case.

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
