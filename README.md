# skill-lens

**An Agent Skill is a prompt. Prompts regress.** `skill-lens` turns *"I think this
`SKILL.md` got better"* into a score, a report, and an exit code your pipeline can gate on.

[![CI](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/ci.yml)
[![Docs](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/docs.yml/badge.svg)](https://emadmokhtar.github.io/skill-evaluator/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/EmadMokhtar/skill-evaluator/blob/main/LICENSE)

**📖 Full documentation: <https://emadmokhtar.github.io/skill-evaluator/>**

## Why

You edit a `SKILL.md`, read the new answer once, and it looks better. Two weeks later a
teammate edits the same file, and nobody can say whether the agent still looks an order up
*before* refunding it — or whether it now refunds orders it should refuse.

`skill-lens` gives that question a real answer. You write eval cases next to your skill. It
runs them, scores what came back, and reports one verdict for the whole run.

Your skills stay yours. Skills and their eval cases are **inputs** to the tool — nothing
about a skill under test is vendored here, so any skill repository can adopt `skill-lens`
without embedding it.

## What it measures

- **What the agent said** — substring, regex and exact-match assertions on the output.
- **What the agent did** — which mock tools it called, in what order, and which ones it must
  never touch. A refund granted without a lookup is invisible to an output assertion.
- **What it cost** — per-case ceilings on tokens, dollars and latency.
- **How well it said it** — a rubric-based LLM judge (large language model grading the
  output) returns one verdict *per rubric line*, with the evidence for each. "Explains it
  plainly" is not a substring.
- **Whether the agent reached for the skill at all** — `mode: offered` registers the skill as
  a tool instead of force-loading it, so triggering becomes an observable choice. Ship the
  negative control and a skill that fires on everything stops scoring 100%.
- **Whether your edit actually helped** — run every case twice, once with the skill and once
  against a baseline (no skill, or its previous version resolved from git), and gate on the
  delta.

## Try it — free, offline, no API key

```bash
uv tool install "skill-lens[pydantic-ai]"
```

(`pip install "skill-lens[pydantic-ai]"` works too. Drop the extra for the offline default
runner alone.)

That puts `skill-lens` on your `PATH`, so the commands below run as written.

The walkthrough uses this repository's own example skills, so clone it too — or point
`skill-lens` at your own directory of `SKILL.md` files instead:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator.git
cd skill-evaluator
```

Working on `skill-lens` itself rather than using it? Run `uv sync` in that checkout for the
development environment, and prefix the commands below with `uv run` — you can then skip the
`uv tool install` above.

A skill is any directory containing `SKILL.md`. Its eval cases live beside it — this
repository ships two:

```
examples/
  greeting/
    SKILL.md
    greeting.eval.yaml
  order-support/
    SKILL.md
    order-support.eval.yaml
```

Point the CLI at one skill directory or at a parent of many — discovery is recursive:

```bash
skill-lens list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	5 case(s)	examples/order-support
```

`list` discovers skills and validates every eval file without calling a runner: no API key,
no spend. Starting on your own skill? `skill-lens init ./skills/my-skill` writes a starter
suite with the placeholders marked, so you fill in the blanks instead of starting from one.

## A case is a few lines of YAML

```yaml
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tags: [smoke, refund]
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
    budget:
      max_tokens: 2000
    assertions:
      - kind: contains
        value: "1234"             # name the order you are talking about
```

## A run reads like a test suite

Your own repository follows the same layout — one directory per skill, eval cases beside the
`SKILL.md`:

```
skills/
  order-support/
    SKILL.md
    order-support.eval.yaml
```

```bash
skill-lens run ./skills
```

```
[PASS] order-support :: names the order it is talking about (fake)
[FAIL] order-support :: refuses a refund outside the return window (fake)
        assertion: failed: contains('return window')
            contains[0]: contains('return window') did not hold
[PASS] order-support :: never leaks a stack trace to the customer (fake)

2 passed, 1 failed, 0 errored — pass rate 67%

Gate FAILED:
  - pass rate 67% is below the required 100%
```

Exit code `0` means the gate passed, `1` means it failed, and `2` means something in your
own files is wrong. That is the whole contract with your pipeline.

The default runner is scripted and offline, so the pipeline above costs nothing to try. To
score a real agent, pass `--runner pydantic-ai` — the `[pydantic-ai]` extra in the install
above is what supplies it (from a checkout: `uv sync --extra pydantic-ai`). See
[Runners](https://emadmokhtar.github.io/skill-evaluator/runners/).

## Gate your pull requests

```yaml
- uses: EmadMokhtar/skill-evaluator@v0.3.0
  with:
    path: ./skills
    runner: pydantic-ai
    model: openai:gpt-4o-mini
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Pin an exact tag. Until 1.0 a minor release may change behaviour, so there is no
floating `v0` tag to follow.

The run publishes a JUnit XML report for your provider's test pane, a Markdown summary for
the job summary or a pull-request comment, and a JSON report for anything else. `skill-lens`
never calls the GitHub API itself — it renders files, your workflow decides where they go.
Copy-pasteable workflows live in [`examples/ci/`](https://github.com/EmadMokhtar/skill-evaluator/tree/main/examples/ci) and in
[CI integration](https://emadmokhtar.github.io/skill-evaluator/ci/).

Once that is green, `baseline: previous` and `repeat: 3` turn the same job into a
*comparative* one: each case runs with and without your edit, several times, and the report
carries the delta —
see [Comparative evals](https://emadmokhtar.github.io/skill-evaluator/comparative-evals/).

> **Running against an unreleased commit?** The action's default `install-spec` pins the
> released version matching its own tag, so point both at the same commit and they cannot
> drift apart:
>
> ```yaml
> - uses: EmadMokhtar/skill-evaluator@<commit-sha>
>   with:
>     path: ./skills
>     install-spec: "skill-lens[pydantic-ai] @ git+https://github.com/EmadMokhtar/skill-evaluator@<commit-sha>"
> ```

## Why a green run means something

Eval tools are easy to fool — mostly by accident, and usually by yourself. These are
deliberate design decisions, each with a test holding it in place:

- **No vacuous passes.** A typo like `assertion:` is rejected rather than silently producing
  a case that checks nothing. An unfilled `TODO(skill-lens)` scaffold stops the run. A judge
  check that passes without citing evidence is recorded as a failure. A run that executed
  zero cases fails the gate — "nothing ran" is a broken run, not a pass.
- **`errored` is not `failed`.** A provider returning 500 is an infrastructure signal, not
  evidence that your skill got worse. The two are counted, reported and gated separately.
- **Nothing spends money behind your back.** The default runner is offline and scripted; the
  LLM judge is off until you turn it on; a run that will cost money prints its plan first.
- **Authoring mistakes stop the run.** A malformed regex or an unknown assertion kind is a
  bug in your files, not a verdict on your skill — exit `2`, naming the file and the field.

The full list, with the reasoning behind each, is in [ARCHITECTURE.md](https://github.com/EmadMokhtar/skill-evaluator/blob/main/ARCHITECTURE.md).

## Documentation

| Topic | Page |
| --- | --- |
| First eval, end to end | [Getting started](https://emadmokhtar.github.io/skill-evaluator/getting-started/) |
| Deciding what to test | [Writing evals](https://emadmokhtar.github.io/skill-evaluator/writing-evals/) |
| Eval YAML reference | [Eval files](https://emadmokhtar.github.io/skill-evaluator/eval-files/) |
| Commands and flags | [CLI](https://emadmokhtar.github.io/skill-evaluator/cli/) |
| `skill-lens.toml` | [Configuration](https://emadmokhtar.github.io/skill-evaluator/configuration/) |
| Real agents, tools, budgets | [Runners](https://emadmokhtar.github.io/skill-evaluator/runners/) |
| Baselines, deltas, `--min-delta` | [Comparative evals](https://emadmokhtar.github.io/skill-evaluator/comparative-evals/) |
| Exit codes and reports | [Gating](https://emadmokhtar.github.io/skill-evaluator/gating/) |
| The action and example workflows | [CI integration](https://emadmokhtar.github.io/skill-evaluator/ci/) |
| How it is built | [ARCHITECTURE.md](https://github.com/EmadMokhtar/skill-evaluator/blob/main/ARCHITECTURE.md) |
| What is checked for vulnerabilities, and where | [Security](https://emadmokhtar.github.io/skill-evaluator/security/) |
| What's shipped, what's next | [Roadmap](https://emadmokhtar.github.io/skill-evaluator/roadmap/) |

## Contributing

Contributions are welcome, and the project is set up so that helping is cheap:

```bash
uv sync
uv run pytest        # the whole suite: offline, no API key, no spend
uv run ruff check .
```

Every test passes with no network access. Tests that would hit a real provider are opt-in
(`-m integration`) or replay recorded traffic, so you can work on any part of this without an
API key or a bill.

Three conventions to know before your first pull request — all three are explained in
[Contributing](https://emadmokhtar.github.io/skill-evaluator/contributing/):

1. **Test-driven.** Write the failing test first.
2. **[Conventional Commits](https://www.conventionalcommits.org/)** for commit messages *and*
   pull-request titles. Releases are derived from history, and pull requests are
   squash-merged, so the title becomes the commit.
3. **Documentation ships with the change**, not as a follow-up. Continuous integration
   checks it.

Good places to start: a new example skill with its eval suite, an adapter for another agent
framework, or anything on the [roadmap](https://emadmokhtar.github.io/skill-evaluator/roadmap/).
Not sure whether an idea fits? Open an issue and ask — that is a perfectly good first
contribution.

## Status

Milestone 5. Discovery, scoring, judging, comparison, reporting, gating and the automated
release pipeline all ship and are tested. Versions are derived from the commit history and
published to PyPI on merge. This is `0.x`: a minor release may still change behaviour, so pin
what you depend on. See the
[roadmap](https://emadmokhtar.github.io/skill-evaluator/roadmap/) for what is shipped and
what is planned.

## License

MIT — see [LICENSE](https://github.com/EmadMokhtar/skill-evaluator/blob/main/LICENSE).
