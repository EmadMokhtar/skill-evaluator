# Eval files

Each file has a top-level `cases:` list. Unknown keys **within a case or an assertion** are
rejected — a typo like `assertion:` would otherwise produce a case that passes vacuously.
Extra keys alongside `cases:` at the top level of the file are ignored.

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Case name, shown in reports |
| `task` | yes | The prompt handed to the runner |
| `assertions` | no | Scoring rules; a case with none passes |
| `tags` | no | Labels for `--tag` filtering |
| `tools` | no | Mock tools the agent may call — see [Declaring tools and scoring the trajectory](runners.md#declaring-tools-and-scoring-the-trajectory) |
| `trajectory` | no | Which tools must/must not have been called, and in what order |
| `budget` | no | Ceilings on tokens, cost, and latency |
| `judge` | no | A rubric for an LLM judge — see [Judging output quality](#judging-output-quality) |
| `mode` | no | `loaded` (default) or `offered` — see [Did the agent reach for the skill?](#did-the-agent-reach-for-the-skill) |

## Judging output quality

Some things an assertion cannot check: "explains it plainly" is not a substring. A `judge:`
block hands those to an LLM judge.

```yaml
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
```

| Field | Required | Meaning |
| --- | --- | --- |
| `expected` | no | Free text describing what a good answer looks like |
| `rubric` | yes | One statement per line, each checked independently |

The judge returns **one verdict per rubric entry, with the evidence for it**. skill-eval
derives the verdict and the score from those per-check results; the judge is never asked for
a blended number, because an unsupported PASS hidden inside one is invisible.

Two rules follow from that, and both are mechanical rather than a prompt asking nicely:

- **A check that passes without citing evidence is recorded as a failure.** An unsupported
  PASS is an LLM judge's characteristic failure mode.
- **A verdict whose checks don't line up with the rubric is `errored`, not `failed`** —
  malformed structured output is an infra problem, not evidence about the skill.

An empty `rubric`, or a blank entry within one, is an authoring error: a check that verifies
nothing would score as a pass nobody verified.

Judging costs money, so it is opted into explicitly with `judge = "pydantic-ai"` in
[`skill-eval.toml`](configuration.md). The default `judge = "fake"` does not grade at all —
and rather than passing a rubric it never checked, it reports the case as **errored**. Judge
spend is reported as "judge overhead", separately from what the runs themselves cost, and
never counts against a case's `budget:`.

## Did the agent reach for the skill?

`mode: offered` stops force-loading the skill. Instead it is registered as a tool, named
after the skill and described by its frontmatter `description`. If the agent calls it, it
receives the skill's instructions and carries on; if it doesn't, it never sees them — so the
triggering decision is a real choice, and an observable one.

```yaml
  - name: reaches for the skill on a refund question
    mode: offered
    task: I want a refund for order 1234
    trajectory:
      skill_triggered: true

  - name: leaves an unrelated question alone
    mode: offered
    task: What's the capital of Egypt?
    trajectory:
      skill_triggered: false
```

**Always ship the negative control.** A suite of positives alone scores a skill that fires on
everything at 100%.

Three things to know:

- The offered tool call lands in the trajectory like any other, so it counts toward
  `max_calls`.
- Check it with `skill_triggered`, not by naming it in `called:` — that list only accepts
  tools the case itself declares.
- The tool name is the skill's name normalised to an identifier (`order-support` becomes
  `order_support`). A case tool that collides with it is an authoring error.

Setting `skill_triggered` on a `mode: loaded` case is an authoring error too: a loaded skill
is always in force, so the check could never be false. Running an offered case on a runner
that does not support the mode is **errored**, never a quiet pass.

## Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |

Every assertion in a case must hold for the case to pass. An unsupported `kind` or a malformed
regex aborts the run as an authoring error rather than being reported as a skill failure.

## Where eval files are found

For each discovered skill, in order:

1. an `evals/` directory beside `SKILL.md` — every `.yaml` / `.yml` file in it, or
2. any `*.eval.yaml` file beside `SKILL.md`.

`--evals <path>` overrides discovery with an explicit file or directory. Skills with no eval
files are reported as **skipped** — visible in the output, never silently ignored.
