# Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Scaffolding, config, CLI skeleton, release plumbing | shipped |
| M1 | Loaders, protocols, `FakeRunner`, assertion evaluator, orchestrator, console + JSON reporters, gating | shipped |
| M2 | PydanticAI runner, trajectory + budget evaluators, cost/latency capture, cassette test tier | shipped |
| M3 | LLM-as-judge evaluator (per-check verdicts), triggering evals with negative controls | shipped |
| M4 | Comparative evals: `--baseline`/`--repeat`, delta reporting, `--min-delta` gating | shipped |
| M5 | CI/CD polish: JUnit XML + Markdown reporters, GitHub Action, bounded concurrency | shipped (part 1) |
| M6 | Real-execution tools: sandboxed built-in toolset, `file-produced`/`json-schema` assertions | planned |
| M7 | DX: `skill-eval init` scaffolder, more examples | `init` shipped; examples planned |
| M8 | LangChain adapter (optional) | planned |

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

Part 2 — the automated release pipeline (`cz bump` on merge to main, Trusted Publishing to
PyPI) and the manual cassette-refresh workflow — is specified and waiting on the PyPI project
and repository secrets it needs.
