# skill-eval M3 — Design

**Date:** 2026-08-03
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9 M3)

## 1. Scope

M2 proved the pipeline can run a real agent and score what it did. M3 adds the two things
that cannot be checked deterministically: **whether the answer was any good**, and **whether
the agent reached for the skill at all**.

- **`judges/`** — a third protocol seam, alongside `Runner` and `Evaluator`, so the judge can
  call an LLM without a framework type leaking into `evaluators/`.
- **`JudgeEvaluator`** — rubric-based scoring with **per-check verdicts carrying evidence**.
- **A `judge:` block on eval cases** — free-text `expected:` plus a natural-language `rubric:`.
- **`offered` case mode** — the skill is a tool the agent may choose rather than a system
  prompt it cannot refuse, making the triggering decision observable.
- **`trajectory.skill_triggered`** — the triggering check, with **negative controls**.
- **Evaluator-level `errored`** — the orchestrator learns that an evaluator, not just a
  runner, can report an infra failure.

### Explicitly deferred

| Deferred | Milestone | Why |
| --- | --- | --- |
| Judge N-sample majority vote | later | Already deferred by the parent spec §7. Temperature 0 plus evidence-bearing per-check output is the cheaper half of the reliability story; sampling triples judge spend for a gain we have not yet measured. |
| Judge sight of the transcript / trajectory | later | A rubric about *what the agent did* is `TrajectoryEvaluator`'s job, and it answers deterministically for free. Feeding the transcript to the judge invites a paid, fuzzy verdict on a question already answered exactly. |
| Offering more than one skill at a time | M4+ | Discrimination between skills ("did it pick the *right* one?") needs the multi-skill report shape M4 builds for baselines. M3 offers only the skill under test. |
| Baseline / repeat / delta over judge scores | M4 | Unchanged from M2's deferral. |

## 2. Decisions

1. **The judge gets its own seam, not a widened invariant.** `evaluators/` stays
   framework-free; `judges/pydantic_ai.py` becomes the second — and last — module allowed to
   import an agent framework. This mirrors `Runner` exactly and keeps a scripted `FakeJudge`
   available for the zero-cost test tier.
2. **skill-eval computes the verdict; the judge only supplies evidence.** The judge returns
   per-check `{id, passed, evidence}` and nothing else. `passed` and `score` are derived
   locally, the same way `AssertionEvaluator` and `TrajectoryEvaluator` derive theirs. Asking
   a model for a blended number is precisely the failure the parent spec warns about.
3. **A pass without evidence is not a pass.** An unsupported PASS is the judge's
   characteristic failure mode, so it gets a mechanical defence rather than a prompt asking
   nicely.
4. **A malformed verdict is `errored`, not `failed`.** Structured output that does not match
   the rubric is the harness misbehaving, not evidence about the skill.
5. **Offering a skill means offering it as a tool that returns its instructions.** Anything
   less makes the rest of an offered run fiction — an agent acting on a skill it never
   received.
6. **The runner records `skill_triggered`, the evaluator scores it.** `Evaluator.evaluate`
   takes `(case, result)` and has no `Skill`, so the component that knows what it offered is
   the one that reports whether it was taken.
7. **Judge spend is never charged to the skill.** `BudgetSpec` measures the skill's
   efficiency; judging is harness overhead and is reported separately.
8. **Real judging is opted into explicitly**, like `--runner pydantic-ai`, and an
   unconfigured judge errors rather than passing. M3 must not make `skill-eval run` start
   spending money on its own, and must not let a rubric score green without being judged.

## 3. The `judges/` seam

```
judges/base.py         Judge protocol: judge(JudgeRequest) -> JudgeVerdict
judges/prompt.py       renders a JudgeRequest into prompt text (framework-neutral)
judges/fake.py         FakeJudge — scripted, offline, zero-cost
judges/pydantic_ai.py  PydanticAIJudge — the only new module importing the framework
```

```python
@runtime_checkable
class Judge(Protocol):
    name: str
    def judge(self, request: JudgeRequest) -> JudgeVerdict: ...
```

`JudgeRequest` is a flat value object, not `(case, result)`:

```python
class RubricCheck(BaseModel):     # one thing to verify
    id: str
    text: str

class JudgeRequest(BaseModel):
    task: str
    output: str
    expected: str = ""
    checks: list[RubricCheck]
```

A judge therefore knows nothing about eval-case shape: it grades an output against a list of
checks. That keeps `FakeJudge` trivial, keeps the prompt renderer testable in isolation, and
leaves the seam reusable if a later milestone judges something other than a case output.

```python
class JudgeVerdict(BaseModel):
    checks: list[CheckResult] = []
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_note: str = ""
    model: str = ""
    error: str | None = None
```

**Judges never raise.** Provider failures, exhausted retries, and unparseable output all come
back as `JudgeVerdict(error=...)`, exactly as `RunResult.error` works for runners.

### `PydanticAIJudge`

Temperature 0, PydanticAI structured output typed to the verdict shape, and the same
transient-retry policy M2 built for the runner (`retries`, `retry_backoff_seconds`,
bounded exponential backoff, the same transient-status set). Cost is priced through the
existing `runners/pricing.py` helpers and degrades to `0.0` plus a `cost_note` rather than
raising — unchanged invariant.

### `FakeJudge`

Mirrors `FakeRunner`: scripted verdicts keyed by `JudgeRequest.task`, with an optional
default, returned as a deep copy so a caller cannot corrupt scripted state.

**Unscripted, it returns `JudgeVerdict(error="no judge configured; set judge = ...")`.** This
is the safety property that lets `judge = "fake"` be the built-in default without creating a
vacuous green: a case that declares a rubric and configures no real judge comes back
`errored`, never `passed`. Same reasoning as M2's unpriced-cost-limit rule — nothing was
verified, so nothing may be reported as verified.

## 4. `JudgeEvaluator`

`name = "judge"`. Holds only rubric logic; the `Judge` arrives by constructor injection.

```
evaluate(case, result):
  case.judge is None                  -> vacuous pass, detail "no judge checks"
  build JudgeRequest from case.judge.expected, case.judge.rubric, case.task, result.output
  verdict := judge.judge(request)
  verdict.error                       -> EvalScore(errored=True, passed=False, score=0.0)
  ids(verdict) != ids(request)        -> EvalScore(errored=True, passed=False, score=0.0)
  check.passed and not check.evidence -> recorded as failed, reason in its detail
  passed := all checks passed;  score := fraction passed
```

Rubric entries are plain strings. Ids are generated positionally as `r1..rN`, so authors need
not invent identifiers and each verdict still maps back to the check it graded by id rather
than by the order the model happened to emit them in.

`EvalScore.checks` carries the per-check results through to the reporters, so the evidence
reaches the JSON report rather than being flattened into a sentence.

## 5. Triggering

### The `offered` mode

```
mode = "loaded"   (default, unchanged)
    system prompt := the skill, as M2 built it
    tools         := the case's mock tools

mode = "offered"
    system prompt := a neutral assistant preamble; the skill body does not appear
    tools         := the case's mock tools
                   + one synthetic tool:
                        name        = the skill's name, sanitised to an identifier
                        description = the skill's frontmatter description
                        parameters  = none
                        returns     = the skill's instructions
```

Calling the synthetic tool delivers the instructions, so the agent only has the skill when it
chose to have it, and the remainder of the run proceeds realistically with it loaded. This
mirrors how progressive-disclosure skill harnesses actually behave, and it means triggering
is a plain `ToolCall` in the trajectory rather than a new capture mechanism.

Name sanitisation (`order-support` → `order_support`) lives in `runners/tools.py` beside the
other framework-neutral tool construction, as a single deterministic function both the runner
and the case loader call.

The synthetic tool appears in `result.tool_calls` like any other, so **it counts toward
`trajectory.max_calls`**. Documented rather than special-cased: the message history stays the
authoritative record of what the model asked for.

### The check

```yaml
  - name: does not fire on unrelated questions   # negative control
    mode: offered
    task: What's the weather in Cairo?
    trajectory:
      skill_triggered: false
```

`RunResult.skill_triggered: bool | None` — set by the runner (`None` outside offered mode),
compared by `TrajectoryEvaluator` against `TrajectorySpec.skill_triggered`. It joins
`called` / `forbidden` / `order` / `max_calls` as one more check, counted in the same
fraction.

**Negative controls are the point.** A positives-only suite scores a skill that fires on
everything at 100%, so `examples/` ships both directions and the docs say why.

### Error classification

| Situation | Classification | Where caught |
| --- | --- | --- |
| `skill_triggered` set on a `mode: loaded` case | authoring error, exit 2 | case loader — nothing could ever make it false |
| a case tool whose name collides with the skill's synthetic tool name | authoring error, exit 2 | case loader (it already receives the `Skill`) |
| spec declares `skill_triggered`, runner reported `None` | `errored` | trajectory evaluator — the runner does not support offered mode |

## 6. Case surface

```yaml
cases:
  - name: explains the refund window in plain language
    task: Why can't I return this?
    judge:
      expected: A plain-language explanation of the 30-day window, no jargon.
      rubric:
        - The reply states the return window is 30 days
        - The reply avoids policy jargon like "RMA" or "SKU"
        - The reply does not invent a reason
```

**`JudgeSpec`** — `expected: str = ""`, `rubric: list[str]`. `extra="forbid"`. A `judge:`
block with an empty `rubric` is an authoring error: it declares an intent to judge while
giving the judge nothing to check.

**Deviation from the parent spec, deliberate.** §9 sketches top-level `expected:` and
`rubric:` keys. They are nested in a `judge:` block instead, following M2's decision #2: each
evaluator gets exactly one input block, `extra="forbid"` protects that block, and the YAML
stays symmetric with `tools:` / `trajectory:` / `budget:`.

## 7. Model changes — all additive

```python
class CheckResult(BaseModel):        # new
    id: str
    passed: bool
    evidence: str = ""

class EvalScore(BaseModel):          # + three fields
    ...
    checks: list[CheckResult] = []
    errored: bool = False
    cost_usd: float = 0.0            # eval-side spend, never the skill's

class RunResult(BaseModel):          # + one field
    ...
    skill_triggered: bool | None = None

class TrajectorySpec(BaseModel):     # + one field
    ...
    skill_triggered: bool | None = None

class EvalCase(BaseModel):           # + two fields
    ...
    mode: Literal["loaded", "offered"] = "loaded"
    judge: JudgeSpec | None = None
```

`RunReport` gains a `judge_cost_usd` property summing `EvalScore.cost_usd`, kept apart from
the run cost the console reporter already prints.

An `EvalScore` with `errored=True` must also have `passed=False`; a validator enforces it so
the two can never disagree.

## 8. Orchestrator

One rule, and the reason it is not two:

```python
scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
if any(score.errored for score in scores):
    status = "errored"
else:
    status = "passed" if all(score.passed for score in scores) else "failed"
```

A judge endpoint returning 500 must not look like a skill that got worse. The
`errored` ≠ `failed` invariant was always about that distinction; M2 simply had no evaluator
capable of an infra failure, so it read the runner alone. Extending it preserves the
invariant's intent rather than its narrow implementation.

The default evaluator list becomes
`[AssertionEvaluator(), TrajectoryEvaluator(), BudgetEvaluator(), JudgeEvaluator(judge)]`.

## 9. Config & CLI

```toml
judge = "fake"          # built-in default; "pydantic-ai" for real judging
judge_model = ""        # empty falls back to `model`
```

New flag: `--judge-model`. Resolution order is unchanged — CLI flag > config > built-in
default. Secrets stay environment-only; the preflight check covers the judge model's provider
key whenever a judge that needs one is selected.

`judge` defaults to `"fake"` for the same reason `default_runner` defaults to `"fake"`:
upgrading to M3 must never start spending money on its own. The unscripted-`FakeJudge`-errors
rule (§3) is what makes that default safe rather than dishonest.

## 10. Testing

**Tier 1 — pipeline (every PR, zero cost, no HTTP).**
- `JudgeEvaluator` against a scripted `FakeJudge`: all-pass, partial, empty-evidence-fails,
  id-mismatch-errors, judge-error-errors, unscripted-judge-errors, no-`judge:`-block passes.
- `judges/prompt.py` rendering, as a pure function.
- `TrajectoryEvaluator.skill_triggered`: positive, negative control, and the
  runner-reported-`None` error path.
- Offered mode in `PydanticAIRunner` via PydanticAI's `FunctionModel`: the synthetic tool is
  registered, calling it returns the instructions, `skill_triggered` is reported both ways,
  and the skill body is absent from the system prompt.
- Case loader: the two new authoring errors, and `judge:` with an empty rubric.
- Orchestrator: an errored score yields an errored case.
- The framework-import guard test widens from one allowed module to exactly two.

**Tier 2 — cassettes (every PR, real fidelity, zero cost).** One recorded judged case and one
recorded offered-mode run covering both the positive and the negative control. Same
replay-only, skip-if-absent, secret-scrubbed rules as M2.

**Tier 3 — live integration (opt-in, real money).** The `examples/` suites, extended per §11.

## 11. Examples

`examples/order-support` gains three cases: one judged on a rubric, and an offered pair — a
positive ("I want a refund for order 1234") and a negative control ("What's the weather in
Cairo?"). The pair is the milestone's headline argument made executable: run the positives
alone and a skill that fires on everything scores perfectly.

CI's dogfood step stays `skill-eval list ./examples`, which now also exercises `judge:`,
`mode:`, and `skill_triggered` schema validation plus the two new authoring-error checks on
real files — still zero-cost.

## 12. Invariants this milestone must not break

- `errored` ≠ `failed` — now enforced for evaluators as well as runners.
- Judges never raise for provider failures; they set `JudgeVerdict.error`.
- A run executing zero cases fails the gate.
- Authoring errors abort the run and exit 2 — now including a malformed `judge:` block, an
  empty rubric, `skill_triggered` on a loaded case, and a skill/tool name collision.
- Exit codes: pass `0`, gate fail `1`, user/authoring error `2`.
- `extra="forbid"` on every user-authored model, `JudgeSpec` included.
- All file IO pins `encoding="utf-8"`; YAML goes through `yaml_loading.safe_load`.
- Secrets from environment variables only.
- `skill_eval` (underscore) never appears in user-facing output.
- Agent-framework types appear in exactly two modules: `runners/pydantic_ai.py` and
  `judges/pydantic_ai.py`.
- Nothing scores a vacuous pass: an unverified rubric errors, exactly as an unpriceable cost
  limit does.
