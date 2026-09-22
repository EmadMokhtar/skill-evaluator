# skill-lens

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

A skill is a directory containing a `SKILL.md` file. `skill-lens` discovers those
directories, finds the eval cases declared beside them, runs each case against a
runner, scores the result, and turns the whole run into a single exit code you can
gate a pipeline on.

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under
test is vendored here, so any skill repository can adopt `skill-lens` without
embedding it.

!!! info "Stability"
    This is `0.x`. A minor release may still change behaviour, so pin what you depend on.
    See the [roadmap](roadmap.md) for what is shipped and what is planned.

## Install

```bash
uv tool install "skill-lens[pydantic-ai]"
```

`pip install "skill-lens[pydantic-ai]"` works the same way. The `pydantic-ai` extra is only
needed to evaluate against a real agent; `skill-lens` on its own is enough for the offline
default runner. The `langchain` extra installs the LangChain runner and judge the same way;
the two can be installed together. An installed GitHub Copilot CLI or Claude Code needs no
extra and no API key: `--runner copilot` or `--runner claude-code` starts the product
itself, and `judge = "copilot"` or `judge = "claude-code"` in `skill-lens.toml` grades
rubrics through it — see [Product runners](runners.md#product-runners).

To work on `skill-lens` itself, or to have the example skills to hand, install from a
checkout instead — every command then runs as `uv run skill-lens ...`:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator.git
cd skill-evaluator
uv sync                      # add --extra pydantic-ai for the real-agent runner
```

## Where to go next

| If you want to | Read |
| --- | --- |
| Write your first eval and run it | [Getting started](getting-started.md) |
| Learn the vocabulary — case, runner, judge, arm, gate | [Concepts](concepts.md) |
| Look up an eval YAML field or assertion kind | [Eval files](eval-files.md) |
| Look up a command or flag | [CLI](cli.md) |
| Configure defaults for a repo | [Configuration](configuration.md) |
| Evaluate against a real agent, with tools and budgets | [Runners](runners.md) |
| Understand exit codes and CI behavior | [Gating](gating.md) |
| Understand how the tool is built | [Architecture](architecture.md) |
| Work on skill-lens itself | [Contributing](contributing.md) |
