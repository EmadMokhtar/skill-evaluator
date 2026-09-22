# Roadmap

What `skill-lens` does today, what is being considered next, and what it will
deliberately not do.

## Development milestones

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

## What's shipped

Organised by what it does, not by when it arrived. For the change-by-change history, see
[CHANGELOG.md](https://github.com/EmadMokhtar/skill-evaluator/blob/main/CHANGELOG.md),
which `cz bump` generates from the commit history.

| Capability | Where it is documented |
| --- | --- |
| Skill discovery, eval cases, assertion kinds and per-check scoring | [Eval files](eval-files.md) |
| The gate, its exit codes, the JSON report, and what a failing case shows | [Gating and exit codes](gating.md) |
| Real agents through PydanticAI and LangChain, one run through both | [Runners](runners.md) |
| Installed products as the runner and as the judge, with no API key | [Product runners](runners.md#product-runners) |
| Mock tools, shared tool libraries, and per-call mock returns | [Mock tools](eval-files.md#mock-tools) |
| Which tools were called, in what order, and with what arguments | [Declaring tools and scoring the trajectory](runners.md#declaring-tools-and-scoring-the-trajectory) |
| A rubric-based judge with per-check evidence | [Judging output quality](eval-files.md#judging-output-quality) |
| `offered` cases, which measure whether the agent reached for the skill | [Did the agent reach for the skill?](eval-files.md#did-the-agent-reach-for-the-skill) |
| Comparative runs: two arms, `--repeat`, the delta, `--min-delta` | [Comparative evals](comparative-evals.md) |
| Workspaces, bundled files, and sandboxed script execution | [The workspace](runners.md#the-workspace), [Running bundled scripts](security.md#running-bundled-scripts) |
| Token, cost and latency budgets | [Budget limits and pricing](runners.md#budget-limits-and-pricing) |
| Repository defaults, including a self-hosted model endpoint | [Configuration](configuration.md) |
| Scaffolding a suite, and importing a real MCP server's tools | [`init`](cli.md#init), [`mcp-import`](cli.md#mcp-import) |
| JUnit and Markdown reporters, `--concurrency`, the composite action | [CI integration](ci.md) |
| Releases derived from commit history, published over Trusted Publishing | [Releasing](releasing.md) |

## What's next

Nothing here is committed to a date.

- **A per-skill `min_delta`.** `per_skill_min` already sets a pass rate per skill, but the
  required improvement is one number for the whole run, so one weak skill sets the bar for
  all of them.
- **Efficiency regression gates.** The delta already reports the token, cost and latency
  differences between the arms, but nothing gates on them: a skill that got more expensive
  without getting better still passes.
- **An explicit `--baseline-ref <rev>`.** `--baseline` takes `none` or `previous`; there is
  no way to compare against a revision you choose.
- **Flagging checks that fail in *both* arms.** A check that *passes* in both is already
  flagged as low signal. One that fails in both is reported as "no change", which is true
  and unhelpful.

## Not planned

- **An HTML reporter.** Nothing consumes it. The console, JSON, JUnit and Markdown
  reporters already cover the terminal, a machine-readable record, CI test panes, and
  GitHub's step summary and pull-request comments.
- **Process or subinterpreter pools.** A run waits on the network, not on the CPU, so more
  cores buy nothing. The orchestrator is typed against `concurrent.futures.Executor`, so
  swapping the pool is a small change if that ever stops being true.
- **A results dashboard, authoring skills, or running skills in production.** All three are
  stated non-goals: the tool is a CI gate whose contract is an exit code, and the skills it
  measures stay inputs to it. See
  [Scope and non-goals](architecture.md#scope-and-non-goals).

## The rename to skill-lens

The project's original name could not be registered on PyPI — the registry folds separators
and look-alike characters before comparing, which collapsed it onto the existing project
`skilleval`. The distribution, the command, the config file and the Python package all moved
to `skill-lens` together. The GitHub repository keeps its name.
