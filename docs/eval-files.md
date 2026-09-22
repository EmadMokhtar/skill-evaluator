# Eval files

Each file has a top-level `cases:` list. Unknown keys **within a case or an assertion** are
rejected — a typo like `assertion:` would otherwise produce a case that passes vacuously.
Extra keys alongside `cases:` at the top level of the file are ignored, with one exception
skill-lens reads: `tool_libraries:` — see [Sharing tools across eval
files](#sharing-tools-across-eval-files).

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Case name, shown in reports |
| `task` | yes | The prompt handed to the runner |
| `assertions` | no | Scoring rules; a case with none passes |
| `tags` | no | Labels for `--tag` filtering |
| `tools` | no | Mock tools the agent may call — see [Mock tools](#mock-tools) |
| `trajectory` | no | Which tools must/must not have been called, in what order, and with what arguments — see [What a tool was called with](runners.md#what-a-tool-was-called-with) |
| `budget` | no | Ceilings on tokens, cost, and latency |
| `judge` | no | A rubric for an LLM judge — see [Judging output quality](#judging-output-quality) |
| `workspace` | no | A temporary directory and the files it starts with |
| `mode` | no | `loaded` (default) or `offered` — see [Did the agent reach for the skill?](#did-the-agent-reach-for-the-skill) |

## Workspaces

A `workspace:` block gives one case a real, contained temporary directory: created fresh
for that work item, seeded with the files it declares, and deleted once the case is
scored — unless [`--keep-workspace`](cli.md) says otherwise.

```yaml
    workspace:
      files:
        sales.csv: |
          region,units
          north,120
          south,80
```

It is **opt-in**. A case with no `workspace:` block gets no temporary directory and no
extra tools, so every suite written before this existed keeps running byte-identically.
Declaring `workspace: {}` with no `files:` is still meaningful — it hands the agent an
empty directory to generate something into, for a skill whose whole job is producing a
file from nothing.

With the block present, the agent also gets three built-in tools:

| Tool | Does |
| --- | --- |
| `list_files` | List every file in the working directory, one relative path per line |
| `read_file` | Read a text file. `path` is relative to the working directory |
| `write_file` | Create or replace a text file |

A skill that ships `scripts/`, `references/` or `assets/` beside `SKILL.md` also gets
`list_skill_files`, `read_skill_file` and — only when the run enables it with
`allow_scripts` or `--allow-scripts` — `run_script`. See
[Bundled files and scripts](runners.md#bundled-files-and-scripts). All six names are
reserved in any case with a `workspace:` block, whether or not the skill has a bundle, so a
case's own `tools:` may not declare a name that collides with one of these six — that is an
authoring error. A `trajectory:` block may name any of them like any other tool:

```yaml
    trajectory:
      called: [run_script, write_file]
```

Naming a built-in in a case with no `workspace:` block is an authoring error too: the
bundle tools exist only where the workspace does.

Like the offered-skill tool below, a call to one of these six lands in the trajectory
like any other tool call, so it counts toward `max_calls` and any `budget:` ceilings too.

See [The workspace](runners.md#the-workspace) for containment and the size caps, and
[Assertion kinds](#assertion-kinds) below for scoring a produced file rather than the chat
output.

## Mock tools

A `tools:` list declares the tools the agent may call. Nothing executes: calling one records
the call and returns `returns` verbatim, so the trajectory is the model's own choice and
the run has no side effects. See [Declaring tools and scoring the
trajectory](runners.md#declaring-tools-and-scoring-the-trajectory) for how the calls are
scored.

A tool declares its arguments one of two ways:

```yaml
    tools:
      - name: lookup_order              # ^[A-Za-z0-9_-]{1,64}$ — what providers accept
        description: Look up an order by its id
        parameters:                     # the shorthand: name -> primitive type
          order_id: string              # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
      - name: get-pull-request
        description: Get details of a specific pull request
        input_schema:                   # a full JSON Schema, passed to the agent verbatim
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
            labels: {type: array, items: {type: string}}
          required: [owner, pull_number]
        returns: '{"number": 1}'
```

`parameters:` is for a tool you describe by hand. Every key is required and the schema is
closed (`additionalProperties: false`), because you wrote every key. `input_schema:` is
for a tool that must match a real server's declared schema — optional arguments, enums,
arrays, nested objects — and reaches the agent exactly as written; nothing is added. It
must be a valid JSON Schema whose top-level `type` is `object`, which is what every
provider requires of a tool. A tool that declares both, or an `input_schema` that is not
a valid object schema, is an authoring error (exit `2`) caught before any case runs.

[`skill-lens mcp-import`](cli.md#mcp-import) writes an `input_schema:` block from an MCP
server's own `tools/list` listing, so a mock for a real server's tool is copied rather
than transcribed.

Tool names follow the rule both OpenAI and Anthropic enforce, `^[A-Za-z0-9_-]{1,64}$`, so
a hyphenated MCP tool name such as `get-pull-request` is kept as the server spells it. A
name outside the rule is an authoring error; skill-lens never rewrites one.

### Answering differently per call

One `returns:` string answers every call the same way. A skill whose instructions loop
over a tool — fetch work item A, follow its parent link, fetch B, stop when there is no
parent — needs the mock to answer differently, and `returns:` takes two more shapes for
that. **The shape says which rule applies.**

A **list of strings** is a sequence, consumed in the order the calls arrive: the first
call gets the first entry, the second call the second, and every call after the list is
used up gets the **last entry again**.

```yaml
    tools:
      - name: get_work_item
        description: Fetch a work item by id, with its parent link
        parameters:
          id: string
        returns:
          - '{"id": "A", "parent": "B"}'
          - '{"id": "B", "parent": null}'      # the third call and every later one get this too
    trajectory:
      called: [get_work_item]
      max_calls: 2                            # the check for a loop that should have stopped
```

The last entry repeating is the steady state a skill that keeps calling should see — the
item with no parent, the job that is done. It is not a check on how many calls were made:
`trajectory.max_calls` is. A sequence ignores the arguments, so it is the right shape when
the *position* of the call decides the answer (polling until done) and the wrong one when
the *argument* does: a model that issues several calls in one turn gets the entries in
whatever order the framework runs them.

A **list of `when:`/`value:` mappings** is a lookup, answered by the first entry whose
`when:` keys all equal the call's arguments — a subset is enough; the call may carry more
arguments than `when:` names. An entry with no `when:` matches every call, which makes it
the fallback.

```yaml
    tools:
      - name: get_work_item
        description: Fetch a work item by id, with its parent link
        parameters:
          id: string
        returns:
          - when: {id: "A"}
            value: '{"id": "A", "parent": "B"}'
          - when: {id: "B"}
            value: '{"id": "B", "parent": null}'
          - value: '{"error": "not found"}'   # no when: -- every other call lands here
```

Values are compared as YAML and JSON parse them: `when: {id: 1}` matches a call with the
integer `1`, `when: {id: "1"}` a call with the string `"1"`, and a `true` matches only a
boolean — never a `1`. Without a fallback, a call that matches nothing gets the fixed reply
`no response is scripted for get_work_item with arguments {"id": "C"}` (the arguments as
sorted JSON): the skill asked for something the case did not anticipate, which the
transcript then shows, and a mock tool never raises.

Three lookup mistakes are authoring errors (exit `2`), caught before any case runs, because
each is a check that could never fire: a `when:` key the tool can never carry (one outside
`parameters:`, or outside a closed `input_schema` — `additionalProperties: false` with its
`properties` listed; an open schema may key on any name), an empty `when: {}` (drop the key
to declare a fallback), and an entry an earlier one already answers — a fallback above it,
the same `when:`, or a `when:` it only narrows — since the first match wins. So are an
empty list (`returns: ''` is how an empty reply is spelled) and a list that mixes strings
with mappings.

A [`ref:`](#sharing-tools-across-eval-files) may set `returns:` in any of the three
shapes; a lookup's `when:` keys are checked against the parameters the library declared.

### Sharing tools across eval files

Several skills often front one API — one MCP server behind nine skills — and every eval
file that mocks `get_pull_request` would otherwise carry the same block. A **tool
library** declares a tool once. It is a YAML file with one top-level `tools:` list, the
same block a case takes, and exactly what [`skill-lens mcp-import`](cli.md#mcp-import)
prints — so `skill-lens mcp-import tools.json > shared-tools/server.yaml` writes one:

```yaml
# shared-tools/order-api.yaml
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000", "status": "delivered", "days_since_delivery": 0}'
```

An eval file imports libraries with `tool_libraries:` beside `cases:`, and a case names a
tool with `ref:`:

```yaml
tool_libraries:
  - ../../shared-tools/order-api.yaml    # relative to this file; a directory imports every .yaml/.yml in it

cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tools:
      - ref: lookup_order                # the library's name, description and schema
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
```

The library owns the tool's **contract** — name, description, `parameters:` or
`input_schema:` — and the case owns the **scenario**: a `ref:` may set `returns:` (a
string, a sequence or a lookup, as [above](#answering-differently-per-call)) and nothing
else. Any other key beside `ref:` is an authoring error naming it; a case that
needs a different contract declares the tool inline, and a `ref:` may sit in the same list
as inline tools. After loading, the case is exactly what it would have been with the
library's block pasted in: `trajectory:` names, the six reserved built-in names and the
`mode: offered` collision rule all read the resolved tool, and every runner sees a plain
mock.

Library paths are relative to the eval file — never to the working directory, never
absolute — so the same file loads identically under discovery, under `--evals`, under
`skill-lens list` and from any checkout. A library is checked as an eval file is: an
unfilled `TODO(skill-lens)`, a tool name outside the rule, an unknown key, or an invalid
schema is an authoring error (exit `2`) naming the library file and the tool's position.
So is an unresolvable `ref:` — no `tool_libraries:` key, a name no import declares (the
message lists the names they do), a missing file — a name two imported files both declare
(never resolved by position), or one file imported twice.

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
| `artifacts` | no | Workspace files the judge may read, graded alongside the output — see [Workspaces](#workspaces) |

The judge returns **one verdict per rubric entry, with the evidence for it**. skill-lens
derives the verdict and the score from those per-check results; the judge is never asked for
a blended number, because an unsupported PASS hidden inside one is invisible.

Two rules follow from that, and both are mechanical rather than a prompt asking nicely:

- **A check that passes without citing evidence is recorded as a failure.** An unsupported
  PASS is an LLM judge's characteristic failure mode.
- **A verdict whose checks don't line up with the rubric is `errored`, not `failed`** —
  malformed structured output is an infra problem, not evidence about the skill.

An empty `rubric`, or a blank entry within one, is an authoring error: a check that verifies
nothing would score as a pass nobody verified.

`artifacts` names [workspace](#workspaces) files the judge may read, so a rubric can grade
the document a skill produced rather than the chat message about it:

```yaml
    judge:
      expected: A short Markdown report with one line per region and a total.
      rubric:
        - The report states a total of 200 units.
      artifacts: [report.md]
```

Each artifact reaches the judge fenced against its own content — labelled with the name you
gave it, but the content itself is read as data, never as instructions, even one that reads
like it is trying to talk to the judge. `artifacts` on a case with no `workspace:` block, or
naming a file no case could ever produce, is an authoring error.

Judging costs money, so it is opted into explicitly with `judge = "pydantic-ai"` (or
`"langchain"`, or an installed product — `"copilot"`, `"claude-code"`, `"cli"` — which
needs no API key, but spends the product's own quota) in
[`skill-lens.toml`](configuration.md#judging). The default
`judge = "fake"` does not grade at all —
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
- The tool name is the skill's name normalised to what providers accept — ASCII letters,
  digits and `_`, at most 64 characters (`order-support` becomes `order_support`, `café`
  becomes `caf_`). A case tool that collides with it is an authoring error. This
  normalisation applies only to the offered-skill tool; a case's own tools keep their names.

Setting `skill_triggered` on a `mode: loaded` case is an authoring error too: a loaded skill
is always in force, so the check could never be false. Running an offered case on a runner
that does not support the mode is **errored**, never a quiet pass.

Under a [product runner](runners.md#product-runners) there is no offered tool: the skill
sits in the product's own skill directory, the bare task is sent, and `skill_triggered`
comes from the product's own load signal (Copilot's `skill` tool call — its `skill.invoked`
event is only what a slash invocation emits — Claude Code's `Skill` tool call). A product
with no such signal — `cli` — makes `mode: offered` an authoring error
under that runner, never a silent `false` that would pass every negative control.

## Which runners serve which case features

| Case feature | `fake` | `pydantic-ai` / `langchain` | `copilot` / `claude-code` | `cli` |
| --- | --- | --- | --- | --- |
| `assertions:` | yes | yes | yes | yes |
| `tools:` (mock tools) | yes | yes | authoring error | authoring error |
| `trajectory:` | yes | yes | yes, the product's tool names | authoring error |
| `mode: offered` | yes | yes | yes | authoring error |
| `budget:` | yes | yes | see [Product runners](runners.md#product-runners) | latency only |
| `workspace:` | yes | yes | yes — the product's working directory | yes |
| `judge:` | yes | yes | yes | yes |

An authoring error here is found in preflight and exits 2 before any case runs. Under
`cli`, `budget: max_tokens` and `max_cost_usd` are declared limits the runner cannot
measure, so each is a failing *not evaluated* check rather than an error; only
`max_latency_ms` is evaluated.

## Unfilled scaffolds

`skill-lens init` writes placeholder fields holding the literal `TODO(skill-lens)`.
Loading a case that still contains one is an **authoring error**: the run aborts with
exit `2` naming the file, the case, and the field.

That is deliberate, and it is the loader's rule rather than the scaffolder's — a
hand-written stub is refused the same way. An unfinished eval that ran would either pass
while checking nothing or fail while saying nothing about the skill, and both are worse
than a run that stops and tells you which field to fill in.

Comments are discarded before the check, so a file may discuss the token freely. Mapping
keys are checked as well as values — `workspace: files:` is keyed by filename, and an
unfilled filename would otherwise seed a file literally named after the placeholder.

## Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |
| `file-produced` | `file` exists in the workspace |
| `json-schema` | the output (or `file`) parses as JSON and validates against `json_schema` |

Every assertion in a case must hold for the case to pass. An unsupported `kind` or a malformed
regex aborts the run as an authoring error rather than being reported as a skill failure.

`file` is a **modifier**, not a kind of its own. Set it on `contains`, `not_contains`, `regex`
or `equals` and that assertion reads the named [workspace](#workspaces) file instead of the
run's output text:

```yaml
    assertions:
      - kind: contains
        value: "north"
        file: report.md
```

`file:` (on any kind, `file-produced` and `json-schema` included) in a case with no
`workspace:` block is an authoring error — there would be no file to look at, so the
assertion could never hold.

A `file:` naming a file that was never produced **fails** the assertion, `not_contains`
included: an unreadable file fails regardless of kind, rather than being read as an implicit
"the value isn't there" — an author checking `not_contains` against a file the skill never
wrote should not expect that to pass.

The same split applies to what the run *put* at the path. A `file:` that could never name
a workspace file — empty, absolute, containing `..` — is an authoring error and aborts the
run. A well-formed `file:` whose target the workspace refuses to read — a symbolic link a
bundled script planted that points outside the workspace, a FIFO, a symlink loop, or a
file larger than `max_file_bytes` — **fails** the assertion, with the refusal as the
check's evidence: that is the skill's doing, not the author's. See
[The workspace](runners.md#the-workspace) for the read rules.

## Per-check results

`assertions`, `trajectory` and `budget` each report one result per declared check, not just
one verdict for the whole block — the same shape the LLM judge already uses for its rubric.
Every check carries a stable id, derived from what the case declared rather than from what the
run produced, so the same id names the same check whether the skill was loaded or not. That is
what makes [comparative evals](comparative-evals.md) able to pair up a check across the
candidate and baseline arms — including flagging one that passed either way as
[low-signal](comparative-evals.md#low-signal-checks-and-high-variance-cases).

| Source | Check id format | Example |
| --- | --- | --- |
| `assertions` | `{kind}[{index}]` — positionally stable | `contains[0]`, `regex[2]` |
| `trajectory.called` | `called:{tool}` | `called:lookup_order` |
| `trajectory.forbidden` | `forbidden:{tool}` | `forbidden:issue_refund` |
| `trajectory.order` | `order` | `order` |
| `trajectory.max_calls` | `max_calls` | `max_calls` |
| `trajectory.skill_triggered` | `skill_triggered` | `skill_triggered` |
| `trajectory.call_args` | `call_args[{index}]` — positionally stable, so two entries may name one tool | `call_args[0]` |
| `budget.max_tokens` | `max_tokens` | `max_tokens` |
| `budget.max_cost_usd` | `max_cost_usd` | `max_cost_usd` |
| `budget.max_latency_ms` | `max_latency_ms` | `max_latency_ms` |

Each check also carries **evidence** — the same text that would otherwise only appear in the
evaluator's summary `detail`. In the JSON report, every score's `checks` list carries these
ids and evidence regardless of whether a baseline ran; the console only prints evidence for
checks that failed, to keep a passing run's output short.

The "a pass without evidence is a failure" rule (see
[Judging output quality](#judging-output-quality) above) is specific to the LLM judge, where
an unsupported PASS is the characteristic failure mode it exists to defend against. It is not
applied to assertion, trajectory or budget checks — their evidence is generated
deterministically from the same comparison that produced the verdict, so it cannot go missing
independently of it.

## Where eval files are found

For each discovered skill, in order:

1. an `evals/` directory beside `SKILL.md` — every `.yaml` / `.yml` file in it, or
2. any `*.eval.yaml` file beside `SKILL.md`.

`--evals <path>` overrides discovery with an explicit file or directory. Skills with no eval
files are reported as **skipped** — visible in the output, never silently ignored.

A `tool_libraries:` entry resolves against the eval file's own directory whichever way the
file was found, so `--evals` does not change where a library is looked for.
