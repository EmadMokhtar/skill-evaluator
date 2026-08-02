# Docs infrastructure design

**Date:** 2026-08-02
**Status:** approved, not yet implemented
**Scope:** documentation only — no change to `skill-eval` runtime behavior, except one
small refactor in `evaluators/assertion.py` (see §4.1).

## 1. Why

The project ships M2 with a single 370-line `README.md` carrying install, eval-file
reference, CLI reference, configuration, gating, architecture and contributing. That file is
at the limit of what a README can hold, there is no published documentation, and nothing
prevents the docs from drifting as M3–M8 land. There is also no reviewer guidance for GitHub
Copilot, so its PR reviews cannot see the invariants that this codebase treats as decided
behavior.

This spec covers four deliverables, landing as one PR before M3 starts:

1. A user-facing documentation site built with MkDocs and published to GitHub Pages.
2. `ARCHITECTURE.md` at the repository root.
3. A per-PR check that documentation stays current, at three layers.
4. Custom instructions for GitHub Copilot PR reviews.

## 2. Documentation site

### 2.1 Source layout

`docs/` becomes the MkDocs `docs_dir`. The existing `docs/superpowers/` tree stays on disk as
a historical record and is kept out of the build with MkDocs' built-in `exclude_docs:` — no
plugin required.

```
mkdocs.yml
docs/
  index.md            what skill-eval is, why it exists, install
  getting-started.md  first skill, first eval file, first run
  eval-files.md       case fields, assertion kinds, discovery rules
  cli.md              commands and flags
  configuration.md    skill-eval.toml, environment variables, precedence
  runners.md          fake and pydantic-ai, mock tools, trajectories, budgets, pricing
  gating.md           gate rules, exit codes, JSON report
  architecture.md     includes ARCHITECTURE.md via a snippet
  contributing.md     development loop, Conventional Commits, the docs policy
  roadmap.md          milestone table
  superpowers/        excluded from the build; historical plans and specs
```

Content for pages 2–8 is moved out of `README.md`, not copied. After the move, `README.md`
is a landing page: what it is, install, a short quickstart, links into the site, license.
No prose lives in two places.

### 2.2 Configuration

- Theme: `mkdocs-material` — light/dark toggle, code copy button, search.
- Markdown extensions: `admonition`, `pymdownx.superfences`, `pymdownx.snippets`
  (with `base_path` including the repo root, so `ARCHITECTURE.md` can be included),
  `tables`, and `toc` with permalinks.
- `site_name: skill-eval`, `site_url: https://emadmokhtar.github.io/skill-evaluator/`,
  `repo_url` pointing at the GitHub repo.
- Dependencies go in a `docs` dependency-group in `pyproject.toml`; the local command is
  `uv sync --group docs` then `uv run mkdocs serve`.

Versioned documentation (`mike`) is deliberately out of scope pre-1.0.

### 2.3 Publishing

`.github/workflows/docs.yml`, triggered on push to `main`:

- builds with `mkdocs build --strict`,
- publishes with `actions/upload-pages-artifact` and `actions/deploy-pages`
  (`permissions: pages: write, id-token: write`), so there is no `gh-pages` branch.

Pull requests do **not** deploy. They run `mkdocs build --strict` as a job in `ci.yml`, so a
broken nav entry, a dead reference or an unparseable snippet fails the PR.

**Manual repository step:** Settings → Pages → Source must be set to *GitHub Actions*. The
first deploy fails until this is done.

## 3. `ARCHITECTURE.md`

A single root-level file — the conventional location, and discoverable when browsing GitHub.
`docs/architecture.md` includes it with a `pymdownx.snippets` directive
(`--8<-- "ARCHITECTURE.md"`) so exactly one copy exists.

Sections:

1. **Scope and non-goals** — skills under test and their eval cases are *inputs*; nothing
   about a skill under test is vendored.
2. **The two protocols** — `Runner.run(skill, case) -> RunResult` and
   `Evaluator.evaluate(case, result) -> EvalScore`, with signatures.
3. **Module map** — a table of module → responsibility for everything under
   `src/skill_eval/`.
4. **Data flow** — the discovery → matrix → score → aggregate → gate pipeline.
5. **Core data models** — `Skill`, `EvalCase`, `RunResult`, `EvalScore`, `CaseOutcome`,
   `RunReport`, and which fields are derived rather than stored.
6. **Invariants, and why each exists** — the list currently condensed in `CLAUDE.md`:
   `errored` ≠ `failed`; a zero-case run fails the gate; authoring errors abort rather than
   score; the exit-code contract; `extra="forbid"`; UTF-8 and typed parse errors;
   `yaml_loading.safe_load`; secrets from the environment only; agent-framework types confined
   to runner adapters; `RunResult.tokens` derived; cost lookup degrades rather than raises;
   mock tools accept any arguments; cassettes replay-only and secret-free.
7. **Extension points** — how to add a runner, an evaluator, a reporter.
8. **Testing tiers** — offline (`FakeRunner`, default, zero cost), `cassette` (replayed
   provider traffic, selected by default), `integration` (real API, opt-in, costs money).

`CLAUDE.md` keeps its condensed agent-facing invariant list and gains a link here, rather
than growing further.

## 4. Keeping documentation current

Three layers, chosen together: executable checks catch drift precisely, the heuristic catches
stale prose that no assertion can see, and the written instructions tell a contributor what
to do before either fires.

### 4.1 Executable checks — `tests/test_docs.py`

Offline, zero-cost, no network. Each assertion targets real drift, never style:

| Check | Source of truth | Scope checked |
| --- | --- | --- |
| Every command and every option is documented | the Typer app, introspected — not `--help` text | `docs/cli.md` |
| Every `Config` field is documented | `config.Config.model_fields` | `docs/configuration.md` |
| Every assertion `kind` is documented | `evaluators.assertion.ASSERTION_KINDS` | `docs/eval-files.md` |
| Every `EvalCase` field is documented | `models.EvalCase.model_fields` | `docs/eval-files.md` |
| Every page is reachable from the nav | `mkdocs.yml` nav vs. files on disk | — |
| Every relative Markdown link resolves | files on disk | `docs/`, `README.md`, `ARCHITECTURE.md` |

Introspecting the Typer app rather than parsing `--help` output keeps the test stable against
formatting changes in Typer.

`evaluators/assertion.py` currently dispatches assertion kinds through an `if`-chain, which
cannot be introspected. Implementation adds a module-level `ASSERTION_KINDS` tuple used both
by the dispatch and by this test, so a new kind cannot be added without the test noticing.
This is the only runtime file this work touches.

`mkdocs build --strict` runs as a separate CI job (§2.3) and covers nav and reference errors
that the tests do not.

### 4.2 Heuristic check — `scripts/check_docs_updated.py`

Mirrors the existing `scripts/check_commits.py` in shape: a small script with its own unit
test (`tests/test_check_docs_updated.py`), invoked from a CI job on pull requests.

Rule: if the diff between the PR base and head touches `src/skill_eval/**` and touches
none of `docs/**`, `README.md`, `ARCHITECTURE.md`, `mkdocs.yml`, the job fails with a message
naming the changed source files.

Escape hatch: the PR label `no-docs-needed` skips the job. A label is used rather than a
commit trailer because PRs are squash-merged, which rewrites the message.

This check is knowingly imprecise. Pure refactors, dependency bumps and test-only changes
under `src/` will trip it, and a one-word docs edit satisfies it. It is a prompt, not a proof.

### 4.3 Written instructions

- `CLAUDE.md` gains a **Documentation** section: documentation ships with the change rather
  than as a follow-up; a table mapping the kind of change to the file that must be updated;
  and the two local commands to run (`uv run mkdocs build --strict`,
  `uv run pytest tests/test_docs.py`).
- `.github/pull_request_template.md` adds a documentation checkbox alongside the existing
  Conventional Commits requirement for the PR title.

## 5. Copilot review instructions

### 5.1 Repository-wide

`.github/copilot-instructions.md` carries the project-wide frame: what `skill-eval` is, the
two seams, the invariant list, the Conventional Commits requirement on the PR title (because
a squash-merge makes the title the commit on `main`), the test-driven convention, and an
explicit *do not suggest* list — agent-framework imports in the core, bare `yaml.safe_load`,
raising from a runner on provider failure, and dropping `extra="forbid"`.

### 5.2 Path-scoped

Files under `.github/instructions/`, each with `applyTo:` frontmatter, so review comments stay
targeted rather than evaluating every rule against every file:

| File | `applyTo` | Focus |
| --- | --- | --- |
| `runners.instructions.md` | `src/skill_eval/runners/**` | framework isolation, never raise for provider failure, cost lookup degrades, mock tools accept any arguments, cassettes replay-only |
| `evaluators.instructions.md` | `src/skill_eval/evaluators/**` | pass/fail/skipped semantics, authoring errors propagate rather than score |
| `models-loaders.instructions.md` | `models.py`, `config.py`, `yaml_loading.py`, `cases/**`, `skills/**` | `extra="forbid"`, UTF-8 pinning, typed parse errors naming file and field |
| `cli-gating.instructions.md` | `cli.py`, `gating.py`, `orchestrator.py`, `reporters/**` | exit-code contract, zero-case run fails the gate, `skill_eval` never in user-facing output |
| `tests.instructions.md` | `tests/**` | test-first, offline determinism, marker tiers, the `conftest.py` chdir |
| `docs.instructions.md` | `docs/**`, `*.md`, `mkdocs.yml` | nav registration, the docs tests, no duplication with `README.md` |

**Manual repository step:** Copilot code review must be enabled on the repository (or added
as a required reviewer) before any of these files are read.

## 6. Files touched

**New:** `mkdocs.yml`; ten pages under `docs/`; `ARCHITECTURE.md`;
`.github/workflows/docs.yml`; `.github/pull_request_template.md`;
`.github/copilot-instructions.md`; six files under `.github/instructions/`;
`scripts/check_docs_updated.py`; `tests/test_docs.py`; `tests/test_check_docs_updated.py`.

**Modified:** `README.md` (reduced to a landing page); `pyproject.toml` (`docs` dependency
group); `.github/workflows/ci.yml` (docs-build job, docs-freshness job); `CLAUDE.md`
(Documentation section, link to `ARCHITECTURE.md`);
`src/skill_eval/evaluators/assertion.py` (`ASSERTION_KINDS` tuple).

## 7. Acceptance

- `uv run mkdocs build --strict` succeeds locally and in CI.
- `uv run pytest` passes offline, including the new `tests/test_docs.py` and
  `tests/test_check_docs_updated.py`.
- A PR touching only `src/skill_eval/**` fails the docs-freshness job; the same PR with the
  `no-docs-needed` label passes it.
- A push to `main` publishes the site to `https://emadmokhtar.github.io/skill-evaluator/`.
- `README.md` no longer duplicates reference content held in `docs/`.
