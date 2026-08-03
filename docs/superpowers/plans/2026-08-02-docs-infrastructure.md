# Docs Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a MkDocs documentation site to GitHub Pages, add a root `ARCHITECTURE.md`, gate every PR on documentation staying current, and give GitHub Copilot the review instructions this codebase's invariants require.

**Architecture:** `docs/` becomes the MkDocs source directory, with the existing `docs/superpowers/` tree excluded from the build. Reference content moves out of `README.md` — it is not copied — so no prose lives in two places. Freshness is enforced in three layers: executable tests that assert docs match introspected source, a `mkdocs build --strict` CI job, and a heuristic script that fails a PR touching `src/` without touching docs.

**Tech Stack:** MkDocs + mkdocs-material, GitHub Actions (`deploy-pages`), pytest, Typer/Click introspection, `uv` dependency groups.

## Global Constraints

- Spec: [`docs/superpowers/specs/2026-08-02-docs-infrastructure-design.md`](../specs/2026-08-02-docs-infrastructure-design.md). Read it before starting.
- The user-facing name is **`skill-eval`**. The string `skill_eval` (underscore) must never appear in user-facing output or documentation prose, except as a literal Python import path or file path.
- Conventional Commits are enforced by a `commit-msg` pre-commit hook and by CI. Every commit message in this plan is already conventional — use it verbatim.
- Site URL: `https://emadmokhtar.github.io/skill-evaluator/`. Repo URL: `https://github.com/EmadMokhtar/skill-evaluator`.
- All file IO in Python pins `encoding="utf-8"`.
- YAML parsing in product and test code goes through `skill_eval.yaml_loading.safe_load`, never `yaml.safe_load`.
- Tests must pass offline with no network and no API key. `pytest` runs with `--block-network` and deselects the `integration` marker by default.
- `tests/conftest.py` has an autouse fixture that chdirs every test into a fresh `tmp_path`. Any test that reads repository files **must** anchor on `Path(__file__).resolve().parents[1]`, never on `Path.cwd()`.
- Line length is 100 (`ruff`). Run `uv run ruff format .` and `uv run ruff check .` before every commit.
- Two repository settings must be changed by a human and are **not** part of this plan: Settings → Pages → Source = *GitHub Actions*, and enabling Copilot code review.

---

## File Structure

**Created:**

| Path | Responsibility |
| --- | --- |
| `mkdocs.yml` | Site config: theme, extensions, nav, `exclude_docs` |
| `docs/index.md` | Landing page: what it is, why, install |
| `docs/getting-started.md` | First skill, first eval file, first run |
| `docs/eval-files.md` | Eval YAML reference: case fields, assertion kinds, discovery |
| `docs/cli.md` | Command and flag reference |
| `docs/configuration.md` | `skill-eval.toml`, env vars, precedence |
| `docs/runners.md` | Runners, mock tools, trajectories, budgets, pricing |
| `docs/gating.md` | Gate rules, exit codes, JSON report |
| `docs/architecture.md` | Thin page that includes `ARCHITECTURE.md` |
| `docs/contributing.md` | Dev loop, Conventional Commits, docs policy |
| `docs/roadmap.md` | Milestone table |
| `ARCHITECTURE.md` | The "why" document: protocols, module map, invariants |
| `tests/test_docs.py` | Executable docs-drift assertions |
| `scripts/check_docs_updated.py` | Heuristic src-without-docs check |
| `tests/test_check_docs_updated.py` | Unit tests for that script |
| `.github/workflows/docs.yml` | Build + deploy to GitHub Pages on `main` |
| `.github/pull_request_template.md` | PR checklist including docs |
| `.github/copilot-instructions.md` | Repo-wide Copilot review instructions |
| `.github/instructions/*.instructions.md` | Six path-scoped Copilot instruction files |

**Modified:**

| Path | Change |
| --- | --- |
| `src/skill_eval/evaluators/assertion.py` | Add introspectable `ASSERTION_KINDS` |
| `README.md` | Reduced to a landing page |
| `pyproject.toml` | `docs` dependency group |
| `.github/workflows/ci.yml` | `docs` job, `docs-freshness` job |
| `CLAUDE.md` | Documentation section, link to `ARCHITECTURE.md` |
| `.gitignore` | Ignore `site/` |

---

### Task 1: Make assertion kinds introspectable

`evaluators/assertion.py` dispatches on an `if`-chain, which Task 5's docs test cannot enumerate. Extract the kinds into a module-level tuple that both the dispatch and the test read, so a new kind cannot be added without the docs test noticing.

**Files:**
- Modify: `src/skill_eval/evaluators/assertion.py:18-30`
- Test: `tests/test_assertion_evaluator.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `skill_eval.evaluators.assertion.ASSERTION_KINDS: tuple[str, ...]` — every supported `AssertionSpec.kind` value, in documentation order. Used by Task 5.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assertion_evaluator.py`:

```python
def test_assertion_kinds_lists_every_supported_kind():
    """ASSERTION_KINDS is the single source of truth for supported kinds.

    The docs test in tests/test_docs.py enumerates this tuple, so a kind that
    dispatches but is missing here would ship undocumented.
    """
    from skill_eval.evaluators.assertion import ASSERTION_KINDS

    assert ASSERTION_KINDS == ("contains", "not_contains", "regex", "equals")


def test_every_listed_kind_actually_dispatches():
    """No entry in ASSERTION_KINDS may raise UnknownAssertionKind."""
    from skill_eval.evaluators.assertion import ASSERTION_KINDS, AssertionEvaluator

    for kind in ASSERTION_KINDS:
        case = EvalCase(
            name="c",
            task="t",
            assertions=[AssertionSpec(kind=kind, value="x")],
        )
        # Must not raise; pass/fail is irrelevant here.
        AssertionEvaluator().evaluate(case, RunResult(output="x"))
```

Check the imports already at the top of that file. If `AssertionSpec`, `EvalCase`, or `RunResult` is not imported there, add:

```python
from skill_eval.models import AssertionSpec, EvalCase, RunResult
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_assertion_evaluator.py -k assertion_kinds -v
```

Expected: FAIL with `ImportError: cannot import name 'ASSERTION_KINDS'`.

- [ ] **Step 3: Add the tuple and drive dispatch from it**

In `src/skill_eval/evaluators/assertion.py`, replace the `_check` function (currently lines 18–30) with:

```python
# The single source of truth for supported assertion kinds. `_check` dispatches
# from this mapping and tests/test_docs.py enumerates it, so a kind cannot be
# added in one place and forgotten in the other.
_CHECKS: dict[str, Callable[[str, str], bool]] = {
    "contains": lambda value, output: value in output,
    "not_contains": lambda value, output: value not in output,
    "regex": lambda value, output: re.search(value, output) is not None,
    "equals": lambda value, output: output.strip() == value,
}

ASSERTION_KINDS: tuple[str, ...] = tuple(_CHECKS)


def _check(spec: AssertionSpec, output: str) -> bool:
    try:
        check = _CHECKS[spec.kind]
    except KeyError:
        raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}") from None
    return check(spec.value, output)
```

Add to the imports at the top of the file:

```python
from collections.abc import Callable
```

**Important:** the original `regex` branch wraps `re.error` into `InvalidAssertionValue`. Read lines 23–27 of the current file before editing and preserve that behavior exactly — if the original reads

```python
    if spec.kind == "regex":
        try:
            return re.search(spec.value, output) is not None
        except re.error as exc:
            raise InvalidAssertionValue(...) from exc
```

then keep the `try/except re.error` in the new `_check` wrapper around the `check(...)` call rather than inside the lambda, so the error message text is unchanged:

```python
def _check(spec: AssertionSpec, output: str) -> bool:
    try:
        check = _CHECKS[spec.kind]
    except KeyError:
        raise UnknownAssertionKind(f"unknown assertion kind: {spec.kind!r}") from None
    try:
        return check(spec.value, output)
    except re.error as exc:
        raise InvalidAssertionValue(f"invalid regex {spec.value!r}: {exc}") from exc
```

Match the exact `InvalidAssertionValue` message string that is already in the file — do not invent a new one, or the existing test asserting it will break.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest -q
```

Expected: all tests pass, including the pre-existing assertion-evaluator tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check .
git add src/skill_eval/evaluators/assertion.py tests/test_assertion_evaluator.py
git commit -m "refactor: dispatch assertion kinds from an introspectable table"
```

---

### Task 2: MkDocs toolchain and site skeleton

Stand up a site that builds locally with two pages. Later tasks add pages and extend the nav.

**Files:**
- Create: `mkdocs.yml`, `docs/index.md`, `docs/getting-started.md`
- Modify: `pyproject.toml`, `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `uv run mkdocs build --strict`; a `nav:` block in `mkdocs.yml` that Tasks 3 and 4 extend; `docs/` established as `docs_dir` with `superpowers/` excluded.

- [ ] **Step 1: Add the docs dependency group**

In `pyproject.toml`, inside the existing `[dependency-groups]` table, add a `docs` group after the `dev` group:

```toml
docs = [
    "mkdocs>=1.6",
    "mkdocs-material>=9.5",
]
```

- [ ] **Step 2: Install it**

```bash
uv sync --group docs
```

Expected: `mkdocs` and `mkdocs-material` resolve and install.

- [ ] **Step 3: Write `mkdocs.yml`**

```yaml
site_name: skill-eval
site_description: Run evaluations on Agent Skills (SKILL.md) — in CI/CD or on demand.
site_url: https://emadmokhtar.github.io/skill-evaluator/
repo_url: https://github.com/EmadMokhtar/skill-evaluator
repo_name: EmadMokhtar/skill-evaluator
edit_uri: edit/main/docs/

# docs/superpowers/ is a historical record of design specs and plans. It lives
# in the repo but is not part of the published site.
exclude_docs: |
  superpowers/

theme:
  name: material
  features:
    - navigation.sections
    - navigation.top
    - content.code.copy
    - search.highlight
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      toggle:
        icon: material/weather-night
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      toggle:
        icon: material/weather-sunny
        name: Switch to light mode

markdown_extensions:
  - admonition
  - tables
  - toc:
      permalink: true
  - pymdownx.superfences
  # base_path reaches the repo root so docs/architecture.md can include the
  # root-level ARCHITECTURE.md instead of duplicating it.
  - pymdownx.snippets:
      base_path: ["."]
      check_paths: true

nav:
  - Home: index.md
  - Getting started: getting-started.md
```

!!! note
    Do not add `pymdownx.emoji` or mermaid `custom_fences`. Both require
    `!!python/name:` YAML tags, which would break the plain-YAML nav parsing in
    Task 5.

- [ ] **Step 4: Write `docs/index.md`**

```markdown
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
    budgets. See the [roadmap](roadmap.md).

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
```

!!! warning "Forward links break `--strict`"
    MkDocs escalates an unresolved relative link to an error under `--strict`. Do
    **not** add rows for pages that do not exist yet — Task 3 Step 3 and Task 4
    Step 5 extend this table as their pages land.

- [ ] **Step 5: Write `docs/getting-started.md`**

Move the quickstart content from `README.md` lines 19–70 (the `## Quickstart` section) verbatim into this file, under an H1. Demote the existing `##` heading levels by one where needed so the page has exactly one H1. The page must read:

```markdown
# Getting started

A skill is a directory containing `SKILL.md`. Its eval cases live beside it:

...
```

followed by the moved directory-tree block, the `greeting.eval.yaml` YAML block (including its explanatory comments — keep them, they explain why the regex is deliberately loose), the `uv run skill-eval list ./examples` block and its output, and the paragraph explaining that `list` is free and needs no API key.

That paragraph contains a README anchor link, `[Running against a real agent](#running-against-a-real-agent)`. The page it should point at does not exist until Task 3, and an unresolved link fails `--strict`. **Delete the link markup for now**, leaving the plain words "running against a real agent"; Task 3 Step 3 restores it as a page link. Add no other cross-references in this task.

- [ ] **Step 6: Ignore the build output**

Append to `.gitignore`:

```
# mkdocs build output
site/
```

- [ ] **Step 7: Build the site strictly**

```bash
uv run mkdocs build --strict
```

Expected: `INFO - Documentation built in ...`, exit 0, **no warnings**. A message like
`Doc file 'index.md' contains a link 'eval-files.md', but the target is not found` means a
forward link slipped into Step 4 or Step 5 — remove it rather than weakening `--strict`.

- [ ] **Step 8: Preview it once by eye**

```bash
uv run mkdocs serve
```

Open `http://127.0.0.1:8000`, confirm the theme renders, the dark-mode toggle works,
and `docs/superpowers/` does **not** appear in the nav or search. Stop the server.

- [ ] **Step 9: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add mkdocs.yml docs/index.md docs/getting-started.md pyproject.toml uv.lock .gitignore
git commit -m "docs: add mkdocs-material site skeleton"
```

---

### Task 3: Move the reference documentation out of README

Six reference pages, all moved — not copied — from `README.md`. After this task `README.md` is a landing page.

**Files:**
- Create: `docs/eval-files.md`, `docs/cli.md`, `docs/configuration.md`, `docs/runners.md`, `docs/gating.md`, `docs/roadmap.md`
- Modify: `README.md`, `mkdocs.yml` (nav)

**Interfaces:**
- Consumes: `mkdocs.yml` nav from Task 2.
- Produces: the six pages Task 5's tests assert against — `docs/cli.md` (every flag), `docs/configuration.md` (every `Config` field), `docs/eval-files.md` (every `EvalCase` field and every entry of `ASSERTION_KINDS`).

- [ ] **Step 1: Create the six pages by moving README sections**

Each page gets exactly one H1 and the moved content with heading levels demoted by one. Source line ranges refer to `README.md` as it stands before this task.

| New page | H1 | Moved from `README.md` |
| --- | --- | --- |
| `docs/eval-files.md` | `# Eval files` | lines 71–107 (`## Eval files`, `### Assertion kinds`, `### Where eval files are found`) |
| `docs/cli.md` | `# CLI` | lines 109–117 (`## CLI`) |
| `docs/runners.md` | `# Runners` | lines 119–195 (`## Running against a real agent` and both `###` subsections) |
| `docs/configuration.md` | `# Configuration` | lines 197–236 (`## Configuration`) |
| `docs/gating.md` | `# Gating and exit codes` | lines 238–285 (`## Gating and exit codes` and `## JSON report`, the latter demoted to `##`) |
| `docs/roadmap.md` | `# Roadmap` | lines 305–317 (`## Roadmap`) |

Two README sections are intentionally **not** moved. Lines 287–303 (`## Architecture`) are
superseded by `ARCHITECTURE.md` in Task 4 — drop them. Lines 319–366 (`## Contributing`)
become `docs/contributing.md` in Task 4, which recovers them from git; leave them alone here.

Rewrite every cross-reference that was a README anchor into a page link:

- in `docs/eval-files.md`: `[Declaring tools and scoring the trajectory](#declaring-tools-and-scoring-the-trajectory)` → `[Declaring tools and scoring the trajectory](runners.md#declaring-tools-and-scoring-the-trajectory)`
- in `docs/runners.md`: keep `### Declaring tools and scoring the trajectory` and `### Budget limits and pricing` as `##` headings so that anchor resolves.

- [ ] **Step 2: Complete the CLI page**

`README.md`'s CLI section is only a usage block. `docs/cli.md` is now the reference Task 5 tests against, so it must name **every** command and **every** long flag. Write it as:

```markdown
# CLI

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <name>] [--tag <tag>]
                      [--min-pass-rate <float>] [--json-output <path>] [--config <file>]
skill-eval list <path> [--evals <path>]
skill-eval --version
```

`<path>` is a skill directory or a directory of skill directories. Discovery is
recursive.

## `run`

Discover skills, run their eval cases, score them, and gate on the results.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |
| `--runner <name>` | `fake` | `fake` or `pydantic-ai` — see [Runners](runners.md) |
| `--model <name>` | `openai:gpt-4o-mini` | Model id, passed to runners that use one |
| `--tag <tag>` | none | Only run cases carrying this tag |
| `--min-pass-rate <float>` | `1.0` | Required overall pass rate, `0.0`–`1.0` |
| `--json-output <path>` | none | Write a machine-readable report here |
| `--config <file>` | upward discovery | Path to `skill-eval.toml` |

Each flag overrides the corresponding key in [configuration](configuration.md).
Exit codes are documented in [Gating](gating.md).

## `list`

Show the skills that would be evaluated and how many cases each has. Discovers and
validates every eval file without calling a runner — free, and no API key required.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	2 case(s)	examples/order-support
```

## `--version`

Print the installed version and exit.
```

- [ ] **Step 3: Extend the nav and restore the deferred links**

Replace the `nav:` block in `mkdocs.yml` with:

```yaml
nav:
  - Home: index.md
  - Getting started: getting-started.md
  - Reference:
      - Eval files: eval-files.md
      - CLI: cli.md
      - Configuration: configuration.md
      - Runners: runners.md
      - Gating and exit codes: gating.md
  - Roadmap: roadmap.md
```

Now that the target pages exist, add the deferred links from Task 2. In `docs/index.md`,
extend the "Where to go next" table to:

```markdown
| If you want to | Read |
| --- | --- |
| Write your first eval and run it | [Getting started](getting-started.md) |
| Look up an eval YAML field or assertion kind | [Eval files](eval-files.md) |
| Look up a command or flag | [CLI](cli.md) |
| Configure defaults for a repo | [Configuration](configuration.md) |
| Evaluate against a real agent, with tools and budgets | [Runners](runners.md) |
| Understand exit codes and CI behavior | [Gating](gating.md) |
```

(The `Architecture` and `Contributing` rows are added in Task 4 Step 5.) Delete the
`!!! warning "Forward links break --strict"` admonition — it was scaffolding for Task 2.

In `docs/getting-started.md`, restore the link Task 2 Step 5 stripped: the plain words
"running against a real agent" become `[running against a real agent](runners.md)`. Then
append to the end of the page:

```markdown
Next: the full [eval file reference](eval-files.md), or
[running against a real agent](runners.md).
```

- [ ] **Step 4: Reduce `README.md` to a landing page**

Replace the entire file with:

```markdown
# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

**📖 Full documentation: <https://emadmokhtar.github.io/skill-evaluator/>**

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under test
is vendored here, so any skill repo can adopt `skill-eval` without embedding it.

> **Status:** M2. The full pipeline — discovery, scoring, reporting, gating — runs offline
> against `FakeRunner` (the default, scripted, free) and against real agents through
> `pydantic-ai` (provider-flexible), scoring output text, tool-use trajectories, and
> efficiency budgets.

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
```

Point the CLI at a single skill directory or at a parent directory of many — discovery is
recursive:

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	2 case(s)	examples/order-support
```

`list` discovers skills and validates every eval file without calling a runner — free, and
no API key required.

## Documentation

| Topic | Page |
| --- | --- |
| First eval, end to end | [Getting started](https://emadmokhtar.github.io/skill-evaluator/getting-started/) |
| Eval YAML reference | [Eval files](https://emadmokhtar.github.io/skill-evaluator/eval-files/) |
| Commands and flags | [CLI](https://emadmokhtar.github.io/skill-evaluator/cli/) |
| `skill-eval.toml` | [Configuration](https://emadmokhtar.github.io/skill-evaluator/configuration/) |
| Real agents, tools, budgets | [Runners](https://emadmokhtar.github.io/skill-evaluator/runners/) |
| Exit codes and CI | [Gating](https://emadmokhtar.github.io/skill-evaluator/gating/) |
| How it is built | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Contributing | [Contributing](https://emadmokhtar.github.io/skill-evaluator/contributing/) |

## License

MIT — see [LICENSE](LICENSE).
```

!!! note
    The `ARCHITECTURE.md` row points at a file Task 4 creates, and the `Contributing`
    row at a site URL. Neither breaks anything now: `README.md` is not part of the
    site, so `mkdocs build --strict` never reads it, and Task 5's dead-link test —
    which *does* read it — runs after Task 4.

- [ ] **Step 5: Build strictly**

```bash
uv run mkdocs build --strict
```

Expected: exit 0, no warnings.

- [ ] **Step 6: Verify nothing was lost**

```bash
git diff --stat README.md
```

Expected: a large deletion. Then confirm each moved section landed:

```bash
grep -l "not_contains" docs/eval-files.md && grep -l "per_skill_min" docs/configuration.md && grep -l "max_cost_usd" docs/runners.md && grep -l "Gate FAILED" docs/gating.md
```

Expected: all four filenames printed.

- [ ] **Step 7: Commit**

```bash
git add README.md mkdocs.yml docs/
git commit -m "docs: move reference documentation from README into the site"
```

---

### Task 4: `ARCHITECTURE.md`, the architecture page, and contributing

**Files:**
- Create: `ARCHITECTURE.md`, `docs/architecture.md`, `docs/contributing.md`
- Modify: `mkdocs.yml` (nav), `docs/index.md` (restore table rows), `CLAUDE.md`

**Interfaces:**
- Consumes: `pymdownx.snippets` with `base_path: ["."]` from Task 2.
- Produces: `ARCHITECTURE.md` at the repo root, included by `docs/architecture.md`; `docs/contributing.md`, which Task 7 extends with the docs policy.

- [ ] **Step 1: Write `ARCHITECTURE.md`**

Before writing, read these files so the module map and invariants match reality:
`src/skill_eval/models.py`, `orchestrator.py`, `gating.py`, `runners/base.py`,
`evaluators/base.py`, and `CLAUDE.md`.

```markdown
# Architecture

How `skill-eval` is built, and why it is built this way. For how to *use* it, see the
[documentation site](https://emadmokhtar.github.io/skill-evaluator/).

## Scope and non-goals

`skill-eval` runs evaluations on Anthropic-style Agent Skills — directories containing a
`SKILL.md` file. It is a CLI and a library, designed to run as a CI gate where the exit
code is the contract, or on demand during development.

Skills under test and their eval cases are **inputs**. Nothing about a skill under test is
vendored here. That is the central constraint: any skill repository can adopt `skill-eval`
without embedding it, and `skill-eval` can be released independently of anything it evaluates.

Non-goals: authoring skills, running skills in production, and hosting a results dashboard.

## The two protocols

The design rests on two protocols. Everything else is plumbing around them.

```python
class Runner(Protocol):
    name: str
    def run(self, skill: Skill, case: EvalCase) -> RunResult: ...
```

```python
class Evaluator(Protocol):
    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore: ...
```

`Runner` is the seam every agent framework plugs into. `Evaluator` is the seam every
scoring strategy plugs into. Adding a framework or a scoring rule means adding one
implementation of one protocol — no change to the orchestrator, the reporters, or the gate.

## Module map

| Module | Responsibility |
| --- | --- |
| `models.py` | Every Pydantic model in the project. No other module defines a data shape. |
| `cli.py` | Typer entry point. Wires config → loaders → runner → orchestrator → reporters → gate, and owns the exit-code contract. |
| `orchestrator.py` | Builds and runs the skill × case × runner matrix, applying every evaluator to each result. |
| `gating.py` | Turns a `RunReport` into a pass/fail decision plus reasons and an exit code. |
| `config.py` | Loads `skill-eval.toml` by explicit path or upward discovery. Never reads secrets. |
| `yaml_loading.py` | A YAML loader that does not treat bare `yes`/`no`/`on`/`off` as booleans. |
| `skills/loader.py` | Walks a path for `SKILL.md` files and parses them into `Skill` models. |
| `cases/loader.py` | Finds and parses eval YAML for a skill into `EvalCase` models. |
| `runners/base.py` | The `Runner` protocol. |
| `runners/fake.py` | A deterministic, offline, scripted runner. The default, and the backbone of the zero-cost test tier. |
| `runners/pydantic_ai.py` | The PydanticAI adapter. **The only module that imports an agent framework.** |
| `runners/tools.py` | Builds framework-neutral `MockTool`s (name + JSON schema + callable) from a case's `tools:` block. |
| `runners/preflight.py` | Verifies the provider API key is present before any spend. |
| `runners/pricing.py` | Turns provider usage into USD. Degrades rather than raising. |
| `evaluators/base.py` | The `Evaluator` protocol. |
| `evaluators/assertion.py` | Rule-based scoring of the final output text. |
| `evaluators/trajectory.py` | Scoring which tools were called, in what order, and how many times. |
| `evaluators/budget.py` | Scoring efficiency: tokens, cost, latency. |
| `reporters/console.py` | Human-readable run summary. |
| `reporters/json_reporter.py` | Machine-readable run report. |

## Data flow

```
path
  └─ skills/loader (walk for SKILL.md) ──────────────► [Skill]
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml) ──► [EvalCase]

matrix: for each (skill × case × runner)
    Runner.run ──► RunResult ──► each Evaluator ──► [EvalScore]
                                                       └─► CaseOutcome

aggregate ──► RunReport ──► reporters/  ──► console + JSON
                        └─► gating      ──► exit code
```

## Core data models

All live in `models.py`.

| Model | Carries |
| --- | --- |
| `Skill` | name, description, instructions, path |
| `EvalCase` | name, task, `tools`, `assertions`, `trajectory`, `budget`, `tags` |
| `RunResult` | output, tool calls, transcript, token split, latency, cost, model, `error` |
| `EvalScore` | one evaluator's `passed` / `score` / `detail` |
| `CaseOutcome` | one (skill, case, runner) triple: status plus its scores and result |
| `RunReport` | every outcome, plus skipped and tag-filtered skills |

Two fields are **derived, not stored**: `RunResult.tokens` (the input/output split summed)
and `RunResult.errored` (`error is not None`). Aggregates on `RunReport` — `total`,
`passed`, `failed`, `errored`, `pass_rate` — are likewise computed from `outcomes`.

## Invariants, and why

These are decided behaviors, not accidents. Several were bugs caught in review. Each has a
test asserting it.

**`errored` is not `failed`.** `failed` means the case ran and scored below the bar — an
*eval* signal about the skill. `errored` means the runner itself blew up — an *infra*
signal about the harness. Conflating them makes a broken API key look like a bad skill.
Runners therefore **never raise** for provider failures; they set `RunResult.error`.
Errored cases fail the gate by default so CI never goes green on a run that did not
actually happen.

**A run executing zero cases fails the gate.** "Nothing ran" is a broken run, not a pass —
otherwise a mistyped path reports success forever. `gating.evaluate_gate` distinguishes the
causes: no skills found, all skills skipped for having no cases, or every case filtered out
by `--tag`.

**Authoring errors abort the run; they never score as failures.** An unknown assertion
`kind`, a malformed regex, an undeclared tool name in a `trajectory` block, or an unknown
YAML key is a mistake in the user's files — it says nothing about the skill. Scoring it as
a failure would be a lie about the skill's quality. `orchestrator.run_evals` lets these
propagate; `cli.py` catches them via `_AUTHORING_ERRORS` and exits 2.

**Exit codes are the CI contract.** Gate passed `0`, gate failed `1`, user or authoring
error `2`. In `cli.py`, a JSON-write failure escalates to 2 only when the gate itself
passed — a write problem must never mask an already-failing gate.

**`extra="forbid"` on every user-authored model.** `EvalCase`, `AssertionSpec`, `ToolSpec`,
`TrajectorySpec`, `BudgetSpec`, `Config`. Without it, a typo like `assertion:` yields a
case that passes vacuously — the worst possible failure mode for an eval tool. It is also
on `RunResult`, where it makes writing the derived `tokens` field a loud error rather than
a total that silently disagrees with the split it was priced from.

**All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error
(`SkillParseError`, `CaseParseError`, `ConfigError`) naming the file and the field.

**YAML goes through `yaml_loading.safe_load`.** PyYAML's `SafeLoader` implements YAML 1.1,
which turns bare `yes`/`no`/`on`/`off` into booleans. An assertion `value: yes` is meant as
the string.

**Secrets come from environment variables only** — never from `skill-eval.toml`. A config
file is committed; a key must not be.

**No agent-framework type appears outside `runners/pydantic_ai.py`.** `runners/tools.py`
builds framework-neutral mock tools and the adapter wraps them. A test asserts the string
`pydantic_ai` does not appear in `tools.py`. This is what keeps the `Runner` seam real
rather than nominal.

**Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` plus a
`cost_note`. Pricing is reporting metadata; it must never be why a run errors. In
`BudgetEvaluator`, an unpriceable cost limit is *skipped* — not counted as passed — so a
case whose only budget check is an unpriced cost limit fails, because nothing was verified.

**Mock tools accept any arguments.** A model hallucinating an argument is an eval signal
about the skill; raising would surface it as an infra error instead.

**Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act.
A missing cassette skips; a mismatched request fails rather than reaching the network.

**`skill_eval` (underscore) never appears in user-facing output.** The user-facing name is
`skill-eval` everywhere: command, config file, distribution.

**`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.

## Extension points

**Adding a runner.** Implement `Runner` in a new module under `runners/`, register it in
`cli._RUNNERS`, and put every framework import inside that module. Set
`needs_api_key = True` if it spends money — `cli.py` then runs the preflight key check
before constructing it. Never raise for a provider failure; return a `RunResult` with
`error` set.

**Adding an evaluator.** Implement `Evaluator` in a new module under `evaluators/` and add
it to the evaluator list in `orchestrator.py`. Return `passed=False` for a real failure;
never treat a check you could not perform as passed.

**Adding a reporter.** Add a module under `reporters/` taking `(report, gate)` and
returning a string. `cli.py` decides when to call it.

**Adding an assertion kind.** Add an entry to `_CHECKS` in `evaluators/assertion.py` and
document it in `docs/eval-files.md`. `tests/test_docs.py` fails until you do both.

## Testing tiers

| Tier | Marker | Cost | Selected by default |
| --- | --- | --- | --- |
| Pipeline | none | free, offline, deterministic (`FakeRunner`) | yes |
| Cassette | `cassette` | free — replays recorded provider traffic | yes |
| Live | `integration` | real API spend, needs a key | no |

`pytest` runs with `--block-network`, so an accidental network call in the default tiers
fails loudly rather than silently costing money. Development is test-driven: the failing
test comes first.
```

- [ ] **Step 2: Write `docs/architecture.md`**

```markdown
--8<-- "ARCHITECTURE.md"
```

That single line is the whole file. `pymdownx.snippets` with `base_path: ["."]` resolves
it against the repository root, so the root file stays the only copy.

- [ ] **Step 3: Write `docs/contributing.md`**

Task 3 already replaced `README.md`, so recover the old content from git rather than the
working tree:

```bash
git show HEAD~1:README.md | sed -n '319,366p'
```

That prints the `## Contributing`, `### Development`, and `### Conventional Commits are
required` sections. Move them into this page with `# Contributing` as the H1 and the `###`
headings demoted to `##`.

One cross-reference in that text is a README anchor —
`[Running against a real agent](#running-against-a-real-agent)`. Rewrite it as
`[Running against a real agent](runners.md)`.

Then add a docs section to the development command list:

```markdown
## Documentation

```bash
uv sync --group docs
uv run mkdocs serve            # live preview at http://127.0.0.1:8000
uv run mkdocs build --strict   # as CI runs it
uv run pytest tests/test_docs.py
```
```

(The full docs policy is added to this page in Task 7.)

- [ ] **Step 4: Extend the nav**

In `mkdocs.yml`, add to the end of `nav:`, after the `Roadmap` entry:

```yaml
  - Architecture: architecture.md
  - Contributing: contributing.md
```

- [ ] **Step 5: Add the last two index table rows**

Append to the "Where to go next" table in `docs/index.md`:

```markdown
| Understand how the tool is built | [Architecture](architecture.md) |
| Work on skill-eval itself | [Contributing](contributing.md) |
```

- [ ] **Step 6: Point `CLAUDE.md` at the new file**

In `CLAUDE.md`, in the `## Invariants that are easy to break` section, insert immediately
under the heading's introductory paragraph:

```markdown
The full rationale for each of these — plus the module map and extension points — is in
[`ARCHITECTURE.md`](ARCHITECTURE.md). Keep the two in sync: this list is the condensed
form, that file is the explanation.
```

- [ ] **Step 7: Build strictly**

```bash
uv run mkdocs build --strict
```

Expected: exit 0. Then confirm the snippet actually inlined rather than rendering as
literal text:

```bash
grep -c "The two protocols" site/architecture/index.html
```

Expected: `1` or more. A `0` means `base_path` is wrong or `check_paths` silently passed.

- [ ] **Step 8: Commit**

```bash
git add ARCHITECTURE.md docs/ mkdocs.yml CLAUDE.md
git commit -m "docs: add ARCHITECTURE.md and contributing page"
```

---

### Task 5: Executable docs-drift tests

**Files:**
- Create: `tests/test_docs.py`
- Test: itself

**Interfaces:**
- Consumes: `ASSERTION_KINDS` (Task 1); the pages from Tasks 3 and 4; `mkdocs.yml` nav.
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the tests**

```python
"""Assert the documentation has not drifted from the code.

Every check here targets real drift -- a flag, field, or assertion kind that
exists in code but appears nowhere in the docs -- never prose style. Stale
wording is not detectable here; scripts/check_docs_updated.py is the (blunter)
backstop for that.

The autouse `isolate_cwd` fixture in conftest.py chdirs every test into a fresh
tmp_path, so everything below anchors on REPO_ROOT rather than Path.cwd().
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.main import get_command

from skill_eval.cli import app
from skill_eval.config import Config
from skill_eval.evaluators.assertion import ASSERTION_KINDS
from skill_eval.models import EvalCase
from skill_eval.yaml_loading import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"

# docs/superpowers/ is a historical record of specs and plans, excluded from the
# site (see mkdocs.yml) and from every check here.
EXCLUDED_DIR = "superpowers"

# Typer/Click add these to every command; they are not project surface area.
IGNORED_FLAGS = {"--help", "--install-completion", "--show-completion"}


def _page(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _site_pages() -> set[str]:
    """Every published Markdown page, as a docs/-relative posix path."""
    return {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*.md")
        if EXCLUDED_DIR not in path.relative_to(DOCS).parts
    }


def _nav_pages() -> set[str]:
    """Every page reachable from the mkdocs.yml nav, flattened."""
    config = safe_load(MKDOCS_YML.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(config["nav"])
    return found


def test_every_cli_command_is_documented():
    text = _page("cli.md")
    for name in get_command(app).commands:
        assert f"`{name}`" in text, f"command {name!r} is not documented in docs/cli.md"


def test_every_cli_option_is_documented():
    text = _page("cli.md")
    command = get_command(app)
    # The group's own options (e.g. --version) plus every subcommand's.
    all_params = list(command.params)
    for subcommand in command.commands.values():
        all_params.extend(subcommand.params)

    for param in all_params:
        if param.param_type_name != "option":
            continue
        for flag in param.opts:
            if flag in IGNORED_FLAGS or not flag.startswith("--"):
                continue
            assert flag in text, f"flag {flag} is not documented in docs/cli.md"


def test_every_config_field_is_documented():
    text = _page("configuration.md")
    for field in Config.model_fields:
        assert f"`{field}`" in text, f"config key {field!r} is not in docs/configuration.md"


def test_every_eval_case_field_is_documented():
    text = _page("eval-files.md")
    for field in EvalCase.model_fields:
        assert f"`{field}`" in text, f"case field {field!r} is not in docs/eval-files.md"


def test_every_assertion_kind_is_documented():
    text = _page("eval-files.md")
    for kind in ASSERTION_KINDS:
        assert f"`{kind}`" in text, f"assertion kind {kind!r} is not in docs/eval-files.md"


def test_every_page_is_reachable_from_the_nav():
    orphans = _site_pages() - _nav_pages()
    assert not orphans, f"pages not in the mkdocs.yml nav: {sorted(orphans)}"


def test_the_nav_has_no_missing_pages():
    missing = _nav_pages() - _site_pages()
    assert not missing, f"nav entries with no file on disk: {sorted(missing)}"


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _markdown_files() -> list[Path]:
    files = [
        path
        for path in DOCS.rglob("*.md")
        if EXCLUDED_DIR not in path.relative_to(DOCS).parts
    ]
    files.append(REPO_ROOT / "README.md")
    files.append(REPO_ROOT / "ARCHITECTURE.md")
    return files


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_relative_links_resolve(path: Path):
    for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        # Strip any anchor; only the file part is checked.
        relative = target.split("#", 1)[0]
        if not relative:
            continue
        resolved = (path.parent / relative).resolve()
        assert resolved.exists(), f"{path.name}: dead link to {target!r}"
```

- [ ] **Step 2: Run them**

```bash
uv run pytest tests/test_docs.py -v
```

Expected: all pass. Every failure is a real gap — fix the **documentation**, not the test.
Common first failures and their fixes:

| Failure | Fix |
| --- | --- |
| `flag --json-output is not documented` | add the row to the `run` table in `docs/cli.md` |
| `case field 'trajectory' is not in docs/eval-files.md` | add the row to the case-fields table |
| `pages not in the mkdocs.yml nav` | add the page to `nav:` |
| `dead link to 'ARCHITECTURE.md'` | check the link is relative to the file that contains it |

- [ ] **Step 3: Prove the tests actually bite**

Temporarily add a bogus flag to `cli.py`:

```python
    bogus: Annotated[bool, typer.Option("--bogus", help="temporary")] = False,
```

as a parameter of `run`, then:

```bash
uv run pytest tests/test_docs.py::test_every_cli_option_is_documented -v
```

Expected: FAIL with `flag --bogus is not documented in docs/cli.md`. **Revert the `cli.py`
change.** Re-run and confirm it passes again. Do not commit the bogus flag.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest -q
```

Expected: all pass, offline.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add tests/test_docs.py
git commit -m "test: assert documentation matches the CLI, config and models"
```

---

### Task 6: The docs-freshness script

**Files:**
- Create: `scripts/check_docs_updated.py`, `tests/test_check_docs_updated.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/check_docs_updated.py` with `SOURCE_PREFIX: str`, `DOCS_PATHS: tuple[str, ...]`, `source_changes(paths: Iterable[str]) -> list[str]`, `touches_docs(paths: Iterable[str]) -> bool`, `changed_files(base: str, head: str) -> list[str]`, and `main(argv: Sequence[str] | None = None) -> int`. Task 7 invokes it from CI as `python3 scripts/check_docs_updated.py <base> <head>`.

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for scripts/check_docs_updated.py.

The script is not an importable package, so it is loaded from its path -- the
same shape scripts/check_commits.py would use. Only the pure functions are
tested; the git call is left to CI.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_docs_updated.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_docs_updated", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def test_source_changes_finds_package_files():
    paths = ["src/skill_eval/gating.py", "tests/test_gating.py", "README.md"]
    assert check.source_changes(paths) == ["src/skill_eval/gating.py"]


def test_source_changes_ignores_everything_outside_the_package():
    paths = ["tests/test_gating.py", "pyproject.toml", "examples/greeting/SKILL.md"]
    assert check.source_changes(paths) == []


def test_touches_docs_accepts_any_documented_surface():
    for path in ["docs/cli.md", "README.md", "ARCHITECTURE.md", "mkdocs.yml"]:
        assert check.touches_docs([path]), path


def test_touches_docs_rejects_the_historical_record():
    """docs/superpowers/ is a specs archive, not user documentation.

    A PR that only adds a design spec has not documented its code change.
    """
    assert not check.touches_docs(["docs/superpowers/specs/2026-08-02-x.md"])


def test_touches_docs_rejects_unrelated_paths():
    assert not check.touches_docs(["src/skill_eval/cli.py", "tests/test_cli.py"])


def test_main_passes_when_no_source_changed(monkeypatch, capsys):
    monkeypatch.setattr(check, "changed_files", lambda base, head: ["tests/test_cli.py"])
    assert check.main(["BASE", "HEAD"]) == 0


def test_main_passes_when_source_and_docs_both_changed(monkeypatch):
    monkeypatch.setattr(
        check, "changed_files", lambda base, head: ["src/skill_eval/cli.py", "docs/cli.md"]
    )
    assert check.main(["BASE", "HEAD"]) == 0


def test_main_fails_when_source_changed_without_docs(monkeypatch, capsys):
    monkeypatch.setattr(
        check, "changed_files", lambda base, head: ["src/skill_eval/cli.py"]
    )
    assert check.main(["BASE", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "src/skill_eval/cli.py" in out
    assert "no-docs-needed" in out
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_check_docs_updated.py -v
```

Expected: FAIL — `FileNotFoundError` or `spec is None`, because the script does not exist.

- [ ] **Step 3: Write the script**

```python
#!/usr/bin/env python3
"""Fail a PR that changes skill-eval source without touching documentation.

This is deliberately a heuristic, not a proof. It cannot tell a stale sentence
from a fresh one; it only notices that the package changed and no documented
surface did. Pure refactors, dependency bumps and internal-only changes will
trip it -- that is the accepted cost. The escape hatch is the `no-docs-needed`
label on the PR, checked in the workflow rather than here.

The precise checks (a flag, config key, or assertion kind that exists in code
but nowhere in the docs) live in tests/test_docs.py.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Sequence

SOURCE_PREFIX = "src/skill_eval/"

# What counts as having documented the change. docs/superpowers/ is deliberately
# absent: it is a historical archive of specs and plans, and adding one is not
# the same as documenting a code change for users.
DOCS_PATHS = ("docs/", "README.md", "ARCHITECTURE.md", "mkdocs.yml")
EXCLUDED_DOCS_PREFIX = "docs/superpowers/"

LABEL = "no-docs-needed"


def changed_files(base: str, head: str) -> list[str]:
    """Paths changed between `base` and `head`, as git reports them."""
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def source_changes(paths: Iterable[str]) -> list[str]:
    """The changed paths that live inside the package."""
    return [path for path in paths if path.startswith(SOURCE_PREFIX)]


def touches_docs(paths: Iterable[str]) -> bool:
    """True when any changed path is a documented surface."""
    return any(
        path.startswith(DOCS_PATHS) and not path.startswith(EXCLUDED_DOCS_PREFIX)
        for path in paths
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: check_docs_updated.py <base-ref> <head-ref>")
        return 2

    paths = changed_files(args[0], args[1])
    sources = source_changes(paths)
    if not sources or touches_docs(paths):
        return 0

    listed = "\n".join(f"  - {path}" for path in sources)
    print(
        "This PR changes skill-eval source but no documentation:\n"
        f"{listed}\n\n"
        "Update whichever of these the change affects:\n"
        "  - docs/            user-facing documentation\n"
        "  - ARCHITECTURE.md  design, invariants, extension points\n"
        "  - README.md        the landing page\n"
        "  - mkdocs.yml       navigation\n\n"
        f"If the change genuinely needs no documentation, add the `{LABEL}` "
        "label to the PR."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_check_docs_updated.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Smoke-test it against real git**

```bash
python3 scripts/check_docs_updated.py main HEAD; echo "exit=$?"
```

Expected: `exit=0` — this branch has changed documentation. The output should be empty.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check .
git add scripts/check_docs_updated.py tests/test_check_docs_updated.py
git commit -m "ci: add a check that source changes come with documentation"
```

---

### Task 7: Wire the checks into CI, the PR template, and CLAUDE.md

**Files:**
- Modify: `.github/workflows/ci.yml`, `CLAUDE.md`, `docs/contributing.md`
- Create: `.github/pull_request_template.md`

**Interfaces:**
- Consumes: `scripts/check_docs_updated.py` (Task 6), the `docs` dependency group (Task 2).
- Produces: two new CI jobs, `docs` and `docs-freshness`.

- [ ] **Step 1: Add the docs-build job**

Append to `.github/workflows/ci.yml`, at the same indentation as the existing `test` job:

```yaml
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --group docs

      - name: Build the docs site
        # --strict turns a dead reference or an unresolvable snippet into a
        # failure rather than a warning nobody reads.
        run: uv run mkdocs build --strict
```

- [ ] **Step 2: Add the docs-freshness job**

Append below it:

```yaml
  docs-freshness:
    # Blunt backstop for stale prose, which tests/test_docs.py cannot see. The
    # `no-docs-needed` label is the escape hatch for refactors and internal-only
    # changes; a label is used rather than a commit trailer because PRs are
    # squash-merged, which rewrites the message.
    if: >-
      github.event_name == 'pull_request' &&
      !contains(github.event.pull_request.labels.*.name, 'no-docs-needed')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          # `git diff base...head` needs both sides of the history.
          fetch-depth: 0

      - name: Check that documentation was updated
        env:
          # Passed via env rather than interpolated into the script body, matching
          # the conventional-commits job.
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        # No dependencies: the script is stdlib-only, so this needs no uv sync.
        run: python3 scripts/check_docs_updated.py "$BASE_SHA" HEAD
```

- [ ] **Step 3: Validate the workflow YAML parses**

```bash
uv run python -c "from skill_eval.yaml_loading import safe_load; from pathlib import Path; d=safe_load(Path('.github/workflows/ci.yml').read_text(encoding='utf-8')); print(sorted(d['jobs']))"
```

Expected: `['conventional-commits', 'docs', 'docs-freshness', 'test']`.

- [ ] **Step 4: Write the PR template**

Create `.github/pull_request_template.md`:

```markdown
## What and why

<!-- What changes, and what problem it solves. The diff shows what; explain why. -->

## Checklist

- [ ] The PR title follows [Conventional Commits](https://www.conventionalcommits.org/) —
      it becomes the commit on `main` when this is squash-merged.
- [ ] Documentation is updated in the same PR: `docs/` for user-facing behavior,
      `ARCHITECTURE.md` for design or invariants, `mkdocs.yml` for a new page.
      (If genuinely not needed, add the `no-docs-needed` label.)
- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run mkdocs build --strict` passes if any documentation changed.
```

- [ ] **Step 5: Add the Documentation section to `CLAUDE.md`**

Insert a new section in `CLAUDE.md` immediately before `## Conventions`:

```markdown
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
uv run mkdocs build --strict
uv run pytest tests/test_docs.py
```

`docs/superpowers/` is a historical archive of specs and plans. It is excluded from the
published site and does **not** count as documenting a change.
```

- [ ] **Step 6: Mirror the policy on the contributing page**

Append to `docs/contributing.md`, after the `## Documentation` command block added in
Task 4:

```markdown
Documentation ships with the change, not as a follow-up. Two CI jobs enforce it: `docs`
builds the site with `--strict`, and `docs-freshness` fails a PR that changes
`src/skill_eval/**` without touching `docs/`, `README.md`, `ARCHITECTURE.md` or
`mkdocs.yml`. When a change genuinely needs no documentation — a pure refactor, a
dependency bump — add the `no-docs-needed` label to the PR.

`tests/test_docs.py` is the precise half of the same idea: it asserts that every command,
flag, config key, `EvalCase` field and assertion kind appears in the docs, that every page
is in the nav, and that no relative link is dead.
```

- [ ] **Step 7: Run everything**

```bash
uv run pytest -q && uv run ruff check . && uv run mkdocs build --strict
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/ci.yml .github/pull_request_template.md CLAUDE.md docs/contributing.md
git commit -m "ci: gate pull requests on the docs building and staying current"
```

---

### Task 8: Publish to GitHub Pages

**Files:**
- Create: `.github/workflows/docs.yml`

**Interfaces:**
- Consumes: the `docs` dependency group and `mkdocs.yml` (Task 2).
- Produces: a deployed site at `https://emadmokhtar.github.io/skill-evaluator/`.

- [ ] **Step 1: Write the workflow**

```yaml
name: Docs

on:
  push:
    branches: [main]
  # Allows a redeploy without a code change.
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

# One deploy at a time, and never cancel one mid-flight -- a cancelled deploy can
# leave Pages serving a half-uploaded artifact.
concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --group docs

      - name: Build the site
        run: uv run mkdocs build --strict

      - uses: actions/configure-pages@v5

      - uses: actions/upload-pages-artifact@v3
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Validate it parses and reproduce the build locally**

```bash
uv run python -c "from skill_eval.yaml_loading import safe_load; from pathlib import Path; d=safe_load(Path('.github/workflows/docs.yml').read_text(encoding='utf-8')); print(sorted(d['jobs']))"
uv sync --group docs && uv run mkdocs build --strict && test -f site/index.html && echo "site built"
```

Expected: `['build', 'deploy']`, then `site built`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci: publish the docs site to github pages"
```

- [ ] **Step 4: Record the manual step**

This workflow fails on its first run until a human sets **Settings → Pages → Source** to
*GitHub Actions*. Note this in the PR description; it is not something the workflow can do
for itself.

---

### Task 9: Copilot review instructions

**Files:**
- Create: `.github/copilot-instructions.md`, `.github/instructions/runners.instructions.md`, `.github/instructions/evaluators.instructions.md`, `.github/instructions/models-loaders.instructions.md`, `.github/instructions/cli-gating.instructions.md`, `.github/instructions/tests.instructions.md`, `.github/instructions/docs.instructions.md`

**Interfaces:**
- Consumes: `ARCHITECTURE.md` (Task 4), which these files reference rather than restate at length.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the repository-wide instructions**

Create `.github/copilot-instructions.md`:

```markdown
# Copilot instructions for skill-eval

`skill-eval` is a standalone CLI and library that runs evaluations on Agent Skills
(`SKILL.md` files). Skills under test and their eval cases are **inputs** — nothing about a
skill under test is vendored here. The tool runs as a CI gate where the exit code is the
contract. Full design: [`ARCHITECTURE.md`](../ARCHITECTURE.md).

## The two seams

- `Runner.run(skill, case) -> RunResult` — where every agent framework plugs in.
- `Evaluator.evaluate(case, result) -> EvalScore` — where every scoring strategy plugs in.

`models.py` holds every Pydantic model; no other module defines a data shape.

## Invariants — flag any change that breaks one

These are decided behaviors with tests asserting them, not accidents.

1. **`errored` is not `failed`.** `failed` = the case ran and scored below the bar (an eval
   signal). `errored` = the runner itself blew up (an infra signal). Runners must **never
   raise** for provider failures — they set `RunResult.error`. Errored cases fail the gate.
2. **A run executing zero cases fails the gate.** "Nothing ran" is a broken run.
3. **Authoring errors abort the run; they never score as failures.** An unknown assertion
   kind, a bad regex, or an unknown YAML key is a mistake in the user's files and says
   nothing about the skill. They propagate out of the orchestrator and `cli.py` exits 2.
4. **Exit codes are the CI contract:** gate passed `0`, gate failed `1`, user or authoring
   error `2`. A JSON-write failure escalates to 2 only when the gate already passed.
5. **`extra="forbid"`** on every user-authored model. Without it a typo like `assertion:`
   yields a case that passes vacuously.
6. **All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error naming the
   file and field.
7. **YAML goes through `skill_eval.yaml_loading.safe_load`**, never `yaml.safe_load`.
8. **Secrets come from environment variables only** — never from `skill-eval.toml`.
9. **No agent-framework type appears outside `runners/pydantic_ai.py`.**
10. **Cost lookup degrades, never raises.** An unpriced model yields `cost_usd = 0.0` and a
    `cost_note`. An unpriceable budget check is *skipped*, not passed.
11. **`skill_eval` (underscore) never appears in user-facing output.** The user-facing name
    is `skill-eval` everywhere.

## Do not suggest

- Importing an agent framework anywhere outside `runners/pydantic_ai.py`.
- Replacing `yaml_loading.safe_load` with `yaml.safe_load`.
- Raising from a runner when a provider call fails.
- Removing `extra="forbid"` to make a model more permissive.
- Making a mock tool reject unexpected arguments — a hallucinated argument is an eval
  signal, and raising would surface it as an infra error.
- Treating a check that could not be performed as passed.

## Conventions

- **Test-driven.** The failing test comes first. The default test tier must stay zero-cost,
  offline and deterministic — `pytest` runs with `--block-network`.
- **Conventional Commits are enforced, not stylistic.** `cz bump` derives the version and
  changelog from history. PRs are squash-merged, so **the PR title becomes the commit on
  `main`** and must be conventional too. Flag a non-conforming PR title in review.
- **Documentation ships with the change.** A change to a flag, config key, `EvalCase` field
  or assertion kind must update the matching page under `docs/`; a change to a protocol or
  an invariant must update `ARCHITECTURE.md`.
- Line length 100, `ruff` for lint and format.
```

- [ ] **Step 2: Write the runners instructions**

Create `.github/instructions/runners.instructions.md`:

```markdown
---
applyTo: "src/skill_eval/runners/**"
---

# Reviewing runner code

This directory is the framework boundary. Everything the core sees is a plain `RunResult`.

- **Never raise for a provider failure.** Timeouts, rate limits, auth errors and malformed
  responses become `RunResult(error=...)`. Raising turns an infra problem into an
  unhandled crash and loses the errored/failed distinction the gate depends on.
- **Only `pydantic_ai.py` may import an agent framework.** `tools.py` builds
  framework-neutral `MockTool`s — a name, a JSON schema and a callable — and the adapter
  wraps them. A test asserts the string `pydantic_ai` does not appear in `tools.py`.
- **Mock tools accept any arguments.** A model hallucinating an argument is an eval signal
  about the skill; rejecting it would surface as an infra error instead.
- **Nothing executes in a mock tool.** It records the call and returns its canned value, so
  the trajectory is genuinely the model's choice and a run has no side effects.
- **Pricing never fails a run.** An unpriced model yields `cost_usd = 0.0` plus a
  `cost_note`. Flag any code path where a pricing lookup can raise.
- **`RunResult.tokens` is derived** from the input/output split. Flag any attempt to set it.
- **`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted
  state.
- **Cassettes are replay-only and secret-free.** Recording is a deliberate, key-bearing act.
  A missing cassette skips; a mismatched request fails rather than reaching the network.
  Flag any credential or account-identifying header that could reach a recorded file.
- A runner that spends money sets `needs_api_key = True` so the preflight check runs before
  any request.
```

- [ ] **Step 3: Write the evaluators instructions**

Create `.github/instructions/evaluators.instructions.md`:

```markdown
---
applyTo: "src/skill_eval/evaluators/**"
---

# Reviewing evaluator code

An evaluator turns a `RunResult` into a pass/fail verdict with a score and human-readable
detail. It scores the skill — it never reports on the harness.

- **A check that could not be performed is not a pass.** `BudgetEvaluator` *skips* an
  unpriceable cost limit rather than passing it, so a case whose only budget check is an
  unpriced cost limit fails — nothing was verified. Flag any "if we can't check it, assume
  it's fine" branch.
- **Authoring errors propagate; they never become a failing score.** An unknown assertion
  kind, a malformed regex, or a tool name in a `trajectory` block that the case never
  declared is a mistake in the user's files. Scoring it as a failure would be a lie about
  the skill. These raise and `cli.py` exits 2.
- **New assertion kinds go in `_CHECKS`** in `assertion.py` and must be documented in
  `docs/eval-files.md`. `tests/test_docs.py` fails until both happen.
- `detail` is read by a human staring at a red CI run. It should name what failed and with
  what value, not just that something did.
- Every assertion in a case must hold for the case to pass. A case with no assertions passes.
```

- [ ] **Step 4: Write the models and loaders instructions**

Create `.github/instructions/models-loaders.instructions.md`:

```markdown
---
applyTo: "src/skill_eval/models.py,src/skill_eval/config.py,src/skill_eval/yaml_loading.py,src/skill_eval/cases/**,src/skill_eval/skills/**"
---

# Reviewing models and loaders

This is where user-authored files become typed objects. Every mistake here is silent.

- **`extra="forbid"` on every user-authored model** — `EvalCase`, `AssertionSpec`,
  `ToolSpec`, `TrajectorySpec`, `BudgetSpec`, `Config`. Without it a typo like `assertion:`
  yields a case that passes vacuously, the worst failure mode an eval tool has. Flag any
  removal or any new user-authored model that omits it.
- **`models.py` holds every data shape.** Other modules import from it; they do not define
  their own.
- **All file IO pins `encoding="utf-8"`** and re-raises `OSError`/`UnicodeDecodeError` as a
  typed parse error (`SkillParseError`, `CaseParseError`, `ConfigError`) naming the file and
  the field. A raw traceback reaching the user is a bug.
- **YAML goes through `yaml_loading.safe_load`.** PyYAML's `SafeLoader` is YAML 1.1 and
  turns bare `yes`/`no`/`on`/`off` into booleans; an assertion `value: yes` is meant as the
  string.
- **Secrets never come from `skill-eval.toml`.** A config file is committed; a key must not
  be. Flag any new config field that would hold a credential.
- **Derived values are properties, not stored fields** — `RunResult.tokens`, `errored`, and
  the `RunReport` aggregates. A stored copy can disagree with its source.
- Skills with no eval files are reported as **skipped**, visibly — never silently dropped.
```

- [ ] **Step 5: Write the CLI and gating instructions**

Create `.github/instructions/cli-gating.instructions.md`:

```markdown
---
applyTo: "src/skill_eval/cli.py,src/skill_eval/gating.py,src/skill_eval/orchestrator.py,src/skill_eval/reporters/**"
---

# Reviewing the CLI, gate, orchestrator and reporters

This is the contract surface. CI depends on the exit code; humans depend on the output.

- **Exit codes are the contract:** gate passed `0`, gate failed `1`, user or authoring error
  `2`. A JSON-write failure escalates to 2 **only when the gate itself passed** — it must
  never mask an already-failing gate. Flag any change that widens or reorders these.
- **A run executing zero cases fails the gate.** "Nothing ran" is a broken run, not a pass —
  otherwise a mistyped path reports success forever. The reason must name the cause: no
  skills found, all skills skipped for having no cases, or every case filtered out by
  `--tag`.
- **Errored cases fail the gate by default**, so CI never goes green on a run that did not
  happen.
- **Authoring errors abort the run.** `orchestrator.run_evals` deliberately lets them
  propagate; `cli.py` catches them via `_AUTHORING_ERRORS` and prints the message without a
  traceback. Flag any `try`/`except` in the orchestrator that would swallow one into a
  failing score.
- **`skill_eval` (underscore) never appears in user-facing output.** The name is
  `skill-eval`: command, config file, distribution, prose.
- A new runner must be registered in `cli._RUNNERS`, and if it spends money it needs the
  preflight key check before construction so a missing key costs nothing and exits 2.
- Gate reasons are read by whoever is staring at a red pipeline. Each should say what failed
  and against which threshold.
```

- [ ] **Step 6: Write the tests instructions**

Create `.github/instructions/tests.instructions.md`:

```markdown
---
applyTo: "tests/**"
---

# Reviewing tests

- **Test-driven:** the failing test comes first. A PR adding behavior with no test that
  would have failed before it is incomplete.
- **The default tier is zero-cost, offline and deterministic.** `pytest` runs with
  `--block-network` and deselects `integration`. Flag any default-tier test that could reach
  the network, need a key, or depend on wall-clock time or ordering.
- **Marker tiers:** unmarked = offline pipeline (`FakeRunner`); `cassette` = replays
  recorded provider traffic, free, selected by default; `integration` = real API spend,
  opt-in only.
- **`conftest.py` chdirs every test into a fresh `tmp_path`** so config upward-discovery
  cannot pick up an ambient `skill-eval.toml`. A test that reads repository files must
  anchor on `Path(__file__).resolve().parents[1]`, never `Path.cwd()`. Flag any test relying
  on the working directory.
- **Cassettes must be secret-free.** Both request and response headers are scrubbed, on two
  different vcrpy hooks. Flag anything that could write a credential or an account
  identifier to disk.
- A test asserting an error path should assert the message a user actually sees, not just
  the exception type.
```

- [ ] **Step 7: Write the docs instructions**

Create `.github/instructions/docs.instructions.md`:

```markdown
---
applyTo: "docs/**,*.md,mkdocs.yml"
---

# Reviewing documentation

- **`README.md` is a landing page.** Reference prose lives in `docs/`. Flag any PR that
  reintroduces command, config or eval-file reference material into the README — it will
  drift.
- **`ARCHITECTURE.md` has exactly one copy**, at the repository root.
  `docs/architecture.md` includes it with a `pymdownx.snippets` directive. Flag any
  duplication of its content.
- **A new page must be added to `nav:` in `mkdocs.yml`.** `tests/test_docs.py` fails on an
  orphan page, and `mkdocs build --strict` fails on a nav entry with no file.
- **`docs/superpowers/` is a historical archive** of specs and plans. It is excluded from
  the built site and does not count as documenting a change. Its contents were superseded by
  what shipped — read `src/` as the source of truth.
- Documentation ships **with** the change: a new flag updates `docs/cli.md`, a new config
  key updates `docs/configuration.md`, a new assertion kind updates `docs/eval-files.md`, a
  new invariant updates `ARCHITECTURE.md`.
- Do not add `pymdownx.emoji` or mermaid `custom_fences` to `mkdocs.yml`: both need
  `!!python/name:` YAML tags, which break the plain-YAML nav parsing in `tests/test_docs.py`.
- Relative links must resolve — there is a test for it.
```

- [ ] **Step 8: Verify every instruction file has valid frontmatter**

```bash
uv run python - <<'PY'
from pathlib import Path
from skill_eval.yaml_loading import safe_load

for path in sorted(Path(".github/instructions").glob("*.instructions.md")):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    front = text.split("---", 2)[1]
    data = safe_load(front)
    assert "applyTo" in data, path
    print(f"{path.name}: {data['applyTo']}")
PY
```

Expected: six lines, each naming the file and its `applyTo` glob.

- [ ] **Step 9: Confirm the referenced paths exist**

```bash
uv run python - <<'PY'
from pathlib import Path
globs = []
for path in Path(".github/instructions").glob("*.instructions.md"):
    front = path.read_text(encoding="utf-8").split("---", 2)[1]
    for pattern in front.split("applyTo:", 1)[1].strip().strip('"').split(","):
        pattern = pattern.strip()
        matches = list(Path(".").glob(pattern))
        print(f"{pattern}: {len(matches)} match(es)")
        assert matches, f"{path.name}: {pattern} matches nothing"
PY
```

Expected: every pattern reports at least one match. A zero means a typo in the glob.

- [ ] **Step 10: Run the full suite one last time**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mkdocs build --strict && uv run skill-eval list ./examples
```

Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add .github/copilot-instructions.md .github/instructions/
git commit -m "docs: add copilot review instructions for pull requests"
```

---

## Verification before opening the PR

- [ ] `uv run pytest -q` — all pass, offline
- [ ] `uv run ruff check .` and `uv run ruff format --check .` — clean
- [ ] `uv run mkdocs build --strict` — exit 0
- [ ] `uv run skill-eval list ./examples` — still lists both example skills
- [ ] `python3 scripts/check_docs_updated.py main HEAD` — exit 0
- [ ] `grep -rn "skill_eval" README.md docs/*.md ARCHITECTURE.md` — every hit is a literal
      import path or file path, never prose
- [ ] PR title is conventional. Suggested:
      `docs: publish a documentation site and gate pull requests on docs freshness`
- [ ] PR description records the two manual repository settings: Pages → Source =
      *GitHub Actions*, and enabling Copilot code review
