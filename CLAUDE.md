# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`skill-eval` is a standalone CLI + library that runs evaluations on Anthropic-style Agent
Skills (`SKILL.md` files). Skills under test and their eval cases are **inputs** — nothing
about a skill-under-test is vendored here. The tool is meant to run as a CI gate (exit code
is the contract) or on demand.

Currently at **M4**: the pipeline runs real agents through `PydanticAIRunner`
(provider-flexible, via PydanticAI), scores tool use and efficiency as well as
output text, and is tested against recorded provider traffic. `FakeRunner`
remains the default and the backbone of the zero-cost test tier. M3 adds a
rubric-based LLM judge that scores output quality with per-check evidence, and
an `offered` case mode that measures whether the agent chose to trigger the
skill at all, negative controls included. M4 makes every measurement
comparative: each case can run in a candidate arm and a baseline arm
(`--baseline none` or `--baseline previous`, resolved from git), optionally
sampled `--repeat N` times, with the report gaining a delta and `--min-delta`
gating on it. Milestones are defined in
`docs/superpowers/specs/2026-07-30-skill-eval-design.md` §9; the M2 design is
in `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`, the M3 design
is in `docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`, and the M4
design is in `docs/superpowers/specs/2026-08-03-skill-eval-m4-design.md`.

## Commands

```bash
uv sync                              # install (dev deps included)
uv run pytest                        # test suite (integration marker deselected by default)
uv run pytest tests/test_gating.py::test_name -v   # single test
uv run pytest -m integration          # opt-in tier; needs OPENAI_API_KEY, costs real money
uv run pytest tests/test_cassettes.py --record-mode=once   # re-record cassettes (needs a key)
uv run ruff check .                  # lint
uv run ruff format .                 # format (CI runs --check)
uv run skill-eval list ./examples     # dogfood discovery; CI runs this as a self-check
uv run pre-commit install --hook-type commit-msg   # once per clone
```

## Architecture

Three protocols carry the whole design; everything else is plumbing around those seams:

- **`Runner`** (`runners/base.py`) — `run(skill, case) -> RunResult`. The seam every agent
  framework plugs into. **No agent-framework type may appear in the core** — frameworks
  live only inside runner adapters.
- **`Evaluator`** (`evaluators/base.py`) — `evaluate(case, result) -> EvalScore`. The seam
  every scoring strategy plugs into.
- **`Judge`** (`judges/base.py`) — `judge(request) -> JudgeVerdict`. The seam every
  LLM-as-judge implementation plugs into.

Data flow (`orchestrator.run_evals`):

```
path → skills/loader (walk for SKILL.md) → [Skill]
         └─ per skill → cases/loader (evals/ dir or *.eval.yaml) → [EvalCase]
matrix: for each (skill × case × runner):
    Runner.run → RunResult → each Evaluator → EvalScore
aggregate → RunReport → reporters/ + gating → exit code
```

`models.py` holds every Pydantic model; the other modules import from it and never define
their own data shapes.

## Invariants that are easy to break

These are decided behaviors, not accidents — several were bugs caught in review. Preserve
them, and expect a test asserting each.

The full rationale for each of these — plus the module map and extension points — is in
[`ARCHITECTURE.md`](ARCHITECTURE.md). Keep the two in sync: this list is the condensed
form, that file is the explanation.

- **`errored` ≠ `failed`.** `failed` = the case ran and scored below bar (an eval signal).
  `errored` = the runner itself blew up (an infra signal). Runners must **never raise** for
  provider failures — set `RunResult.error` instead. Errored cases fail the gate by default.
- **A run executing zero cases fails the gate.** "Nothing ran" is a broken run, not a pass.
  `gating.evaluate_gate` distinguishes the causes (no skills found / all skipped for having
  no cases / all filtered out by `--tag`).
- **Authoring errors abort the run; they never score as failures.** An unknown assertion
  `kind:`, a malformed regex, or an unknown YAML key is a mistake in the user's files, not a
  signal about the skill. `orchestrator.run_evals` deliberately lets these propagate; `cli.py`
  catches them via `_AUTHORING_ERRORS` and exits 2.
- **Exit codes are the CI contract:** gate pass `0`, gate fail `1`, user/authoring error `2`.
  In `cli.py`, a JSON-write failure only escalates to 2 when the gate itself passed — it must
  not mask an already-failing gate.
- **`extra="forbid"`** on `EvalCase` / `AssertionSpec` / `ToolSpec` / `TrajectorySpec` /
  `BudgetSpec` / `Config` / `RunResult`. Without it a typo like `assertion:` yields a
  vacuously-passing case.
- **All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error
  (`SkillParseError` / `CaseParseError` / `ConfigError`) naming the file and field.
- **YAML goes through `yaml_loading.safe_load`**, never `yaml.safe_load`. The custom loader
  stops YAML 1.1 from turning bare `yes`/`no`/`on`/`off` into booleans.
- **Secrets come from environment variables only** — never from `skill-eval.toml`.
- **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is
  `skill-eval` everywhere: command, config file, distribution.
- **`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.
- **No agent-framework type may appear outside `runners/pydantic_ai.py` and
  `judges/pydantic_ai.py`.** `runners/tools.py` builds framework-neutral `MockTool`s (name +
  JSON schema + callable); the adapters wrap them. `tests/test_framework_isolation.py` guards
  this: it asserts no other module under `src/skill_eval/` imports `pydantic_ai` at the top
  level.
- **`RunResult.tokens` is derived**, not stored — `extra="forbid"` makes writing it a loud
  error rather than a total that silently disagrees with the input/output split it was priced from.
- **Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
  `cost_note`; pricing is reporting metadata and must never be why a run errors. An unpriceable
  `max_cost_usd` is skipped in `BudgetEvaluator` — not counted as passed, recorded as a failing
  check instead — so **any** budget block declaring it fails the case, even one whose other
  priced limits (`max_tokens`, `max_latency_ms`) all hold; `score` still excludes the unpriced
  limit from its divisor, so it neither inflates nor deflates that number. A repo running an
  unpriced model with a budget block that mixes a priced limit and `max_cost_usd` will see
  those cases turn red on upgrade — drop `max_cost_usd` for that provider rather than relying
  on the skip to be silently ignored.
- **Mock tools accept any arguments.** A model hallucinating an argument must not raise, or an
  eval signal would surface as an infra error.
- **Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act;
  a missing cassette skips rather than fails, but a mismatched request fails rather than
  reaching the network.
- **An errored *evaluator* errors the case.** `errored` ≠ `failed` now applies to evaluators
  too: a judge endpoint returning 500 must not read as a skill that got worse.
- **Judges never raise for provider failures** — they set `JudgeVerdict.error`.
- **skill-eval derives `passed` and `score` from per-check verdicts.** The judge is never
  asked for a blended number, and a check that passes without evidence is recorded as a
  failure.
- **An unscripted `FakeJudge` errors rather than passing.** That is what makes
  `judge = "fake"` safe as the built-in default: an unchecked rubric is never a green case.
- **Judge spend never enters `RunResult`.** It lives on `EvalScore.cost_usd` and is reported
  as judge overhead; `budget:` measures the skill, not the harness.
- **A `version:` that YAML does not parse as a string is an authoring error.** `SkillParseError`,
  exit 2. YAML resolves `1.20` and `1.2` to the same float, so two genuinely different versions
  would silently compare equal under `--baseline previous`; three-part semver (`1.0.0`) is
  already a string and needs no quoting.
- **Absent `--baseline`, behavior is identical to the single-arm run.** `none` is a *kind* of
  baseline; the flag being unset — not `--baseline none` — is what turns comparison off.
- **Baseline outcomes never count toward the gate's pass rate or `errored`.** Every
  `RunReport` aggregate reads `candidate_outcomes`; `baseline_outcomes` / `baseline_errored`
  surface the comparison side apart from them.
- **The baseline arm never receives the skill's name under `--baseline none`.** A skill with
  both `description` and `instructions` empty gets `BASELINE_PREAMBLE` instead of the normal
  `# {name}` header, keyed on emptiness rather than on which arm is running.
- **An unresolvable baseline is reported, never assumed to be "no change".** `resolve_previous`
  returns `BaselineUnavailable`; treating silence as "no change" would let a repo pass
  `--min-delta` forever by deleting its git history.
- **The delta is paired: a case excluded from one arm is excluded from both.** Keeping the
  surviving half of a broken pair would bias the aggregate with an unmeasured comparison.
- **`--min-delta` without a baseline is a user error (exit 2); gating on a delta with nothing
  comparable fails.** Both are the vacuous-pass rejection this project applies everywhere else.
- **Low-signal and high-variance flags never change the exit code.** They are diagnostics
  about the eval suite, not verdicts on the skill.
- **`resolve_previous` never raises for environmental failures** — no `git`, no repo, an
  untracked `SKILL.md`, an exhausted history window all come back as `BaselineUnavailable`.

## Documentation

Documentation ships **with** the change, never as a follow-up. Two CI jobs enforce this
(`docs`, `docs-freshness`) and `tests/test_docs.py` asserts the docs still match the code.

| When you change | Update |
| --- | --- |
| A CLI command or flag | `docs/cli.md` |
| A `Config` field | `docs/configuration.md` |
| An `EvalCase` field or assertion kind | `docs/eval-files.md` |
| Runner behavior, tools, budgets, pricing | `docs/runners.md` |
| Gate rules, exit codes, the JSON report | `docs/gating.md` |
| A protocol, an invariant, or the module map | `ARCHITECTURE.md` |
| Anything needing a new page | the page plus `nav:` in `mkdocs.yml` |

`README.md` is a landing page only. Reference prose lives in `docs/` — do not reintroduce
it in the README, and do not duplicate `ARCHITECTURE.md` into `docs/architecture.md`
(that page includes the root file via a snippet).

Before pushing:

```bash
uv sync --group docs           # mkdocs lives in the docs group; plain `uv sync` skips it
uv run mkdocs build --strict
uv run pytest tests/test_docs.py
```

`docs/superpowers/` is a historical archive of specs and plans. It is excluded from the
published site and does **not** count as documenting a change.

When a change genuinely needs no documentation — a pure refactor, a dependency bump — add
the `no-docs-needed` label to the PR to satisfy the `docs-freshness` gate.

## Conventions

- **Test-driven:** write the failing test first. The pipeline tier (`FakeRunner`) must stay
  zero-cost, offline, and deterministic — every test passes with no network.
- **Conventional Commits are enforced, not stylistic.** `cz bump` derives the version and
  changelog from history. CI checks the PR title with `cz check` and every branch commit with
  `scripts/check_commits.py`. PRs are squash-merged, so **the PR title becomes the commit on
  main** — it must be conventional too. `scripts/legacy-commits.txt` exempts two pre-convention
  commits and should only ever shrink.
- `tests/conftest.py` chdirs every test into a fresh `tmp_path` so config upward-discovery
  can't pick up an ambient `skill-eval.toml`. Tests needing real discovery pass an explicit
  `start=`.
- `docs/superpowers/plans/` is a **historical record** — its code blocks were superseded by
  what shipped. Read `src/` as the source of truth; the design spec in `docs/superpowers/specs/`
  is still current.
