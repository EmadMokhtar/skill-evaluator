# Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Scaffolding, config, CLI skeleton, release plumbing | shipped |
| M1 | Loaders, protocols, `FakeRunner`, assertion evaluator, orchestrator, console + JSON reporters, gating | shipped |
| M2 | PydanticAI runner, trajectory + budget evaluators, cost/latency capture, cassette test tier | shipped |
| M3 | LLM-as-judge evaluator (per-check verdicts), triggering evals with negative controls | shipped |
| M4 | Comparative evals: `--baseline`/`--repeat`, delta reporting, `--min-delta` gating | shipped |
| M5 | CI/CD polish: JUnit XML + Markdown reporters, GitHub Action, bounded concurrency | shipped |
| M6 | Real-execution tools: sandboxed built-in toolset, `file-produced`/`json-schema` assertions | Part 1 shipped; Part 2 planned |
| M7 | DX: failing cases explain themselves, `--case`, `init` batch mode and workspace case, versioned example, quickstart | shipped |
| M8 | LangChain runner and judge (`[langchain]` extra); runner matrix | Part 1 shipped; Part 2 planned |

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

Deferred to part 2: running a script bundled with the skill under test. That is its own
spec, because executing code that shipped with the artifact under evaluation is a
different trust decision from writing files into a temporary directory — a `SKILL.md`
under evaluation is, by construction, code nobody has vetted yet, and `skill-lens` is
designed to run in CI against repository credentials.

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

Deferred: a `references/` layout example (bundled files are not loaded until M6 part 2),
a repeatable `--case`, and the gating features carried over from M4 and M5 (per-skill
`min_delta`, `--baseline-ref`, both-arms-fail flagging).

## What M8 part 1 shipped

A second agent framework behind the same seams. `--runner langchain` drives LangChain
1.x's `create_agent` with the same tools, prompt and workspace the PydanticAI runner
uses, and `judge = "langchain"` grades rubrics with the same prompt and per-check
verdict contract, so the `[langchain]` extra is self-sufficient. The prompt rules and the
retry policy were extracted into two framework-neutral modules so both adapters share
one implementation. Anthropic joined both extras. Part 2 — running every case through
more than one runner in one invocation — has its own pull request; see the
[M8 design](https://github.com/EmadMokhtar/skill-evaluator/blob/main/docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md).

## The rename to skill-lens

The project's original name could not be registered on PyPI — the registry folds separators
and look-alike characters before comparing, which collapsed it onto the existing project
`skilleval`. The distribution, the command, the config file and the Python package all moved
to `skill-lens` together. The GitHub repository keeps its name.
