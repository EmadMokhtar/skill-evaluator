# skill-eval — Design

**Date:** 2026-07-30
**Status:** Approved (design), pending implementation plan

## 1. Purpose

`skill-eval` is a standalone CLI + Python library that runs **evaluations on Anthropic-style Agent Skills** (`SKILL.md` artifacts). It is built to learn and apply **production-grade eval practices for agents and their skills**, and is designed to run both in **CI/CD** (as a build gate) and via **manual trigger**.

The skills under test and their eval cases are **inputs** to the tool — nothing about a skill-under-test is vendored into this repo. Any skill repo can adopt `skill-eval` without embedding it.

### Goals
- Evaluate whether a skill improves an agent's behavior, across multiple eval "levels" (cheap triggering checks → full agent runs).
- **Measure improvement, not just absolute quality.** An eval that only runs with the skill loaded cannot separate "the skill works" from "the model would have done it anyway", so the tool runs each case against a **baseline** (no skill, or the previous version of the skill) and reports the delta (M4).
- Be framework-agnostic in the core; run real agents behind pluggable adapters.
- Score with four complementary evaluator types: deterministic assertions, trajectory/tool-use checks, efficiency budgets, and LLM-as-judge.
- Produce CI-gating outputs (exit codes/thresholds, JSON, JUnit XML, Markdown/HTML) plus cost & latency tracking.
- Teach the fundamentals: we own the eval loop rather than delegating it to a single library.

### Non-goals
- Not a general-purpose LLM benchmarking platform.
- Not a hosted service — it's a binary/library invoked locally or in CI.
- Not tied to any one agent framework or eval-platform vendor.

## 2. Core concept

The entire design rests on **two protocols**:

- **`Runner`** — turns `(skill, case)` into a `RunResult`. A `RunResult` carries the final output text, the transcript/messages, the tool-call **trajectory**, and tokens/latency/cost. The runner takes the whole case, not just its task string, because it also sets up the environment the case declares (its mock tools).
- **`Evaluator`** — turns `(case, run_result)` into an `EvalScore` (pass/fail + numeric score + detail).

Everything else — loading, orchestrating, reporting, gating — is plumbing around those two seams. **No agent-framework type (PydanticAI, LangChain) ever appears in the core**; frameworks live only inside `Runner` adapters.

### What is being evaluated (skill mapping)
`SKILL.md` remains the artifact. Each runner adapter loads the skill's instructions into the agent's system prompt and exposes the skill's bundled scripts as tools, then runs the task prompt. We evaluate whether the skill improves that agent's behavior — portably across frameworks/providers.

## 3. Architecture & components

This is the **target** architecture. Unannotated entries shipped in M0+M1; a milestone tag
like `(M2)` marks what is not built yet and when it lands — §9 defines each milestone.

```
src/skill_eval/                # import module (hyphens invalid in identifiers, so underscore)
  models.py        # Pydantic v2: Skill, EvalCase, RunResult, EvalScore, RunReport
  yaml_loading.py  # shared strict-bool YAML loader (bare yes/no/on/off stay strings)
  skills/loader.py # walk a path for SKILL.md files → [Skill] (frontmatter, body, scripts)
  cases/loader.py  # discover & parse evals (*.eval.yaml / evals/) → [EvalCase]
  runners/
    base.py        # Runner protocol: run(skill, case) -> RunResult
    fake.py        # deterministic, no-API runner (backbone of our own tests)
    pydantic_ai.py # (M2) adapter #1 (real, primary — provider-flexible)
    tools.py       # (M2) case-declared mock tools; (M6) sandboxed real-execution toolset
    langchain.py   # (M8, optional) adapter #2 — only if it slots in cleanly
  evaluators/
    base.py        # Evaluator protocol: evaluate(case, result) -> EvalScore
    assertion.py   # contains / not_contains / regex / equals
                   #   (M6) json-schema / file-produced
    trajectory.py  # (M2) tool called, order, forbidden tools, max_calls
                   #   (M3) skill-triggered, incl. negative controls
    budget.py      # (M2) max_tokens / max_cost_usd / max_latency_ms
    judge.py       # (M3) LLM-as-judge: rubric -> per-check pass + evidence
  orchestrator.py  # matrix (skill × case × runner) → RunReport
                   #   (M2) retries; (M4) baseline pairing + repeats; (M5) concurrency
  reporters/       # console, json; (M5) junit, markdown/html
  gating.py        # thresholds -> exit code; (M4) delta thresholds
  config.py        # skill-eval.toml: default runner, thresholds
                   #   (M2) model, retries; (M3) judge model; (M5) concurrency
  cli.py           # Typer: run / list; (M7) init
```

Repo-level (outside the package):
```
examples/               # real sample skills + their eval suites (dogfooding + live tests)
tests/
  cassettes/            # recorded real API interactions for the replay test tier
.github/workflows/      # ci.yml (test/lint) + release.yml (bump → tag → publish)
pyproject.toml          # deps, [tool.commitizen], single-source [project].version
CHANGELOG.md            # generated by Commitizen from conventional commits
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
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <id>] [--tag <tag>] \
                      [--report console,json,junit,md] [--min-pass-rate <f>] \
                      [--baseline none|previous] [--repeat <n>] [--min-delta <f>] \
                      [--config <file>]
skill-eval list <path>          # discover skills + their eval cases, no execution
skill-eval init <skill-dir>     # scaffold an eval file next to a skill
```

- `<path>` — a single skill dir (`SKILL.md`) **or** a parent dir containing many skill dirs. Discovery is recursive; the everyday CI call is simply `skill-eval run ./skills`.
- `--evals` — optional explicit eval file/dir; otherwise discovered beside each skill.
- `--model` — provider-qualified model id for real runners (e.g. `openai:gpt-4o-mini`), M2.
- `--baseline` / `--repeat` / `--min-delta` — comparative evals (M4): run each case with and without the skill, sample it `n` times, and gate on the improvement rather than the absolute pass rate.
- `--config` — optional `skill-eval.toml`; otherwise discovered by searching upward from CWD (see §6).
- Resolution order for runner/judge/thresholds: **CLI flag > config file > built-in default**.
- Skills with **no eval files** are reported as **skipped** (visible, never silently ignored).

## 6. Configuration

`skill-eval.toml` (all optional; CLI overrides). Located via `--config`, or otherwise discovered by searching upward from the current working directory — repo root is the conventional home, not a hard requirement:
- `default_runner`, `model`, `temperature`, `judge_model`, `concurrency`, `retry` policy.
- Threshold defaults: `min_pass_rate` (overall) and optional per-skill thresholds; `min_delta` once comparative evals land (M4).
- Which reporters to emit.
- **Secrets (API keys) come from environment variables only — never from config.**

## 7. Error handling

- **Errored vs. failed is a first-class distinction:**
  - **failed** — case ran but scored below bar (an *eval* signal).
  - **errored** — the runner blew up (API 5xx, timeout, missing key — an *infra* signal).
  - Both are tracked separately in the report. **Errored cases fail the gate by default** so CI never goes green on a broken run.
- **A run that executed zero cases fails the gate.** "Nothing ran" is a broken run, not a pass — otherwise a mistyped path or a renamed directory reports success forever. The reason distinguishes the causes: no skills found, all skills skipped (no eval cases), or all cases filtered out by `--tag`.
- **Authoring errors abort the run** rather than scoring as failures. An unsupported assertion `kind:`, a malformed regex, or an unknown key in an eval file is a mistake in the user's YAML, not a signal about the skill — so the orchestrator lets these propagate and the CLI reports them cleanly. Unknown keys are rejected (`extra="forbid"`) so a typo like `assertion:` can't yield a vacuously-passing case.
- **Exit codes are a contract:** gate pass → `0`, gate fail → `1`, user/authoring error → `2`.
- Transient runner errors get **retries with backoff**.
- A **preflight check** verifies required API keys before spending anything.
- Skill/YAML/config parse errors **fail fast** with a precise message (which file, which field). This includes unreadable and non-UTF-8 files — all file IO pins `encoding="utf-8"` rather than inheriting a platform default.
- **Judge reliability:** temperature 0 + Pydantic structured output, with each rubric check returning its own verdict **and the evidence for it** — an unsupported PASS is the judge's characteristic failure. Optional N-sample majority vote is deferred.

## 8. Testing strategy (the tool's own tests)

Three tiers, from cheapest/most-deterministic to most-realistic:

1. **Pipeline tier (`FakeRunner`) — every PR, zero cost.** `FakeRunner` returns scripted `RunResult`s so the whole pipeline (loaders → evaluators → orchestrator → reporters → gating) is tested with **zero API cost and full determinism**. Unit tests cover loaders, each evaluator (fed synthetic `RunResult`s), reporters, and gating logic.
2. **Recorded/replay tier (cassettes) — every PR, real fidelity, zero cost.** Real provider HTTP interactions are captured once to cassettes under `tests/cassettes/` (via VCR-style recording) and **replayed deterministically** in CI. Default mode is *replay-only*: a request with no matching cassette fails rather than hitting the network. Cassettes are **secret-redacted** (auth headers / API keys scrubbed on record). A manual "refresh cassettes" workflow (needs API keys) re-records when provider behavior changes.
3. **Live integration tier — nightly/manual, real $.** PydanticAI runs against a real provider (cheap model) on the real **example skills** in `examples/`, behind an opt-in `@pytest.mark.integration` marker that is **skipped unless an API key is present**. This doubles as **dogfooding** — `skill-eval` evaluating real skills is itself the smoke test. Runs on a schedule / manual dispatch, never blocking per-PR.

Development follows test-driven development: write the failing test first, then the implementation.

## 9. Roadmap / milestones

Each milestone is independently shippable and leaves the tool working end-to-end (via `FakeRunner` until real adapters land).

- **M0 — Scaffolding & release plumbing:** uv project, `src/skill_eval/` layout, ruff + pytest, `skill-eval.toml` config loader, Typer CLI skeleton. **Commitizen configured** (conventional-commit `commit-msg` hook, single-source version in `pyproject.toml`); CI workflow runs lint + the pipeline-tier tests. `skill-eval --help` runs. (See §12.)
- **M1 — Core engine (deterministic, zero-cost):** Skill loader (multi-skill discovery), YAML case loader, `Runner`/`Evaluator` protocols, `FakeRunner`, **Assertion evaluator**, orchestrator (skill × case × runner), console + JSON reporter, gating + exit code. Fully tested against `FakeRunner` — proves the whole loop with no API spend.
- **M2 — PydanticAI runner + Trajectory & Budget evaluators + cassettes:** first real adapter (provider-flexible), case-declared **mock tools**, tool-call/trajectory capture, **Trajectory evaluator** (`called` / `forbidden` / `order` / `max_calls`), **Budget evaluator** (`max_tokens` / `max_cost_usd` / `max_latency_ms`), cost & latency capture, retries + API-key preflight. Real `examples/` skills. Stand up the **recorded/replay (cassette) test tier** and the live-integration marker (§8). Detailed design: `2026-08-01-skill-eval-m2-design.md`.
- **M3 — LLM-as-judge + triggering evals:** rubric-based judge at temperature 0 with structured output — **per-check verdicts carrying evidence** (`{overall_pass, score, checks:[{id, pass, evidence}]}`), not one blended number, since an unsupported PASS is the judge's main failure mode. Cases gain a free-text `expected:` and a natural-language `rubric:` list. Adds the second runner mode: the skill is *offered* (name + description) rather than force-loaded, so `trajectory.skill_triggered` can measure whether the agent chose it — evaluated with **negative controls** (`should_trigger: false`), because a positives-only set scores a skill that fires on everything at 100%.
- **M4 — Comparative evals (measure improvement, not quality):** `--baseline none|previous` runs every case twice — with the skill and without it (or against the previous version) — and `--repeat N` samples each configuration `N` times. The report gains a `delta` block (pass rate, tokens, cost, latency) plus per-case mean/stddev; gating learns `--min-delta` so CI can require that a `SKILL.md` edit actually *improved* things. Reporting flags **low-signal assertions** (those passing in both configurations, which inflate the with-skill score while measuring nothing) and **high-variance cases**, where an unstable pass rate points at ambiguous skill instructions.
- **M5 — CI/CD polish + automated release:** JUnit XML + Markdown/HTML summary reporters, GitHub Action example + PR-comment summary, per-skill thresholds, bounded orchestrator concurrency. **Automated release pipeline** (`cz bump` on merge to main → tag → Trusted-Publishing to PyPI) and the manual "refresh cassettes" workflow (§12).
- **M6 — Real-execution tools:** a sandboxed built-in toolset (read/write/list files, run a bundled skill script) rooted in a per-case temp workspace, per-case input `files:`, and the `file-produced` / `json-schema` assertion kinds. Moves evals from "did the model choose the right tool" to "did it produce the right artifact" — the level both the OpenAI and agentskills.io eval guides operate at.
- **M7 — DX & docs:** `skill-eval init` scaffolder, docs, more `examples/` skills + eval suites, quickstart.
- **M8 (optional) — LangChain adapter:** only if it slots cleanly behind `Runner`; enables cross-framework matrix. Droppable.

**Ordering principle:** the entire pipeline is exercised at **M1 with zero cost**, then real runners and richer evaluators swap in behind stable seams. Each milestone is a working tool, not a partial one.

**Prior art consulted (2026-08-01)** — OpenAI's *Evaluating Agent Skills* and agentskills.io's *Evaluating skill output quality*. Four ideas were adopted from them and are folded into the milestones above: baseline/delta comparison and repeat-with-variance (M4), efficiency as a first-class goal category alongside outcome/process/style (M2 Budget evaluator, M4 delta), negative controls for triggering (M3), and evidence-bearing per-check judge output (M3).

## 10. Stack & tooling

- **uv** for env/deps (lockfile, fast) and for `uv build` at release.
- **Pydantic v2** for all models (pairs naturally with PydanticAI / pydantic-evals patterns).
- **Typer** for the CLI.
- **pytest** for the tool's own tests; **ruff** for lint/format.
- **VCR-style HTTP recording** (e.g. `vcrpy`/`pytest-recording`) for the cassette test tier.
- **Commitizen** for conventional-commit linting, versioning, changelog, and tagging.
- **PydanticAI** as the primary real runner (provider-flexible). LangChain optional/deferred.

## 11. Naming

- User-facing name everywhere: **`skill-eval`** (command, config file `skill-eval.toml`, `skill-eval init`, distribution name).
- Internal Python import module: `skill_eval` (hyphens illegal in Python identifiers) — folder `src/skill_eval/`. Never appears in any user-typed command or doc.

## 12. Versioning & release

**Conventional commits + Commitizen.**
- All commits follow Conventional Commits; a `commit-msg` git hook runs `cz check` to reject non-conforming messages.
- **Single source of version:** `[project].version` in `pyproject.toml`, with `[tool.commitizen] version_provider = "uv"` — bumps the version **and** keeps `uv.lock` in sync in the same step (which `pep621` would not). Runtime reads it via `importlib.metadata.version("skill-eval")` so `skill_eval.__version__` never drifts.
- `cz bump` computes the SemVer bump from the commits since the last tag, updates the version, regenerates `CHANGELOG.md`, and creates the `vX.Y.Z` tag.

**Automated release on merge to main.**
1. A push to `main` triggers a **release job** that runs `cz bump`. If no commits warrant a bump, it is a **no-op** and nothing is released.
2. When a bump occurs, the job commits the version/changelog change back to `main` and pushes the `vX.Y.Z` tag. Loop-safety: the bump commit carries a skip marker so it does not re-trigger the release job; a concurrency guard serializes releases.
3. Pushing the tag triggers the **publish job**: `uv build` (sdist + wheel) → publish to PyPI via **Trusted Publishing (OIDC)** — no stored API tokens. A protected `pypi` GitHub Environment gates the publish.
4. Optional pre-release: publish RCs to **TestPyPI** to smoke-test the package before the real index.

**Requirements & caveats.**
- The release job needs push access to protected `main` (a GitHub App or PAT with a bypass allowance) and conventional commit history — so PRs are squash-merged with a conventional title, or commits are already conventional.
- The cassette test tier has a separate **manual "refresh cassettes"** workflow (holds API keys, re-records, opens a PR with updated cassettes) so replay stays truthful without keys in the normal CI path.
