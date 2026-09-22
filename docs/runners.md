# Runners

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent there are three ways: the `pydantic-ai` framework extra, the `langchain`
framework extra, or an installed agent product (`copilot`, `claude-code`, or a
command you name as `cli`). A framework extra needs a key in the environment and a
model; a product needs neither, because it uses its own auth — see
[Product runners](#product-runners). With a framework extra:

```bash
uv tool install "skill-lens[pydantic-ai]"       # or "skill-lens[langchain]", or both
export OPENAI_API_KEY=...
skill-lens run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
skill-lens run ./skills --runner langchain --model openai:gpt-4o-mini
skill-lens run ./skills --runner pydantic-ai --runner langchain --model openai:gpt-4o-mini
```

From a checkout instead, the extra comes from `uv sync --extra pydantic-ai` (or
`--extra langchain`) and every command runs as `uv run skill-lens ...`.

## Two frameworks, one measurement

| Runner | Extra | Agent loop | Bundled providers |
| --- | --- | --- | --- |
| `pydantic-ai` | `skill-lens[pydantic-ai]` | PydanticAI `Agent` | OpenAI, Anthropic |
| `langchain` | `skill-lens[langchain]` | LangChain 1.x `create_agent` (LangGraph underneath) | OpenAI, Anthropic |

Both runners receive the same inputs — the same system prompt built by one shared
function, the same mock tools, the same offered-mode skill tool, the same workspace
tools, and the same [bundle tools](#bundled-files-and-scripts) (`list_skill_files`,
`read_skill_file`, and `run_script` under `allow_scripts`) — and produce the same
`RunResult`, so a case passing under one and failing under the other says something about
the skill's instructions, not about the harness. The judge is chosen separately
(`judge = "pydantic-ai"`, `"langchain"`, or a product — see
[Judging with a product](#judging-with-a-product)), and one judge grades every runner's
output.

Naming both — `--runner` repeated, or `default_runner = ["pydantic-ai", "langchain"]` in
`skill-lens.toml` — runs every case through each in one invocation and produces one
report, with one outcome per `(skill, case, runner)` and the runner named on every case
line. That is the cross-framework measurement: a skill whose instructions hold up under
two agent loops is more likely to hold up under a third. Every outcome counts toward the
[gate](gating.md#more-than-one-runner), and the same runner twice is refused so none
counts double. The `Plan:` line includes the runner factor, because two runners spend
twice; see [CLI](cli.md) for the flag and [CI](ci.md#running-the-matrix) for a
matrix job.

`--model` is passed to each framework unchanged. `openai:` and `anthropic:` are spelled
the same in both; other providers differ (PydanticAI `google-gla:`, LangChain
`google_genai:`) and need their provider package installed beside the extra
(`pip install langchain-google-genai`, or `pydantic-ai-slim[google]`).

Per turn, LangChain reports token usage on each model response and the served model
name in the response metadata; the runner sums the former and reads the latter from the
last response that carries one. A LangChain judge that returns a structured verdict the
schema cannot parse is recorded as **errored**, never as a failed check — an unreadable
verdict is an infrastructure signal, not a low score.

API keys are read from the environment only — never from `skill-lens.toml`.
`skill-lens` checks for the key before making any request, so a missing key costs
nothing and exits 2.

## Product runners

A skill written for a named product — GitHub Copilot CLI, Claude Code — is deployed into
that product's skill directory and loaded by that product's own mechanics. A framework
runner approximates that; a **product runner** starts the product itself, in its
non-interactive mode, with the skill placed where the product discovers skills, and reads
the result from the product's machine-readable trace. No provider API key is involved: the
product uses its own auth.

```bash
skill-lens run ./skills --runner copilot
skill-lens run ./skills --runner claude-code
skill-lens run ./skills --runner copilot --runner pydantic-ai --model openai:gpt-4o-mini
```

| Runner | Starts | Skill directory | Trace |
| --- | --- | --- | --- |
| `copilot` | `copilot -p <prompt> --allow-all-tools --output-format json --no-custom-instructions --no-auto-update` | `.agents/skills/<name>/` | Copilot's JSONL (one JSON object per line) |
| `claude-code` | `claude -p <prompt> --output-format stream-json --verbose --dangerously-skip-permissions --setting-sources project --strict-mcp-config --no-session-persistence` | `.claude/skills/<name>/` | Claude Code's `stream-json` |
| `cli` | the `command` in `[runners.cli]` | `skills_dir` (default `.agents/skills`) | none — stdout is the output |

The flags are the verified minimum: what the product needs to run without a terminal,
what emits the trace, and what keeps *your* setup out of the eval. `--no-custom-instructions`
stops a stray `AGENTS.md` or a personal instructions file shaping a Copilot run;
`--setting-sources project --strict-mcp-config` keeps your own hooks, plugins and MCP
servers out of a Claude Code run while the project skill is still discovered and
OAuth auth still works; `--no-session-persistence` writes nothing under `~/.claude`.
Personal skills and plugins under `~/.copilot` do still load for Copilot — that is the
product as you have it; for a hermetic run (one that loads nothing from your personal
setup) point `COPILOT_HOME` at an empty directory and set `COPILOT_GITHUB_TOKEN`. Add flags
with `[runners.<name>] args` (for example, a model); replace the whole argv with `command`.
See
[Configuration](configuration.md#product-runners).

**What the product sees.** The eval's working directory (the case's workspace when it
declares one, a fresh temporary directory otherwise) holds `SKILL.md` **verbatim (its
text as written; line endings are normalised)** —
products honour frontmatter keys skill-lens does not model, such as `allowed-tools` — and
beside it `scripts/`, `references/` and `assets/`, nothing else. The prompt is the case's
`task`, verbatim: the product owns its system prompt, and skill-lens adds no preamble.
`mode: loaded` invokes the skill through the product's own spelling (`/<name> <task>` for
both presets; `invoke` under `cli`, default `{task}`); `mode: offered` sends the bare task
and reads the product's own load signal — the `skill` tool call in Copilot, the `Skill`
tool call in Claude Code — so a negative control is measured, never assumed. Copilot's
`skill.invoked` event is the slash invocation's: a skill the model chose itself is a
request for the `skill` tool and no event, so the parser reads both. Under
`--baseline none` the baseline arm has no skill directory and gets the bare task in both
modes; under `--baseline previous` the previous version is delivered with its own bundle.

**What a product runner can measure.**

| | `copilot` | `claude-code` | `cli` |
| --- | --- | --- | --- |
| Output text and `assertions:` | yes | yes | yes (stdout) |
| `trajectory:` (the product's own tool names, e.g. `bash`, `Bash` — spelled as the product spells them; skill-lens cannot list a product's tools, so a name is not checked) | yes | yes | no — an authoring error |
| `mode: offered` / `skill_triggered` | yes | yes | no — an authoring error |
| `budget: max_tokens` (input + cache read + cache write, plus output) | when the trace reports usage; otherwise a failing "not evaluated" check | when the trace reports usage; otherwise a failing "not evaluated" check | failing "not evaluated" check |
| `budget: max_cost_usd` | failing "not evaluated" check — Copilot bills per premium request (its billing unit: one counted request, not tokens), and the note says how many | yes, at list price (the provider's published per-token price), as the product reports it in `total_cost_usd` | failing "not evaluated" check |
| `budget: max_latency_ms` | yes | yes | yes |
| `tools:` (mock tools) | authoring error | authoring error | authoring error |

Token counts under a product runner include the product's own system prompt and tool
definitions — tens of thousands of tokens for a one-line answer under Claude Code. A
`max_tokens` budget written for a framework runner will not transfer to a product runner;
set a separate budget, or run budget cases through the framework runners only.

Tool calls are what the model *requested* (Copilot's `toolRequests`, Claude Code's
`tool_use` blocks), not what executed — a refused call was still the model's choice, the
same rule the framework runners apply. A case the runner cannot serve is an **authoring
error** (exit 2) found in preflight, before any case runs and before any quota is spent —
never a vacuous pass. A `trajectory:` name is not among the things checked: under a product
it names one of the product's own tools, which skill-lens cannot enumerate, so a misspelled
`called: [bassh]` is a failing check whose evidence says the tool was never called and whose
failure excerpt shows the calls that did happen — read those before blaming the skill. `--model` is not read by a product runner: a flag nothing reads is
refused as a user error rather than silently ignored; set the product's model in its table.

**Errors.** A timeout (`timeout_seconds`, default 600), a non-zero exit, a product-reported
failure, a trace cut short by `max_output_bytes`, a prompt over 100 KiB (the prompt travels
as one argument, and Linux caps one at 128 KiB), and a missing executable at run time are
all **errored** cases — never raised, never failed. A complete trace carrying the product's
own error message is reported in preference to the exit code; a cut trace names the cap to
raise. Preflight checks the executable is on `PATH` and, for the two presets, that it
actually starts (`--version`); `cli` has no version command, so only the `PATH` lookup
applies to it. The report names the product, its executable and — where there is one — its
version on every run.

**Trust.** The product runs with permission prompts disabled and inherits your whole
environment — it needs its own auth, and cannot run non-interactively otherwise. No
skill-lens sandbox applies; the skill's bundled scripts are reachable through the product's
own shell whatever `allow_scripts` says, which governs only skill-lens's `run_script` tool.
**Naming a product runner is that decision**, and every report says so. See
[Security](security.md#product-runners).

### Judging with a product

`judge = "copilot"`, `"claude-code"` or `"cli"` grades every `judge:` block through that
product, started from the same `[runners.<name>]` table the runner uses — `command`, `args`,
`timeout_seconds` and `max_output_bytes` all apply; `skills_dir` and `invoke` do not, because
no skill is delivered. A repository can run under one product and grade under another, or
under a framework judge, or grade a framework runner's output through a product: the judge is
chosen by the `judge` key alone. No provider API key is needed.

```toml
default_runner = "claude-code"
judge = "claude-code"           # grades through the same product as the runner
```

**The prompt.** The shared judge prompt — the same grading rules, the same fenced response
and artifacts the framework judges send — goes in as one text prompt, because a product has
no structured-output mode and its system prompt is its own. One closing line asks for a
single JSON object and nothing else: `{"checks": [{"id": ..., "passed": ..., "evidence":
...}, ...]}`, one entry per rubric check. The judge runs in an empty temporary directory
with no skill delivered: it grades text, it never invokes the skill under test, and the
directory is removed after every call. Each preset grades with its tool restriction
appended after the table's `args`, so it has no tools at all while it judges: Claude Code
with `--tools ""`, Copilot with `--available-tools=skill-lens-none`. Copilot's flag keeps
only the tools it names and ignores an empty list, so the list names one tool that does not
exist and the model is left with none, built-in and MCP alike — verified against `copilot`
1.0.37 by reading the tool list it sends the model, which is empty; `--excluded-tools` takes
no wildcard, and `--deny-tool` governs approval prompts, not what the model sees. A
`command` that names another executable drops the restriction along with the version probe,
because a wrapper is not known to accept it; `cli` has none. The table's `args` or `command`
may not carry that same flag (`--tools`, `--available-tools`) when the product judges: both
products accumulate a repeated flag rather than taking the last one (verified: `claude
--tools Bash --tools ""` runs Bash; Copilot sends `bash` under `--available-tools=bash
--available-tools=skill-lens-none`), so the entry would give the judge tools back, and
preflight refuses it as a user error (exit 2) naming the entry, before any case runs. A
graded response that reads
like an instruction then has nothing to act with — the prompt says the response is untrusted
data, but that is a request, not a guarantee; the restriction is what makes acting on it
impossible, and it does not stop such an instruction from swaying the verdict itself; see
[Security](security.md#product-runners).

**The verdict.** Exactly one top-level JSON object in the reply is the verdict: the first
balanced `{ ... }` — the earliest `{` whose `}` closes it, braces inside JSON strings not
counted — is validated strictly as `JudgeOutput`: a `checks` list of `{id, passed,
evidence}` entries, and no other key beside it. Prose around it and a code fence are fine,
and a brace pair in prose (`{name}`, say) is not an object — only a span that parses as
JSON counts. Two or more such objects — for example a graded response that quotes a forged verdict
before the model gives its real one — is an unreadable verdict, never a choice between
them: it is refused the same way as no object at all, not resolved silently in the earlier
one's favour. A reply with no readable verdict — prose only, a cut-off object, the wrong
shape, an empty object, a key beside `checks`, two or more top-level objects — is an
**errored** case (`judge failed: JudgeOutputInvalid: ...` naming the mismatch), never a low
score: the same rule as the framework judges, because an unreadable verdict is an
infrastructure signal. From there the verdict is handled as under every judge: a verdict
whose check ids do not match the rubric is errored, and a pass with no evidence (an empty
or missing `evidence`) is recorded as a failure. A product failure — a timeout, a non-zero
exit, a reply over `max_output_bytes`, a prompt over 100 KiB, an executable that vanished
after preflight — is errored the same way.

**What is read and what is not.** `judge_model` and `judge_temperature` are not read: no
product exposes a temperature, and a product's model is set with `[runners.<name>] args`.
`--judge-model` with a product judge is a user error (exit 2). Tokens, cost and the model
come from the product's trace exactly as for the runner — Claude Code reports cost at list
price, Copilot reports premium requests as a note, `cli` reports neither usage nor cost — and
are reported as judge overhead, never against the case's `budget:`. Preflight finds the
judge's executable and, for a preset, runs its `--version`, before any case runs; a product
serving as both runner and judge is listed once on the report.

## Declaring tools and scoring the trajectory

An eval case can declare the tools the agent may call. Nothing executes: a tool
records the call and returns its canned value, so the trajectory is the model's
own choice and the run has no side effects.

```yaml
cases:
  - name: checks the order before refusing
    task: I want a refund for order 1234
    tools:
      - name: lookup_order
        description: Look up an order by its id
        parameters:
          order_id: string
        returns: '{"id": "1234", "days_since_delivery": 45}'
      - name: issue_refund
        description: Issue a refund for an order
        parameters:
          order_id: string
        returns: '{"ok": true}'
    trajectory:
      called: [lookup_order]        # each of these ran
      forbidden: [issue_refund]     # none of these ran
      order: [lookup_order]         # ran in this relative order
      max_calls: 3                  # no looping
      call_args:                    # and what a call carried
        - tool: lookup_order
          contains: {order_id: "1234"}
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 30000
    assertions:
      - kind: contains
        value: "1234"
```

`order` is a relative subsequence: unrelated calls may appear in between, but the
listed tools must not appear out of sequence.

### What a tool was called with

`called` proves a tool ran; `call_args` proves what it ran *with* — the difference between
a skill that asked for `status: active` and one that fetched everything and filtered
afterwards, which `called` alone cannot see. Each entry names one declared tool and one
expected argument shape:

```yaml
    trajectory:
      call_args:
        - tool: list_pull_request_threads
          contains: {status: active}       # a subset: these keys, with these values
          every: true                      # on every call, not just one
        - tool: reply_to_thread
          equals: {thread_id: 42, body_file: reply.md}   # the whole argument dict
```

The match is structural, against the argument dict the runner recorded — never against a
serialised string, so key order and whitespace cannot matter:

- **`contains` is a subset at every level.** A mapping matches when every key the entry
  names is present with a matching value; keys the call carried but the entry did not name
  are ignored. A list matches element by element at the same length — `[bug]` does not
  match `[bug, urgent]`. Anything else is a scalar and must be equal.
- **`equals` is the same comparison, except a mapping must carry exactly the keys the entry
  names.** `equals: {}` asserts the tool was called with no arguments at all.
- **Nothing is coerced.** `"1"` never equals `1`, and a boolean never equals a number —
  Python would say `True == 1`, and an author who wrote `limit: 1` must not pass on a call
  that sent `true`. Bare `yes` / `no` / `on` / `off` stay strings, as everywhere in an eval
  file.

By default an entry holds when **at least one** call to the tool matched. `every: true`
requires every call to match — "it never fetched unfiltered" rather than "it eventually
filtered". Under either, a tool that was never called fails the check: "every call
matched" over zero calls would be a pass nobody verified. An entry carries exactly one of
`contains` and `equals`, and `contains: {}` is refused — it matches every call, which is
`called:` spelled longer. Both are authoring errors (exit `2`), caught at load time.

Each entry is its own check — `call_args[0]`, `call_args[1]`, … in file order — so two
entries may name the same tool. A failing check's evidence shows the arguments every call to
that tool carried, as one line of JSON cut at a fixed length with the number of characters
removed stated; the full calls are in the failure excerpt every reporter prints for a
non-passing case. Arguments a runner could not parse sit under a `_raw` key and never match
a structural check, with the raw text in the evidence — a capture problem reads as a failed
check that shows the payload, never as a pass.

A tool declares its arguments with the `parameters:` shorthand shown above, or with a full
`input_schema:` when it must match a real server's declared JSON Schema; see [Mock
tools](eval-files.md#mock-tools). The shorthand is closed — every key required,
`additionalProperties: false` — because the author wrote every key. A declared
`input_schema` is handed to the agent verbatim, because fidelity to the server it stands in
for is its reason to exist. Each framework may still normalise it on the way to the
provider — LangChain inlines `$ref` and drops `$defs`, PydanticAI passes it untouched — so
the schema in a request log can differ in spelling from the eval file while meaning the
same thing. [`skill-lens mcp-import`](cli.md#mcp-import) writes one from the server's own
`tools/list` listing.

A tool declared in a [tool library](eval-files.md#sharing-tools-across-eval-files) and
named with `ref:` is resolved by the case loader before any runner is involved, so a
runner never sees a reference — only the `ToolSpec` it named, with the case's own
`returns:`.

`returns:` may also be a list — of strings, consumed in call order with the last one
repeating, or of `when:`/`value:` entries matched against the call's arguments; see
[Answering differently per call](eval-files.md#answering-differently-per-call). The
runner builds every mock afresh for each run, and for each *attempt* of a run: a
transient provider failure that is retried starts a new conversation, and its tools start
from the top of the sequence too, so the retried attempt is shown exactly what the first
one was. A sequence's counter is locked, so a framework that runs several calls from one
model turn in parallel still hands out each entry once — in whichever order it ran them.

Under `fake`, `pydantic-ai` and `langchain`, every tool name in `called`, `forbidden`,
`order` or a `call_args` entry's `tool` must be declared in that case's `tools:` — or be
a built-in, in a case with a `workspace:` block — including `forbidden`, since forbidding
a tool the agent was never offered in the first place is a check that can never fire. A
name that isn't declared is an
authoring error (the run aborts, exit `2`), not a failing case, because a check that can
never pass tells you nothing about the skill.

That check is the runner's, made in preflight for the cases that will run under it — before
any case runs and before any spend — rather than the eval file's, because which tools a case
has depends on the runner: under a [product runner](#product-runners) the same block names
the product's own tools (`Bash`), which skill-lens cannot list, so no name is refused there.
One invocation may run one case through both kinds. `skill-lens list` calls no runner, so it
accepts any name; `run` is where the rule applies.

## The workspace

A case that declares a [`workspace:`](eval-files.md#workspaces) block gets a real,
contained temporary directory in addition to the canned tools above. `list_files`,
`read_file` and `write_file` are the only way in or out of it.

**Containment.** Every path a tool is given is resolved relative to the workspace root and
then checked against it: an absolute path, one containing `..`, or one that resolves outside
the root through a symlink is refused before it touches the filesystem. The root itself is
resolved once, at creation — on macOS `/tmp` is a symlink to `/private/tmp`, and comparing an
unresolved root against a resolved candidate path would make every containment check compare
two spellings of the same directory.

**Regular files only.** A path that exists and is neither a regular file nor a directory —
a FIFO (a named pipe), a device, a socket — is refused, and so is a symbolic-link loop,
whichever exception the running Python raises for it. The check is a `stat`, never an
`open`: opening a FIFO blocks until the other end connects, which no reader in skill-lens
ever is, so a bundled script that planted one under the name an assertion reads would
otherwise hang the run. The same rule applies to the skill's bundle, so `read_skill_file`
cannot block on a FIFO committed to the repository either.

**Reads are capped too.** `read_file`, a `file:` assertion and a judge artifact refuse a
file larger than `max_file_bytes` before reading a byte of it — `refused: report.md is
2,000,001 bytes; max_file_bytes is 1,000,000`. `read_file` uses the
[configured](configuration.md) value; the assertion, the judge and `read_skill_file` use
the built-in default of 1 MB. A sparse file has whatever apparent size a script gives it at
almost no cost on disk, so the cap on `st_size` is what bounds what reaches a model or an
evaluator. A `file:` assertion scores the refusal as a failed check; the judge sees the
artifact rendered as `(too large to read)` — distinct from `(not produced)`, because the
file does exist. `file-produced` is unaffected: it asks whether the file exists, not whether
it is readable.

**The caps.** Three [configured](configuration.md) limits — `max_file_bytes`, `max_files`,
`max_total_bytes` — bound what one case may write. They default to roughly 100x a realistic
artifact (a report or a JSON file is kilobytes), so they only bind when a run is genuinely
stuck — writing the same file repeatedly, or many small ones — never on a skill that
legitimately produces something large. A refusal always names the limit it hit and that
limit's value, e.g. `refused: report.md would be 1,200,000 bytes; max_file_bytes is
1,000,000` — a generic "too large" would leave you guessing which of the three caps stopped
you and what to raise it to.

**A built-in tool never raises.** A refused path, a file that does not exist, content that
is not valid UTF-8 — every one of those comes back to the model as an ordinary tool-result
string, never an exception. The model choosing a bad path is a fact about the run worth
scoring; an exception would turn it into an infra error and hide that signal.

**The workspace preamble is identical in both arms.** Telling the agent a working directory
exists — and how to use `list_files`, `read_file` and `write_file` — is text appended to
whatever system prompt the arm already has, byte-for-byte the same whether the skill is
loaded, replaced by the neutral baseline preamble, or offered as a tool under `mode:
offered`, and it names no skill. Added to the candidate arm only, that text would itself
become part of what `--min-delta` measures. The bundle tools below add nothing to it; they
describe themselves.

`--keep-workspace` (see [CLI](cli.md)) keeps every case's directory instead of deleting it
after scoring, for inspecting exactly what a run wrote. Every kept directory is printed,
however keeping was turned on.

## Bundled files and scripts

The Agent Skills layout puts three directories beside `SKILL.md`: `scripts/`,
`references/` and `assets/`. When at least one of them exists, a case with a `workspace:`
block also gets:

| Tool | Does | Needs |
| --- | --- | --- |
| `list_skill_files` | List the bundled files, one path per line, relative to the skill's directory | a bundle |
| `read_skill_file` | Read one bundled text file, e.g. `references/style.md` | a bundle |
| `run_script` | Run a file under `scripts/` with the workspace as its current directory | a file under `scripts/` **and** `allow_scripts` |

Only those three directories are reachable. `SKILL.md` itself, `*.eval.yaml` and `evals/`
are refused — `refused: 'SKILL.md' is not under scripts/, references/ or assets/; only
those three directories are readable` — because an eval file holds the expected answers,
and a tool that could read it would hand the agent its answer key. A binary file under
`assets/` gets the same `not valid UTF-8 text` message `read_file` gives; copying one into
the workspace is not supported yet. The tools describe themselves without naming the skill:
the instruction to run `scripts/count.py` comes from `SKILL.md`, which is what is under
measurement, and the workspace preamble is unchanged and still identical in both arms.

The bundle tools need the `workspace:` block because the workspace is the script's working
directory and, with its scratch directory, the only host area the sandbox lets it write
to. A case without one gets no bundle tools, and a `trajectory:` naming one there is the
same authoring error the workspace tools already raise. The tools are registered in
`mode: offered` cases too — an agent that declines the skill has no reason to call them,
and one that triggers it needs them exactly as a `loaded` case does. All six built-in
names are reserved in every workspace case; see [Workspaces](eval-files.md#workspaces).

`examples/log-triage` is a skill that can only pass by running its script: the counts its
case asserts are what `scripts/count_levels.py` prints for the seeded log, and a model that
guesses gets them wrong. It also reads `references/report-format.md` for the layout of the
file it writes.

### Running bundled scripts

`run_script(path, args)` runs the interpreter configured for the file's extension
([`script_interpreters`](configuration.md#bundled-scripts)), with the workspace as the
current directory and the model's `args` as command-line arguments, and returns:

```
exit code: 0
stdout:
ERROR: 4
INFO: 3
WARN: 2
stderr:
(empty)
```

A script that runs past the timeout gets `stopped after 30 s (script_timeout_seconds)` in
place of the exit line. Every other outcome is text the model reads too:

| What happened | What the model reads |
| --- | --- |
| The script exited non-zero | `exit code: 1`, then both streams as above |
| A path outside `scripts/` | `refused: 'references/x.md' is not under scripts/; only bundled scripts can be run` |
| No such script | `refused: no such script 'scripts/x.py'; bundled scripts: scripts/count_levels.py` |
| An extension with no interpreter | `refused: 'scripts/x.rb' has no configured interpreter; script_interpreters allows: py, sh` |
| The interpreter is not on `PATH` | `refused: scripts/x.py needs python3, which is not on PATH` |
| The interpreter will not start, or an argument holds a NUL byte | `refused: cannot start python3: <error>` |

None of these is `errored`: an unrunnable script is a fact about the skill, and the model
reading that fact is the eval signal. `run_script` never raises. Standard input is
`/dev/null`; a script that needs input takes it as arguments or from a workspace file.

**The portable guards**, on every platform:

- The environment is rebuilt from an allowlist — `PATH`, `HOME`, `LANG`, `LC_ALL`,
  `LC_CTYPE`, `TZ`, plus `SystemRoot`, `COMSPEC`, `PATHEXT` and `USERPROFILE` on Windows,
  where Python does not start without them — and `PYTHONDONTWRITEBYTECODE=1` and
  `PYTHONIOENCODING=utf-8` are set. Nothing else skill-lens holds reaches the script, the
  provider key first among them: it is absent by construction, not by remembering to delete
  it. What the allowlist prevents is *inheritance*. A script runs as the same user as
  skill-lens, and an operating system lets a same-user process ask the kernel for another
  process's environment — the block copied at `exec`, which is where the provider key sits
  for the whole run. On Linux that is `/proc/<pid>/environ`; when scripts are enabled
  skill-lens marks itself non-dumpable (`prctl(PR_SET_DUMPABLE, 0)`), which makes its
  `/proc/<pid>/*` root-owned so a same-user read is refused — root ignores that, and the
  report says whether it applied (`scripts: on, sandbox: none (bwrap not found on PATH);
  harness environment hidden from same-user processes (PR_SET_DUMPABLE)`). Under `bwrap`
  the harness is in another PID namespace and has no `/proc` entry to read. On macOS the
  read is the `kern.procargs2` sysctl behind `ps -E`, and nothing closes it: `sandbox-exec`
  cannot execute `/bin/ps` (it is setuid root), but a script can call the sysctl directly,
  and that read is not gated by the sandbox at all — verified against a blanket `(deny
  sysctl-read)` that refused every other sysctl in the same process. So on macOS, and on a
  Linux runner without `bwrap`, a script that goes looking can find the harness's provider
  key. Set `script_sandbox = "required"` wherever a provider key is present, and treat a
  macOS run with scripts on as one that exposes that key to the skill under test.
- `TMPDIR`/`TMP`/`TEMP` point at a scratch directory outside the workspace, deleted after
  the call, so a script's temporary files never appear in `list_files`, `file-produced` or
  the judge's artifacts.
- A script runs with `shell=False`, its arguments as argv. Nothing the model sends is ever
  joined into a command line.
- The script's whole process group is killed after **every** exit — a normal one as much
  as a wall-clock timeout (`script_timeout_seconds`) — so a script that starts `sleep 1000`
  and exits at once leaves nothing behind. Where Python provides `os.waitid` — Linux, and
  macOS from Python 3.13 — the exit is observed before the leader is reaped, the group is
  killed, and only then is it reaped, so the pid cannot have been handed to an unrelated
  process by the time the kill runs; elsewhere the child is waited for first and the group
  killed after, which is still safe because POSIX never hands out a pid while a process
  group with that id exists. One honest limit: the kill is
  `os.killpg`, so a script that calls `os.setsid()` leaves that group and survives it on
  every POSIX platform; only the `bwrap` backend closes that gap (`--unshare-pid` puts the
  script in its own PID namespace and `--die-with-parent` takes it down with the sandbox)
  — and a stock Ubuntu runner may not have a working `bwrap`. On Windows the kill is
  `taskkill /T /F`, which walks the tree from the parent: it covers the timeout, where the
  parent is still alive, but after the parent has exited on its own it finds no tree, so a
  background process a script started stays running there.
- stdout and stderr are written to files in the scratch directory, not held in memory, and
  read back capped at `max_script_output_bytes` each; a cut ends with `... [truncated, N
  bytes omitted]` stating the exact count. The harness reads them through the descriptors
  it opened before the process started, never by re-opening the path, so a script cannot
  swap a symlink or a FIFO into their place.
- A script writes to disk directly, so the workspace caps cannot refuse it beforehand.
  After the call the directory is measured, and if it is over `max_files` or
  `max_total_bytes` the tool result ends with a `warning:` line (`warning: the working
  directory now holds 12,345,678 bytes; max_total_bytes is 5,000,000`) and every later
  `write_file` is refused. Nothing bounds what a script can *leave* on disk — a sparse file
  has any apparent size at almost no cost — so the bound that matters is on what is read
  back: every reader refuses a file over `max_file_bytes` before opening it (see
  [The workspace](#the-workspace)).

**The OS sandbox**, where one exists (`script_sandbox = "auto"`, the default), is a second
layer on top. Under either backend a script cannot open a network connection; cannot write
to the host filesystem outside the workspace and its scratch directory (under `bwrap`,
writes under the temporary directory and `/dev/shm` land in an in-memory mount that is
discarded when the script exits — bounded, like output, only by the timeout); and cannot
read anything under the system temporary directory except the workspace, the scratch
directory and the skill's own bundle — so under `--concurrency N` a script cannot read the
baseline arm's workspace or another case's scratch directory. One difference between the
two: `bwrap`'s `--unshare-net` isolates the network stack only, so a Unix-domain socket the
CI user can reach through the filesystem — `/var/run/docker.sock` is the usual one — is
still connectable there, whereas macOS's `(deny network*)` covers Unix sockets too. A
runner whose user can reach the Docker socket should not run scripts you would not run by
hand. The bundle is allowed back
explicitly because a `--baseline previous` bundle is extracted under that temporary
directory. Reads anywhere else are allowed (see below).

| | Backend | How |
| --- | --- | --- |
| macOS | `sandbox-exec` | A profile that allows everything, then denies `network*` and `file-write*`, re-allows writes under the workspace and the scratch directory (plus `file-write-data` on `/dev/null`), denies `file-read*` under the temporary directory, then re-allows reads under the workspace, the scratch directory and the bundle. Later rules win. `sandbox-exec` is marked deprecated in Apple's documentation, is present and working on current macOS, and is what Bazel, Chromium and Claude Code use. |
| Linux | `bwrap` (bubblewrap) | `--ro-bind / /`, `--dev /dev`, `--proc /proc`, an empty `--tmpfs` over the temporary directory so sibling workspaces vanish, `--bind` for the workspace and the scratch directory, `--ro-bind` for the bundle, then `--unshare-net --unshare-pid --die-with-parent --new-session`. Needs unprivileged user namespaces or a setuid install. A stock Ubuntu CI image may not ship `bwrap`, or may refuse unprivileged user namespaces; the report's `sandbox:` line says which. `--unshare-net` does not block Unix-domain sockets (see above). |
| Windows | none | The portable guards only. `"required"` refuses to run. |

The probe runs once per run, after discovery and before any case. The backend is
*executed* — `sandbox-exec -p '(version 1)(allow default)(deny network*)' /usr/bin/true`, or
`bwrap --ro-bind / / --dev /dev --proc /proc --unshare-net --unshare-pid --die-with-parent
-- /bin/true` — not merely found on `PATH`, because present is not the same as working: a
stock Ubuntu runner may not ship `bwrap` at all, or may refuse unprivileged user namespaces,
and the first line of that refusal is what the report shows. The result is on every report
that enabled scripts —
`scripts: on, sandbox: bwrap`, or `scripts: on, sandbox: none (bwrap not found on PATH)` —
so you can tell from a log whether the isolation you expected applied. `"required"` turns a
missing backend into exit 2 before any money is spent. The probe fails closed on one more
thing: a temporary-directory path containing a double quote cannot be written into a
`sandbox-exec` profile safely, so it reports `none` with a detail naming that, and
`"required"` then exits 2.

The same preflight resolves the interpreters. For every discovered skill with a bundle,
each file under `scripts/` whose extension is in `script_interpreters` must have its
interpreter on `PATH`, or the run exits 2 naming the script and the interpreter. This covers
every *discovered* skill — including one that `--tag` or `--case` filters out, or that has
no cases at all — so a missing interpreter for a skill that would never run still exits 2:
a fail-closed check that quietly skipped some skills would not be one. A file under
`scripts/` with an unmapped extension (a `data.json`, a `helper.txt`) is not an error; it
is simply not runnable. Preflight checks the candidate's bundles only; a baseline script
whose interpreter is missing is a refusal the model reads, not an error.

**What the sandbox does not do.** It does not hide the rest of the filesystem: a script
can read whatever the CI user can read (the interpreter and its libraries live there),
and what it prints reaches the model and the report. The sandbox is defence in depth (a
second layer that limits damage); the `allow_scripts` opt-in is the decision. Do not enable
scripts for a skill you would not run by hand. See
[Security](security.md#running-bundled-scripts).

**Under `--baseline previous`**, the previous version's `scripts/`, `references/` and
`assets/` come from git too, so the old instructions are never paired with the new scripts.
They are extracted — `git archive`, then `tarfile` with its `data` filter, which is why
skill-lens requires Python 3.11.4 or later — from the commit that last edited `SKILL.md` at
the previous version, into a temporary directory that is deleted when the run ends, however
it ends; `--keep-workspace` does not keep it, because a baseline bundle is an input, not an
output. The version is the authority, and that has two consequences: a bundle-only commit
made after that `SKILL.md` edit is invisible to the baseline, and a very large historical
`assets/` can hit the 10-second git timeout, which is reported as a baseline note
(`cannot archive commit <sha>`), never an error. A commit with none of the three
directories gives the baseline no bundle tools — never the candidate's. `--baseline none`
has no bundle: there is no skill. See
[How `previous` is resolved](comparative-evals.md#how-previous-is-resolved).

## Budget limits and pricing

The `budget` block sets ceilings on tokens, cost, and latency. Pricing comes from
`genai-prices`, which carries data for widely-used models but not every provider — for
example, Groq and Mistral models have no pricing entry yet. When a model cannot be priced,
`cost_usd` degrades to `0.0` and a note is recorded explaining why; the note always appears
in the JSON report. On the console it surfaces as budget-evaluator failure detail, which is
only printed for a case that fails — and a declared `max_cost_usd` that cannot be priced
always fails its case (see below), so the note is always printed alongside it on the console,
never silently on a passing case. An aggregate line ("some costs not priced" / "Total cost:
not priced") also appears in the console totals whenever any outcome's pricing degraded,
pointing you at the JSON for the per-case detail.
An unpriceable cost limit is **skipped rather than silently passed**, so a case with an
unpriced cost limit and no other budget checks will fail, because nothing was actually
verified. If other budget limits are declared alongside (tokens, latency), they are still
evaluated normally and contribute to `score` — but the case's `passed` verdict still requires
every declared limit to hold, and a skipped cost limit never holds. **A budget block whose
priced limits all hold still fails the case if it also declares an unpriceable
`max_cost_usd`** — the skipped check counts as a failure of that one check, even though it
does not lower `score` below what the priced checks alone would give it. If you adopt
`skill-lens` against a provider `genai-prices` cannot price, omit `max_cost_usd` from the
budget block for that provider rather than expecting it to be silently ignored.

The same rule applies to tokens. A runner that cannot count tokens sets `usage_note`, and
`max_tokens` is then a failing *not evaluated* check, exactly as `max_cost_usd` is under
`cost_note`: `0` tokens is not a measurement, and `0 <= max_tokens` would otherwise pass
every limit. Today that is the `cli` product runner, and `copilot` when its trace reports
no usage; see [Product runners](#product-runners).

If you are upgrading from a version of `skill-lens` where this budget block previously passed
some other way, note the change: a repo that runs an unpriced model with a `budget:` block
mixing a priced limit (e.g. `max_tokens`) and `max_cost_usd` will now see those cases turn red
— they always failed to verify the cost limit; only the reporting of `passed` has caught up
with `score`.

## Comparative runs and the baseline arm

Under `--baseline` (see [Comparative evals](comparative-evals.md)), the runner is called
twice per case: once with the candidate skill, once with the baseline skill. Both calls go
through the same `Runner.run(skill, case)` seam — there is no separate "baseline mode" a
runner has to implement.

A skill with **both `description` and `instructions` empty** — which is exactly what
`--baseline none` constructs — gets a neutral system prompt instead of the normal
`# {skill.name}` header:

```
You are a helpful assistant.
```

This keeps the skill's name out of a prompt that is explicitly meant to measure what the
skill's own text contributes. The rule is keyed on the skill being empty, not on which arm is
running, so a runner never has to know — or be trusted to know — which arm it is serving.
