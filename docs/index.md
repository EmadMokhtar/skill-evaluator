# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

A skill is a directory containing a `SKILL.md` file. `skill-eval` discovers those
directories, finds the eval cases declared beside them, runs each case against a
runner, scores the result, and turns the whole run into a single exit code you can
gate a pipeline on.

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under
test is vendored here, so any skill repository can adopt `skill-eval` without
embedding it.

!!! info "Status: M2"
    The full pipeline — discovery, scoring, reporting, gating — runs offline against
    `FakeRunner` (the default, scripted, free) and against real agents through
    `pydantic-ai`. It scores output text, tool-use trajectories, and efficiency
    budgets. See the roadmap.

## Install

```bash
uv sync
```

For evaluating against a real agent, install the extra:

```bash
pip install 'skill-eval[pydantic-ai]'
```

## Where to go next

| If you want to | Read |
| --- | --- |
| Write your first eval and run it | [Getting started](getting-started.md) |
