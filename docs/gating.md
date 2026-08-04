# Gating and exit codes

Exit codes are the CI contract:

| Code | Meaning |
| --- | --- |
| `0` | Gate passed |
| `1` | Gate failed |
| `2` | User or authoring error (bad path, malformed YAML, unknown assertion kind) |

A run fails the gate when the overall pass rate is below `min_pass_rate`, when a configured
per-skill minimum is not met, or when any case **errored**. Two distinctions matter:

- **failed** — the case ran and scored below the bar. An *eval* signal.
- **errored** — something in the harness blew up rather than the skill scoring badly: the
  runner (API error, timeout, missing key), or an evaluator (a judge endpoint returning 500,
  a judge verdict that does not match its rubric, an offered case on a runner that does not
  support the mode). An *infra* signal, and it fails the gate by default so CI never goes
  green on a broken run.

A case that fails its assertions drags the pass rate below the bar and fails the gate:

```
[FAIL] badskill :: expects something absent (fake)
        assertion: failed: contains('NEVER_PRESENT')

0 passed, 1 failed, 0 errored — pass rate 0%

Gate FAILED:
  - pass rate 0% is below the required 100%
```

**A run that executed zero cases also fails.** "Nothing ran" is a broken run, not a pass —
otherwise a mistyped path reports success forever. The reason names the cause: no skills found,
all skills skipped for having no eval cases, or every case filtered out by `--tag`.

```
Skipped (no eval cases): badskill

0 passed, 0 failed, 0 errored — pass rate 0%

Gate FAILED:
  - no eval cases ran: all discovered skill(s) were skipped for having no eval cases: badskill
```

**Every gate rule above reads the candidate arm only.** Under `--baseline`, a run also
produces baseline outcomes, but `min_pass_rate`, `per_skill_min`, `fail_on_error` and the
zero-cases check never see them — a strong baseline means the skill was unnecessary, not that
CI should go red. With no baseline, candidate and baseline are the same (empty) set, so none
of these numbers move from what they were before comparative evals existed.

## Gating on the delta (`--min-delta`)

`--min-delta <float>` adds three more gate rules, all evaluated against the
[delta](comparative-evals.md#the-delta-block) between the candidate and baseline arms:

- the pass-rate delta is below `min_delta`;
- **no case was comparable** — a delta gate that verified nothing must never report a pass,
  the same principle that fails a run executing zero cases;
- a skill's baseline **could not be resolved** — named, with the reason — because treating an
  unresolvable baseline as "no change" would let a repository pass this gate forever by
  deleting its git history.

`--min-delta` requires `--baseline`; passing one without the other is a user/authoring error
(exit `2`), not a gate failure, since the configuration is rejected before any case runs. A
deliberately skipped baseline (an `offered` case under `--baseline none`) is not, on its own,
a gate reason — nothing went wrong there. See
[Comparative evals](comparative-evals.md#-min-delta) for the full picture, including how the
delta is paired and what makes a case comparable.

**Low-signal checks and high-variance cases are advisory.** They are printed alongside a
comparative run's output to point at weak spots in the eval suite, but they never affect the
exit code — see
[Low-signal checks and high-variance cases](comparative-evals.md#low-signal-checks-and-high-variance-cases).

## JSON report

`--json-output report.json` writes a machine-readable report alongside the console output:
a `summary` block (counts, overall and per-skill pass rates, token/cost/latency totals),
`skipped_skills`, `tag_filtered_skills`, a per-case `outcomes` list, a top-level `delta` block,
`baseline_notes`, and the `gate` decision with its reasons.

Comparative evals changed this document additively, not by rewriting what was already there:
every M3 field means what it always meant, and M4 only adds fields alongside them — `arm` and
`repeat_index` on each outcome, `baseline_errored` in `summary`, and the top-level `delta`
(`null` when no baseline arm ran) and `baseline_notes`. A tool reading only the M3 fields
keeps working unmodified.

Each entry in `outcomes` carries `arm` (`"candidate"` or `"baseline"`) and `repeat_index`
(0-based), so a comparative run's raw per-repetition results can be reconstructed from the
JSON even though the console collapses them to one line per case.

`delta` is the full comparison object — pass-rate, token, cost and latency deltas, per-case
stats, low-signal checks, high-variance cases and notes — and is `null` when no baseline arm
ran. `baseline_notes` lists why a skill's or case's baseline was skipped or unavailable.
`summary.baseline_errored` counts errored baseline repetitions apart from `summary.errored`,
which is candidate-only, for the same reason the gate itself only reads the candidate arm
(above): an errored baseline invalidates that case's delta, it does not mean the skill broke.

`summary`'s token, cost and latency totals sum **both** arms — money spent is money spent —
while `summary.passed` / `summary.failed` / `summary.errored` / `summary.pass_rate` stay
candidate-only, because those are what the gate reads.
