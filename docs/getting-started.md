# Getting started

This page takes one skill from nothing to a CI gate. Every command is shown with what it
prints. The first four steps cost nothing and need no API key.

## 1. Install

```bash
uv tool install "skill-lens[pydantic-ai]"
```

`pip install "skill-lens[pydantic-ai]"` works the same way. The extra supplies the
real-agent runner; drop it if you only want the offline default. A `langchain` extra provides
the second real-agent runner, and an installed GitHub Copilot CLI or Claude Code is a third
way that needs no extra and no API key — see [Runners](runners.md). From a checkout of this
repository, `uv sync --extra pydantic-ai` and prefix every command below with `uv run`.

## 2. Scaffold a suite

A skill is a directory containing `SKILL.md`. Its eval cases live beside it. Say you have:

```
skills/
  refund/
    SKILL.md
```

```bash
skill-lens init ./skills/refund
```

```
Wrote skills/refund/evals/refund.eval.yaml
Fill in every TODO(skill-lens), then run: skill-lens list skills/refund
```

The file holds five cases — the common case, the policy edge with mock tools and a
trajectory check, both halves of a triggering pair, and a workspace case for a skill that
writes a file. Every value you must supply reads `TODO(skill-lens)`. A case still holding
one **refuses to run** (exit `2`, naming the field), so the scaffold can never pass by
checking nothing. Delete the cases that do not apply; keep the ones that do.

Pointed at a directory of skills, `init` scaffolds every skill that has no suite and skips
the rest — see [CLI](cli.md#init).

## 3. Fill it in

For a first run, two cases are enough. Replace the generated file with:

```yaml
# skills/refund/evals/refund.eval.yaml
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tags: [smoke]
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      called: [lookup_order]      # it must look the order up
      forbidden: [issue_refund]   # and must not refund this one
    assertions:
      - kind: contains
        value: "1234"             # name the order you are talking about

  - name: never leaks a stack trace
    task: I want a refund for order 1234
    assertions:
      - kind: not_contains
        value: Traceback
```

Mock tools execute nothing: calling one records the call and returns `returns` verbatim,
so the trajectory is genuinely the model's choice. The full field reference is
[Eval files](eval-files.md); deciding *which* cases a skill needs is
[Writing evals](writing-evals.md).

## 4. Validate for free

```bash
skill-lens list ./skills
```

```
refund	2 case(s)	skills/refund
```

`list` discovers skills and validates every eval file without calling a runner — no key,
no spend. A malformed file, an unknown assertion kind or a leftover `TODO(skill-lens)`
stops here with exit `2`.

## 5. Run offline and read a failure

```bash
skill-lens run ./skills
```

The default runner is `fake`: scripted, offline, free. It answers every task with
`[fake] <skill> handled: <task>` and never calls a tool, so it exercises the whole pipeline
and fails the first case — which is what we want to look at:

```
[FAIL] refund :: refuses a refund outside the return window (fake)
        trajectory: lookup_order was never called
            called:lookup_order: lookup_order was never called
        output: [fake] refund handled: I want a refund for order 1234
[PASS] refund :: never leaks a stack trace (fake)

1 passed, 1 failed, 0 errored — pass rate 50%

Gate FAILED:
  - pass rate 50% is below the required 100%
```

Read it top down. The `contains('1234')` assertion held — the fake echo names the order —
but the trajectory check did not: `lookup_order` was never called. Below the checks is
what the agent actually did: its `output:`, and a `tool calls:` list when there were any
(here there were none, which is exactly the problem). A real agent that answered the same
way would fail for the same reason, and you would see the words it chose. Output is cut at
500 characters; a cut is never silent, and `--full-output` prints all of it. Exit code `0`
means the gate passed, `1` failed, `2` something in your own files is wrong — that is the
whole contract with your pipeline. See [Gating](gating.md).

## 6. Run against a real agent

```bash
export OPENAI_API_KEY=...
skill-lens run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
```

Before spending anything the CLI prints its ceiling:

```
Plan: up to 1 arm(s) x 1 repeat(s) x 1 runner(s) x 2 case(s) = 2 runs
```

Rerun one case by any distinctive part of its name:

```bash
skill-lens run ./skills --runner pydantic-ai --case "return window"
```

A `--case` that matches nothing fails the gate rather than reporting an empty success.
Runners, tools and budgets are covered in [Runners](runners.md).

## 7. Commit a configuration

Rather than repeat the flags, commit `skill-lens.toml` at your repository root:

```toml
default_runner = "pydantic-ai"
model = "openai:gpt-4o-mini"
judge = "pydantic-ai"        # turns on the LLM judge for cases with a rubric
min_pass_rate = 1.0
concurrency = 4
```

Flags still win over the file. Every key, annotated, is in
[`examples/skill-lens.toml`](https://github.com/EmadMokhtar/skill-evaluator/blob/main/examples/skill-lens.toml);
the reference is [Configuration](configuration.md). Secrets never go in the file — API keys
come from the environment only.

## 8. Gate pull requests

```yaml
- uses: EmadMokhtar/skill-evaluator@v0.6.0
  with:
    path: ./skills
    runner: pydantic-ai
    model: openai:gpt-4o-mini
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

The action installs `skill-lens`, runs it, publishes a JUnit report for the test pane and a
Markdown summary for the job summary or a pull-request comment, and exits with the gate's
code. Complete workflows are in [CI integration](ci.md).

## Next: did the edit help?

Once the gate is green, the interesting question is whether an edit to `SKILL.md` made the
skill *better*. Add `version:` to the frontmatter (three-part, like `1.0.0` — see
[why it must be text](comparative-evals.md#version-and-why-it-must-be-quoted)), bump it
with each meaningful edit, and run:

```bash
skill-lens run ./skills --runner pydantic-ai --baseline previous
```

Every case runs twice — the working copy and the previous version resolved from git — and
the report carries the delta. `--min-delta` turns that into a gate. This repository's own
`examples/greeting` is versioned for exactly this walkthrough:
[Comparative evals](comparative-evals.md#try-it-on-the-shipped-examples).
