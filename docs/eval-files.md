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

Judging costs money, so it is opted into explicitly with `judge = "pydantic-ai"` in
[`skill-lens.toml`](configuration.md). The default `judge = "fake"` does not grade at all —
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
