# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under test is
vendored here, so any skill repo can adopt `skill-eval` without embedding it.

> **Status:** M3. The full pipeline — discovery, scoring, reporting, gating — runs offline
> against `FakeRunner` (the default, scripted, free) and against real agents through
> `pydantic-ai` (provider-flexible), scoring output text, tool-use trajectories, and
> efficiency budgets. A rubric-based LLM judge scores output quality, and `mode: offered`
> measures whether an agent chose to use the skill at all. See [Roadmap](#roadmap).

## Install

```bash
uv sync
```

## Quickstart

A skill is a directory containing `SKILL.md`. Its eval cases live beside it:

```
examples/
  greeting/
    SKILL.md
    greeting.eval.yaml
```

```yaml
# greeting.eval.yaml
cases:
  - name: greets the named person in one sentence
    task: greet Ada
    tags: [smoke]
    budget:
      max_tokens: 500
    assertions:
      - kind: contains
        value: Ada
      - kind: not_contains
        value: Traceback
      # SKILL.md asks for "one short sentence"; this regex only checks "one line,
      # under 120 chars, ending in . ! or ?" -- it doesn't (and can't, with a
      # regex) verify single-sentence-ness. It's deliberately looser than that
      # prose because real model output legitimately varies (e.g. two short
      # clauses joined by a comma), so don't tighten it without re-recording
      # against a real provider.
      - kind: regex
        value: "^[^\\n]{1,120}[.!?]\"?\\s*$"
```

Point the CLI at a single skill directory or at a parent directory of many — discovery is
recursive:

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	5 case(s)	examples/order-support
```

`list` discovers skills and validates every eval file without calling a runner — free, and no
API key required. The shipped examples assert real model behavior, so actually running them
(`skill-eval run`) needs the `pydantic-ai` runner — see
[Running against a real agent](#running-against-a-real-agent) below. The judged case also needs
`judge = "pydantic-ai"` in `skill-eval.toml` — the judge is configured independently of the
runner, and the default `judge = "fake"` reports it errored rather than grading it. The
zero-cost `fake` runner (the default) is what the test suite itself runs on.

## Eval files

Each file has a top-level `cases:` list. Unknown keys **within a case or an assertion** are
rejected — a typo like `assertion:` would otherwise produce a case that passes vacuously.
Extra keys alongside `cases:` at the top level of the file are ignored.

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Case name, shown in reports |
| `task` | yes | The prompt handed to the runner |
| `assertions` | no | Scoring rules; a case with none passes |
| `tags` | no | Labels for `--tag` filtering |
| `tools` | no | Mock tools the agent may call — see [Declaring tools and scoring the trajectory](#declaring-tools-and-scoring-the-trajectory) |
| `trajectory` | no | Which tools must/must not have been called, and in what order |
| `budget` | no | Ceilings on tokens, cost, and latency |
| `mode` | no | `"loaded"` (default, force-loaded) or `"offered"` — see [Does the agent even reach for the skill?](#does-the-agent-even-reach-for-the-skill) |
| `judge` | no | `expected:` text plus a `rubric:` list for LLM-as-judge scoring — see [Judging output quality](#judging-output-quality) |

### Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |

Every assertion in a case must hold for the case to pass. An unsupported `kind` or a malformed
regex aborts the run as an authoring error rather than being reported as a skill failure.

### Where eval files are found

For each discovered skill, in order:

1. an `evals/` directory beside `SKILL.md` — every `.yaml` / `.yml` file in it, or
2. any `*.eval.yaml` file beside `SKILL.md`.

`--evals <path>` overrides discovery with an explicit file or directory. Skills with no eval
files are reported as **skipped** — visible in the output, never silently ignored.

## CLI

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <name>]
                      [--judge-model <name>] [--tag <tag>] [--min-pass-rate <float>]
                      [--json-output <path>] [--config <file>]
skill-eval list <path> [--evals <path>]
```

`<path>` is a skill directory or a directory of skill directories.

## Running against a real agent

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent, install the extra and pick a model:

```bash
pip install 'skill-eval[pydantic-ai]'
export OPENAI_API_KEY=...
skill-eval run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
```

API keys are read from the environment only — never from `skill-eval.toml`.
`skill-eval` checks for the key before making any request, so a missing key costs
nothing and exits 2.

### Declaring tools and scoring the trajectory

An eval case can declare the tools the agent may call. Nothing executes: a tool
records the call and returns its canned value, so the trajectory is the model's
own choice and the run has no side effects.

```yaml
cases:
  - name: checks the order before refusing
    task: I want a refund for order 1234
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      called: [lookup_order]        # each of these ran
      forbidden: [issue_refund]     # none of these ran
      order: [lookup_order]         # ran in this relative order
      max_calls: 3                  # no looping
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 30000
    assertions:
      - kind: contains
        value: "1234"
```

`order` is a relative subsequence: unrelated calls may appear in between, but the
listed tools must not appear out of sequence.

Every tool name in `called`, `forbidden`, or `order` must be declared in that case's
`tools:` — including `forbidden`, since forbidding a tool the agent was never offered in
the first place is a check that can never fire. A name that isn't declared is an
authoring error (the run aborts, exit `2`), not a failing case, because a check that can
never pass tells you nothing about the skill.

### Budget limits and pricing

The `budget` block sets ceilings on tokens, cost, and latency. Pricing comes from
`genai-prices`, which carries data for widely-used models but not every provider — for
example, Groq and Mistral models have no pricing entry yet. When a model cannot be priced,
`cost_usd` degrades to `0.0` and a note is recorded explaining why; the note always appears
in the JSON report. On the console it surfaces as budget-evaluator failure detail, which is
only printed for a case that fails — so if the cost limit is the only budget check declared,
the skip fails the case and the note prints; if token or latency limits are declared
alongside and pass, the case passes and the note is silent on screen. Either way, an aggregate
line ("some costs not priced" / "Total cost: not priced") appears in the console totals
whenever any outcome's pricing degraded, pointing you at the JSON for the per-case detail.
An unpriceable cost limit is **skipped rather than silently passed**, so a case with an
unpriced cost limit and no other budget checks will fail,
because nothing was actually verified. If other budget limits are declared alongside (tokens,
latency), they are still evaluated normally, and only the cost limit is skipped. To adopt
`skill-eval` against a provider without pricing data, simply omit `max_cost_usd` from the budget
block for that provider.

### Judging output quality

Some things an assertion cannot check — "explains it plainly" is not a substring.
A `judge:` block hands those to an LLM judge, which returns one verdict per rubric
entry **with the evidence for it**:

```yaml
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
```

skill-eval derives the verdict and the score from the per-check results; the judge is
never asked for a blended number. **A check that passes without citing evidence is
recorded as a failure** — an unsupported PASS is a judge's characteristic failure mode.

Judging costs money, so it is opted into explicitly:

```toml
judge = "pydantic-ai"
judge_model = ""    # empty falls back to `model`
```

The default `judge = "fake"` does not grade at all — and rather than passing a rubric it
never checked, it reports the case as **errored**. Judge spend is reported as "judge
overhead", separately from what the runs themselves cost, and never counts against a
case's `budget:`.

### Does the agent even reach for the skill?

`mode: offered` stops force-loading the skill and offers it as a tool instead, named
after the skill (normalized to a Python identifier, e.g. `order-support` → `order_support`)
and described by its frontmatter `description`. If the agent calls it, it receives the
skill's instructions and carries on; if it doesn't, it never sees them.

```yaml
  - name: reaches for the skill on a refund question
    mode: offered
    task: I want a refund for order 1234
    trajectory:
      skill_triggered: true

  - name: leaves an unrelated question alone
    mode: offered
    task: What's the capital of Egypt?
    trajectory:
      skill_triggered: false
```

**Always ship the negative control.** A suite of positives alone scores a skill that
fires on everything at 100%.

Two things to know: the offered tool call appears in `tool_calls` like any other, so it
counts toward `max_calls`; and it is checked with `skill_triggered`, not by naming it in
`called:` (which only accepts tools the case itself declares).

## Configuration

`skill-eval.toml` is optional. It is located via `--config`, or otherwise discovered by
searching upward from the current directory — the repo root is the conventional home, not a
requirement.

```toml
default_runner = "fake"
min_pass_rate = 1.0
fail_on_error = true

[per_skill_min]
greeting = 0.9
```

| Key | Default | CLI override |
| --- | --- | --- |
| `default_runner` | `"fake"` | `--runner` |
| `model` | `"openai:gpt-4o-mini"` | `--model` |
| `temperature` | `0.0` | — |
| `retries` | `2` | — |
| `retry_backoff_seconds` | `1.0` | — |
| `judge` | `"fake"` | — |
| `judge_model` | `""` (falls back to `model`) | `--judge-model` |
| `min_pass_rate` | `1.0` | `--min-pass-rate` |
| `fail_on_error` | `true` | — |
| `per_skill_min` | `{}` | — |

Resolution order is **CLI flag > config file > built-in default**. API keys come from
environment variables only and are never read from config.

`--judge-model` only has an effect once `judge` is set to a real judge (currently
`"pydantic-ai"`); under the default `judge = "fake"` it is accepted but silently unused, since
`FakeJudge` never grades anything.

`model`, `temperature`, `retries`, and `retry_backoff_seconds` matter to a runner or judge that
reads them (currently `pydantic-ai` for both); `FakeRunner` and `FakeJudge` ignore them. `model`
also doubles as the fallback for `judge_model` when it's empty. `temperature` accepts a float
or the literal string `"unset"`, for reasoning models that reject any explicit temperature:

```toml
default_runner = "pydantic-ai"
model = "openai:gpt-4o-mini"
temperature = 0.0            # or "unset" for reasoning models, which reject it
retries = 2
retry_backoff_seconds = 1.0
```

## Gating and exit codes

Exit codes are the CI contract:

| Code | Meaning |
| --- | --- |
| `0` | Gate passed |
| `1` | Gate failed |
| `2` | User or authoring error (bad path, malformed YAML, unknown assertion kind) |

A run fails the gate when the overall pass rate is below `min_pass_rate`, when a configured
per-skill minimum is not met, or when any case **errored**. Two distinctions matter:

- **failed** — the case ran and scored below the bar. An *eval* signal.
- **errored** — the runner or an evaluator blew up: an API error, timeout, or missing key from
  the runner, but also an evaluator that couldn't do its job — for example a `judge:` block with
  no judge configured, or a `trajectory.skill_triggered` check against a runner that doesn't
  support `mode: offered`. An *infra* signal either way, and it fails the gate by default so CI
  never goes green on a broken run.

A case that fails its assertions drags the pass rate below the bar and fails the gate:

```
[FAIL] badskill :: expects something absent (fake)
        assertion: failed: contains('NEVER_PRESENT')

0 passed, 1 failed, 0 errored — pass rate 0%

Gate FAILED:
  - pass rate 0% is below the required 100%
```

**A run that executed zero cases also fails.** "Nothing ran" is a broken run, not a pass —
otherwise a mistyped path reports success forever. The reason names the cause: no skills found,
all skills skipped for having no eval cases, or every case filtered out by `--tag`.

```
Skipped (no eval cases): badskill

0 passed, 0 failed, 0 errored — pass rate 0%

Gate FAILED:
  - no eval cases ran: all discovered skill(s) were skipped for having no eval cases: badskill
```

## JSON report

`--json-output report.json` writes a machine-readable report alongside the console output:
a `summary` block (counts, overall and per-skill pass rates, token/cost/latency totals),
`skipped_skills`, `tag_filtered_skills`, a per-case `outcomes` list, and the `gate` decision
with its reasons.

## Architecture

The design rests on three protocols; everything else is plumbing around them.

- **`Runner`** — `run(skill, case) -> RunResult`, carrying the output, transcript, tool-call
  trajectory, and tokens/latency/cost. This is the seam every agent framework plugs into.
- **`Evaluator`** — `evaluate(case, result) -> EvalScore`, a pass/fail verdict with a numeric
  score and human-readable detail.
- **`Judge`** — `judge(request) -> JudgeVerdict`, one verdict per rubric check, each carrying
  its evidence. The seam every LLM-as-judge implementation plugs into.

```
path → skill loader → [Skill] → case loader → [EvalCase]
     → orchestrator (skill × case × runner) → RunReport → reporters + gating → exit code
```

No agent-framework type appears in the core; frameworks live only inside `Runner` and `Judge`
adapters (`runners/pydantic_ai.py`, `judges/pydantic_ai.py`).
Full design: [`docs/superpowers/specs/2026-07-30-skill-eval-design.md`](docs/superpowers/specs/2026-07-30-skill-eval-design.md),
with the M2 additions in [`docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`](docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md)
and the M3 additions in [`docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`](docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md).

## Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Scaffolding, config, CLI skeleton, release plumbing | shipped |
| M1 | Loaders, protocols, `FakeRunner`, assertion evaluator, orchestrator, console + JSON reporters, gating | shipped |
| M2 | PydanticAI runner, trajectory + budget evaluators, cost/latency capture, cassette test tier | shipped |
| M3 | LLM-as-judge evaluator (per-check verdicts), triggering evals with negative controls | shipped |
| M4 | Comparative evals: `--baseline`/`--repeat`, delta reporting, `--min-delta` gating | planned |
| M5 | CI/CD polish: JUnit XML + Markdown/HTML reporters, GitHub Action, automated release | planned |
| M6 | Real-execution tools: sandboxed built-in toolset, `file-produced`/`json-schema` assertions | planned |
| M7 | DX & docs: `skill-eval init` scaffolder, docs, more examples | planned |
| M8 | LangChain adapter (optional) | planned |

## Contributing

### Development

```bash
uv sync
uv run pytest                  # test suite
uv run ruff check .            # lint
uv run ruff format --check .   # formatting (as CI runs it)
uv run skill-eval list ./examples
```

Tests marked `integration` hit real provider APIs and are deselected by default; run them with
`uv run pytest -m integration`. Tests marked `cassette` replay recorded provider traffic —
zero cost, no key needed, and selected by default. Everything else passes offline with no API
spend. `uv run skill-eval run ./examples` needs the `pydantic-ai` runner (see
[Running against a real agent](#running-against-a-real-agent)); the shipped examples now
assert real model behavior, so `list` is what dogfoods discovery for free.

Development is test-driven: write the failing test first, then the implementation.

### Conventional Commits are required

Every commit message **and** every pull request title must follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope][!]: <description>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`,
`style`, `revert`. Use the imperative mood, lowercase, no trailing period.

This is not stylistic. `cz bump` derives the next version and the changelog from
commit history, so a non-conforming message silently breaks the release. Because
PRs are **squash-merged**, the PR title becomes the commit on `main` — so the
title is what release automation actually reads.

Install the hook once per clone so bad messages are rejected before they land:

```bash
uv run pre-commit install --hook-type commit-msg
```

CI enforces the same rules on every PR: the title is checked with `cz check`, and
every commit on the branch with `scripts/check_commits.py`. Two docs-only commits
predating the convention are exempted in `scripts/legacy-commits.txt`; that list
should only ever shrink.

## License

MIT — see [LICENSE](LICENSE).
