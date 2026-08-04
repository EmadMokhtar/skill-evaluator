# Comparative evals

## Why

A case that only ever runs with the skill loaded can tell you the score, but not what earned
it. If the model would have answered the same way with no skill at all, a green case is
measuring the model, not the skill. Comparative evals close that gap by running each case
twice — once with the skill, once without (or against an earlier version of it) — and
reporting the difference.

## The two arms

Every case can run in two **arms**:

- **candidate** — the skill under test, exactly as it runs today. This is the only arm that
  existed before M4, and it is the only arm the gate reads.
- **baseline** — a comparison point, selected with `--baseline`:
  - `none` — an **empty skill**: same name, no description, no instructions. This isolates
    what the skill's text contributes, as opposed to what the model would do unprompted.
  - `previous` — the skill's **prior version**, resolved from git (see below). This isolates
    what a specific edit changed.

**Omitting `--baseline` is what turns comparison off.** `none` names a *kind* of baseline (an
empty skill), not the absence of one — so leaving the flag unset, not passing `--baseline
none`, is the only way to get a single-arm run. With no flag, `skill-eval run` behaves exactly
as it did before M4: one arm, no delta block, byte-identical console output. Upgrading to a
version of `skill-eval` that supports comparison must never silently double anyone's bill.

The baseline skill is built once per skill, per run — not once per case, and not once per
repetition:

| `--baseline` | Baseline skill |
| --- | --- |
| `none` | `name` kept (for grouping in the report); `description` and `instructions` both empty |
| `previous` | The prior version parsed from git, same `name` and `path` as recorded in that commit |

**The baseline arm never receives the skill's name, description, or instructions** when both
are empty. The system prompt normally opens with `# {skill.name}` as a header; for an empty
`--baseline none` skill that would leak the skill's own name into the very prompt used to
measure whether the skill's text mattered. Instead, a skill whose description and
instructions are both empty gets a neutral preamble instead of that header:

```
You are a helpful assistant.
```

The rule keys on emptiness, not on which arm is running, so no runner has to know — or be
trusted to know — which arm it is serving.

A case's mock tools (`tools:`) are unaffected by the arm. They are the environment the case
declares, not part of the skill, so both arms see the same tools and the comparison stays
honest.

## How `previous` is resolved

`--baseline previous` walks the skill's own git history, rooted at its directory:

1. Confirm the directory is inside a git repository.
2. Confirm `SKILL.md` is tracked.
3. List the commits that touched `SKILL.md`, newest first, bounded to the last **50** commits.
4. Read each candidate commit's `SKILL.md` and parse it. The first one that qualifies as
   genuinely earlier wins:
   - if the **working copy** declares a `version:`, the first commit whose `version` differs
     from the working copy's;
   - if it declares none, the first commit whose **content** differs from the working copy's.

A declared `version:` is the stronger signal — an edit that did not bump it is still
considered *this* version, however much prose changed underneath it. Without a declared
version, differing content is the best evidence available.

The comparison is against the **working copy**, not `HEAD` — so uncommitted edits to
`SKILL.md` are what run as the candidate. This matters for local iteration: you do not need
to commit a change before measuring it.

Resolution happens **once per skill**, not per case or per repetition — it shells out to
`git`, and shelling out once per repetition would multiply subprocess calls by nothing useful.

### When resolution fails

Resolution can fail for reasons that are facts about the repository, not about the skill's
content. Each is reported as its own message, and **none of them raise** — the same
discipline runners follow for provider failures:

| Reason | Meaning |
| --- | --- |
| `git` is not installed | The environment cannot answer the question at all |
| not a git repository | The skill's directory (or an ancestor) has no `.git` |
| `SKILL.md` is not tracked by git | The file exists but was never committed |
| no earlier version found within the searched history | Every commit in the last 50 has the same version (or, unversioned, the same content) |

**An unresolvable baseline is reported, never assumed to be "no change".** Treating "we
couldn't check" as "nothing changed" would let a repository pass `--min-delta` forever by
deleting its git history. Without `--min-delta`, an unresolved baseline is just a note on the
report. With it, it fails the gate (see [`--min-delta`](#-min-delta) below).

## `mode: offered` cases

`mode: offered` cases (see [Did the agent reach for the skill?](eval-files.md#did-the-agent-reach-for-the-skill))
measure whether the agent chose to trigger the skill at all, which makes them the highest-
value case for comparison: a description edit is exactly what a trigger-rate delta measures.

| `--baseline` | Behavior |
| --- | --- |
| `previous` | **Both arms run.** The baseline offers the skill's *old* name and description; the difference in trigger rate between the two arms is how a description edit is measured. |
| `none` | **Candidate-only.** There is no skill to offer in an empty baseline, so `skill_triggered` would be `false` by construction. Running it anyway would spend real money to prove a tautology, and would report the structural artifact as "the skill helped 100%". |

A skipped baseline (offered case, `--baseline none`) is recorded as a note on the report and
excludes that case from the delta. **It is not a baseline failure** — nothing went wrong, the
comparison just isn't meaningful for that case.

## `--repeat N`

`--repeat N` samples each arm `N` times per case. **Each repetition is its own outcome** — it
is scored independently and contributes its own pass/fail to the report, rather than being
collapsed into one verdict per case.

At the default `min_pass_rate = 1.0`, this is exactly "all repetitions must pass": a single
failing repetition drops the pass rate below 1.0 and fails the gate, the same as it would for
one unrepeated case. Below 1.0, a pass rate degrades proportionally — 4 of 5 repetitions
passing is a different signal from 0 of 5, and repeats let you see that instead of averaging
it away.

Repeats also feed [high-variance detection](#low-signal-checks-and-high-variance-cases):
repetitions that disagree with each other are flagged, which is only meaningful once there is
more than one to compare.

## The delta block

`skill-eval` compares the two arms and reports the difference — the **delta**. Every delta is
**candidate minus baseline**:

| Metric | Sign convention |
| --- | --- |
| Pass rate | Higher is better (a positive delta means the candidate scored better) |
| Tokens, cost, latency | Negative is better (a positive delta means the candidate cost or took *more*) |

The reporters say which direction is better next to each number rather than leaving a reader
to work it out.

**The delta is paired.** A case is only comparable when both arms produced at least one
non-errored repetition. Anything that breaks the pair — a skipped baseline (offered +
`none`), an unresolvable baseline, or every repetition of one arm erroring — excludes the
case from **both** halves of the delta, with the reason recorded. Keeping the candidate half
of a broken pair would silently bias the aggregate number, so it is dropped along with its
partner. Aggregate rates are computed only over comparable cases' outcomes.

Console output collapses to one line per case — both arms summarized together — rather than
one line per outcome, since `--repeat 5 --baseline previous` would otherwise print ten lines
for a single case:

```
[PASS] order-support :: refund-request (pydantic-ai)  candidate 5/5  baseline 2/5  +60%
```

followed by a delta block naming the arms it compared:

```
Delta vs baseline (previous)
  pass rate  40% -> 100%  +60%   (higher is better)
  tokens     -60   (negative is better)
  cost       $-0.0002   (negative is better)
  latency    -120ms   (negative is better)
```

## Low-signal checks and high-variance cases

Two diagnostics ride along with the delta. Both describe the **eval suite**, not the skill
under test, and neither changes the exit code.

- **Low-signal check** — a check id that passed in *every* scored repetition of *both* arms
  of a comparable case. It contributed to the with-skill score, but it didn't measure
  anything about the skill, since it also held with no skill loaded at all. It is a candidate
  for tightening or removing.
- **High-variance case** — a (case, arm) whose repetitions were not unanimous — some passed,
  some didn't. Only meaningful with `--repeat > 1`. An unstable pass rate for the same skill,
  same case, same runner usually points at ambiguous instructions in the skill rather than at
  a flaky provider (though a genuinely flaky provider looks identical, which is exactly why
  this is advisory and not a gate).

Both are reported for visibility so a suite's weak spots are named, but **neither ever fails
the gate**. A flag that could block a merge for "this assertion is weak" trains people to
ignore flags, and there is no way to tell a bad skill apart from a flaky provider from the
flag alone.

## `--min-delta`

`--min-delta <float>` turns the delta into a gate: the candidate's pass-rate delta must be at
least this much better than the baseline's.

`--min-delta` **requires a baseline**. Passing it without `--baseline` is a user error and
exits `2` — the alternative is a gate that silently checks nothing, which is the same
vacuous-pass failure mode every other gate rule in this project rejects.

With a baseline set, `--min-delta` fails the gate for three reasons:

1. **The delta is below the bar** — `pass_rate_delta < min_delta`.
2. **No case was comparable** — mirroring "a run executing zero cases fails the gate": a gate
   that verified nothing must never report a pass. A suite made entirely of skipped-baseline
   `offered` cases under `--baseline none` fails through this rule, which is the honest reason
   even though no individual baseline "failed".
3. **A skill's baseline could not be resolved** — naming the skill and the reason. Otherwise a
   repository could pass `--min-delta` forever by deleting its git history.

A *deliberately* skipped baseline (an `offered` case under `--baseline none`) is, on its own,
**not** a gate reason — nothing went wrong. It only becomes one indirectly, through rule 2, if
it leaves nothing comparable behind.

**Baseline outcomes never count toward the gate.** Every other gate rule — `min_pass_rate`,
`per_skill_min`, `fail_on_error`, the zero-cases check — reads the **candidate** arm only. A
strong baseline means the skill was unnecessary, not that CI should go red; the baseline exists
to be compared against, not to be graded on its own.

## Cost

Comparison multiplies spend. `--repeat 5 --baseline previous` runs **10x** as many cases as a
plain run: 5 repetitions x 2 arms. Before starting, when the selected runner needs an API key,
the CLI prints a run plan:

```
Plan: up to 2 arm(s) x 3 repeat(s) x 4 case(s) = 24 runs
```

This is deliberately a **ceiling, not a forecast** — "up to", not "exactly". It applies the
`--tag` filter (which only ever *reduces* the count), but it does not resolve baselines or
evaluate per-case arm rules (`mode: offered` under `--baseline none` drops the baseline arm
for that case; an unresolvable baseline drops it for the whole skill). Reproducing those here
would mean duplicating the orchestrator's discovery — including its git calls — just to print
a line, and both of those only ever bring the real total *down* from the number shown.

## `version:` and why it must be quoted

A skill's frontmatter can declare `version:`, which `--baseline previous` uses to find the
prior release (see [above](#how-previous-is-resolved)). It must parse as a YAML **string**:

```yaml
---
name: order-support
version: "1.2.0"
---
```

An unquoted decimal like `version: 1.20` is rejected at parse time as an authoring error
(`SkillParseError`, exit `2`) rather than silently accepted. YAML resolves `1.20` and `1.2` to
the same floating-point value, so two genuinely different versions written that way would
silently compare equal — and `--baseline previous` would report zero change between versions
that actually differ. Three-part semantic versions like `1.0.0` are already unambiguous
strings in YAML and need no quoting, but quoting is always safe and recommended.

A skill with no `version:` at all is not an error — resolution falls back to comparing file
content instead (see [above](#how-previous-is-resolved)).

## Worked CI example

```bash
skill-eval run ./skills --runner pydantic-ai --baseline previous --repeat 3 --min-delta 0.0
```

This runs every case three times in each arm, compares the candidate's current `SKILL.md`
against the newest earlier version git can find, and fails the build if the candidate did not
do at least as well as that earlier version (`--min-delta 0.0` — "must not regress"). It also
fails if a skill's history has no earlier version to compare against, or if nothing in the
suite was comparable at all.
