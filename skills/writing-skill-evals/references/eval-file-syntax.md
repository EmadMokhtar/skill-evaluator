# Eval file syntax

A file has one top-level `cases:` list. Unknown keys inside a case or an assertion are
rejected — without that, a typo like `assertion:` would yield a case that passes
vacuously.

## Case fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Case name, shown in reports |
| `task` | yes | The prompt handed to the runner |
| `assertions` | no | Output checks; a case with none passes vacuously |
| `tools` | no | Mock tools the agent may call |
| `trajectory` | no | Which tools must and must not have been called, and in what order |
| `budget` | no | Ceilings on tokens, cost, and latency |
| `judge` | no | A rubric for an LLM judge |
| `mode` | no | `loaded` (default) or `offered` |
| `tags` | no | Labels for `--tag` filtering |

## Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |

Every assertion must hold for the case to pass. An unknown kind or a malformed regex
aborts the run as an authoring error rather than being reported as a skill failure.

## Tools

Nothing executes. Calling a mock tool records the call and returns `returns` verbatim, so
the trajectory is genuinely the model's choice and a run has no side effects. Mock tools
accept any arguments — a hallucinated argument must not surface as an infra error.

```yaml
    tools:
      - name: lookup_order          # must be a valid identifier
        description: Look up an order by its id
        parameters:
          order_id: string          # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
```

## Trajectory

```yaml
    trajectory:
      called: [lookup_order]        # must have been called
      forbidden: [issue_refund]     # must not have been
      order: [lookup_order, issue_refund]   # relative order, not exhaustive
      max_calls: 3
      skill_triggered: true         # mode: offered only
```

Every name in `called`, `forbidden` and `order` must be a tool the case itself declares.

## Budget

```yaml
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 20000
```

An unpriced model makes a cost limit unverifiable, so that check is skipped rather than
counted as passed — a cost limit as the only budget check then fails the case, because
nothing was verified.

## Judge

```yaml
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
```

One verdict per rubric entry, each with its evidence; skill-eval derives pass and score
from those. A check that passes without evidence is recorded as a failure. An empty
rubric, or a blank entry, is an authoring error. Judging costs money and is opted into
with `judge = "pydantic-ai"` in `skill-eval.toml`; the default `judge = "fake"` reports a
judged case as **errored** rather than passing a rubric nobody checked.

## Triggering (`mode: offered`)

The skill is not force-loaded; it is registered as a tool named after the skill
(`order-support` becomes `order_support`) and described by its frontmatter description.
Calling it delivers the instructions. Check it with `skill_triggered`, not by naming the
tool in `called:`. Setting `skill_triggered` on a `loaded` case is an authoring error.

## Placeholders

`skill-eval init` writes `TODO(skill-eval)` into every field you must supply. A case still
containing one aborts the run with exit 2, naming the field.
