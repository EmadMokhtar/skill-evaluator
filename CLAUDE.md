# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`skill-lens` is a standalone CLI + library that runs evaluations on Anthropic-style Agent
Skills (`SKILL.md` files). Skills under test and their eval cases are **inputs** — nothing
about a skill-under-test is vendored here. The tool is meant to run as a CI gate (exit code
is the contract) or on demand.

Currently at **M7 (complete)**: the pipeline runs real agents through `PydanticAIRunner`
(provider-flexible, via PydanticAI), scores tool use and efficiency as well as
output text, and is tested against recorded provider traffic. `FakeRunner`
remains the default and the backbone of the zero-cost test tier. M3 adds a
rubric-based LLM judge that scores output quality with per-check evidence, and
an `offered` case mode that measures whether the agent chose to trigger the
skill at all, negative controls included. M4 makes every measurement
comparative: each case can run in a candidate arm and a baseline arm
(`--baseline none` or `--baseline previous`, resolved from git), optionally
sampled `--repeat N` times, with the report gaining a delta and `--min-delta`
gating on it. M5 part 1 makes a run legible to CI: `--junit-output` and
`--markdown-output` reporters, `--concurrency N` over the work matrix, and a
composite GitHub Action with example workflows. M5 part 2 automates releasing
itself: a merge to `main` verifies, bumps the version from the commit history
with `cz bump`, tags it, and publishes to PyPI over Trusted Publishing, with a
manual workflow to refresh the recorded provider traffic. See
[Releasing](docs/releasing.md). M6 part 1 gives a case a contained workspace
with `list_files`/`read_file`/`write_file`, `file-produced` and `json-schema`
assertions, a `file:` modifier and `judge: artifacts:`. M7 makes a failing
case explain itself (output and tool calls in every reporter, `--full-output`),
adds `--case`, brings `init` up to M6 with a workspace case and a batch mode,
and ships a versioned comparative example, an annotated config and an
end-to-end quickstart. M8 part 1 adds a LangChain runner and judge behind the same
protocols, installable as the `[langchain]` extra, with the prompt rules and retry loop
extracted into `runners/prompting.py` and `runners/retry.py`. Milestones are defined in
`docs/superpowers/specs/2026-07-30-skill-eval-design.md` §9; the M2 design is
in `docs/superpowers/specs/2026-08-01-skill-eval-m2-design.md`, the M3 design
is in `docs/superpowers/specs/2026-08-03-skill-eval-m3-design.md`, the M4
design is in `docs/superpowers/specs/2026-08-03-skill-eval-m4-design.md`, the
M5 design is in `docs/superpowers/specs/2026-08-05-skill-eval-m5-design.md`,
the M6 design is in `docs/superpowers/specs/2026-09-10-skill-lens-m6-design.md`,
the M7 design is in `docs/superpowers/specs/2026-09-11-skill-lens-m7-design.md`, and the
M8 design is in `docs/superpowers/specs/2026-09-11-skill-lens-m8-design.md`.

## Commands

```bash
uv sync                              # install (dev deps included)
uv run pytest                        # test suite (integration marker deselected by default)
uv run pytest tests/test_gating.py::test_name -v   # single test
uv run pytest -m integration          # opt-in tier; needs OPENAI_API_KEY, costs real money
uv run pytest tests/test_cassettes.py --record-mode=once      # record a cassette that doesn't exist yet (needs a key)
uv run pytest tests/test_cassettes.py --record-mode=rewrite   # refresh cassettes that already exist (needs a key)
uv run ruff check .                  # lint
uv run ruff format .                 # format (CI runs --check)
uv audit --preview-features audit --locked   # known vulnerabilities in uv.lock; exceptions only in [tool.uv.audit]
uv run skill-lens list ./examples     # dogfood discovery; CI runs this as a self-check
uv run pre-commit install --hook-type commit-msg --hook-type pre-push   # once per clone
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
- **An unfilled scaffold aborts the run.** A case still containing `TODO(skill-lens)` is
  an authoring error (exit 2), checked in `cases/loader.py` before validation so the
  message names the field. The rule is the loader's, so hand-written stubs get it too.
- **`extra="forbid"`** on `EvalCase` / `AssertionSpec` / `ToolSpec` / `TrajectorySpec` /
  `BudgetSpec` / `Config` / `RunResult`. Without it a typo like `assertion:` yields a
  vacuously-passing case.
- **All file IO pins `encoding="utf-8"`** and re-raises as a typed parse error
  (`SkillParseError` / `CaseParseError` / `ConfigError`) naming the file and field.
- **YAML goes through `yaml_loading.safe_load`**, never `yaml.safe_load`. The custom loader
  stops YAML 1.1 from turning bare `yes`/`no`/`on`/`off` into booleans.
- **Secrets come from environment variables only** — never from `skill-lens.toml`.
- **`skill_lens` (underscore) never appears in user-facing output.** The user-facing name is
  `skill-lens` everywhere: command, config file, distribution. The GitHub repository keeps its
  older name, `skill-evaluator`, so `uses: EmadMokhtar/skill-evaluator@v<version>` installing
  `skill-lens` is expected, not a mistake. `tests/test_naming.py` fails if the pre-rename name
  reappears outside `docs/superpowers/`, which is a historical archive and is never rewritten.
  `CHANGELOG.md` is exempt on the same grounds — `cz bump` regenerates it from pre-rename commit
  subjects, so editing it would misquote history and be undone by the next release — but a
  narrower test pins the exact lines history produced, so a commit subject written after the
  rename that carries the old name still fails.
- **`FakeRunner.run` returns `model_copy(deep=True)`** so a caller cannot corrupt scripted state.
- **No agent-framework type may appear outside the four adapter modules** —
  `runners/pydantic_ai.py`, `judges/pydantic_ai.py`, `runners/langchain.py`,
  `judges/langchain.py`. `runners/tools.py` builds framework-neutral `AgentTool`s (name +
  JSON schema + callable); `runners/prompting.py` and `runners/retry.py` hold the prompt and
  retry rules both adapters share; the adapters wrap them. `tests/test_framework_isolation.py`
  guards this: it asserts no other module under `src/skill_lens/` imports `pydantic_ai`,
  `langchain*` or `langgraph` at the top level.
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
- **skill-lens derives `passed` and `score` from per-check verdicts.** The judge is never
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
- **JUnit reports the candidate arm only, and `<failure>`/`<error>` mirror `failed`/`errored`.**
  A failing baseline is evidence the skill helped, not a red build. `errored` covers a runner
  that raised *and* an evaluator that did — `_error_body` reads `RunResult.error` first and
  falls back to an errored evaluator's own `detail`, so a judge endpoint returning 500 is never
  misreported as the runner's fault.
- **JUnit output is always well-formed XML.** `ElementTree` emits control characters raw, so
  illegal characters are stripped before they reach the tree. A zero-case run emits an
  `<error>`, never an empty `tests="0"` suite that would render green against exit code 1.
- **Markdown truncation gives up detail before it gives up meaning.** Optional blocks are
  dropped first; if gate reasons still overflow the budget they are elided behind a truthful
  `+N more reasons` count rather than cut silently, so a clipped comment can never imply the
  reasons it shows were all of them; only a budget too small to hold the verdict itself falls
  back to a hard character cut. It lives in the renderer, because only the renderer knows where
  a `<details>` block ends or a count line would be cut in half.
- **Reporters never do IO to a service.** The tool renders; the workflow posts.
- **`--concurrency 1` constructs no executor** and runs the plain sequential loop; outcome
  order is submission order rather than completion order at every concurrency level. An
  authoring error still aborts the run deterministically: only the futures queued *after* the
  failure are cancelled, the executor always shuts down with `cancel_futures=True` (including
  when `submit` itself raises), and a result list shorter than the work list is a raised error,
  never a quiet partial run. Discovery is now always a separate sequential pass ahead of
  execution, so a malformed eval file anywhere aborts before any case runs. Runners, judges and
  evaluators must have no mutable state touched by `run`/`evaluate`/`judge`.
- **The action fails closed.** `shell: bash` already runs under `-e`; the run step captures the
  CLI's exit code itself (`code=0; skill-lens run ... || code=$?`) before `-e` can discard it,
  every later step carries `if: always()`, and the final step re-raises with `exit
  "${CODE:-1}"` — an empty code (the run step never finishing at all) fails rather than
  defaulting to success.
- **Nothing publishes that has not been verified in the same run.** `publish` is reachable
  only through `needs:` on a green `verify`; publishing is irreversible, since PyPI refuses a
  re-upload of a version that already exists.
- **A merge with no releasable commit publishes nothing and fails nothing.** `cz bump` exit
  codes 21 and 3 are no-ops, not errors.
- **The pushed release tag is annotated, and the job verifies it actually reached `origin`
  before building.** `git push --follow-tags` pushes only annotated tags, so
  `[tool.commitizen] annotated_tag = true` exists specifically to make Commitizen create one
  instead of its default lightweight tag — without it, the bump commit would reach `main`
  while the tag stayed on the runner and vanished. Because `publish` is reached through
  `needs:`, not through the tag, a silently dropped tag would otherwise go unnoticed all the
  way to PyPI; `release` runs `git ls-remote --tags origin` right after the push and fails
  loudly if the tag is missing.
- **The version in `action.yml` always equals the package version**, and the pairing between a
  version spelling and a `version_files` pattern is guarded in *both* directions — every
  spelling has a pattern that rewrites it, and every pattern still matches a line carrying the
  current version. All three live in `tests/test_release_config.py`. The second direction is
  what stops a reformatted pin from becoming a no-op rewrite, and it fails on the pull request
  rather than at release time, where `cz bump --check-consistency` would abort the release
  instead.
- **The tag prefix is derived, not duplicated.** `release.yml` reconstructs the tag to look it
  up after pushing, and `tests/test_release_workflow.py` requires that spelling to match
  `[tool.commitizen] tag_format`. Changing the format alone would leave the release correctly
  tagged but the lookup wrong — failing *after* the push, which spends a version that can
  never be published.
- **No long-lived publishing credential exists.** Trusted Publishing only.
- **A cassette refresh re-records with `--record-mode=rewrite`, never `once`** — `once` only
  fills in a missing cassette and write-protects one already loaded, so it cannot refresh an
  existing recording, which is the workflow's whole purpose. It then stages the recordings
  (`git add -A -- tests/cassettes`) before either check that follows, because `git diff` can't
  see an untracked file and a freshly re-recorded cassette is exactly that; proves the new
  recordings replay under `--record-mode=none`; and checks the staged diff for secrets —
  all before pushing, and it never opens a pull request that CI has not run on.
- **A built-in workspace tool never raises; it returns a message the model can read.**
  `Workspace`'s own methods raise (`PathRefused`, `WorkspaceError`) for the loader and
  evaluators to catch as authoring/infra errors; `runners/tools.py` catches those same
  exceptions and turns them into ordinary tool results instead.
- **No path outside the workspace root can be read or written.** Checked after resolution,
  against a root that was itself resolved once at creation — an unresolved root (`/tmp` vs
  `/private/tmp` on macOS) would make containment checks compare two spellings of one
  directory.
- **Every work item gets its own workspace, and two arms never share one.** `mkdtemp` is
  atomic; a shared directory would let the baseline arm read the candidate's output.
- **The workspace preamble is byte-identical in both arms and never names the skill** —
  otherwise `--min-delta` measures the preamble instead of the skill.
- **`RunResult.workspace` is non-null only while the directory exists**, and the orchestrator
  stamps it unconditionally after every run so a non-conforming runner cannot smuggle a path
  of its own into the report.
- **Workspace cleanup never changes a verdict**, and lives in a `finally` so an authoring
  error raised by an evaluator still deletes the directory on its way out.
- **A workspace creation or seeding failure is `errored`, never `failed`.** A full disk or a
  half-seeded directory says nothing about the skill.
- **Artifacts reach the judge as fenced, untrusted data**, each under an id derived from the
  trusted name AND the untrusted content — salted because `sha256("")` is a constant a model
  could reproduce from memory. An artifact's boundary is its **first** matching closer, the
  opposite of the response fence's rule, because each artifact block is closed immediately.
- **`file:` or `judge.artifacts` in a case with no `workspace:` block is an authoring error**
  (exit 2), and so is a `judge.artifacts` entry that could never be produced.
- **Judge artifact bytes are capped and truncation is visible**; a sentinel never consumes
  the content budget, because the cap bounds untrusted model content and a sentinel is fixed
  harness text.
- **An unknown assertion kind is caught at load time**, before any case runs and before any
  money is spent.
- **A configured cap reaches the workspace.** A limit read from config and then dropped on
  the way through the orchestrator would leave the default silently in force.
- **Every kept directory is printed, however keeping was turned on** — `--keep-workspace` or
  the config key. A persistent setting with no visible output would fill a disk silently.
- **Output is expanded only under non-passing candidate outcomes, and a cut is never silent.**
  `reporters/failure_context.py` computes one excerpt for all three reporters; passing and
  baseline outcomes are never expanded, and a truncated output states the exact count removed.
- **A `--case` matching nothing fails the gate** — the fourth zero-cases cause. `--case` has
  no config key: a filter that lived in the file would let a green run measure less than the
  repository declares.
- **`init` never creates an `evals/` directory beside existing `*.eval.yaml` files**
  (`scaffold_target`), and **batch `init` never overwrites** — a skill with any eval file is
  skipped and `--force` in batch mode is a user error.
- **The unfilled-scaffold scan covers mapping keys as well as values.**
- **`examples/greeting` stays at `1.1.0` or later.** The bump is what makes `--baseline
  previous` resolvable from a checkout; `tests/test_examples.py` pins it.
- **The dependency audit is one command, spelled identically in three places, and its
  exceptions live in one table.** `uv audit --preview-features audit --locked` runs in
  `security.yml` (every PR, every push to `main`, weekly on a schedule, and on demand), in
  `release.yml`'s `verify` job, and in the `pre-push` hook; `tests/test_security_checks.py`
  requires the three to be byte-identical. Exceptions go in `[tool.uv.audit]` in
  `pyproject.toml`, never on a command line, and only as `ignore-until-fixed` — which stops
  hiding an advisory the day a fix ships — never `ignore`. uv does not validate that table, so
  the test rejects any other key rather than let a typo silently keep a finding. Any finding
  fails; there is no severity threshold. The build backend is audited too: a `build`
  dependency group mirrors `[build-system] requires` (a test keeps them equal), which puts
  `hatchling` in the lockfile, and the release exports that group as a `--build-constraint`
  so `uv build` uses exactly the audited versions rather than a fresh resolution.
- **The audit runs at push time locally, not commit time.** It needs the network; a commit
  hook would fail offline and teach people to skip it. A push needs the network anyway.
- **Ruff's `S` rules are on, and a false positive is suppressed at the site with its reason.**
  Never by switching a rule off for `src/`. `tests/**` and `scripts/**` carry per-directory
  ignores for `assert`, subprocess-with-fixed-argv, XML parsing and literal `/tmp` strings used
  as fake path values; the two `src/` sites
  (`git` found on `PATH` in `baseline.py`; `StrictBoolLoader` in `yaml_loading.py`, which
  *is* a `SafeLoader` subclass ruff cannot see) each carry an inline `noqa` with the reason.
- **Every action is pinned to a commit SHA with a `# vX.Y.Z` comment, and nothing grants
  write access at the workflow level.** A tag can be moved; a commit cannot.
  `tests/test_supply_chain.py` fails any `uses:` that is not `./` or a 40-hex SHA with the
  version comment Dependabot maintains, any workflow whose top-level `permissions` is not empty
  or read-only, and a docs `build` job holding anything but `contents: read`. Dependabot
  (`uv` and `github-actions`, weekly) is what keeps the pins moving; its commit prefixes must
  pass `cz check`, because the PR title becomes the commit `cz bump` parses.
- **The SBOM never enters `dist/`, and the GitHub Release is created only after PyPI accepted
  the upload.** `publish` sends every file in the `dist` artifact to PyPI, which would reject
  an SBOM and fail the upload after the tag is pushed — so the export writes to `sbom/` and
  ships as its own artifact. `github-release` needs `publish`; a release that exists before the
  upload could advertise a version `pip install` cannot find. It is re-run safe: creation is
  guarded by `gh release view`, assets go up with `--clobber`, and its notes are
  `cz changelog <version> --dry-run` — the same commits that chose the version. **The job
  that can write releases installs nothing**: notes, SBOM and distributions are all produced
  in `release` and passed as artifacts, so `github-release` has no checkout and runs no `uv`,
  and no third-party code executes under `contents: write`. The docs `build` job needs
  `pages: read` — `actions/configure-pages` fails the job when its `GET …/pages` is refused.

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
| CI integration, the action, example workflows | `docs/ci.md` |
| The release pipeline, its one-time setup, or the cassette-refresh workflow | `docs/releasing.md` |
| The dependency audit, the `S` lint rules, the exception policy, action pinning, Dependabot, the SBOM, or attestations | `docs/security.md` (and `SECURITY.md` for how to report) |
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
  can't pick up an ambient `skill-lens.toml`. Tests needing real discovery pass an explicit
  `start=`.
- `docs/superpowers/plans/` is a **historical record** — its code blocks were superseded by
  what shipped. Read `src/` as the source of truth; the design spec in `docs/superpowers/specs/`
  is still current.
