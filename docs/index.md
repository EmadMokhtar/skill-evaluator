# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

A skill is a directory containing a `SKILL.md` file. `skill-eval` discovers those
directories, finds the eval cases declared beside them, runs each case against a
runner, scores the result, and turns the whole run into a single exit code you can
gate a pipeline on.

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under
test is vendored here, so any skill repository can adopt `skill-eval` without
embedding it.

!!! info "Status: M5 (part 1)"
    The full pipeline — discovery, scoring, reporting, gating — runs offline against
    `FakeRunner` (the default, scripted, free) and against real agents through
    `pydantic-ai`. It scores output text, tool-use trajectories, and efficiency
    budgets, plus output quality via a rubric-based LLM judge with per-check evidence.
    Each case can also run against a baseline for comparative, delta-gated evals, and
    JUnit/Markdown reporters with a composite GitHub Action make a run CI-legible.
    See the [roadmap](roadmap.md).

## Install

No release has shipped yet (see the [roadmap](roadmap.md)), so install from source:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator.git
cd skill-evaluator
uv sync
```

For evaluating against a real agent, install the extra:

```bash
uv sync --extra pydantic-ai
```

Once a release ships, `pip install skill-eval` (or `skill-eval[pydantic-ai]`) will work too.

## Where to go next

| If you want to | Read |
| --- | --- |
| Write your first eval and run it | [Getting started](getting-started.md) |
| Look up an eval YAML field or assertion kind | [Eval files](eval-files.md) |
| Look up a command or flag | [CLI](cli.md) |
| Configure defaults for a repo | [Configuration](configuration.md) |
| Evaluate against a real agent, with tools and budgets | [Runners](runners.md) |
| Understand exit codes and CI behavior | [Gating](gating.md) |
| Understand how the tool is built | [Architecture](architecture.md) |
| Work on skill-eval itself | [Contributing](contributing.md) |
