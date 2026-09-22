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

`Runner` may also define an optional `preflight(skills, cases_by_skill) -> ProductStatus |
None`. The orchestrator calls it once per run, after discovery and before any case, with the
candidate-arm skills and the cases planned for that runner; it raises an authoring error to
abort the run before anything is spent, and may return a `ProductStatus` for the report.
`FakeRunner`, `PydanticAIRunner` and `LangChainRunner` define it to refuse a `trajectory:`
naming a tool they cannot offer the case (`runners/preflight.py`'s `check_trajectory_names`)
and return None; `ProductRunner` returns its status. The hook is looked up with
`getattr`, so a runner written against an earlier version keeps working. `Judge` may
likewise define an optional `preflight() -> ProductStatus | None`, taking no arguments — a
judge has no cases to inspect — called in the same pass, right after the runners' hooks;
`ProductJudge` defines it, the framework judges and `FakeJudge` do not. A status equal to
one a runner already returned is not added again, so a product serving as both runner and
judge is one entry on `RunReport.products`.

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | Every Pydantic model in the project. No other module defines a data shape. |
| `cli.py` | Typer entry point. Wires config → loaders → runner → orchestrator → reporters → gate, and owns the exit-code contract. |
| `orchestrator.py` | Plans the skill × case × runner × arm × repeat matrix (sequential discovery), then executes it — a plain loop at `concurrency == 1`, a bounded thread pool above it — applying every evaluator to each result. `RunOptions` bundles the per-run settings (kept workspaces, the workspace caps, the script policy); when scripts are on it runs `scripts.preflight` once between discovery and execution; between discovery and execution it also calls every runner's optional `preflight` hook, once each with the candidate-arm cases planned for it, then the judge's optional `preflight()` (no arguments), and puts what they return on `RunReport.products`, a status already present listed once; and `_BaselineStore` owns the directory previous bundles are extracted into, deleted in a `finally`. |
| `gating.py` | Turns a `RunReport` into a pass/fail decision plus reasons and an exit code. |
| `config.py` | Loads `skill-lens.toml` by explicit path or upward discovery. Never reads secrets. Owns `ProductSettings` (one `[runners.<name>]` table) and `Config.product`, which turns a preset plus its table into the `Product` a runner starts; `PRODUCT_NAMES` is the list the CLI accepts. |
| `yaml_loading.py` | A YAML loader that does not treat bare `yes`/`no`/`on`/`off` as booleans. |
| `skills/loader.py` | Walks a path for `SKILL.md` files and parses them into `Skill` models, via `parse_skill_text` — the shared core both `parse_skill_file` and `skills/baseline.py` parse through, so a blob from git and a file on disk go through one code path. |
| `skills/baseline.py` | Resolves a skill's previous version from git history for `--baseline previous`, and extracts that same commit's bundle (`git archive`, `tarfile` with the `data` filter) so the old instructions are paired with the old scripts. Shells out to `git`, never raises for an environmental failure, imports no agent framework. |
| `cases/loader.py` | Finds and parses eval YAML for a skill into `EvalCase` models; imports the file's `tool_libraries:` and resolves every `- ref:` into the library's `ToolSpec` on the raw mapping, before validation. |
| `cases/checks.py` | The checks an eval file and a tool library share: the `TODO(skill-lens)` sentinel walk (`find_unfilled`, keys and values, cycle-safe), `check_tool_schema` (`parameters`/`input_schema` exclusive, `check_schema`, top-level `type: object`) and `check_tool_returns` (a `returns:` lookup's `when:` keys the tool can carry, no entry unreachable), which `check_tool` runs together. Below both loaders so neither imports the other. |
| `cases/tool_libraries.py` | A tool library is a YAML file with one top-level `tools:` list — the block `mcp-import` prints. `parse_tool_library` checks each tool as the case loader would and names the file and position in every refusal; `load_tool_libraries` resolves an eval file's `tool_libraries:` entries against the eval file's directory (file or directory, never absolute) into one `ToolLibrary` that refuses a name declared twice; `ToolLibrary.resolve` turns a `ref:` into its `ToolSpec` or says what to fix. Raises `ToolLibraryError`; the case loader wraps it with the importing file. |
| `scaffold.py` | Renders the starter eval suite `skill-lens init` writes. Pure: a `Skill` in, the file text out, with the IO left to `cli.py`. `scaffold_target` decides where `init` writes. |
| `mcp_import.py` | Turns a saved MCP `tools/list` response into mock-tool YAML: `parse_tools_list` (three accepted shapes, every refusal a `McpImportError`) and `render_tool_mocks` (a pasteable `tools:` block). Pure: text in, text out; `cli.py` reads the file or stdin. Never touches the network. |
| `workspace.py` | The per-case temporary directory: creation, seeding, path containment, and cleanup. Framework-neutral, like every other top-level module. Its methods **raise** (`PathRefused`, `WorkspaceError`) for `cases/loader.py` and the evaluators to catch as authoring or infra errors; `runners/tools.py`'s built-in tools catch those same exceptions and turn them into ordinary tool-result strings instead. |
| `bundle.py` | A read-only view of the three Agent Skills directories beside `SKILL.md` (`scripts/`, `references/`, `assets/`) and nothing else — an eval file beside `SKILL.md` is never readable by the agent. Same "methods raise, tools catch" split as `workspace.py`. |
| `scripts.py` | Runs a bundled script: the policy, the once-per-run preflight (interpreters on `PATH`, the sandbox probe), the allowlisted environment, the scratch directory, the process-group timeout and the capped output read through the harness's own descriptors (both via `process.py`), and the `sandbox-exec` / `bwrap` wrapping. Never raises for a script that will not run; raises `ScriptSetupError` only from preflight. |
| `process.py` | Starts a child in its own process group, waits with a timeout, kills the group after every exit, and reads output through the harness's own handle. Shared by `scripts.py` and `runners/product.py`; imports nothing from the rest of the project. |
| `matching.py` | `structural_match`: one author's mapping against a call's recorded arguments — a subset at every level, or exact under `exact=True`; lists element by element at equal length; a bool only ever equals a bool. Shared by `trajectory.call_args` (`evaluators/trajectory.py`), a mock's `when:` (`ToolResponse.matches` in `models.py`) and the MCP bridge's `tools/call` (`mcp_bridge.py`); imports nothing from the rest of the project. |
| `mcp_bridge.py` | The stdio MCP server a product starts to reach a case's mock tools (`python -m skill_lens.mcp_bridge <spec>`): `initialize`, `ping`, `tools/list` and `tools/call` over JSON-RPC 2.0, one message per line, `returns:` answered in all three shapes by the rules `runners/tools.py` applies (one value; a sequence in call order; a `when:` lookup through `matching.structural_match`), every list and call appended to the record file the spec names; `--check` drives the handlers in-process for preflight. Imports only `matching.py` from the project. |
| `runners/base.py` | The `Runner` protocol. `run` takes optional `workspace=` and `scripts=` keywords, both additive with a default, so a runner written against an earlier version keeps working. |
| `runners/fake.py` | A deterministic, offline, scripted runner. The default, and the backbone of the zero-cost test tier. |
| `runners/prompting.py` | The three preambles and the system-prompt builder every runner calls. Framework-free, so the rules `--min-delta` measures against exist once. |
| `runners/retry.py` | The transient-retry loop and the HTTP status policy every adapter shares; each adapter supplies its own `is_transient`. |
| `runners/pydantic_ai.py` | The PydanticAI runner adapter. **One of the four modules that import an agent framework.** |
| `runners/langchain.py` | The LangChain runner adapter. **One of the four modules that import an agent framework.** |
| `runners/traces.py` | The Copilot JSONL and Claude Code `stream-json` parsers, each producing one `Trace`. Pure functions; a structural problem is `Trace.error`, never a raise. |
| `runners/product.py` | The product runner: a `Product` value (argv template, skill directory, invocation spelling, trace parser, version command, how it takes the MCP bridge), the two presets, skill delivery into the product's working directory, the subprocess invocation — with the bridge's config appended last when the case declares `tools:` — the connection check and the tool-name mapping afterwards, and the once-per-run `preflight`. Imports no agent framework. |
| `runners/mcp.py` | The runner's side of the MCP bridge: `McpSupport` (each product's config flag, tool spelling, config-entry keys and hiding flags, as verified against the product), `write_bridge` (the spec — each tool's schema and its `returns:` in whichever shape — and the product's config in a fresh directory), `Bridge.connected` (the record's `list` event), `restore_tool_names` (the product's spelling back to the case's) and `probe_bridge` (the once-per-run `--check`). Imports no agent framework. |
| `runners/tools.py` | Builds framework-neutral `AgentTool`s (name + JSON schema + callable) from a case's `tools:` block — one canned value, a sequence consumed in call order, or a lookup by argument — the built-in workspace tools, and the bundle tools (`list_skill_files`, `read_skill_file`, and `run_script` when the bundle has scripts and the run enabled them). Owns the six-name `BUILTIN_TOOL_NAMES` the case loader reads. |
| `runners/preflight.py` | What a framework runner verifies before any spend: the provider API key, that every `trajectory:` name is a tool the case will have (`check_trajectory_names`, called from the three framework runners' `preflight`), and the `UnsupportedBaseURL` the two keyed adapters raise for a `base_url` their provider cannot take. |
| `runners/pricing.py` | Turns provider usage into USD. Degrades rather than raising. |
| `evaluators/base.py` | The `Evaluator` protocol. |
| `evaluators/assertion.py` | Rule-based scoring of the final output text. |
| `evaluators/trajectory.py` | Scoring which tools were called, in what order, how many times, and with what arguments. |
| `evaluators/budget.py` | Scoring efficiency: tokens, cost, latency. |
| `evaluators/judge.py` | Rubric scoring. Holds no framework code; takes a `Judge` by injection. |
| `comparison.py` | Turns a two-armed `RunReport` into a `Delta`: pairing, sign conventions, low-signal checks, high-variance cases. Pure — no IO, no provider calls. |
| `judges/base.py` | The `Judge` protocol. |
| `judges/prompt.py` | Renders a `JudgeRequest` into prompt text. Pure, deterministic, no IO. |
| `judges/fake.py` | A scripted, offline judge. The default — and unscripted it *errors* rather than passing, so an unjudged rubric is never a quiet green. |
| `judges/pydantic_ai.py` | The PydanticAI judge adapter. **Another of the four.** |
| `judges/langchain.py` | The LangChain judge adapter. **The last of the four.** |
| `judges/product.py` | The product judge: the shared judge prompt as one text turn closed by a JSON-only line, sent through `runners/product.py`'s `invoke`/`read_trace` from an empty directory with no skill; the first balanced JSON object in the reply validated as `JudgeOutput`, everything else `JudgeVerdict.error`. Defines the judge's `preflight()`. Imports no agent framework. |
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
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml; tool_libraries: → cases/tool_libraries; ref: resolved) ──► [EvalCase]
  └─ scripts.preflight (once, only if allow_scripts) ──► ScriptRuntime | ScriptSetupError (exit 2)
  └─ each Runner.preflight(skills, cases_by_skill), then Judge.preflight() (once, where defined) ──► [ProductStatus] | ProductSetupError (exit 2)

matrix: for each (skill × case × arm × repeat × runner)
    Runner.run ──► RunResult ──► each Evaluator ──► [EvalScore]
                                                       └─► CaseOutcome (arm, repeat_index)

aggregate ──► RunReport ──► comparison.build_delta ──► Delta | None
                        └─► reporters/  ──► console + JSON + JUnit + Markdown
                        └─► gating      ──► exit code
```

`arm` is `"candidate"` for the skill under test and `"baseline"` for the comparison skill;
absent `--baseline` every outcome is `"candidate"` and `build_delta` returns `None`, so the
matrix, the aggregates and the reporters all degrade to exactly the single-arm shape.

## Core data models

All live in `models.py`.

| Model | Carries |
| --- | --- |
| `Skill` | name, description, instructions, `version` (declared frontmatter version, `""` if absent), path, `variant` (`"candidate"` or `"baseline"`), `bundle_root` (the directory whose `scripts/`, `references/` and `assets/` the agent may read; `None` when the skill ships none), `markdown` (the `SKILL.md` text verbatim — its text as written, though line endings are normalised — for a product runner to deliver; `""` for the `--baseline none` skill, so no directory is written) |
| `EvalCase` | name, task, `tools`, `assertions`, `trajectory`, `budget`, `tags` |
| `ToolSpec` | one mock tool: name, description, `parameters` or `input_schema`, and `returns` — a string for every call, a `list[str]` consumed in call order, or a `list[ToolResponse]` (`when:` argument subset → `value`) matched per call; `ToolRef` (`ref` + optional `returns` in the same shapes) is what a case writes to import one from a library |
| `RunResult` | output, tool calls, transcript, token split, latency, cost, `cost_note`, `usage_note` (why the token split is `0` when the runner could not count — a declared `max_tokens` then fails as not evaluated), model, `error` |
| `CheckResult` | one check's `id`, `passed`, `evidence` — emitted by the judge and by assertion/trajectory/budget alike |
| `EvalScore` | one evaluator's `passed` / `score` / `detail`, plus its `checks: list[CheckResult]` |
| `BaselineNote` | why a skill or case has no baseline arm: `kind` (`"unavailable"` or `"skipped"`) plus a reason |
| `CaseOutcome` | one (skill, case, runner, arm, repetition) combination: status plus its scores and result |
| `ScriptStatus` | the once-per-run sandbox decision: `sandbox` (`"sandbox-exec"`, `"bwrap"` or `"none"`), the probe's `detail`, and `hardening` — the note when the harness could hide its own environment from same-user processes (Linux, non-root), else `None` |
| `ScriptNote` | a skill that bundles scripts while execution is off: `skill_name`, `script_count` |
| `ProductStatus` | one agent product a run executed, as a runner or as the judge, as preflight found it: `name`, `executable`, `version`, and `trust` — the fixed sentence about permission prompts, the full environment and the missing sandbox, on the model so the three reporters cannot drift; equal statuses collapse to one entry, so a product in both seats is listed once |
| `RunReport` | every outcome, skipped and tag-filtered skills, `baseline_kind`, `repeat`, `baseline_notes`, `scripts` (`None` when execution was off), `script_notes`, `products` (one `ProductStatus` per product the run executed, as a runner or as the judge, a product in both seats once; empty otherwise) |

Two fields are **derived, not stored**: `RunResult.tokens` (the input/output split summed)
and `RunResult.errored` (`error is not None`). Aggregates on `RunReport` — `total`,
`passed`, `failed`, `errored`, `pass_rate` — read `candidate_outcomes` only (Decision: baseline
outcomes never count toward the gate); `baseline_outcomes` and `baseline_errored` surface the
comparison side apart from them. `pass_rate_by_skill` is likewise candidate-only.

`comparison.py` adds a second layer of models — `ArmStats`, `CaseStats`, `LowSignalCheck`,
`CaseRef` and `Delta` — that are computed from a `RunReport`, never stored on it. `Delta` is
`None` whenever no baseline arm ran, which is the signal reporters use to fall back to the
single-arm rendering.

## Invariants

The project keeps a set of decided behaviours — `errored` is never `failed`, an authoring
error never scores as a failure, nothing publishes that was not verified in the same run —
each with a test asserting it. Several were bugs caught in review. They are collected in one
reference, because they are looked up rather than read:

**[Invariants](https://emadmokhtar.github.io/skill-evaluator/invariants/)**

A change that breaks one of them should change that page in the same pull request, or it is
not a decided behaviour any more.

## Extension points

**Adding a runner.** Implement `Runner` in a new module under `runners/`, and put every
framework import inside that module. A keyed adapter (one that takes a model and an API
key) is registered in `cli._KEYED_RUNNERS`; set `needs_api_key = True` if it spends money —
`cli.py` then runs the preflight key check before constructing it. A product (an executable
and a trace grammar) is a `Product` preset in `runners/product.py`, listed through
`PRODUCT_NAMES` in `config.py`, and needs no new class. Never raise for a provider failure;
return a `RunResult` with `error` set. Build the system prompt with
`runners/prompting.instructions` and wrap provider calls in `runners/retry.run_with_retries`
rather than writing either again — both adapters must measure the same thing — and build
the agent, tools included, *inside* the retried callable, so a mock whose `returns:` is
consumed in call order starts from the top on every attempt. A runner that
must refuse a run before any case executes defines the optional `preflight` hook and raises
an authoring error from it.

**Adding a judge.** The same shape under `judges/`: a keyed adapter is registered in
`cli._KEYED_JUDGES` and receives the shared model, `judge_temperature` and the retry
settings; a product needs no new class — `ProductJudge` wraps whichever `Product` the
name resolves to, so a new preset in `runners/product.py` (with its `judge_args`, if the
product has a verified way to grade without tools) serves as runner and judge at once.
Render the prompt with `judges/prompt.py`, never raise for a provider failure — set
`JudgeVerdict.error` — and return per-check verdicts only; `JudgeEvaluator` derives
`passed` and `score`. A judge that must refuse a run before any case executes defines the
optional no-argument `preflight()` hook.

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
