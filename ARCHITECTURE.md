# Architecture

How `skill-lens` is built, and why it is built this way. For how to *use* it, see the
[documentation site](https://emadmokhtar.github.io/skill-evaluator/).

## Scope and non-goals

`skill-lens` runs evaluations on Anthropic-style Agent Skills — directories containing a
`SKILL.md` file. It is a CLI and a library, designed to run as a CI gate where the exit
code is the contract, or on demand during development.

Skills under test and their eval cases are **inputs**. Nothing about a skill under test is
vendored here. That is the central constraint: any skill repository can adopt `skill-lens`
without embedding it, and `skill-lens` can be released independently of anything it evaluates.

Non-goals: authoring skills, running skills in production, and hosting a results dashboard.

## The three protocols

The design rests on three protocols. Everything else is plumbing around them.

```python
class Runner(Protocol):
    name: str

    def run(
        self,
        skill: Skill,
        case: EvalCase,
        workspace: Workspace | None = None,
        scripts: ScriptRuntime | None = None,
    ) -> RunResult: ...
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
| `orchestrator.py` | Plans the skill × case × runner × arm × repeat matrix (sequential discovery), then executes it — a plain loop at `concurrency == 1`, a bounded thread pool above it — applying every evaluator to each result. `RunOptions` bundles the per-run settings (kept workspaces, the workspace caps, the script policy); when scripts are on it runs `scripts.preflight` once between discovery and execution, and `_BaselineStore` owns the directory previous bundles are extracted into, deleted in a `finally`. |
| `gating.py` | Turns a `RunReport` into a pass/fail decision plus reasons and an exit code. |
| `config.py` | Loads `skill-lens.toml` by explicit path or upward discovery. Never reads secrets. |
| `yaml_loading.py` | A YAML loader that does not treat bare `yes`/`no`/`on`/`off` as booleans. |
| `skills/loader.py` | Walks a path for `SKILL.md` files and parses them into `Skill` models, via `parse_skill_text` — the shared core both `parse_skill_file` and `skills/baseline.py` parse through, so a blob from git and a file on disk go through one code path. |
| `skills/baseline.py` | Resolves a skill's previous version from git history for `--baseline previous`, and extracts that same commit's bundle (`git archive`, `tarfile` with the `data` filter) so the old instructions are paired with the old scripts. Shells out to `git`, never raises for an environmental failure, imports no agent framework. |
| `cases/loader.py` | Finds and parses eval YAML for a skill into `EvalCase` models. |
| `scaffold.py` | Renders the starter eval suite `skill-lens init` writes. Pure: a `Skill` in, the file text out, with the IO left to `cli.py`. `scaffold_target` decides where `init` writes. |
| `workspace.py` | The per-case temporary directory: creation, seeding, path containment, and cleanup. Framework-neutral, like every other top-level module. Its methods **raise** (`PathRefused`, `WorkspaceError`) for `cases/loader.py` and the evaluators to catch as authoring or infra errors; `runners/tools.py`'s built-in tools catch those same exceptions and turn them into ordinary tool-result strings instead. |
| `bundle.py` | A read-only view of the three Agent Skills directories beside `SKILL.md` (`scripts/`, `references/`, `assets/`) and nothing else — an eval file beside `SKILL.md` is never readable by the agent. Same "methods raise, tools catch" split as `workspace.py`. |
| `scripts.py` | Runs a bundled script: the policy, the once-per-run preflight (interpreters on `PATH`, the sandbox probe), the allowlisted environment, the scratch directory, the process-group timeout, capped output read through the harness's own descriptors, and the `sandbox-exec` / `bwrap` wrapping. Never raises for a script that will not run; raises `ScriptSetupError` only from preflight. |
| `runners/base.py` | The `Runner` protocol. `run` takes optional `workspace=` and `scripts=` keywords, both additive with a default, so a runner written against an earlier milestone keeps working. |
| `runners/fake.py` | A deterministic, offline, scripted runner. The default, and the backbone of the zero-cost test tier. |
| `runners/pydantic_ai.py` | The PydanticAI runner adapter. **One of only two modules that import an agent framework.** |
| `runners/tools.py` | Builds framework-neutral `AgentTool`s (name + JSON schema + callable) from a case's `tools:` block, the built-in workspace tools, and the bundle tools (`list_skill_files`, `read_skill_file`, and `run_script` when the bundle has scripts and the run enabled them). Owns the six-name `BUILTIN_TOOL_NAMES` the case loader reads. |
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
| `reporters/failure_context.py` | The excerpt a non-passing case shows — output, cut count, tool-call lines. One helper for all three reporters; no markup. |
| `reporters/json_reporter.py` | Machine-readable run report. |
| `reporters/junit.py` | JUnit XML for CI test panes. `failed`/`errored` map onto `<failure>`/`<error>`, candidate arm only. |
| `reporters/markdown.py` | GitHub-flavored Markdown for step summaries and PR comments, with optional `max_chars` truncation. |

## Data flow

```
path
  └─ skills/loader (walk for SKILL.md) ──────────────► [Skill] (bundle_root if scripts/, references/ or assets/ exists)
        └─ per skill: skills/baseline (once, if --baseline) ──► baseline Skill (+ its own bundle) | note
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml) ──► [EvalCase]
  └─ scripts.preflight (once, only if allow_scripts) ──► ScriptRuntime | ScriptSetupError (exit 2)

matrix: for each (skill × case × arm × repeat × runner)
    Runner.run ──► RunResult ──► each Evaluator ──► [EvalScore]
                                                       └─► CaseOutcome (arm, repeat_index)

aggregate ──► RunReport ──► comparison.build_delta ──► Delta | None
                        └─► reporters/  ──► console + JSON + JUnit + Markdown
                        └─► gating      ──► exit code
```

`arm` is `"candidate"` for the skill under test and `"baseline"` for the comparison skill;
absent `--baseline` every outcome is `"candidate"` and `build_delta` returns `None`, so the
matrix, the aggregates and the reporters all degrade to exactly the pre-M4 shape.

## Core data models

All live in `models.py`.

| Model | Carries |
| --- | --- |
| `Skill` | name, description, instructions, `version` (declared frontmatter version, `""` if absent), path, `variant` (`"candidate"` or `"baseline"`), `bundle_root` (the directory whose `scripts/`, `references/` and `assets/` the agent may read; `None` when the skill ships none) |
| `EvalCase` | name, task, `tools`, `assertions`, `trajectory`, `budget`, `tags` |
| `RunResult` | output, tool calls, transcript, token split, latency, cost, `cost_note`, model, `error` |
| `CheckResult` | one check's `id`, `passed`, `evidence` — emitted by the judge and, since M4, by assertion/trajectory/budget too |
| `EvalScore` | one evaluator's `passed` / `score` / `detail`, plus its `checks: list[CheckResult]` |
| `BaselineNote` | why a skill or case has no baseline arm: `kind` (`"unavailable"` or `"skipped"`) plus a reason |
| `CaseOutcome` | one (skill, case, runner, arm, repetition) combination: status plus its scores and result |
| `ScriptStatus` | the once-per-run sandbox decision: `sandbox` (`"sandbox-exec"`, `"bwrap"` or `"none"`) and the probe's `detail` |
| `ScriptNote` | a skill that bundles scripts while execution is off: `skill_name`, `script_count` |
| `RunReport` | every outcome, skipped and tag-filtered skills, `baseline_kind`, `repeat`, `baseline_notes`, `scripts` (`None` when execution was off), `script_notes` |

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
causes: no skills found, all skills skipped for having no cases, every case filtered out
by `--tag`, or no case name matched `--case`.

**Authoring errors abort the run; they never score as failures.** An unknown assertion
`kind`, a malformed regex, an undeclared tool name in a `trajectory` block, or an unknown
YAML key is a mistake in the user's files — it says nothing about the skill. Scoring it as
a failure would be a lie about the skill's quality. `orchestrator.run_evals` lets these
propagate; `cli.py` catches them via `_AUTHORING_ERRORS` and exits 2.

**Exit codes are the CI contract.** Gate passed `0`, gate failed `1`, user or authoring
error `2`. In `cli.py`, a JSON-write failure escalates to 2 only when the gate itself
passed — a write problem must never mask an already-failing gate.

**An unfilled scaffold is an authoring error, not a failure.** `skill-lens init` writes
`TODO(skill-lens)` into every field the author must supply, and `cases/loader.py`
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

**Secrets come from environment variables only** — never from `skill-lens.toml`. A config
file is committed; a key must not be.

**Agent-framework imports appear in exactly two modules** — `runners/pydantic_ai.py` and
`judges/pydantic_ai.py`. `runners/tools.py` builds framework-neutral mock tools and the
adapter wraps them. `tests/test_framework_isolation.py` scans the whole package for
top-level framework imports and allows only those two files; it matches import *forms*, so
`cli.py` importing our own `skill_lens.runners.pydantic_ai` is not a false positive. This is
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

**`skill_lens` (underscore) never appears in user-facing output.** The user-facing name is
`skill-lens` everywhere: command, config file, distribution. The GitHub repository keeps its
older name, `skill-evaluator`, so `uses: EmadMokhtar/skill-evaluator@v<version>` installing
`skill-lens` is expected, not a mistake. `tests/test_naming.py` fails if the pre-rename name
reappears outside `docs/superpowers/`, which is a historical archive and is never rewritten.
`CHANGELOG.md` is exempt from that scan on the same grounds and for one more: `cz bump`
regenerates it from commit subjects and footers written before the rename, so an edit there
would misquote the commit it came from *and* be undone by the next release. It is not left
unguarded — a second test reads the file and allows only the exact lines history produced, so
the old name arriving through a commit subject written after the rename still fails.

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

### CI surfaces (M5)

**JUnit reports the candidate arm only.** Under `--baseline`, a failing baseline is the
evidence that the skill helped. Rendering it as `<failure>` would paint CI red for the skill
working — the same reason every `RunReport` aggregate reads `candidate_outcomes`.

**`<failure>` is `failed`; `<error>` is `errored`.** The project's central distinction, given a
native rendering: an exploded runner must not look like a skill that got worse. `errored`
covers two different sources, and both must render as `<error>`: a runner that raised, and an
evaluator that did (a judge endpoint returning 500 leaves `RunResult.error` unset and puts its
diagnostic on `EvalScore.detail` instead). `reporters/junit.py`'s `_error_body` reads the
runner's error first and falls back to the errored evaluators' own details, so an evaluator's
own diagnostic is what gets reported — never attributed to the runner that ran cleanly.

**JUnit output is always well-formed XML.** `ElementTree` escapes `&`, `<` and `>` but emits
control characters raw, so a model returning `\x00` would produce a file every parser rejects.
Illegal characters are stripped before they reach the tree.

**A zero-case run produces a JUnit `<error>`, not an empty green suite.** `tests="0"` renders
green in most CI UIs, which would contradict the exit code of 1.

**Markdown truncation gives up detail before it gives up meaning, and never hides how much it
gave up.** Optional blocks (totals, per-skill table, delta, failure detail, low-signal /
high-variance, skipped skills) are dropped first, from the end. If gate reasons still do not
fit, they are elided behind a truthful `+N more reasons` count rather than being cut silently,
so a clipped comment can never imply the reasons it shows were all of them. Only a budget too
small to hold even the verdict and summary falls back to a hard character cut on the assembled
text. Truncation lives in the renderer, not the caller — a caller slicing the returned string
after the fact would cut a `<details>` block open, or a count line in half.

**Reporters never do IO to a service.** They return a string. The CLI writes files; the
workflow posts comments. A GitHub client inside a reporter would put token scopes and network
failure inside a pure function.

**`--concurrency 1` constructs no executor.** The plain sequential loop it falls back to
produces the same ordering and the same exception propagation as any other concurrency level
reading its futures in submission order, and it is what lets the cassette tier (vcrpy is
order-sensitive and not thread-safe) still match requests. It is not, though, a literal replay
of pre-M5 behavior in every respect: discovery is now always a separate, sequential pass that
loads every skill's cases before any of them run, so a malformed eval file anywhere aborts the
whole run before a single case runs — where before M5, discovery and execution were interleaved
per skill, and an earlier skill's cases could complete (and be paid for) before a later skill's
bad file was even read.

**Outcome order is submission order, never completion order.** `render_console` iterates
`report.outcomes` and `build_delta` groups by insertion order, so completion-order results
would make output churn between identical runs.

**Concurrency never turns an authoring error into a case failure, and the surfaced error is
deterministic.** Futures are read in submission order, so the lowest-index failure is always
the one that propagates out of `run_evals`. On a failure, only the futures queued *after* it
are cancelled — a worker dequeues an item before marking its own future running, so a
lower-index future can still be pending, and cancelling it would let a higher-index error
surface instead of the lowest one. The executor is shut down with `cancel_futures=True` on any
exception, including a `submit` call that itself raises (an executor left running keeps its
workers alive, so the interpreter's own exit handler would finish the very work the abort
exists to abandon). If a custom `executor_factory` ever returns fewer results than work items
with no exception to explain it, `_execute` raises rather than handing the gate a quietly
partial run.

**Runners, judges and evaluators must be safe to share across threads.** No mutable instance
state touched by `run`/`evaluate`/`judge`. This holds today for free: the fakes read immutable
dicts and return `model_copy(deep=True)`, the deterministic evaluators have no instance state,
and `PydanticAIRunner` builds a fresh agent per run. It is a constraint on what comes next.

**The action fails closed.** `shell: bash` steps already run under `bash --noprofile --norc
-eo pipefail`, so `-e` is on before the action's own script runs a line; the run step captures
the CLI's exit code itself (`code=0; skill-lens run ... || code=$?`) before `-e` gets a chance
to discard it. Every step after that — publishing the step summary, reading the JSON report,
re-raising the exit code — carries `if: always()`, so a failing run still gets its summary
published and its outputs read. The final step exits `"${CODE:-1}"`: an *empty* code means the
run step never completed at all (a failed install, a cancelled job), and a gate that cannot
prove it passed must fail rather than default to success.

**Nothing publishes that has not been verified in the same run.** `release.yml` chains three
jobs — `verify` (lint, format, the full suite), `release` (`cz bump`, push, build, upload) and
`publish` (download that artifact, upload to PyPI) — with `needs:`, not across separate
workflows. That shape is forced: GitHub starts no new workflow run from a push made with
`GITHUB_TOKEN`, so a `publish` workflow listening on tag pushes would never fire, and the
release would tag and then silently ship nothing. Chaining also gives the property worth
having — `publish` is unreachable except through a green `verify`, and it uploads the artifact
`release` built rather than rebuilding, so the bytes that ship are the bytes that were tested.
Publishing is irreversible: PyPI refuses a re-upload of a version that already exists, which is
why every gate here fails closed. The corollary is that there is **no manual path to PyPI**; a
locally bumped and pushed tag produces a run with nothing to release.

**A merge with no releasable commit publishes nothing and fails nothing.** `cz bump` signals
"nothing to release" through its exit code — `21` (`NoneIncrementExit`) and `3`
(`NoCommitsFoundError`) — so the bump step deliberately runs without `set -e`, which would
discard the code before it could be read, and checks every other command by hand instead.

**The pushed release tag is annotated, and the job proves it reached `origin`.**
`git push --follow-tags` pushes only *annotated* tags, and Commitizen creates a lightweight one
unless told otherwise, so `annotated_tag = true` is what stops the bump commit reaching `main`
while its tag dies on the runner. Because `publish` is reached through `needs:` and not through
the tag, that loss would not stop a release: a `git ls-remote` check runs right after the push
and fails loudly instead. The push is `--atomic` so the commit and tag land together or not at
all, and `cz bump --check-consistency` aborts before writing anything if a file listed in
`version_files` no longer contains the current version — a flag on the command, because
Commitizen reads it only from the CLI and never from `pyproject.toml`. That abort is the last
line rather than the first: `tests/test_release_config.py` asserts the same property on every
pull request, so a reformatted pin is caught where it is cheap to fix instead of costing a
release on `main`.

The tag the lookup builds is checked against `tag_format` rather than trusted. The workflow
hardcodes the `v` prefix that `[tool.commitizen] tag_format` configures — two copies of one
string in two files — so `tests/test_release_workflow.py` derives the prefix from the setting
and requires the workflow to use it. Changing the format alone would tag correctly, push
successfully, and break only the lookup, failing *after* the push: the one unrecoverable state
here, since the version is spent, `publish` never became eligible to re-run, and a fresh run
finds nothing to release.

**No long-lived publishing credential exists.** PyPI accepts the upload because the job proves
its identity with a short-lived token (Trusted Publishing, over OIDC), so `publish` needs
`id-token: write` and nothing else — it cannot write to the repository. Permissions are granted
per job against a workflow-level `permissions: {}`, so a job added later inherits nothing.

**A cassette refresh proves its recordings replay, and checks them for secrets, before pushing.**
It re-records with `--record-mode=rewrite`: `once` only fills in a *missing* cassette and
write-protects one already loaded, so it cannot refresh an existing recording. It then stages
the recordings before either check, because `git diff` cannot see an untracked file and a
freshly recorded cassette is exactly that — without staging, the scan would read as a lock while
checking nothing on the one path that creates a file. It hands back a **branch**, never a pull
request, because a pull request opened with `GITHUB_TOKEN` gets no CI checks, and on a cassette
refresh those checks are the whole point of the review.

### Real-execution tools (M6 part 1)

**A built-in tool never raises; it returns a message the model can read.** `list_files`,
`read_file` and `write_file` in `runners/tools.py` catch `PathRefused`, `OSError` and
`UnicodeError` and turn every one into an ordinary tool-result string. `Workspace`'s own
methods (`resolve`, `read`, `write`) *do* raise — that split is deliberate: an evaluator or
the loader wants an exception, because a refused path there is a genuine authoring error,
but a tool must never raise, because the model choosing a bad path is an eval signal and an
exception would surface it as an infra failure instead.

**No path outside the workspace root can be read or written.** Every candidate path is
resolved and then checked against the root with `Path.is_relative_to`, never trusted from
its spelling alone — `check_relative_path` rejects an absolute path, a drive, or a `..`
segment before resolution even runs, so the refusal message can name what's wrong rather
than a location the author never wrote. The root itself is resolved once, at creation
(`Path(tempfile.mkdtemp(...)).resolve()`): macOS resolves `/tmp` to `/private/tmp`, and an
unresolved root would make every later containment check compare two spellings of the same
directory.

**Every work item gets its own workspace, and two arms never share one.**
`orchestrator._run_one` creates a workspace fresh for each (skill, case, runner, arm,
repeat_index) combination via `tempfile.mkdtemp`, which is atomic — there is no window in
which two work items racing for a directory name could collide. A shared directory would
let the baseline arm read what the candidate wrote, or vice versa, corrupting the very
comparison `--baseline` exists to make.

**The workspace preamble is byte-identical in both arms and never names the skill.**
`WORKSPACE_PREAMBLE` is appended in `_instructions` purely on whether the case has a
workspace, never on which arm is running — the same discipline `BASELINE_PREAMBLE` already
follows for the skill's own text. Added to the candidate arm only, that text would itself
become part of what `--min-delta` measures, inflating (or deflating) a comparison that is
supposed to isolate the skill's contribution.

**`RunResult.workspace` is non-null only while the directory exists, and the orchestrator
stamps it unconditionally.** `_run_one` overwrites whatever the runner returned with
`workspace.root if workspace is not None else None` after every run, including the `None`
case — so a non-conforming adapter that ignores the `workspace` parameter cannot smuggle a
path of its own into the report, and a case with no `workspace:` block can never show one
either. Once the directory is deleted, the field is cleared in the same step, so it can
never point at something that is already gone.

**Workspace cleanup never changes a verdict, and lives in a `finally`.** `Workspace.cleanup`
suppresses its own errors (`shutil.rmtree(..., ignore_errors=True)`) — deleting a temp
directory is harness housekeeping, and a cleanup failure turning a passing case red would be
the tool reporting on itself instead of on the skill. It runs in `_run_one`'s `finally`
block specifically so that an authoring error raised by an evaluator still deletes the
directory on its way out, rather than leaking it.

**A workspace creation or seeding failure is `errored`, never `failed`.** A full disk or a
permissions problem says nothing about the skill under test, so `create_workspace` raises
`WorkspaceError` and `_run_one` reports the case as `errored`, with the exception's message
in `RunResult.error`. Seeding is subject to exactly the same rule: a seed file that cannot
be written trips the same path, because a seed the harness itself could not write says
nothing about the skill either — and a half-seeded directory would be worse than none, so
`create_workspace` cleans up before re-raising.

**Artifacts reach the judge as fenced, untrusted data.** Each is wrapped in its own
`<artifact id="..." name="...">...</artifact id="...">` block, and its id is derived from
both the trusted **name** and the untrusted **content** — salted with the name because
`sha256("")` is a published constant (`e3b0c442...`) any model could reproduce from memory,
so an empty artifact would otherwise get a guessable fence id a *later* artifact could echo
as a forged closer. An artifact's boundary is its **first** matching closing tag — the
opposite of the response fence's "last matching closer wins" rule — because each artifact
block is closed immediately: unlike the response, which is always the last thing before the
checks, an artifact may be followed by more attacker-controlled text (another artifact, or
the checks list itself), so "last wins" would not be a safe rule there.

**`file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error**
(exit 2), and so is a `judge.artifacts` entry that could never be produced (one that fails
`check_relative_path`, e.g. `../escape.txt`). Both are caught in `cases/loader.py` before any
case runs — a case with no filesystem can never satisfy either, so scoring it as a failure
would blame the skill for a check that could not have held under any output.

**Judge artifact bytes are capped, and truncation is visible.** `MAX_ARTIFACT_BYTES` and
`MAX_ARTIFACTS_TOTAL_BYTES` bound the untrusted, model-produced *content* a judge prompt can
carry — a judge handed a large volume of irrelevant text grades worse, not better — and a
cut file is marked with a visible `... [truncated, N bytes omitted]` note rather than being
silently shortened. A sentinel (`(not produced)`, `(not valid UTF-8 text)`, `(omitted,
artifact budget exhausted)`) never consumes that budget: the cap bounds untrusted model
content, and a sentinel is fixed, harness-authored text with nothing for a content budget to
police.

**An unknown assertion kind is caught at load time**, before any case runs and before any
money is spent. `cases/loader.py`'s `_validate_assertions` checks every `kind` against its
own field-requirements table — deliberately a second table, not a reuse of
`AssertionEvaluator`'s `_CHECKS` (one maps a kind to a predicate, the other to which fields
it requires and allows), pinned together by a test rather than an import. Discovery is a
separate sequential pass ahead of execution (an M5 invariant), so a bad kind anywhere in a
suite aborts the whole run before the first case, real or fake, is charged for.

**A configured cap reaches the workspace.** `max_file_bytes`, `max_files` and
`max_total_bytes` flow from `Config` through `cli.py`'s `WorkspaceLimits` construction,
through `orchestrator.run_evals`'s `workspace_limits` parameter, into every
`create_workspace` call. A limit read from config and then dropped somewhere on that path
would leave the built-in default silently in force, and the only symptom would be a refusal
message quoting a number the user never set.

**Every kept directory is printed, however keeping was turned on.** `_kept_workspaces` in
`reporters/console.py` renders a `Kept workspaces` section whenever *any* outcome carries a
non-null `workspace`, regardless of whether `--keep-workspace` or the config file's
`keep_workspace` is what kept it. That is what makes `keep_workspace = true` safe to commit:
a persistent setting that produced no visible output would fill a disk with nothing on
screen to explain why.

### Bundled scripts (M6 part 2)

**Script execution is off unless the run turned it on.** `allow_scripts` /
`--allow-scripts` is the only switch; nothing in an eval file or a `SKILL.md` can enable
it. A `SKILL.md` under evaluation is, by construction, code nobody has vetted, and the
person who writes the eval is usually the person who wrote the skill. Reading the bundle
needs no opt-in: reading a file the repository already contains changes nothing.

**The bundle is `scripts/`, `references/`, `assets/` and nothing else.** `SkillBundle`
refuses any other first path component, so `*.eval.yaml` and `evals/` — the expected
answers — are never readable by the agent. `listing()` also leaves out a symbolic link
whose target resolves outside the root, so it never advertises a file `read` would refuse.

**`Skill.bundle_root` defaults to `None`, and only the loader and the baseline resolver
set it.** Keying the bundle tools on `Skill.path` would leak the candidate's scripts into
the `--baseline none` arm, since every `Skill` has a path; a `Skill` built by hand in a
test has no bundle for the same reason.

**A script's environment is built from an allowlist, never inherited.** `PATH`, `HOME`,
`LANG`, `LC_ALL`, `LC_CTYPE`, `TZ` (plus what Python needs to start on Windows), then
`TMPDIR`/`TMP`/`TEMP`, `PYTHONDONTWRITEBYTECODE` and `PYTHONIOENCODING`. The key that pays
for the run is absent by construction, not by remembering to delete it.

**A script runs with `shell=False`, its arguments as argv, always.** Nothing the model
sends is joined into a command line; a NUL byte in an argument is refused by `Popen`
before anything is spawned, and that refusal is text the model reads.

**A timeout kills the process group, not just the child.** `start_new_session=True` on
POSIX (`CREATE_NEW_PROCESS_GROUP` + `taskkill /T` on Windows), so a script that starts
`sleep 1000` and exits leaves nothing behind. One honest limit: a script that calls
`os.setsid()` leaves the group and survives the kill on macOS; under `bwrap`,
`--unshare-pid` and `--die-with-parent` still take it down with the sandbox.

**Script output is read from files, capped, and a cut is never silent.** Captured into
memory, a script printing gigabytes inside the timeout would take the harness down. The
capture files are read back through the descriptors the harness opened *before* the
process started — `Popen` shares the open-file description with the child, so the inode
is fixed at spawn time — never by re-opening the path, because the script owns the scratch
directory and could put a symlink (to smuggle in another file's content) or a FIFO (whose
open would block forever) at that path.

**`run_script` never raises, and an unrunnable script is never `RunResult.error`.** A
missing script, a path outside `scripts/`, an unmapped extension, an interpreter that is
not on `PATH` or will not start — each is text the model reads; that reading is the eval
signal. Every refusal carries what the model needs for its next call to be right: the
bundled scripts when the name was wrong, the allowed extensions when the interpreter was.

**The sandbox decision is made once per run and appears on the report.** `preflight`
runs in `run_evals` after discovery and before any case: `required` without a backend,
and a missing interpreter for a discovered script, raise `ScriptSetupError` (exit 2)
before any money is spent. The backend is *executed*, not merely found on `PATH`. Preflight
and the "bundles N scripts; execution is off" notes both iterate every *discovered* skill,
including one `--tag` or `--case` filters out or that has no cases — a fail-closed check
that quietly skipped some skills would not be one. Baseline bundles are not preflighted;
an interpreter is looked up again at call time, and a miss there is a refusal, not an
error. The probe also fails closed on a temporary-directory path containing a double
quote, which a `sandbox-exec` profile cannot express safely.

**The sandbox hides other skill-lens temporary directories, and re-allows the bundle
explicitly.** The macOS profile allows by default, denies `network*` and `file-write*`,
re-allows writes under the workspace and scratch (and `file-write-data` on `/dev/null`),
denies `file-read*` under the resolved system temporary directory, then re-allows reads
under the workspace, scratch and the bundle; `bwrap` mounts an empty `tmpfs` over the
temporary directory and binds the same three back in (the bundle read-only). The bundle
must be re-allowed because a `--baseline previous` bundle is extracted *under* the
temporary directory. The temp-dir denial is a `subpath`, not a regex: an escaped path in a
regex literal would silently stop matching on any metacharacter — a fail-open the blanket
`subpath` cannot have. Reads elsewhere are allowed; the docs say so rather than pretend
otherwise.

**A previous baseline carries its own bundle**, extracted with `git archive <sha> -- .`
from the skill directory (verified: that form gives subtree-relative paths; `<sha>:./`
gives an empty archive), filtered to the three directories, with `tarfile`'s `data` filter
— which is why `requires-python` is `>=3.11.4`, the release that added `filter=`. The
commit is the one that last edited `SKILL.md` at the previous version — the version is the
authority — so a bundle-only commit after it is invisible, and a very large historical
`assets/` can hit the 10-second git timeout, which comes back as `BaselineUnavailable`
(`cannot archive commit …`), a note, never an error. A commit with no bundle gives
`bundle_root=None` — never the candidate's.

**Baseline bundle directories are deleted when the run ends, however it ends.**
`_BaselineStore.cleanup()` runs in a `finally` around execution. `--keep-workspace` does
not keep them: they are an input, and the commit they came from is in the report already.

**Bundle tools require a `workspace:` block, and their names are reserved in every
workspace case.** The workspace is the script's working directory and the sandbox's only
writable area; a name that is sometimes free is a name nobody can rely on. The tools are
registered in `mode: offered` too — an agent that declines the skill has no reason to call
them, and one that triggers it needs them exactly as a `loaded` case does.

**The workspace preamble is unchanged** — byte-identical in both arms, naming no skill.
The bundle tools describe themselves; "run `scripts/count.py`" comes from `SKILL.md`,
which is the thing under measurement.

**A script's temporary files never enter the workspace.** `TMPDIR` points at a per-call
scratch directory outside it, deleted afterwards, so `list_files`, `file-produced` and the
judge's artifacts see only what the agent and the script deliberately produced. A script
writes to disk directly, so the workspace caps cannot refuse it beforehand: the directory
is measured after the call, an overrun becomes a `warning:` line on the tool result, and
every later `write_file` is refused by the existing projection. The timeout is the real
bound on what one script can write.

**`scripts=` reaches a runner only when execution is on.** `_run_one` passes the keyword
only when the runtime is set, so a third-party runner written against M6 part 1 keeps
working until the day someone turns scripts on — at which point a `TypeError` names the
runner that cannot take them, rather than the scripts silently never running.

### Developer experience (M7)

**Output is expanded only under non-passing candidate outcomes.** `failure_context` returns
`None` for a passed outcome, a baseline outcome or an outcome with no result, and every
reporter renders nothing on `None`. Fifty green cases stay fifty lines, and a baseline
outcome — which is not the verdict — is never expanded.

**A cut is never silent.** `FailureContext.cut` is the exact number of characters removed
and `cut_note` states it, with the flag that lifts the cap. A truncated excerpt that looked
complete would be worse than none.

**The three reporters render one `FailureContext`.** Console, Markdown and JUnit call the
same helper and may differ only in markup. Two excerpts computed separately would drift the
first time one of them changed.

**A `--case` matching nothing fails the gate.** The fourth zero-cases cause, beside no
skills, no cases and `--tag`: `case_filtered_skills` on `RunReport` records the skills the
filter emptied, and `evaluate_gate` names the flag. A typo in a filter is not a pass.

**`--case` has no config key.** A filter chooses which cases one invocation runs. A config
file that permanently narrowed the suite would let a green run measure less than the
repository declares. `full_output` and `keep_workspace` are rendering knobs with no such
failure mode, which is why they may live in the file.

**`init` never creates an `evals/` directory beside existing `*.eval.yaml` files.**
Discovery prefers `evals/` when it exists, so creating it would hide the files already
there from every later run — silently, with nothing red. `scaffold_target` writes beside
`SKILL.md` in that layout.

**Batch `init` never overwrites.** A skill with any eval file is skipped and named;
`--force` in batch mode is a user error. Rewriting every suite in a repository must never be
one flag away.

**The unfilled-scaffold scan covers mapping keys as well as values.** `workspace: files:` is
keyed by filename; an unfilled filename would otherwise seed a file literally named after
the placeholder.

**`examples/greeting` stays at `1.1.0` or later, with `1.0.0` in history.** `--baseline
previous` resolves an earlier *declared version* from git, so the shipped comparative
example only works because the bump is real and the earlier version is on `main`.

### Security checks

**The dependency audit is one command, spelled identically in three places, and its exceptions
live in one table.** `uv audit` reads `uv.lock` — the exact set anyone installs — and asks OSV
about every package in every extra and group, dev tooling included, because that is what runs
on maintainers' machines and in CI. It runs in `security.yml` on every pull request, every push
to `main`, weekly on a schedule and on demand; in `release.yml`'s `verify` job; and in the
`pre-push` hook. The schedule is what makes it a monitor rather than a check: an advisory can be
published against a lockfile nobody has touched, and only a timer notices. `verify` re-runs it
rather than trusting the pull request's green check because an advisory can land between the
merge and the tag, and nothing publishes that `verify` did not pass.
`tests/test_security_checks.py` requires the three commands to be byte-identical, so an
exception can never apply to CI and not to the release, or to a laptop and not to CI.

Exceptions go in `[tool.uv.audit]` in `pyproject.toml`, which every copy of the command reads,
and only as `ignore-until-fixed`: it stops hiding an advisory the day a fixed version exists,
so the list can only shrink on its own. Plain `ignore` hides a finding forever and is rejected.
uv does not validate that table — a misspelled key is silently dropped and the finding silently
kept — so the same test rejects any key but the allowed one. Any finding fails; a severity
threshold is a decision someone has to defend for every advisory, while "fix it or record why
not" is a decision made once. `--locked` makes uv fail when `pyproject.toml` and `uv.lock`
disagree instead of quietly auditing a fresh resolution nobody installs, and
`--preview-features audit` acknowledges that the command is still a uv preview feature — if
its interface changes, the wiring tests fail on the pull request that bumps uv, not in a
release.

The build backend is audited and pinned too. `uv.lock` records what the project installs, not
what builds it: `[build-system] requires` is resolved fresh at build time, so the artifact
could be produced by a `hatchling` the audit never saw. A `build` dependency group mirrors
those requirements — a test keeps the two lists equal, or the group would audit a backend the
build does not use — which puts the backend and its own dependencies in the lockfile. The
release then exports that group (`uv export --frozen --only-group build`) as a constraint file
for `uv build --build-constraint`, so what builds the published wheel is exactly what `verify`
audited.

**The audit runs at push time locally, not commit time.** It needs the network. A commit hook
would fail offline and teach people to skip it; a push needs the network anyway, so the check
costs nothing extra there and cannot be blamed on a bad connection.

**Ruff's `S` rules are on, and a false positive is suppressed at the site with its reason.**
The `flake8-bandit` family rides on the existing `ruff check`, so it runs everywhere lint does
with no extra step to forget. Three `src/` sites trip it and all are deliberate: `baseline.py`
starts `git` by name because an absolute path is wrong on most machines and a missing git must
come back as `BaselineUnavailable`, never a crash; `yaml_loading.py` passes `StrictBoolLoader`
to `yaml.load`, and ruff cannot see that the loader subclasses `SafeLoader`; `scripts.py`
starts the sandbox probe, the interpreter and (on Windows) `taskkill` from an argv list with
no shell — the module's whole point — and finds `taskkill` on `PATH` by name for the same
reason `baseline.py` finds `git`. Each carries an inline `noqa` with that reason. `tests/**` and `scripts/**` have per-directory ignores for
`assert`, subprocess-with-fixed-argv, XML parsing and literal `/tmp` strings used as fake path
values. A rule is never switched off for `src/`
because one site trips it.

**Every action is pinned to a commit SHA with a `# vX.Y.Z` comment, and nothing grants write
access at the workflow level.** `actions/checkout@v4` runs whatever `v4` points at on the day,
so a compromised or mistaken re-tag would run different code in CI with no change in this
repository; a commit hash cannot be moved. The trailing version comment is what keeps a pin
readable, and it is the comment Dependabot rewrites when it bumps the SHA, so the two never
disagree. Dependabot watches both `uv.lock` and the actions weekly, because a pinned hash never
moves on its own; its commit prefixes are Conventional Commit types, checked by running them
through `cz check`, since the pull-request title becomes the commit that `cz bump` parses.
Permissions are granted per job against a top-level block that is empty or read-only, so a job
added later inherits nothing and the docs `build` job — third-party tooling on the checkout —
never holds the token that publishes to Pages. `tests/test_supply_chain.py` holds all of it.

**The SBOM never enters `dist/`, and the GitHub Release is created only after PyPI accepted the
upload.** The CycloneDX export reads the lockfile `verify` just audited, `--frozen`, for the
runtime dependencies and the `pydantic-ai` extra — what an installer gets, not the dev or docs
groups that ship to nobody. It is written to `sbom/` and uploaded as its own artifact because
`publish` sends every file in the `dist` artifact to PyPI, which would reject an SBOM and fail
the upload after the tag is already pushed. `github-release` needs `publish`: a GitHub Release is
the first outward-facing sign of a version, and one that exists before the upload could
advertise a version `pip install` cannot find. The job is re-run safe — the documented recovery
for any release step — because creation is skipped when `gh release view` finds the release,
and assets are uploaded with `--clobber` so a partial run converges instead of refusing. Its
notes are `cz changelog <version> --dry-run`: the same commits that chose the version number,
and nothing else, are the source of truth for what a release contains. The notes are written in
`release`, which already has the checkout, the history and the tools, and handed on as an
artifact — so `github-release`, the one job holding `contents: write`, has no checkout, runs no
`uv`, and installs nothing. Everything it publishes was built and verified by an earlier job;
a compromised dependency or build hook never executes under the token that can write releases.
(The docs `build` job, by contrast, needs `pages: read` and not only `contents: read`:
`actions/configure-pages` calls `GET /repos/{owner}/{repo}/pages` and fails the job when that
call is refused — a tightening that would only have shown up on the next push to `main`.)

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
