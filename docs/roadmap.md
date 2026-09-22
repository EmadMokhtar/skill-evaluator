# Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Scaffolding, config, CLI skeleton, release plumbing | shipped |
| M1 | Loaders, protocols, `FakeRunner`, assertion evaluator, orchestrator, console + JSON reporters, gating | shipped |
| M2 | PydanticAI runner, trajectory + budget evaluators, cost/latency capture, cassette test tier | shipped |
| M3 | LLM-as-judge evaluator (per-check verdicts), triggering evals with negative controls | shipped |
| M4 | Comparative evals: `--baseline`/`--repeat`, delta reporting, `--min-delta` gating | shipped |
| M5 | CI/CD polish: JUnit XML + Markdown reporters, GitHub Action, bounded concurrency | shipped |
| M6 | Real-execution tools: sandboxed built-in toolset, `file-produced`/`json-schema` assertions, bundled files and `run_script` | shipped |
| M7 | DX: failing cases explain themselves, `--case`, `init` batch mode and workspace case, versioned example, quickstart | shipped |
| M8 | LangChain runner and judge (`[langchain]` extra); runner matrix | shipped |
| M9 | Product runners: `copilot`, `claude-code`, a configured `cli`; product judge | shipped |

## What M4 shipped

Every case can now run in two arms — **candidate** (the skill under test) and **baseline**
(either an empty skill, or the skill's previous version resolved from git) — sampled
`--repeat N` times each. Assertion, trajectory and budget evaluators emit one per-check
verdict per declared item so a check can be paired across arms, and `comparison.py` turns a
two-armed report into a delta: pass-rate, token, cost and latency differences, plus advisory
low-signal and high-variance flags. `--min-delta` lets CI require that an edit to `SKILL.md`
actually improved something, gated on the candidate arm only. Full detail is in
[Comparative evals](comparative-evals.md).

Deferred out of M4, tracked for a later milestone: a per-skill `min_delta`, efficiency
regression gates, an explicit `--baseline-ref <rev>` escape hatch, flagging checks that fail
in *both* arms, and bounded concurrency across arms and repeats (M5's territory once
concurrency lands generally).

## What M5 part 1 shipped

`--junit-output` and `--markdown-output` render a run for CI test panes and for GitHub's step
summary and PR comments. `--concurrency N` overlaps the network waits that dominate a run.
A composite GitHub Action wraps the CLI, with example workflows in
[CI integration](ci.md).

An HTML reporter was dropped as YAGNI — nothing in the milestone consumes it. Process and
subinterpreter pools were deferred: the work is network-bound, so multi-core buys nothing
today, and the orchestrator is typed against `concurrent.futures.Executor` so a different pool
is a one-line change if M6's real tool execution introduces CPU-bound work.

## What M5 part 2 shipped

A merge to `main` now verifies the commit, bumps the version from the commit history, tags
it, and publishes to PyPI over Trusted Publishing — no stored credential anywhere. A manual
workflow refreshes the recorded provider traffic and hands it back as a branch to review.
See [Releasing](releasing.md).

## What M6 part 1 shipped

A case can declare a `workspace:` block: a real, contained temporary directory, created
per work item and seeded with the files it names, plus three built-in tools —
`list_files`, `read_file`, `write_file` — the agent can use inside it. Nothing can be read
or written outside it, and no two arms or repetitions ever share one. Assertions can then
target a produced file instead of the chat output — `file-produced` for existence,
`json-schema` for shape, and `file:` as a modifier that points `contains`, `not_contains`,
`regex` and `equals` at a file — and an LLM judge rubric can read named files through
`judge: artifacts:`, so quality that lives inside a document is finally measurable.
`--keep-workspace` keeps the directories for debugging, and every kept one is printed. Full
detail is in [Workspaces](eval-files.md#workspaces) and [The workspace](runners.md#the-workspace).

Running a script bundled with the skill under test was deferred to part 2, because
executing code that shipped with the artifact under evaluation is a different trust
decision from writing files into a temporary directory. See
[What M6 part 2 shipped](#what-m6-part-2-shipped).

## What M6 part 2 shipped

The agent can now read the files a skill ships beside `SKILL.md` — `scripts/`,
`references/`, `assets/`, and nothing else — through `list_skill_files` and
`read_skill_file`, and, only when the run says `allow_scripts = true` or
`--allow-scripts`, run a bundled script through `run_script` with the workspace as its
working directory. Every script runs under portable guards (an allowlisted environment,
a scratch directory, a process-group timeout, capped output read from files) and under an
OS sandbox where one exists (`sandbox-exec` on macOS, `bwrap` on Linux), probed once per
run; the report says which applied, and `script_sandbox = "required"` makes its absence
exit 2. `--baseline previous` pairs the previous `SKILL.md` with the bundle from the same
commit. `examples/log-triage` is a skill whose eval can only pass by running its script —
and covers the `references/` example M7 deferred. Full detail is in
[Bundled files and scripts](runners.md#bundled-files-and-scripts) and
[Security](security.md#running-bundled-scripts).

Deferred: an `init` scaffold case for script-bearing skills, standard input to scripts,
a per-case timeout, an `unshare`-only Linux fallback, denying reads outside the workspace,
and copying a binary asset into the workspace (`read_skill_file` returns text only).

## What M7 shipped

The original M7 list — `init`, docs, more examples, a quickstart — had mostly shipped
early, so M7 was re-scoped around the developer-experience gaps that using the tool
exposed. A non-passing case now shows the agent's output and its tool calls in the
console, the Markdown summary and the JUnit body alike, from one shared excerpt; the
output is cut at 500 characters and a cut is never silent (`full_output` /
`--full-output` lifts it). `--case <text>` reruns the cases whose name contains the text,
and a filter matching nothing fails the gate. `init` gained a fifth scaffold case for
skills that produce a file, writes beside `SKILL.md` when that is where a skill's evals
already live (so it can never hide them behind a new `evals/` directory), and scaffolds
every skill under a directory that has no suite. `examples/greeting` is versioned so
`--baseline previous` works from a checkout, `examples/skill-lens.toml` annotates every
config key, and [Getting started](getting-started.md) is an end-to-end quickstart.

Deferred: a `references/` layout example (shipped with M6 part 2 as `examples/log-triage`),
a repeatable `--case`, and the gating features carried over from M4 and M5 (per-skill
`min_delta`, `--baseline-ref`, both-arms-fail flagging).

## What M8 shipped

A second agent framework behind the same seams. `--runner langchain` drives LangChain
1.x's `create_agent` with the same tools, prompt and workspace the PydanticAI runner
uses, and `judge = "langchain"` grades rubrics with the same prompt and per-check
verdict contract, so the `[langchain]` extra is self-sufficient. The prompt rules and the
retry policy were extracted into two framework-neutral modules so both adapters share
one implementation. Anthropic joined both extras.

Part 2 made the matrix a single invocation: `--runner` is repeatable, `default_runner`
accepts a list, and the action's `runner` input splits on commas. Every `(skill, case,
runner)` outcome counts toward the gate; a duplicate runner is a user error so no outcome
counts twice. Deferred: a per-runner gate threshold, a judge list, and normalising
provider-prefix spellings across frameworks. See the
[M8 design](https://github.com/EmadMokhtar/skill-evaluator/blob/main/docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md).

## What M9 part 1 shipped

A skill written for a named product is now measured under that product. `--runner
copilot` and `--runner claude-code` start GitHub Copilot CLI or Claude Code in its
non-interactive mode, with `SKILL.md` and its bundle placed verbatim (its text as
written; line endings are normalised) where the
product discovers skills, and read the output, tool calls, tokens and the skill-load signal
back from the product's own trace; `--runner cli` does the same for any command a
`[runners.cli]` table names, with stdout as the output. No provider API key is involved:
the product uses its own auth. The two presets carry the argv the probes verified, and a
`[runners.<name>]` table appends flags with `args` (for example, a model) or replaces the argv with
`command`. A once-per-run preflight hook on the `Runner` protocol finds the executable,
runs its `--version`, and refuses the cases the runner cannot serve — `tools:` under any
product, `trajectory:` or `mode: offered` under `cli` — before any quota is spent. Every
report carries `products`: which product, which version, which executable, and the fixed
trust sentence (permission prompts disabled, full environment, no skill-lens sandbox).
`RunResult.usage_note` makes a token limit the product cannot measure a failing check, as
`cost_note` already did for cost; and `--model` or `--judge-model` with nothing in the
run that reads it is now a user error rather than a flag silently ignored. The subprocess
mechanics (process-group kill, capped read through the harness's own handle) moved to
`process.py`, shared with `run_script`. Full detail is in
[Product runners](runners.md#product-runners), [Configuration](configuration.md#product-runners)
and [Security](security.md#product-runners).

Part 2 grades rubrics through the same product; see
[What M9 part 2 shipped](#what-m9-part-2-shipped). Mock tools under a product, deferred by
the M9 design, shipped afterwards through an MCP bridge; see
[What the MCP bridge shipped](#what-the-mcp-bridge-shipped). Still deferred: a per-case
timeout, an automated hermetic Copilot run (one that loads nothing from the user's personal
setup), and tool-name normalisation across products. See the
[M9 design](https://github.com/EmadMokhtar/skill-evaluator/blob/main/docs/superpowers/specs/2026-09-17-skill-lens-m9-design.md).

## What M9 part 2 shipped

The product judge: `judge = "copilot"`, `"claude-code"` or `"cli"` grades every `judge:`
block through the product named in its `[runners.<name>]` table — the same table that
configures it as a runner — so with part 1 every kind of eval case runs and grades with no
provider API key at all. The shared judge prompt, with the same grading rules, fenced
response and artifacts the framework judges send, goes in as one text prompt closed by a
line asking for the JSON verdict and nothing else, in an empty directory with no skill
delivered and with the product's tools switched off (`--tools ""` under Claude Code,
`--available-tools=skill-lens-none` under Copilot) so the judge cannot act while it
grades. The verdict is the first balanced JSON object in the reply, validated strictly as
`JudgeOutput` with one evidenced entry per rubric check; a reply with no readable verdict
is an **errored** case, never a low score, so an unreadable grader can never pass or fail
a skill. `judge_model` and `judge_temperature` are not read by a product judge, and
`--judge-model` with one is a user error. Full detail is in
[Judging with a product](runners.md#judging-with-a-product),
[Configuration](configuration.md#judging) and [Security](security.md#product-runners).

## What the MCP bridge shipped

A case's `tools:` under `copilot` and `claude-code`, the one case feature a product runner
could not serve: the runner writes the case's mock tools and an MCP config into a temporary
directory, hands the config to the product on its command line (`--mcp-config=` under
Claude Code, `--additional-mcp-config=@` under Copilot), and the product starts
`python -m skill_lens.mcp_bridge` — a stdio MCP server shipped in the package, with no
new dependency — lists its tools and calls them; every call answers with `returns`
verbatim. The trace names the tool the product's way (`mcp__skill-lens__<name>`,
`skill-lens-<name>`), and the runner maps every declared tool back to the case's name, so a
`trajectory:` reads the same under every runner. Preflight starts the bridge once
(`--check`) when a case declares `tools:` and refuses a `[runners.copilot]`
`--available-tools` that would hide the mocks; a product that ran but never listed the
bridge's tools is an errored case, never a failed `called:`. `cli`, and a preset whose
`command` names another executable, still refuse `tools:` in preflight. With this, a suite
that mocks a real MCP server's tools (`mcp-import`, `tool_libraries:`) runs entirely
through a product seat, with no provider API key and no self-hosted model. Full detail is
in [Mock tools under a product](runners.md#mock-tools-under-a-product); the design is in the
[MCP bridge design](https://github.com/EmadMokhtar/skill-evaluator/blob/main/docs/superpowers/specs/2026-09-21-skill-lens-mcp-bridge-design.md).

## The rename to skill-lens

The project's original name could not be registered on PyPI — the registry folds separators
and look-alike characters before comparing, which collapsed it onto the existing project
`skilleval`. The distribution, the command, the config file and the Python package all moved
to `skill-lens` together. The GitHub repository keeps its name.
