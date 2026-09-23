# skill-lens

**An Agent Skill is a prompt. Prompts regress.** `skill-lens` turns *"I think this
`SKILL.md` got better"* into a score, a report, and an exit code your pipeline can gate on.

[![CI](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/ci.yml/badge.svg)](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/ci.yml)
[![Docs](https://github.com/EmadMokhtar/skill-evaluator/actions/workflows/docs.yml/badge.svg)](https://emadmokhtar.github.io/skill-evaluator/)
[![Python 3.11.4+](https://img.shields.io/badge/python-3.11.4%2B-blue.svg)](https://www.python.org/downloads/)
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

## What an eval looks like

```yaml
# an eval file, beside the SKILL.md it tests
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "delivered_days_ago": 61}'
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
        value: "30-day"           # and cite the policy
```

```bash
uv tool install "skill-lens[pydantic-ai]"   # drop the extra for the offline runner alone
skill-lens run ./skills                     # exit 0 passed, 1 failed, 2 your files are wrong
```

That first run is free: the default runner is scripted and offline, so nothing is sent
anywhere and no API key is read. `--runner pydantic-ai`, `--runner copilot` or
`--runner claude-code` scores a real agent instead — see
[Runners](https://emadmokhtar.github.io/skill-evaluator/runners/).

Start at [Getting started](https://emadmokhtar.github.io/skill-evaluator/getting-started/),
which takes one skill from nothing to a CI gate.

## Documentation

| Topic | Page |
| --- | --- |
| First eval, end to end | [Getting started](https://emadmokhtar.github.io/skill-evaluator/getting-started/) |
| The vocabulary, with a glossary | [Concepts](https://emadmokhtar.github.io/skill-evaluator/concepts/) |
| Deciding what to test | [Writing evals](https://emadmokhtar.github.io/skill-evaluator/writing-evals/) |
| Eval YAML reference | [Eval files](https://emadmokhtar.github.io/skill-evaluator/eval-files/) |
| Commands and flags | [CLI](https://emadmokhtar.github.io/skill-evaluator/cli/) |
| `skill-lens.toml` | [Configuration](https://emadmokhtar.github.io/skill-evaluator/configuration/) |
| Real agents, tools, budgets | [Runners](https://emadmokhtar.github.io/skill-evaluator/runners/) |
| Baselines, deltas, `--min-delta` | [Comparative evals](https://emadmokhtar.github.io/skill-evaluator/comparative-evals/) |
| Exit codes and reports | [Gating](https://emadmokhtar.github.io/skill-evaluator/gating/) |
| The action and example workflows | [CI integration](https://emadmokhtar.github.io/skill-evaluator/ci/) |
| The message on your screen | [Troubleshooting](https://emadmokhtar.github.io/skill-evaluator/troubleshooting/) |
| How it is built | [ARCHITECTURE.md](https://github.com/EmadMokhtar/skill-evaluator/blob/main/ARCHITECTURE.md) |
| Why a green run means something | [Invariants](https://emadmokhtar.github.io/skill-evaluator/invariants/) |
| What is checked for vulnerabilities, and where | [Security](https://emadmokhtar.github.io/skill-evaluator/security/) |
| How a release is cut and published | [Releasing](https://emadmokhtar.github.io/skill-evaluator/releasing/) |
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

Discovery, scoring, judging, comparison, real-file workspaces, reporting, gating and the
automated release pipeline all ship and are tested. Versions are derived from the commit
history and published to PyPI on merge. This is `0.x`: a minor release may still change
behaviour, so pin what you depend on. See the
[roadmap](https://emadmokhtar.github.io/skill-evaluator/roadmap/) for what is shipped and
what is planned.

## License

MIT — see [LICENSE](https://github.com/EmadMokhtar/skill-evaluator/blob/main/LICENSE).
