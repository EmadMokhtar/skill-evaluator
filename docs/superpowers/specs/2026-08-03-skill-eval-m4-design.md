# skill-eval M4 — Design

**Date:** 2026-08-03
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M4)

## 1. Scope

M1–M3 answer "did this run score well?". That question cannot separate **the skill worked**
from **the model would have done it anyway**. M4 makes every measurement comparative: each
case runs in two **arms** — with the skill (candidate) and without it, or against its previous
version (baseline) — optionally sampled `N` times, and the report gains the difference.

- **Arms** — the orchestrator runs each case once per arm, and `CaseOutcome` records which.
- **`skills/baseline.py`** — resolves "the previous version" from git, driven by the skill's
  declared `version:`.
- **Repeats** — `--repeat N` samples each arm `N` times; each repetition is its own outcome.
- **Per-check verdicts from deterministic evaluators** — Assertion, Trajectory and Budget
  start emitting one `CheckResult` per declared item, which is what makes a *check* (not just
  a case) comparable across arms.
- **`comparison.py`** — the delta block, plus **low-signal checks** and **high-variance
  cases**.
- **`--min-delta`** — CI can require that a `SKILL.md` edit actually improved something.

### Explicitly deferred

| Deferred | Milestone | Why |
| --- | --- | --- |
| Per-skill `min_delta` | later | `per_skill_min` exists and can be mirrored in an afternoon once one repo actually has two skills whose deltas diverge. Adding it now doubles the gating surface to document and test for a case we have not met. |
| Efficiency regression gates (`--max-token-regression`) | later | Overlaps `budget:`, which already gates absolute efficiency per case. Choosing the unit and threshold deserves evidence from reported deltas first — which M4 produces. |
| `--baseline-ref <rev>` | later | Version-driven history resolution (§4) covers the CI story. An explicit ref is the escape hatch to add when a real repo layout defeats it, not before. |
| Flagging checks that **fail** in both arms | later | Genuinely different from low-signal: it means the skill does not help with that check, which is a true finding, not suite hygiene. It wants its own presentation, not a footnote on this one. |
| Bounded concurrency across arms and repeats | M5 | M4 multiplies wall-clock by `2N`. Concurrency is already M5's, and adding it here would entangle two independent changes. |
| Offering more than one skill at a time | later | M3 deferred this to "M4's multi-skill report shape". That shape turned out to be about *arms*, not about competing skills, so discrimination between skills stays deferred. |

## 2. Decisions

1. **Absent `--baseline` means today's behavior, exactly.** One arm, no delta block, console
   output byte-identical. `none` names a *kind of baseline* (an empty skill), not the absence
   of comparison, so the flag being unset is the only thing that turns comparison off.
   Upgrading to M4 must not silently double anyone's bill.
2. **The baseline arm never sees the skill's name.** `_system_prompt` today emits `# {name}`
   as a header; for an empty baseline that leaks `# refund-handler` into the prompt and the
   delta would then measure the leak. A skill with neither description nor instructions gets a
   neutral preamble instead. The rule keys on emptiness, not on the arm, so no runner needs to
   know which arm it is serving — and a runner that *could* branch on the arm could cheat.
3. **"Previous version" is defined by `version:`, not by `HEAD`.** If the candidate's version
   was already committed, `HEAD` is the candidate and the delta is zero against itself. The
   resolver walks history for the newest commit whose version differs. Unversioned skills fall
   back to the newest commit whose *content* differs — the same intent, weaker evidence.
4. **Each repetition is its own outcome.** At the default `min_pass_rate = 1.0` this is
   identical to "all repetitions must pass"; below 1.0 it degrades proportionally instead of
   treating 4/5 and 0/5 alike. Variance is reported separately rather than being smuggled into
   the pass rate.
5. **Baseline outcomes never count toward the gate.** A strong baseline means the skill was
   unnecessary, not that CI should go red. Every existing gate rule — `min_pass_rate`,
   `per_skill_min`, `fail_on_error`, zero-cases — reads the candidate arm only.
6. **A baseline that cannot be resolved is reported, never assumed.** Treating an unresolvable
   baseline as "no change" would let a repo pass `--min-delta` forever by deleting its git
   history. With `--min-delta` set it fails the gate; without it, it is a note.
7. **An errored baseline run invalidates that case's delta, in both arms.** The delta is a
   *paired* measurement; keeping the candidate half of a broken pair silently biases the
   result. Baseline errors are counted and displayed on their own so they cannot hide, but
   they are not `report.errored` — that number is about the candidate.
8. **Low-signal and high-variance are advisory.** They are diagnostics about the *eval suite*,
   not verdicts on the skill. A flag that blocks a merge for "this assertion is weak" trains
   people to ignore flags, and a flaky provider would be indistinguishable from a bad skill.
9. **`--min-delta` without `--baseline` is a user error (exit 2).** The alternative is a gate
   that silently checks nothing — the same vacuous-pass failure mode the parent spec has
   rejected everywhere else.
10. **Deterministic evaluators emit per-check verdicts.** Comparing whole evaluators across
    arms is too coarse to name the dead-weight assertion, and naming it is most of the value.
    The `checks` field already exists on `EvalScore` for the judge; no new model shape is
    needed, and the console's per-check rendering starts working for them for free.

## 3. Arms and the baseline skill

```python
Arm = Literal["candidate", "baseline"]
BaselineKind = Literal["none", "previous"]
```

`Skill` gains two fields:

```python
version: str = ""                  # parsed from SKILL.md frontmatter; absent today
variant: Arm = "candidate"
```

`version` is read from frontmatter exactly like `name` and `description` — absent means `""`,
never an error. `variant` is set by the orchestrator, is what `FakeRunner` scripts against,
and is what reporters label arms with.

The baseline `Skill` is built per skill, once per run:

| `--baseline` | Baseline skill |
| --- | --- |
| `none` | `Skill(name=skill.name, description="", instructions="", version="", path=skill.path, variant="baseline")` |
| `previous` | The prior version parsed from git (§4), with `variant="baseline"` |

The name is kept for grouping in the report. It cannot reach the prompt: `_system_prompt`
returns a neutral preamble when both description and instructions are empty.

```python
BASELINE_PREAMBLE = "You are a helpful assistant."
```

The case's mock tools are unaffected — they are the environment the *case* declares, not part
of the skill, so both arms get the same tools and the comparison stays honest.

### `mode: offered` cases

| `--baseline` | Behavior |
| --- | --- |
| `previous` | Both arms run. The baseline offers the **old** name and description; the trigger-rate delta is how a description edit is measured, and it is the highest-value delta this milestone produces. |
| `none` | Candidate-only. There is no skill to offer, so `skill_triggered` is false by construction; running it would spend real money to prove a tautology and would report the structural artifact as "the skill helped 100%". |

A skipped baseline is recorded as a note on the report and excludes that case from the delta.
It is never counted as a baseline failure.

## 4. Resolving `previous` — `skills/baseline.py`

```python
def resolve_previous(skill: Skill) -> Skill | BaselineUnavailable: ...
```

The walk, rooted at the skill's directory:

1. `git -C <dir> rev-parse --show-toplevel` — establishes the repo and the path to use.
2. `git -C <dir> log --format=%H -- <SKILL.md>` — commits touching this file, newest first.
3. For each commit, up to a bounded number of them: `git show <sha>:<relpath>`, parse it, and
   accept the first that qualifies —
   - the working copy declares `version:` → the first whose frontmatter `version` **differs**;
   - it does not → the first whose **content** differs.

This requires factoring `parse_skill_text(text, *, name_fallback, path)` out of
`parse_skill_file`, so a blob from git and a file on disk parse through one code path.

`BaselineUnavailable` carries a reason and is **returned, never raised** — the same discipline
runners follow for provider failures:

| Reason |
| --- |
| `git` is not installed |
| not a git repository |
| `SKILL.md` is not tracked by git |
| no earlier version found within the searched history |

Subprocesses run without a shell, decode as UTF-8, and carry a timeout so a hung `git` cannot
hang CI. Resolution happens **once per skill**, not per case or per repetition.

## 5. Per-check verdicts from deterministic evaluators

Assertion, Trajectory and Budget each emit one `CheckResult` per declared item, alongside the
`passed`/`score`/`detail` they already produce. Ids are derived from the **case**, never from
the result, so the same ids appear in both arms and pair up:

| Evaluator | Check ids |
| --- | --- |
| Assertion | `{kind}[{index}]` — `contains[0]`, `regex[2]`, positionally stable |
| Trajectory | `called:{tool}`, `forbidden:{tool}`, `order`, `max_calls`, `skill_triggered` |
| Budget | `max_tokens`, `max_cost_usd`, `max_latency_ms` |

Every emitted check carries evidence (the same text that would have gone into `detail`). The
"a pass without evidence is a failure" rule stays local to `JudgeEvaluator`, where the failure
mode it defends against lives; it is not promoted to `CheckResult`.

Existing behavior is unchanged: a budget cost limit that cannot be priced still yields a
failing case, now visible as a `max_cost_usd` check whose evidence says the limit was not
priced rather than as an unexplained red case.

## 6. `comparison.py`

```python
def build_delta(report: RunReport) -> Delta | None      # None when no baseline arm ran
```

```python
class ArmStats(BaseModel):
    runs: int; errored: int; passed: int
    pass_rate: float; stddev: float                     # population stddev of per-run 0/1
    mean_tokens: float; mean_cost_usd: float; mean_latency_ms: float

class CaseStats(BaseModel):
    skill_name: str; case_name: str; runner: str
    candidate: ArmStats
    baseline: ArmStats | None
    comparable: bool                                    # false → excluded from the delta
    exclusion_reason: str = ""
    low_signal: list[str] = []                          # check ids
    high_variance: bool = False

class LowSignalCheck(BaseModel):
    skill_name: str; case_name: str; check_id: str; evaluator: str

class CaseRef(BaseModel):
    skill_name: str; case_name: str; runner: str; arm: Arm
    pass_rate: float; stddev: float

class Delta(BaseModel):
    baseline_kind: BaselineKind
    pass_rate_candidate: float; pass_rate_baseline: float; pass_rate_delta: float
    tokens_delta: float; cost_usd_delta: float; latency_ms_delta: float
    cases: list[CaseStats]
    low_signal: list[LowSignalCheck]
    high_variance: list[CaseRef]
    notes: list[str]                                    # unresolved / skipped baselines
```

All deltas are **candidate − baseline**. For pass rate, higher is better; for tokens, cost and
latency, negative is better. The reporters say so rather than leaving a reader to guess at a
sign.

**Pairing is the invariant.** A case is comparable only when both arms produced at least one
non-errored repetition. Anything else — baseline skipped (offered + `none`), baseline
unresolvable, every repetition of an arm errored — excludes the case from **both** halves of
the delta, with the reason recorded. Aggregate rates are computed over comparable cases'
outcomes only.

**Low-signal check** — a check id that passed in *every* candidate repetition **and** every
baseline repetition of a comparable case. It contributes to the with-skill score while
measuring nothing about the skill.

**High-variance case** — a (case, arm) whose repetitions are not unanimous. Reported with its
pass rate and stddev; only meaningful at `--repeat > 1`. An unstable pass rate usually points
at ambiguous skill instructions.

## 7. Model changes — all additive

| Model | Change |
| --- | --- |
| `Skill` | `version: str = ""`, `variant: Arm = "candidate"` |
| `CaseOutcome` | `arm: Arm = "candidate"`, `repeat_index: int = 0` |
| `RunReport` | `baseline_kind: BaselineKind \| None = None`, `repeat: int = 1`, `baseline_notes: list[str] = []` |

`RunReport` gains `candidate_outcomes` / `baseline_outcomes` properties, and
`total`, `passed`, `failed`, `errored`, `pass_rate`, `pass_rate_by_skill` all read the
**candidate** arm (Decision 5). With no baseline the two sets are identical, so nothing about
today's numbers moves. A new `baseline_errored` property surfaces Decision 7's hidden count.

`judge_cost_usd` keeps summing every evaluator's spend across **both** arms — that is real
money spent, and under-reporting it would be the worse failure.

Every user-authored model keeps `extra="forbid"`; the new fields are all harness-set.

## 8. Orchestrator

```python
def run_evals(..., baseline: BaselineKind | None = None, repeat: int = 1) -> RunReport
```

Per skill: resolve the baseline skill once, then

```
for case in cases:
    for arm in arms_for(case, baseline):        # skips the baseline arm per §3
        for i in range(repeat):
            outcomes.append(_run_one(..., arm=arm, repeat_index=i))
```

`_run_one` is unchanged apart from stamping `arm` and `repeat_index`. `CaseOutcome.repeat_index`
is a 0-based index of one repetition; `RunReport.repeat` is how many were requested. The names
differ because confusing the two is how an off-by-one reaches a report. Scoring is identical in both
arms — a delta between differently-scored arms would be meaningless.

Authoring errors still abort the run and are never scored as failures. A `BaselineUnavailable`
is *not* an authoring error: it is an environmental fact about the repo, so it becomes a note
plus (under `--min-delta`) a gate reason.

## 9. Gating

`evaluate_gate` gains `min_delta: float | None = None` and, when set:

- fails when `pass_rate_delta < min_delta`;
- fails when **no** case was comparable, naming why (mirroring the existing "no eval cases
  ran" rule — a check that verified nothing is a broken run, not a pass);
- fails when a skill's baseline could not be resolved, naming the skill and the reason.

A deliberately skipped baseline (an `offered` case under `--baseline none`, §3) is **not** a
gate reason on its own — nothing went wrong. It still excludes the case from the delta, so a
suite made entirely of such cases fails through the no-comparable-case rule above, which is
the honest reason.

Every pre-existing rule is untouched, and now reads the candidate arm. Flags never affect the
exit code. Exit codes stay the CI contract: `0` pass, `1` gate fail, `2` user/authoring error.

## 10. Config & CLI

```toml
baseline = ""      # "" (off) | "none" | "previous"
repeat = 1
# min_delta is optional and has no default: omit it and the delta is reported but
# not gated. Setting it to 0.0 is a real, stricter choice — "must not regress".
min_delta = 0.05
```

New flags, each overriding config: `--baseline`, `--repeat`, `--min-delta`.

Rejected at parse time with exit 2, against the **resolved** values (config and flags merged,
so a `baseline` set in `skill-eval.toml` satisfies a `--min-delta` passed on the command line):

- a `min_delta` with no baseline (Decision 9);
- `--repeat` below 1;
- a `--baseline` value outside `none|previous`.

Before starting, when the selected runner needs an API key, the CLI prints the run plan:

```
Plan: 2 arms x 3 repeats x 4 cases = 24 runs
```

`--repeat 5 --baseline previous` is a 10x bill and must not be a surprise.

## 11. Reporters

**Console.** At `repeat == 1` with no baseline, output is byte-identical to M3 — a golden test
holds that line. Otherwise per-case lines collapse to one line per (case, arm):

```
[PASS] order-support :: refund-request (pydantic-ai)  candidate 5/5  baseline 2/5  +60%
        low-signal: contains[0]
        high-variance: baseline 2/5 (stddev 0.49)
```

followed by a delta block naming the arms it compared, and the baseline notes:

```
Delta vs baseline (previous: 1.2.0 -> 1.3.0)
  pass rate   80% -> 100%     +20%
  tokens      1,240 -> 1,180  -60      (negative is better)
  cost        $0.0031 -> $0.0029  -$0.0002
  latency     1.4s -> 1.3s    -0.1s
```

**JSON.** Each outcome gains `arm` and `repeat_index`; the payload gains a top-level `delta` block
(the `Delta` model above) and `baseline` metadata — kind, the version resolved per skill, and
the notes. Absent a baseline, `delta` is `null` and the rest of the document is unchanged.

## 12. Testing

**Tier 1 — pipeline (every PR, zero cost, no HTTP).**
- `resolve_previous` against real temporary git repos: versioned hit, unversioned
  content-diff hit, untracked file, non-repo directory, history exhausted, `git` missing.
  Offline and fast; `git` is already a prerequisite for working in this repo.
- Baseline skill construction, and the no-name-leak rule proven where it matters — through
  `PydanticAIRunner` with PydanticAI's `FunctionModel`, asserting the skill's name and
  description are absent from the system prompt of a `--baseline none` run.
- Offered + `none` skips the baseline arm; offered + `previous` runs both.
- Per-check ids and evidence for all three deterministic evaluators, including id stability
  across arms.
- `build_delta`: pairing and every exclusion reason, low-signal detection, variance flagging,
  sign conventions, and the "no comparable case" path.
- Gating: `min_delta` pass/fail, no-comparable-case, unresolved baseline, and that baseline
  outcomes move none of the existing numbers.
- CLI: the three parse-time rejections, and the run-plan line.
- Reporters: the byte-identical golden for default mode, plus comparative console and JSON.
- Config: the three new fields, including an invalid `baseline` value raising `ConfigError`.

`FakeRunner` gains optional arm-keyed scripting, consulted via `skill.variant` and falling
back to today's task-keyed responses. It still returns `model_copy(deep=True)`.

**Tier 2 — cassettes (every PR, real fidelity, zero cost).** One recorded two-arm run. M4 adds
no new *kind* of provider interaction, but the baseline prompt is a new prompt shape, and the
cassette is what proves it reaches the provider without the skill's name in it. Replay-only,
skip-if-absent, secret-scrubbed, as in M2.

**Tier 3 — live integration (opt-in, real money).** The `examples/` suites with
`--baseline previous --repeat 3`.

## 13. Examples

Both example skills gain `version:` frontmatter so the version-driven resolution path is
dogfooded rather than only unit-tested, and so the repo's own history provides a real previous
version to compare against. CI's dogfood step stays `skill-eval list ./examples` — still
zero-cost, now also exercising `version:` parsing on real files.

## 14. Invariants this milestone must not break

- **Absent `--baseline`, M4 behaves exactly as M3** — one arm, no delta, identical console
  output, identical JSON apart from additive nulls.
- **Baseline outcomes never count toward the gate's pass rate**, and never toward `errored`.
- **The baseline arm never receives the skill's name, description or instructions** under
  `--baseline none`.
- **A baseline that cannot be resolved is reported, never assumed to be "no change"**; with
  `--min-delta` it fails the gate.
- **The delta is paired**: a case excluded from one arm is excluded from both.
- **`--min-delta` without `--baseline` is a user error (exit 2)** — a gate that checks nothing
  must never report a pass.
- **A run that gates on a delta and finds no comparable case fails**, exactly as a run
  executing zero cases does.
- **Low-signal and high-variance never change the exit code.**
- `errored` ≠ `failed`, for runners and evaluators alike.
- Runners and judges never raise for provider failures; `resolve_previous` never raises for
  environmental ones.
- Authoring errors abort the run and exit 2. A missing git history is not an authoring error.
- Exit codes: pass `0`, gate fail `1`, user/authoring error `2`.
- `extra="forbid"` on every user-authored model; all file IO pins `encoding="utf-8"`; YAML
  goes through `yaml_loading.safe_load`.
- Secrets from environment variables only.
- `skill_eval` (underscore) never appears in user-facing output.
- Agent-framework types appear in exactly two modules: `runners/pydantic_ai.py` and
  `judges/pydantic_ai.py`. `skills/baseline.py` shells out to `git` and imports neither.
