# CLI

```
skill-lens run <path> [--evals <path>] [--runner <name>]... [--model <name>]
                      [--judge-model <name>] [--tag <tag>] [--case <text>]
                      [--min-pass-rate <float>]
                      [--json-output <path>] [--junit-output <path>]
                      [--markdown-output <path>] [--markdown-max-chars <int>]
                      [--concurrency <int>] [--config <file>] [--baseline <kind>]
                      [--repeat <int>] [--min-delta <float>]
                      [--keep-workspace | --no-keep-workspace]
                      [--full-output | --no-full-output]
                      [--allow-scripts | --no-allow-scripts]
skill-lens list <path> [--evals <path>]
skill-lens init <path> [--force]
skill-lens mcp-import <source> [--tool <name>]...
skill-lens --version
```

`<path>` is a skill directory or a directory of skill directories. Discovery is
recursive. `init` is the exception: its `<path>` is exactly one skill directory
containing `SKILL.md`, never a directory of skills. `mcp-import` takes no skill path at
all; its `<source>` is a saved `tools/list` response.

## `run`

Discover skills, run their eval cases, score them, and gate on the results.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |
| `--runner <name>` | `fake` | `fake`, `pydantic-ai`, `langchain`, `copilot`, `claude-code` or `cli`; repeatable — every case then runs through each runner named. `copilot`, `claude-code` and `cli` start an installed agent product and need no API key; see [Runners](runners.md) and [Product runners](runners.md#product-runners) |
| `--model <name>` | `openai:gpt-4o-mini` | Model id. Read by `pydantic-ai` and `langchain` (and by a keyed judge, `judge = "pydantic-ai"` or `"langchain"`, whose `judge_model` is unset). Passing it to a run where nothing reads it — `--runner fake`, or only product runners, with no keyed judge falling back to it — is a user error (exit 2): a flag that looks honoured while the product runs its own default model would be a mistake that is easy to miss. A product's model is set with `[runners.<name>] args` in `skill-lens.toml` |
| `--judge-model <name>` | falls back to `--model` | Model id for the LLM judge. Read by `judge = "pydantic-ai"` or `"langchain"` only; passing it under any other judge (the default `"fake"` included) is a user error (exit 2) |
| `--tag <tag>` | none | Only run cases carrying this tag |
| `--case <text>` | none | Only run cases whose name contains `<text>`, case-insensitively — copy any distinctive part of a case name out of a CI log to rerun just that case. Combined with `--tag`, both must hold. No config key: a filter is a property of one invocation |
| `--min-pass-rate <float>` | `1.0` | Required overall pass rate, `0.0`–`1.0` |
| `--json-output <path>` | none | Write a machine-readable report here |
| `--config <file>` | upward discovery | Path to `skill-lens.toml` |
| `--baseline <kind>` | off | Run a second, baseline arm: `none` (no skill loaded) or `previous` (the prior version, from git). Omit for a single-arm run. |
| `--repeat <int>` | `1` | Sample each arm this many times. Each repetition is its own outcome. |
| `--min-delta <float>` | unset | Require the candidate arm to beat the baseline by at least this much. Requires `--baseline`. |
| `--junit-output <path>` | none | Write a JUnit XML report here, for CI test panes |
| `--markdown-output <path>` | none | Write a Markdown summary here, for a job summary or PR comment |
| `--markdown-max-chars <int>` | unset | Truncate the Markdown summary to fit a comment. Detail blocks are dropped first, then gate reasons are elided behind a `+N more` count; a budget too small to hold even the verdict is cut outright. Requires `--markdown-output` |
| `--concurrency <int>` | `1` | Run this many cases at once. The work is network-bound, so the practical ceiling is your provider's rate limit |
| `--keep-workspace` / `--no-keep-workspace` | unset | Keep each case's temporary directory instead of deleting it. Overrides the `keep_workspace` config key in either direction; omitting both flags leaves the config file's value in effect. Every kept directory is printed, under a `Kept workspaces` section, whichever of the flag or the config turned keeping on |
| `--full-output` / `--no-full-output` | unset | Print a failing case's whole output instead of the first 500 characters. Overrides the `full_output` config key in either direction; omitting both flags leaves the config file's value in effect |
| `--allow-scripts` / `--no-allow-scripts` | unset | Run the scripts a skill bundles under `scripts/`. Off by default: a `SKILL.md` under evaluation is unvetted code. Overrides the `allow_scripts` config key in either direction; omitting both flags leaves the config file's value in effect. Every run that enables scripts prints `scripts: on, sandbox: <backend>` so the log shows whether an OS sandbox applied, and exits 2 before any case runs if a bundled script's interpreter is missing or `script_sandbox = "required"` finds no sandbox. See [Running bundled scripts](runners.md#running-bundled-scripts) |

Each flag overrides the corresponding key in [configuration](configuration.md).
Exit codes are documented in [Gating](gating.md). `--baseline`, `--repeat` and `--min-delta`
are covered in full in [Comparative evals](comparative-evals.md).

`--runner` may be given more than once. Every case then runs through every runner named,
each `(skill, case, runner)` is its own outcome, and the same name twice is a user error
(exit 2) — an outcome must never count twice. The flag replaces a `default_runner` list in
`skill-lens.toml`; it never appends to it. A two-runner run spends twice: the `Plan:` line
printed before the first request includes the runner factor.

A non-passing case prints what the agent actually did — its output, and every tool it
called — under the evaluator detail, in the console and in the JUnit and Markdown reports
alike. The output is cut at 500 characters by default; a cut is never silent (`… (1,842 more
characters; --full-output prints them)`). Passing cases stay one line. See
[what a failing case shows](gating.md#what-a-failing-case-shows).

`--repeat` and `--baseline` multiply spend: `--repeat 5 --baseline previous` runs 10x as many
cases as a plain run (5 repetitions x 2 arms). Before a run on a runner that spends — a
framework runner that needs an API key, or a product runner that spends the product's quota —
the CLI prints a run plan:

```
Plan: up to 2 arm(s) x 3 repeat(s) x 1 runner(s) x 4 case(s) = 24 runs
```

This is deliberately a **ceiling, not a forecast** — "up to", not "exactly". It applies the
`--tag` filter, but it does not resolve baselines or evaluate per-case arm rules (a `mode:
offered` case skips the baseline arm under `--baseline none`; a skill whose previous version
cannot be resolved skips it for that whole skill). Both of those only ever *reduce* the real
count from what the plan line shows.

`--judge-model` names the model the judge grades with, but it does not turn judging on: the
judge is selected by the `judge` key in [`skill-lens.toml`](configuration.md#judging), which
defaults to `"fake"` so that upgrading never starts spending money on its own. Because the
flag is read only by `judge = "pydantic-ai"` or `"langchain"`, passing it under any other
judge is a user error (exit 2) rather than a flag that silently did nothing. `--model` follows
the same rule: it is read by the two framework runners, and by a keyed judge whose
`judge_model` is unset; a run that names neither — `--runner fake`, or product runners only —
refuses it, and a product's model is set with `[runners.<name>] args` in
[`skill-lens.toml`](configuration.md#product-runners) instead. A blank model id is rejected
as a user error (exit 2) rather than reaching a provider.

## `list`

Show the skills that would be evaluated and how many cases each has. Discovers and
validates every eval file without calling a runner — free, and no API key required.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |

```bash
uv run skill-lens list ./examples
```

```
csv-report	1 case(s)	examples/csv-report
greeting	1 case(s)	examples/greeting
log-triage	1 case(s)	examples/log-triage
order-support	5 case(s)	examples/order-support
```

## `init`

```bash
skill-lens init <path> [--force]
```

`<path>` is either one skill directory containing `SKILL.md`, or a directory of skill
directories — `init` discovers recursively, exactly as `run` and `list` do.

**One skill.** Writes a starter eval suite of five cases: a common-case case, a policy-edge
case carrying `tools:` and `trajectory:`, both halves of the `mode: offered` triggering pair,
and a `workspace:` case with `file-produced` and `contains ... file:` assertions for a skill
that produces a file (delete it if yours does not).

The file goes where the skill already keeps its evals: `<skill-dir>/evals/<skill-name>.eval.yaml`,
unless the skill has `*.eval.yaml` beside `SKILL.md` and no `evals/` directory, in which case
it goes beside `SKILL.md` too. Discovery prefers `evals/` when it exists, so `init` never
creates that directory next to files it would hide.

Every field you have to supply holds the placeholder `TODO(skill-lens)`, and a case still
containing one aborts the run as an [authoring error](eval-files.md#unfilled-scaffolds).
The generated file is therefore never a green suite that checks nothing.

| Flag | Meaning |
| --- | --- |
| `--force` | Overwrite an existing eval file. Without it, an existing file is a user error. One skill only. |

**A directory of skills.** Every skill with no eval file gets a scaffold; every skill that
already has one is skipped and named. Nothing existing is ever rewritten, and `--force` is
a user error in this mode — rewriting every suite in a repository must never be one flag
away.

```
Wrote skills/refund/evals/refund.eval.yaml
Skipped order-support: already has 1 eval file(s)
Wrote skills/triage/evals/triage.eval.yaml
Fill in every TODO(skill-lens), then run: skill-lens list skills
```

Exit `0` on success, including when every skill already had a suite (`Nothing to do`).
Exit `2` when the path holds no `SKILL.md` anywhere under it, when a `SKILL.md` is
malformed, when a target file exists and `--force` was not given (one skill), when `--force`
is given for a directory of skills, or when a file cannot be written.

## `mcp-import`

```bash
skill-lens mcp-import <source> [--tool <name>]... > tools.yaml
```

Turns a saved MCP `tools/list` response into a `tools:` block you paste under a case.
MCP (Model Context Protocol) is the protocol an agent uses to discover and call the tools
a separate server exposes; `tools/list` is the request that returns each tool's name,
description and the JSON Schema of its arguments. Hand-copying that into a mock is where
the mock drifts from the server it stands in for — this command copies it instead.

`<source>` is a JSON file, or `-` to read stdin. Any of the three shapes people save is
accepted: the whole JSON-RPC response (`{"jsonrpc": ..., "result": {"tools": [...]}}`),
just its result (`{"tools": [...]}`), or the bare array (`[...]`). To capture one, ask any
MCP client for the listing. The inspector's CLI mode prints the listing as JSON:
`npx @modelcontextprotocol/inspector --cli <your server command> --method tools/list >
tools.json`. Claude Code's `/mcp` panel lists the same schemas.

| Flag | Meaning |
| --- | --- |
| `--tool <name>` | Import only this tool. Repeat the flag for several. A name the listing does not carry is a user error, and the message lists the names it does. |

**What is copied, and what is not.** Each tool's `name` and `inputSchema` are copied
verbatim — the schema lands in [`input_schema:`](eval-files.md#mock-tools), so the agent
sees exactly what the live server would declare, optional arguments and all. `description`
is copied when the server gives one. `returns:` is **always** the `TODO(skill-lens)`
placeholder: the listing says nothing about what a call returns, and inventing a value
would be exactly the silent drift the import exists to remove. A missing description gets
the placeholder too. Until you replace them, the file refuses to run as an
[authoring error](eval-files.md#unfilled-scaffolds) rather than passing a case that checks
nothing. When the server declares an `outputSchema`, it is written as a comment above
`returns:` so you can shape the value against it.

```yaml
# Written by `skill-lens mcp-import`. Replace every TODO(skill-lens); until you do,
# skill-lens refuses to run the file rather than pass a case that checks nothing.
tools:
  - name: get_pull_request
    description: Get details of a specific pull request
    input_schema:
      type: object
      properties:
        owner:
          type: string
        pull_number:
          type: integer
      required:
      - owner
      - pull_number
    # The server declares this output schema; shape `returns` to match it:
    #   {"properties": {"number": {"type": "integer"}}, "type": "object"}
    returns: TODO(skill-lens) the JSON this tool returns
```

Tool names are kept as the server spells them — `get-pull-request` stays hyphenated,
because the mock has to answer to the name the skill's prose and the live server use. A
name outside `^[A-Za-z0-9_-]{1,64}$` (the rule OpenAI and Anthropic enforce) is a user
error naming the tool, never a rewrite.

Stdout carries nothing but the block, so `> tools.yaml` captures exactly it; every error
goes to stderr. The command never touches the network. Exit `0` on success; exit `2` for
an unreadable file, invalid JSON, a shape that is not a `tools/list` listing, a listing the
server answered with an error, a tool without an `inputSchema` (or one that is not a valid
JSON Schema of `type: object` — the case loader would refuse it too), a name no provider
would register, a listing that carries `nextCursor` (one page of several — capture every page),
or an unknown `--tool`.

## `--version`

Print the installed version and exit.
