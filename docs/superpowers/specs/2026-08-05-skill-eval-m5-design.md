# skill-eval M5 — Design

**Date:** 2026-08-05
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M5, §12)

## 1. Scope

M1–M4 built the measurement. M5 makes it *land somewhere*: in the CI systems that consume
test results, in the pull request a skill author is looking at, and in wall-clock time that a
two-armed, repeated run has made 2N times longer.

The milestone splits into two PRs, along the line of what needs setup outside this repo.

**Part 1 — the tool** (this spec's subject; pure code, testable offline):

- **JUnit XML reporter** — the universal CI test-result format. Makes `failed` vs `errored`
  visible in every CI UI natively.
- **Markdown reporter** — the two GitHub surfaces that matter: `$GITHUB_STEP_SUMMARY` and a
  sticky PR comment.
- **Bounded orchestrator concurrency** — `--concurrency N` over the flattened work matrix.
- **A composite GitHub Action plus example workflows** — the action is the behavior; the
  workflows demonstrate it and are real files the docs include.
- **Per-skill thresholds** — already shipped; verified, documented, no code change (§9).

**Part 2 — release automation** (separate PR, §10): `release.yml`, `publish.yml` and the
manual cassette-refresh workflow. Held back because none of it can run until a PyPI project
with a trusted publisher, a protected `pypi` environment, a `main`-push credential and an
`OPENAI_API_KEY` secret exist — administration this repo cannot do for itself.

### Explicitly deferred

| Deferred | Milestone | Why |
| --- | --- | --- |
| HTML reporter | later | Nothing in this milestone consumes it. A self-contained HTML report is a real maintenance surface — styling, escaping, dark mode, and re-syncing with the report shape every time a later milestone extends it — for a file that GitHub, GitLab and Jenkins users would never open. JUnit covers the CI surface and Markdown covers the human one. |
| Process or subinterpreter pools | later | The work is network-bound: a work item is one provider round trip of 1–20s, against sub-millisecond of local CPU (YAML parsing, regex, arithmetic). Multi-core buys ~0%; overlapping the waits buys ~N×. The orchestrator is typed against `concurrent.futures.Executor` so this stays a one-line change if M6's real tool execution introduces CPU-bound work. |
| An async `Runner` protocol | later | Cheaper per slot than threads, but it changes a core seam that both runners, both judges and every evaluator implement, to win a resource we are not short of. |
| Per-skill `min_delta`, `--max-token-regression` | later | Carried over from M4's deferred list unchanged; nothing in M5 alters the argument. |
| `skill-eval` posting to the GitHub API itself | never | §2.9. |
| Config keys for output paths | later | `--json-output` has never had one. Output destinations are a property of an invocation, not of a project. |

## 2. Decisions

1. **Reporters render; they never do IO to a service.** `render_junit` and `render_markdown`
   are pure functions returning a string, exactly like `render_console` and `render_json`. The
   CLI writes files; the *workflow* posts the comment. A GitHub API client inside skill-eval
   would put network failure, token scopes and rate limits inside a reporter, and would make
   the tool's behavior depend on which CI it runs under.
2. **JUnit reports the candidate arm only.** A baseline failure is the *desired* result — it
   is the evidence the skill helped. Emitting it as `<failure>` would paint CI red for the
   skill working. This is the existing rule that every `RunReport` aggregate reads
   `candidate_outcomes`, applied to a new surface.
3. **`<failure>` and `<error>` map 1:1 onto `failed` and `errored`.** The distinction this
   project is built around finally gets a native rendering: JUnit consumers already show
   errors apart from failures, so "the runner blew up" stops looking like "the skill got
   worse" in the CI UI.
4. **A zero-case run emits a JUnit `<error>`, not an empty suite.** `tests="0"` renders green
   in most CI UIs, which would directly contradict exit code 1. "A run executing zero cases
   fails the gate" has to hold on every surface that claims to report the run.
5. **JUnit output is always well-formed XML.** Built with `xml.etree.ElementTree`, never
   string concatenation, because skill names, case names, model output and judge evidence are
   all user- or model-controlled. ElementTree escapes `&`/`<`/`>` but passes control
   characters through raw, so a model emitting `\x00` would produce a file real parsers
   reject; illegal characters are stripped before they reach the tree.
6. **Markdown truncation gives up detail before it gives up meaning, and never hides how much
   it gave up.** The verdict and the gate reasons are assembled first, ahead of every table
   (§4), so a clipped comment still opens with the answer. Optional detail blocks are the first
   thing dropped. If gate reasons still do not fit, they are elided behind a truthful `+N more
   reasons` count rather than being cut in silence, so a clipped comment can never imply the
   reasons it shows were all of them. Only a budget too small to hold the verdict, the summary
   and that count itself falls back to a hard character cut.
7. **The renderer owns truncation, the GitHub layer owns the number.** Only the renderer knows
   where a `<details>` block ends, so a `.slice()` in the workflow would cut mid-block. But
   65,536 is a fact about GitHub comments, not about skill-eval, so `--markdown-max-chars`
   defaults to unset and the action supplies the value.
8. **`--concurrency 1` creates no executor and behaves identically to today.** Same as
   `baseline` defaulting to `""` and `judge` to `"fake"`: upgrading must never change spend,
   ordering, or behavior on its own.
9. **Outcome order is submission order, never completion order.** `render_console` iterates
   `report.outcomes` and `build_delta` groups by dict insertion order, so completion-order
   results would make console output and case ordering churn between identical runs.
10. **Concurrency must not convert an authoring error into a case failure.** A malformed
    assertion still aborts the whole run with exit 2, and *which* error surfaces is
    deterministic.
11. **The action re-raises the CLI's exit code, after writing the summary.** Exit codes are
    the CI contract. Writing the summary only on success — the standard bug in these actions —
    would hide the report exactly when it is needed.
12. **One behavior, two documented surfaces, one test stopping them from disagreeing.**
    `action.yml` is the behavior; the example workflows demonstrate it; `tests/test_action.py`
    asserts the action's inputs and the CLI's flags stay in step.

## 3. JUnit reporter — `reporters/junit.py`

```python
def render_junit(report: RunReport, gate: GateResult | None = None,
                 delta: Delta | None = None) -> str: ...
```

`delta` is accepted for signature symmetry with the other reporters and is unused: JUnit has
no vocabulary for "this case improved". The delta lives in JSON and Markdown.

### Shape

```xml
<?xml version="1.0" encoding="utf-8"?>
<testsuites name="skill-eval" tests="12" failures="2" errors="0" skipped="1" time="8.214">
  <testsuite name="pdf" tests="6" failures="1" errors="0" skipped="0" time="4.102">
    <testcase classname="pdf" name="extracts-tables" time="0.812"/>
    <testcase classname="pdf" name="rejects-scans" time="0.640">
      <failure message="assertion: expected output to contain 'not searchable'">
        assertion: expected output to contain 'not searchable'
        judge/cites-the-page: no evidence given
      </failure>
    </testcase>
  </testsuite>
  <testsuite name="unused-skill" tests="1" failures="0" errors="0" skipped="1" time="0">
    <testcase classname="unused-skill" name="(no eval cases)">
      <skipped message="no eval cases"/>
    </testcase>
  </testsuite>
</testsuites>
```

### Rules

- **One `<testsuite>` per skill**, in report order. One `<testcase>` per **candidate** outcome
  (§2.2). `classname` is the skill name, `name` the case name — the pair CI systems use as a
  test's identity across builds, so both must be stable.
- **`time`** is `latency_ms / 1000`, JUnit's unit. Suite and root times are sums.
- **`<failure>`** for `status == "failed"`, body = each failing evaluator's `detail`, then each
  failing check as `evaluator/check_id: evidence`, falling back to `no evidence given` — the
  same phrasing `render_console` uses, since an unsupported pass is the judge's characteristic
  failure mode and the report must not hide that a check passed on nothing.
  **`<error>`** for `status == "errored"`, body = `RunResult.error`. `message` is the first
  line of the body, truncated to 1000 characters; some CI UIs render the attribute in a
  fixed-width column.
- **Repeats:** with `report.repeat > 1`, each repetition is its own `<testcase>` with
  ` [run 2/3]` appended to `name`. JUnit consumers key on `classname`+`name` and silently
  collapse duplicates otherwise. No suffix when `repeat == 1`, so the ordinary case stays
  clean.
- **Skipped and tag-filtered skills** each get a suite holding one `<testcase>` named
  `(no eval cases)` / `(no cases matched --tag)` with a `<skipped>` child. JUnit's skipped is
  exactly this concept, and it surfaces "this skill has no coverage" in every CI UI. Skipped
  cases count toward `tests` and `skipped`, never toward `failures` or `errors`.
- **Zero cases** (§2.4): a `<testsuite name="skill-eval">` holding one `<testcase>` named
  `no eval cases ran` with an `<error>` whose body is the gate's reasons — or a generic
  message when no `gate` was passed.
- **`_xml_safe(text)`** (§2.5) strips every character outside XML 1.0's legal set —
  `\t`, `\n`, `\r`, `#x20–#xD7FF`, `#xE000–#xFFFD`, `#x10000–#x10FFFF` — before it reaches
  the tree, and is applied to every attribute value and text node.

## 4. Markdown reporter — `reporters/markdown.py`

> **Corrected 2026-08-06, after implementation.** This section originally said truncation
> "never drops... the gate reasons" (echoed in §2 Decision 6 and §12) while also describing a
> fallback that hard-truncates them — two claims that cannot both be true. It also said model
> output and judge evidence "go inside fenced code blocks" unconditionally. Both are rewritten
> below to describe what `reporters/markdown.py` actually does, so this spec stays a truthful
> record rather than reading as if it always said this. See `ARCHITECTURE.md`'s "CI surfaces
> (M5)" section and `docs/ci.md` for the user-facing version.

```python
def render_markdown(report: RunReport, gate: GateResult | None = None,
                    delta: Delta | None = None, max_chars: int | None = None) -> str: ...
```

### Shape

```markdown
## skill-eval — ❌ gate failed

**10/12 passed** · 2 failed · 0 errored · pass rate 83%

### Gate failed
- pass rate 83% is below the required 100%

| Metric | Value |
| --- | --- |
| Tokens | 3,412 |
| Cost | $0.0041 |
| Judge overhead | $0.0006 |
| Latency | 8.21s |

### Per skill
| Skill | Pass rate | Passed | Failed | Errored |
| --- | --- | --- | --- | --- |
| `pdf` | 83% | 5 | 1 | 0 |

### Delta vs baseline (previous)
Pass rate **58% → 83%** (**+25%**, higher is better)

| Metric (mean, per case) | Delta | Better when |
| --- | --- | --- |
| Tokens | −310 | negative |
| Cost | −$0.0002 | negative |
| Latency | −140ms | negative |

<details><summary>Failures (2)</summary>

... per-case detail, output and evidence in fenced blocks ...

</details>

<details><summary>Low-signal checks (3)</summary> ... </details>
<details><summary>High-variance cases (1)</summary> ... </details>
<details><summary>Baseline notes (1)</summary> ... </details>

<sub>Skipped (no eval cases): unused-skill</sub>
```

### Rules

- **The verdict line and the gate reasons come first**, before any table, so a clipped comment
  still opens with the answer (§2.6).
- **Both the emoji and the word** appear (`❌ gate failed`): the output is read in plaintext
  contexts — logs, terminals, notification digests — where an emoji alone carries nothing.
- **Cost and judge overhead stay separate rows**, matching the existing invariant that judge
  spend is harness overhead and is never charged to the skill.
- **The delta block renders only when `delta is not None`.** When `report.baseline_kind` is set
  but no delta was produced, the block is replaced by the same warning `render_console` emits,
  so a comparative run whose baseline never materialised cannot look like an ordinary one.
- **Low-signal and high-variance blocks carry the "advice, never a gate failure" sentence**
  that the console already prints. In a PR comment the temptation to read every red-looking
  item as a failure is much stronger than in a terminal.
- **Escaping:** skill and case names are pipe-escaped (`|` → `\|`) and wrapped in an inline code
  span before entering a table — GFM's table extension splits a row on `|` *before* inline
  parsing, so an escaped code span is still required even though the pipe never renders as a
  delimiter. Evaluator detail and judge evidence are model-controlled text of unknown shape:
  single-line content gets an inline code span, multi-line content gets a fenced block instead
  (so it is not squashed onto one line) — either delimiter is sized longer than the longest run
  of backticks already in the text, so the content cannot break out of it.
- **Truncation** is structural, not a single string slice, and escalates through three stages,
  each strictly more destructive than the last. The renderer first assembles the full report:
  verdict, summary, gate reasons, then every optional block (totals, per-skill table, delta,
  failures, low-signal/high-variance, skipped) in order. If that fits the budget, it is returned
  as-is — no truncation marker, because nothing was truncated. Otherwise:
  1. **Drop optional blocks from the end**, one at a time, appending
     `_Truncated — see the JSON report artifact._`, until it fits or none remain.
  2. **Still over budget: elide gate reasons from the end**, one at a time, each cut announced
     by a `- _+N more reasons — see the JSON report._` line, until it fits or zero reasons are
     shown.
  3. **Still over budget** — the verdict, the summary and that one elision line do not fit —
     **hard-cut the assembled text** to `max_chars` with a plain slice, marker included. This is
     the only stage that can truncate mid-word, and it is reached only when the budget cannot
     hold the report's irreducible minimum.
  A negative `max_chars` is clamped to `0` before any of this runs, so "nothing fits" is
  explicit rather than `s[:-5]`-style slicing from the end, which would keep nearly the whole
  report while claiming to be a ceiling. Deterministic and testable offline.

## 5. Bounded concurrency

```python
def run_evals(..., concurrency: int = 1,
              executor_factory: Callable[[int], Executor] | None = None) -> RunReport: ...
```

### Two phases

**Phase 1 — planning, sequential.** Walk skills, load cases, apply the tag filter, resolve each
baseline. This stays serial and once-per-skill: it shells out to git, and parallelising it
would multiply subprocess spawns to save nothing. It produces a flat list of work items:

```python
@dataclass(frozen=True)
class _WorkItem:
    skill: Skill              # the arm's skill (candidate or baseline)
    case: EvalCase
    runner: Runner
    arm: Arm
    repeat_index: int
    report_skill_name: str
```

Building the list is where today's five nested loops go, so their ordering — skill, case, arm,
runner, repetition — is preserved exactly.

**Phase 2 — execution.** `_run_one` over each work item.

- **`concurrency == 1` never constructs an executor** and runs the plain loop (§2.8). Not an
  optimisation: it is what makes the default path byte-identical, keeps the cassette tier
  (vcrpy is order-sensitive and not thread-safe) deterministic, and keeps exception
  propagation exactly as it is today.
- **Above 1**, `executor_factory(concurrency)` — defaulting to
  `ThreadPoolExecutor(max_workers=n, thread_name_prefix="skill-eval")` — receives every work
  item via `submit`. Futures are kept in submission order and read in that order (§2.9), so
  `report.outcomes` is identical to the sequential result regardless of what finishes first.
- **Authoring errors** (§2.10): `future.result()` re-raises in the calling thread, so
  `UnknownAssertionKind` and friends still propagate out of `run_evals` to the CLI's
  `_AUTHORING_ERRORS` handler and exit 2. Reading in submission order makes the surfaced error
  deterministic. On the first exception the executor is shut down with
  `shutdown(wait=False, cancel_futures=True)` rather than the `with` block's implicit
  `wait=True`, so an abort does not block on every in-flight provider call.
- **Thread safety is a property of the components, and holds today:** `FakeRunner` and
  `FakeJudge` read immutable dicts and return `model_copy(deep=True)`; `AssertionEvaluator`,
  `TrajectoryEvaluator` and `BudgetEvaluator` have no instance state at all; `JudgeEvaluator`
  holds only its judge; `PydanticAIRunner` holds configuration and builds a fresh `Agent` per
  run. Nothing shared is mutated. This is a constraint on future runners and evaluators, and
  belongs in `ARCHITECTURE.md`.
- **`concurrency < 1`** raises `ValueError` from `run_evals` and `typer.BadParameter` from the
  CLI, mirroring `repeat`.

Concurrency parallelises evaluation too, since evaluators run inside `_run_one` — so a judged
suite overlaps its judge calls as well as its runner calls. That is intended; judge spend is
still reported apart from run spend.

## 6. Config & CLI

One new config key. Output destinations stay CLI-only, matching `--json-output`.

```toml
# skill-eval.toml
concurrency = 1   # default: sequential. Raise it for real-runner suites; the
                  # practical ceiling is the provider's rate limit, not the CPU.
```

New flags on `run`:

| Flag | Default | Notes |
| --- | --- | --- |
| `--junit-output PATH` | unset | Writes JUnit XML. |
| `--markdown-output PATH` | unset | Writes the Markdown summary. |
| `--markdown-max-chars N` | unset | Truncate the Markdown to fit a comment. |
| `--concurrency N` | `Config.concurrency` | Overrides the config value. |

**Write failures** follow the rule `--json-output` already established, factored into one
helper now that three files can be written: report each failure on its own line, and escalate
to exit 2 **only if the gate itself passed** — a write problem must never mask an already-red
gate.

## 7. The GitHub Action — `action.yml`

A composite action at the repository root.

**Inputs.** Every `run` flag, kebab-cased (`path`, `evals`, `runner`, `model`, `judge-model`,
`tag`, `config`, `baseline`, `repeat`, `min-pass-rate`, `min-delta`, `concurrency`,
`json-output`, `junit-output`, `markdown-output`, `markdown-max-chars`), plus three that are
about the *environment* rather than the run:

- `install-spec` (default `skill-eval[pydantic-ai]`) — passed verbatim to `uv tool install`.
  One input covers a PyPI release, a pinned version, a git ref or a local path. This is also
  what lets the action work before Part 2 publishes to PyPI: pin
  `git+https://github.com/EmadMokhtar/skill-evaluator@main`.
- `working-directory` (default `.`)
- `step-summary` (default `true`) — append the Markdown to `$GITHUB_STEP_SUMMARY`.

`json-output`, `junit-output` and `markdown-output` default to real paths rather than unset,
because the action needs the JSON to produce its outputs. `markdown-max-chars` stays unset by
default even here (§2.7) — the *workflow* supplies it when it intends to post a comment, since
the step summary allows 1 MiB and truncating it by default would lose detail for nothing.

**Outputs:** `exit-code`, `passed`, `pass-rate`, `json-report`, `junit-report`,
`markdown-report`.

**Steps.** Install uv → `uv tool install "$install-spec"` → run the CLI with `set +e`, capturing
the exit code → append the Markdown to the step summary → read `summary.pass_rate` and
`gate.passed` out of the JSON for the outputs → `exit $code` (§2.11). The JSON-reading step
must tolerate a **missing** file: exit 2 means an authoring error aborted before any report was
written, and the action must still report `exit-code` rather than dying in `jq`.

### Example workflows

Real files under `examples/ci/`, pulled into `docs/ci.md` with `pymdownx.snippets` so
documented YAML cannot drift from YAML that exists. `examples/` is inert — only
`.github/workflows/` executes.

- **`examples/ci/skill-eval.yml`** — the action, plus a sticky PR comment guarded by
  `if: github.event.pull_request.head.repo.full_name == github.repository`. GitHub withholds
  write tokens from fork-triggered `pull_request` runs, so an unguarded comment step fails on
  exactly the PRs an open-source project gets most. Fork PRs still get the step summary, the
  JUnit rendering and the correct exit code — the substance. `docs/ci.md` documents the
  `workflow_run` two-workflow pattern as the upgrade for repos that need comments on forks, and
  says plainly that `pull_request_target` is not it.
- **`examples/ci/skill-eval-cli.yml`** — the same gate without the action, for people who want
  the raw CLI.

### Guarding the two surfaces

`tests/test_action.py` parses `action.yml` and introspects the Typer command:

1. Every CLI-backed action input maps to a real `run` option.
2. Every `run` option is exposed as an action input, except an explicit allowlist.

Direction 2 is the one that matters: it fails the build when a future flag is added and the
action is forgotten. Direction 1 catches typos and removed flags.

A CI job also runs the action against a tiny fixture skill with `runner: fake` and asserts the
three report files appear and the exit code is 0. `FakeRunner` returns a default response for
unscripted cases, so this is a genuine end-to-end smoke test at zero cost — and it catches the
composite-action mistakes (a missing `shell: bash`, a bad `${{ inputs.x }}`) that no
YAML-parsing unit test can see.

## 8. Testing

Everything below is offline, deterministic and free.

| File | Covers |
| --- | --- |
| `tests/test_junit_reporter.py` | `failed`→`<failure>` / `errored`→`<error>`; candidate-only; repeat suffixes and their absence at `repeat == 1`; skipped-skill suites; the zero-case synthetic error; `_xml_safe` on control characters; hostile names (`<`, `&`, quotes) surviving a round trip through `ElementTree.fromstring`; time conversion. |
| `tests/test_markdown_reporter.py` | Verdict, totals, per-skill table, delta block, the no-delta-but-baseline warning; low-signal/high-variance advisory sentence; `\|` escaping in names; fenced blocks around output containing backticks; truncation dropping optional blocks while keeping the verdict and every gate reason; truncation of an over-budget essential block. |
| `tests/test_orchestrator.py` | `concurrency=4` produces outcomes identical to `concurrency=1`, ordering included; authoring errors still propagate and are deterministic; `concurrency < 1` rejected; a custom `executor_factory` is used when passed and *not* constructed at `concurrency == 1`. |
| `tests/test_cli.py` | New flags write their files; exit codes unchanged by their presence; the write-failure rule for each; `--concurrency 0` exits 2. |
| `tests/test_action.py` | Both drift directions between `action.yml` inputs and `run` flags. |
| `tests/test_config.py` | `concurrency` parsed and defaulted. Validation lives in the CLI, not the model, mirroring how `repeat` resolves config-then-flag before checking — so a `concurrency = 0` in `skill-eval.toml` is rejected by the CLI test above, not here. |
| `tests/test_docs.py` | New flags and the config key appear in `docs/cli.md` / `docs/configuration.md`. |

## 9. Per-skill thresholds — already shipped

`Config.per_skill_min`, both gating branches (below-bar, and named-but-produced-no-results
including the skipped-skill wording), `tests/test_gating.py`, `tests/test_config.py` and
`docs/configuration.md` all exist and are current. M5 records this as verified. No code change.

## 10. Part 2 — release automation (separate PR)

Specified here so the milestone is whole, implemented after the external setup exists.

- **`release.yml`** — on push to `main`: `cz bump`, commit the version and changelog back,
  push the `vX.Y.Z` tag. No-op when no commit warrants a bump. The bump commit carries a skip
  marker so it cannot re-trigger the job; a `concurrency:` guard serialises releases. Needs a
  credential that can push to protected `main` — `GITHUB_TOKEN` cannot bypass branch
  protection.
- **`publish.yml`** — on tag: `uv build`, publish to PyPI via Trusted Publishing (OIDC, no
  stored token), gated by a protected `pypi` environment.
- **`refresh-cassettes.yml`** — `workflow_dispatch` only, holds `OPENAI_API_KEY`, runs
  `pytest tests/test_cassettes.py --record-mode=once`, opens a PR with the updated cassettes.
  Keeps replay truthful without a key in the normal CI path.

Part 2 also moves the `v1` floating tag that `uses: EmadMokhtar/skill-evaluator@v1` resolves
to; until then the action is used via `uses: ./` or a pinned SHA.

## 11. Documentation

| Change | Update |
| --- | --- |
| `--junit-output`, `--markdown-output`, `--markdown-max-chars`, `--concurrency` | `docs/cli.md` |
| `concurrency` | `docs/configuration.md` |
| JUnit's failure/error mapping, the zero-case error, skipped suites | `docs/gating.md` |
| The action, its inputs and outputs, both example workflows, the fork-PR caveat and the `workflow_run` upgrade | `docs/ci.md` (new page + `nav:`) |
| Reporter list, the concurrency phases, the new invariants, the thread-safety constraint on future runners and evaluators | `ARCHITECTURE.md` |
| M5 status | `docs/roadmap.md` |
| Milestone paragraph and invariant list | `CLAUDE.md` |

## 12. Invariants this milestone must not break

New:

1. **JUnit reports the candidate arm only.** A baseline failure is evidence the skill helped,
   not a red build.
2. **`<failure>` is `failed`; `<error>` is `errored`.** The distinction survives into the CI UI.
3. **JUnit output is always well-formed XML.** Illegal characters are stripped, never emitted.
4. **A zero-case run produces a JUnit error, not an empty green suite.**
5. **Markdown truncation gives up detail before meaning, and never hides how much it gave up.**
   Optional blocks are dropped first; gate reasons that still do not fit are elided behind a
   truthful `+N more reasons` count rather than being cut in silence; only a budget too small to
   hold the verdict itself falls back to a hard character cut. (Corrected 2026-08-06 — see §4.)
6. **`--concurrency 1` constructs no executor and is byte-identical to a sequential run.**
7. **Outcome order is submission order, never completion order.**
8. **Concurrency never turns an authoring error into a case failure**, and the surfaced error
   is deterministic.
9. **Runners, judges and evaluators must be safe to share across threads** — no mutable
   instance state touched by `run`/`evaluate`/`judge`.
10. **Reporters never do IO to a service.** The tool renders; the workflow posts.
11. **The action re-raises the CLI's exit code**, and writes the step summary before it does.

Preserved, and re-checked by this milestone's tests: `errored` ≠ `failed`; a zero-case run
fails the gate; authoring errors abort rather than scoring as failures; exit codes 0/1/2; a
write failure escalates to 2 only when the gate passed; judge spend never enters `RunResult`;
baseline outcomes never reach a gate aggregate; `skill_eval` never appears in user-facing
output.
