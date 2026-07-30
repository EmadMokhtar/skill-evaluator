# skill-eval — Design

**Date:** 2026-07-30
**Status:** Approved (design), pending implementation plan

## 1. Purpose

`skill-eval` is a standalone CLI + Python library that runs **evaluations on Anthropic-style Agent Skills** (`SKILL.md` artifacts). It is built to learn and apply **production-grade eval practices for agents and their skills**, and is designed to run both in **CI/CD** (as a build gate) and via **manual trigger**.

The skills under test and their eval cases are **inputs** to the tool — nothing about a skill-under-test is vendored into this repo. Any skill repo can adopt `skill-eval` without embedding it.

### Goals
- Evaluate whether a skill improves an agent's behavior, across multiple eval "levels" (cheap triggering checks → full agent runs).
- Be framework-agnostic in the core; run real agents behind pluggable adapters.
- Score with three complementary evaluator types: deterministic assertions, trajectory/tool-use checks, and LLM-as-judge.
- Produce CI-gating outputs (exit codes/thresholds, JSON, JUnit XML, Markdown/HTML) plus cost & latency tracking.
- Teach the fundamentals: we own the eval loop rather than delegating it to a single library.

### Non-goals
- Not a general-purpose LLM benchmarking platform.
- Not a hosted service — it's a binary/library invoked locally or in CI.
- Not tied to any one agent framework or eval-platform vendor.

## 2. Core concept

The entire design rests on **two protocols**:

- **`Runner`** — turns `(skill, task)` into a `RunResult`. A `RunResult` carries the final output text, the transcript/messages, the tool-call **trajectory**, and tokens/latency/cost.
- **`Evaluator`** — turns `(case, run_result)` into an `EvalScore` (pass/fail + numeric score + detail).

Everything else — loading, orchestrating, reporting, gating — is plumbing around those two seams. **No agent-framework type (PydanticAI, LangChain) ever appears in the core**; frameworks live only inside `Runner` adapters.

### What is being evaluated (skill mapping)
`SKILL.md` remains the artifact. Each runner adapter loads the skill's instructions into the agent's system prompt and exposes the skill's bundled scripts as tools, then runs the task prompt. We evaluate whether the skill improves that agent's behavior — portably across frameworks/providers.

## 3. Architecture & components

```
src/skill_eval/                # import module (underscore required by Python)
  models.py        # Pydantic v2: Skill, EvalCase, RunResult, EvalScore, RunReport
  skills/loader.py # walk a path for SKILL.md files → [Skill] (frontmatter, body, scripts)
  cases/loader.py  # discover & parse evals (*.eval.yaml / evals/) → [EvalCase]
  runners/
    base.py        # Runner protocol: run(skill, task) -> RunResult
    fake.py        # deterministic, no-API runner (backbone of our own tests)
    pydantic_ai.py # adapter #1 (real, primary — provider-flexible)
    langchain.py   # adapter #2 (optional, deferred)
  evaluators/
    base.py        # Evaluator protocol: evaluate(case, result) -> EvalScore
    assertion.py   # contains / regex / equals / json-schema / file-produced
    trajectory.py  # tool called, order, forbidden tools, skill-triggered
    judge.py       # LLM-as-judge: rubric -> score + rationale (structured output)
  orchestrator.py  # matrix (skill × case × runner), concurrency, retries → RunReport
  reporters/       # console, json, junit, markdown/html
  gating.py        # thresholds -> exit code
  config.py        # skill-eval.toml: default runner, judge model, thresholds, concurrency
  cli.py           # Typer: run / list / init
```

### Component responsibilities
- **Skill loader** — given an input path, walks the tree for `SKILL.md` files and produces normalized `Skill` models (frontmatter `name`/`description`, body instructions, bundled scripts/resources). A single skill dir is the degenerate one-match case.
- **Case loader** — discovers each skill's eval cases by convention (an `evals/` dir or `*.eval.yaml` beside the skill), or via explicit `--evals`/mapping override. Parses YAML into `EvalCase` models. A Python escape hatch registers custom evaluators by name.
- **Runner (pluggable)** — the `Runner` protocol; adapters implement it. `FakeRunner` ships from day one (scripted, deterministic, zero-cost). `PydanticAIRunner` is the first real adapter (provider-flexible: Anthropic/OpenAI/Gemini/Groq/local). `LangChainRunner` is optional/deferred and only built if it slots cleanly behind the same protocol.
- **Evaluators (pluggable)** — the `Evaluator` protocol; three built-ins: Assertion, Trajectory, LLM-judge.
- **Orchestrator** — builds and runs the **skill × case × runner** matrix with bounded concurrency and a retry policy; aggregates into a `RunReport` grouped by skill (per-skill + overall pass rates).
- **Reporters** — render a `RunReport` to console, JSON, JUnit XML, and Markdown/HTML.
- **Gating** — applies thresholds → process exit code.
- **CLI (Typer)** — `run`, `list`, `init`.

## 4. Data flow

```
input path ─▶ Skill loader ─▶ [Skill]          (walk tree for SKILL.md)
                 └─ per skill ─▶ Case loader ─▶ [EvalCase]   (evals/ or *.eval.yaml)
orchestrator: for each (skill × case × runner):
    Runner.run(skill, task) ─▶ RunResult
    for each evaluator in case:  evaluate(case, result) ─▶ EvalScore
aggregate ─▶ RunReport (grouped by skill) ─▶ reporters + gating ─▶ exit code
```

## 5. CLI & inputs

The tool is a **standalone evaluator**; skills and evals are external inputs.

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--filter <tag>] \
                      [--report console,json,junit,md] [--min-pass-rate <f>] \
                      [--config <file>]
skill-eval list <path>          # discover skills + their eval cases, no execution
skill-eval init <skill-dir>     # scaffold an eval file next to a skill
```

- `<path>` — a single skill dir (`SKILL.md`) **or** a parent dir containing many skill dirs. Discovery is recursive; the everyday CI call is simply `skill-eval run ./skills`.
- `--evals` — optional explicit eval file/dir; otherwise discovered beside each skill.
- `--config` — optional `skill-eval.toml`; otherwise discovered from CWD.
- Resolution order for runner/judge/thresholds: **CLI flag > config file > built-in default**.
- Skills with **no eval files** are reported as **skipped** (visible, never silently ignored).

## 6. Configuration

`skill-eval.toml` at repo root (all optional; CLI overrides):
- `default_runner`, `judge_model`, `concurrency`, `retry` policy.
- Threshold defaults: `min_pass_rate` (overall) and optional per-skill thresholds.
- Which reporters to emit.
- **Secrets (API keys) come from environment variables only — never from config.**

## 7. Error handling

- **Errored vs. failed is a first-class distinction:**
  - **failed** — case ran but scored below bar (an *eval* signal).
  - **errored** — the runner blew up (API 5xx, timeout, missing key — an *infra* signal).
  - Both are tracked separately in the report. **Errored cases fail the gate by default** so CI never goes green on a broken run.
- Transient runner errors get **retries with backoff**.
- A **preflight check** verifies required API keys before spending anything.
- Skill/YAML parse errors **fail fast** with a precise message (which file, which field).
- **Judge reliability:** temperature 0 + Pydantic structured output. Optional N-sample majority vote is deferred.

## 8. Testing strategy (the tool's own tests)

- **`FakeRunner` is the backbone:** it returns scripted `RunResult`s so the whole pipeline (loaders → evaluators → orchestrator → reporters → gating) is tested in CI with **zero API cost and full determinism**.
- Unit tests for loaders, each evaluator (fed synthetic `RunResult`s), reporters, and gating logic.
- Integration tests that hit real providers live behind an opt-in pytest marker (not run by default CI).
- Development follows TDD (per superpowers:test-driven-development).

## 9. Roadmap / milestones

Each milestone is independently shippable and leaves the tool working end-to-end (via `FakeRunner` until real adapters land).

- **M0 — Scaffolding:** uv project, `src/skill_eval/` layout, ruff + pytest, `skill-eval.toml` config loader, Typer CLI skeleton, GitHub Actions workflow stub. `skill-eval --help` runs.
- **M1 — Core engine (deterministic, zero-cost):** Skill loader (multi-skill discovery), YAML case loader, `Runner`/`Evaluator` protocols, `FakeRunner`, **Assertion evaluator**, orchestrator (skill × case × runner), console + JSON reporter, gating + exit code. Fully tested against `FakeRunner` — proves the whole loop with no API spend.
- **M2 — PydanticAI runner + Trajectory evaluator:** first real adapter (provider-flexible), tool-call/trajectory capture, **Trajectory evaluator**, cost/latency capture. First real end-to-end eval of a sample skill.
- **M3 — LLM-as-judge evaluator:** rubric-based judge, structured output, temp 0, rationale in report.
- **M4 — CI/CD polish:** JUnit XML + Markdown/HTML summary reporters, GitHub Action example + PR-comment summary, per-skill thresholds, optional baseline/regression compare.
- **M5 — DX & docs:** `skill-eval init` scaffolder, docs, example skill + eval suite, quickstart.
- **M6 (optional) — LangChain adapter:** only if it slots cleanly behind `Runner`; enables cross-framework matrix. Droppable.

**Ordering principle:** the entire pipeline is exercised at **M1 with zero cost**, then real runners and richer evaluators swap in behind stable seams. Each milestone is a working tool, not a partial one.

## 10. Stack & tooling

- **uv** for env/deps (lockfile, fast).
- **Pydantic v2** for all models (pairs naturally with PydanticAI / pydantic-evals patterns).
- **Typer** for the CLI.
- **pytest** for the tool's own tests; **ruff** for lint/format.
- **PydanticAI** as the primary real runner (provider-flexible). LangChain optional/deferred.

## 11. Naming

- User-facing name everywhere: **`skill-eval`** (command, config file `skill-eval.toml`, `skill-eval init`, distribution name).
- Internal Python import module: `skill_eval` (hyphens illegal in Python identifiers) — folder `src/skill_eval/`. Never appears in any user-typed command or doc.
