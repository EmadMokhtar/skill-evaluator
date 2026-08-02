# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`skill-eval` is a standalone CLI + library that runs evaluations on Anthropic-style Agent
Skills (`SKILL.md` files). Skills under test and their eval cases are **inputs** — nothing
about a skill-under-test is vendored here. The tool is meant to run as a CI gate (exit code
is the contract) or on demand.

Currently at **M2**: the pipeline runs real agents through `PydanticAIRunner`
(provider-flexible, via PydanticAI), scores tool use and efficiency as well as
output text, and is tested against recorded provider traffic. `FakeRunner`
remains the default and the backbone of the zero-cost test tier. Milestones are
defined in `docs/superpowers/specs/2026-07-30-skill-eval-design.md` §9; the M2
design is in `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`.

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

Two protocols carry the whole design; everything else is plumbing around those seams:

- **`Runner`** (`runners/base.py`) — `run(skill, case) -> RunResult`. The seam every agent
  framework plugs into. **No agent-framework type may appear in the core** — frameworks
  live only inside runner adapters.
- **`Evaluator`** (`evaluators/base.py`) — `evaluate(case, result) -> EvalScore`. The seam
  every scoring strategy plugs into.

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
- **`extra="forbid"`** on `EvalCase` / `AssertionSpec` / `Config`. Without it a typo like
  `assertion:` yields a vacuously-passing case.
- **All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error
  (`SkillParseError` / `CaseParseError` / `ConfigError`) naming the file and field.
- **YAML goes through `yaml_loading.safe_load`**, never `yaml.safe_load`. The custom loader
  stops YAML 1.1 from turning bare `yes`/`no`/`on`/`off` into booleans.
- **Secrets come from environment variables only** — never from `skill-eval.toml`.
- **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is
  `skill-eval` everywhere: command, config file, distribution.
- **`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.
- **No agent-framework type may appear outside `runners/pydantic_ai.py`.** `runners/tools.py`
  builds framework-neutral `MockTool`s (name + JSON schema + callable); the adapter wraps them.
  A test asserts `pydantic_ai` does not appear in `tools.py`.
- **`RunResult.tokens` is derived**, not stored — `extra="forbid"` makes writing it a loud
  error rather than a total that silently disagrees with the input/output split it was priced from.
- **Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
  `cost_note`; pricing is reporting metadata and must never be why a run errors. An unpriceable
  cost limit is skipped in `BudgetEvaluator` — not counted as passed — so an unpriced cost limit
  as the only budget check causes the case to fail (nothing was verified).
- **Mock tools accept any arguments.** A model hallucinating an argument must not raise, or an
  eval signal would surface as an infra error.
- **Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act;
  a missing cassette skips rather than fails, but a mismatched request fails rather than
  reaching the network.

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
