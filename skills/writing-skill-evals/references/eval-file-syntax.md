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
| `workspace` | no | A temporary directory and the files it starts with |
| `mode` | no | `loaded` (default) or `offered` |
| `tags` | no | Labels for `--tag` filtering |

## Workspaces

A `workspace:` block gives one case a real, contained temporary directory: created fresh,
seeded with the files it declares, and deleted once the case is scored.

```yaml
    workspace:
      files:
        sales.csv: |
          region,units
          north,120
```

It is opt-in: a case with no `workspace:` block gets no directory and no extra tools, so
every suite written before this existed keeps running byte-identically. Declaring
`workspace: {}` with no files is still meaningful — it hands the agent an empty directory to
generate into.

With the block present, the agent also gets three built-in tools — `list_files`, `read_file`
and `write_file` — none of which a case's own `tools:` may name. A `trajectory:` block may
name them like any other tool:

```yaml
    trajectory:
      called: [read_file, write_file]
```

## Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |
| `file-produced` | `file` exists in the workspace |
| `json-schema` | the output (or `file`) parses as JSON and validates against `json_schema` |

Every assertion must hold for the case to pass. An unknown kind or a malformed regex
aborts the run as an authoring error rather than being reported as a skill failure.

`file` is a **modifier**, not a kind of its own: set it on `contains`, `not_contains`,
`regex` or `equals` and that assertion reads the named workspace file instead of the run's
output text.

```yaml
    assertions:
      - kind: contains
        value: "north"
        file: report.md
```

`file:` on any assertion in a case with no `workspace:` block is an authoring error — there
would be no file to look at.

## Tools

Nothing executes. Calling a mock tool records the call and returns `returns` verbatim, so
the trajectory is genuinely the model's choice and a run has no side effects. Mock tools
accept any arguments — a hallucinated argument must not surface as an infra error.

```yaml
    tools:
      - name: lookup_order          # ^[A-Za-z0-9_-]{1,64}$ (what providers accept)
        description: Look up an order by its id
        parameters:
          order_id: string          # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
      - name: get-pull-request      # a real MCP tool: keep the server's name
        description: Get details of a specific pull request
        input_schema:               # the server's JSON Schema, verbatim; never with parameters
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
          required: [owner, pull_number]
        returns: '{"number": 1}'
```

`parameters:` is closed (every key required, no extras); `input_schema:` is passed as
written and must be a valid JSON Schema of `type: object`. For a tool a real MCP server
exposes, `skill-lens mcp-import tools.json` writes the `input_schema:` block from the
server's `tools/list` listing — `returns:` still has to be filled in by hand.

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
      artifacts: [report.md]
```

One verdict per rubric entry, each with its evidence; skill-lens derives pass and score
from those. A check that passes without evidence is recorded as a failure. An empty
rubric, or a blank entry, is an authoring error. Judging costs money and is opted into
with `judge = "pydantic-ai"` in `skill-lens.toml`; the default `judge = "fake"` reports a
judged case as **errored** rather than passing a rubric nobody checked.

`artifacts` names workspace files the judge may read, so a rubric can grade the document a
skill produced rather than the chat message about it. Each is fenced against its own
content before it reaches the judge, so a file's content is read as data, never as
instructions — even one that looks like it is trying to talk to the judge. `artifacts` in a
case with no `workspace:` block, or naming a file that could never be produced, is an
authoring error.

## Triggering (`mode: offered`)

The skill is not force-loaded; it is registered as a tool named after the skill
(`order-support` becomes `order_support`) and described by its frontmatter description.
Calling it delivers the instructions. Check it with `skill_triggered`, not by naming the
tool in `called:`. Setting `skill_triggered` on a `loaded` case is an authoring error.

## Placeholders

`skill-lens init` writes `TODO(skill-lens)` into every field you must supply. A case still
containing one aborts the run with exit 2, naming the field. Mapping keys are checked too —
an unfilled filename under `workspace: files:` is refused like any other placeholder.
