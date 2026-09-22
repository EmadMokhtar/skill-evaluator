# Troubleshooting

Every entry on this page is keyed by **the message you see**, not by the feature that
produced it. Search the page for the words on your screen.

Two facts explain most of what follows.

**Exit codes are the contract** (see [Gating and exit codes](gating.md)):

| Code | Meaning |
| --- | --- |
| `0` | The gate passed |
| `1` | The gate failed — cases ran, and the result was below the bar |
| `2` | A user or authoring error — something in your own files or flags is wrong, so nothing was measured |

**`failed` is not `errored`.** A *failed* case ran and scored below the bar: that is a
signal about the skill. An *errored* case means the harness itself broke — a provider
timed out, a judge endpoint returned HTTP 500 (an internal server error) — and that is a
signal about your setup, not about the skill. Errored cases fail the gate by default. See
[Outcome: passed, failed, errored](concepts.md#outcome-passed-failed-errored) and the
invariant [`errored` is not `failed`](invariants.md#errored-is-not-failed).

Messages below are quoted as the code spells them. Where a message is assembled from
parts, the parts that vary are written as `<placeholders>` and named underneath.

Some of them arrive wrapped. skill-lens reports a bad *value* — whether it came from a flag
or from `skill-lens.toml` — through the command-line parser, which draws its own error box
and prefixes the text with `Invalid value:`. So `unknown judge: nope`, which can only come
from the config file, still reaches you as `Invalid value: unknown judge: nope`. Everything
else — a malformed eval file, a missing key, a product that cannot run here — is printed as
a plain line with no box and no prefix. Either way the quoted text below is the part to
search for.

## Exit code 2: something in your own files is wrong

**What you see.** The run stops before any case result is printed, and the process exits
`2`. Most of these messages are a single line naming the file and the field. The unknown-key
message is the exception: it ends with Pydantic's own report, which runs to four lines — the
field, `Extra inputs are not permitted`, and a documentation link.

**What it means.** A mistake in your eval files is not a verdict on the skill. A case that
cannot be read says nothing about the skill under test, so skill-lens aborts the run
instead of scoring that case as failed. See
[Authoring errors abort the run; they never score as failures](invariants.md#authoring-errors-abort-the-run-they-never-score-as-failures).
The exit code is what separates the two situations: `1` means the skill needs work, `2`
means your eval files or flags do.

**What to do.** Fix the file and the field the message names, then run again. These are the
four common causes.

### An unknown assertion `kind`

```
<file>: case '<case name>' assertion #<n> has unknown kind '<kind>'. Known kinds: contains, equals, file-produced, json-schema, not_contains, regex.
```

`kind:` has to be one of those six, spelled exactly: `not_contains` takes an underscore,
`file-produced` and `json-schema` take hyphens. This is checked when the file loads, before
any case runs and before any money is spent — see
[An unknown assertion kind is caught at load time](invariants.md#an-unknown-assertion-kind-is-caught-at-load-time)
and [Assertion kinds](eval-files.md#assertion-kinds).

A case built in Python instead of loaded from YAML skips that check and meets the
evaluator's own, which is shorter: `unknown assertion kind: '<kind>'`.

### A malformed regex

```
invalid regex pattern '<pattern>': <the message Python's own re module raised>
```

For example:

```
invalid regex pattern '(unclosed': missing ), unterminated subpattern at position 0
```

Unlike the other three, this one surfaces when the assertion *runs*, not when the file
loads, so earlier cases in the same run may already have spent money. Test a `regex:`
pattern before committing it.

### An unknown key

```
<file>: case #<n> invalid (<field names>): <the message Pydantic raised>
```

`<field names>` is the comma-separated list of top-level keys Pydantic objected to, and
Pydantic's own text says `Extra inputs are not permitted` for an unknown one. Every
user-authored model refuses unknown keys, so a typo like `assertion:` for `assertions:` is
an error rather than a case that quietly checks nothing and passes. See
[`extra="forbid"` on every user-authored model](invariants.md#extraforbid-on-every-user-authored-model).

Check the key against [Eval files](eval-files.md); a near-miss spelling is the usual cause.

### An unfilled scaffold

```
<file>: case #<n> still has the scaffold placeholder TODO(skill-lens) at <path to the field>. Fill it in -- an unfinished eval cannot say anything about the skill.
```

`skill-lens init` and `skill-lens mcp-import` write the literal text `TODO(skill-lens)`
wherever you still have to decide something. A case that still carries one has not been
written yet, so it is refused rather than run. Replace every occurrence, including in a
file you wrote by hand — the rule belongs to the loader, not to the generator. The scan
covers mapping keys as well as values.

A [tool library](eval-files.md#sharing-tools-across-eval-files) is checked the same way and
has its own wording:

```
tool library <file>: tool #<n> still has the scaffold placeholder TODO(skill-lens) at <path to the field>. Fill it in -- an unfinished mock cannot stand in for anything.
```

See [Unfilled scaffolds](eval-files.md#unfilled-scaffolds) and
[An unfilled scaffold is an authoring error, not a failure](invariants.md#an-unfilled-scaffold-is-an-authoring-error-not-a-failure).

### Other things that exit 2

| Message | Cause |
| --- | --- |
| `<VARIABLE> is not set, and model '<model>' needs it. Export it in your environment -- skill-lens never reads secrets from skill-lens.toml.` | The provider key for the model you named is missing from the environment. `<VARIABLE>` is that provider's variable, such as `OPENAI_API_KEY`. Keys are never read from `skill-lens.toml`. |
| `--min-delta requires --baseline none or --baseline previous` | You asked the gate to check an improvement without asking for anything to compare against. See [`--min-delta` without `--baseline` is a user error (exit 2)](invariants.md#-min-delta-without-baseline-is-a-user-error-exit-2). |
| `--model is empty; name a model such as openai:gpt-4o-mini` | `--model`, or `model` in `skill-lens.toml`, resolved to blank. The same message appears for `--judge-model`. |
| `unknown judge: <name>` | The `judge` key in `skill-lens.toml` names a judge that does not exist. |

Four more exit-2 causes have their own sections below:
[UndeclaredTool](#undeclaredtool), a
[product runner that cannot run here](#a-product-runner-that-cannot-run-here), a
[`--model` or `--judge-model` nothing reads](#a-model-or-judge-model-nothing-reads), and
[`script_sandbox = "required"` with no backend](#script_sandbox-required-with-no-backend).

## "no eval cases ran"

**What you see.** Exit `1`, no results, and one line under `Gate FAILED:`.

```
Skipped (no eval cases): badskill

0 passed, 0 failed, 0 errored — pass rate 0%

Gate FAILED:
  - no eval cases ran: all discovered skill(s) were skipped for having no eval cases: badskill
```

**What it means.** A run that executed nothing fails on purpose. "Nothing ran" is a broken
run, not a pass — otherwise a mistyped path would report success forever. See
[A run executing zero cases fails the gate](invariants.md#a-run-executing-zero-cases-fails-the-gate).

**What to do.** The reason names which of the four causes it was. `<names>` is the
comma-separated list of the skills concerned.

| Reason | What happened | What to do |
| --- | --- | --- |
| `no eval cases ran: no skills were found` | The path you gave holds no `SKILL.md` at any depth. | Check the path. `skill-lens list <path>` prints what discovery finds, calls no runner and needs no API key. |
| `no eval cases ran: all discovered skill(s) were skipped for having no eval cases: <names>` | A `SKILL.md` was found, but it has no `evals/` directory and no `*.eval.yaml` file beside it. | Write cases, or generate a starting point with `skill-lens init <path>`. See [Where eval files are found](eval-files.md#where-eval-files-are-found). |
| `no eval cases ran: the --tag filter excluded every case for skill(s): <names>` | `--tag` named a tag no case carries. A tag has to match a `tags:` entry exactly. | Drop `--tag`, or fix it against the `tags:` lists in the eval files. |
| `no eval cases ran: the --case filter matched no case for skill(s): <names>` | `--case` matched no case name. It is a case-insensitive *substring* of the `name:`, not the whole name, so a match is easy — unless the text is not in any name. | Copy a distinctive part of the case name out of the eval file, or out of an earlier log line. `--case` is applied after `--tag`, so a skill emptied by `--tag` is reported under `--tag` alone. It has no config key on purpose, so a repository cannot quietly measure less than it declares: see [`--case` has no config key](invariants.md#-case-has-no-config-key). |

## A baseline that could not be resolved

**What you see.** Under `--baseline previous`, a note on the report:

```
Baseline notes:
  - greeting: baseline unavailable — SKILL.md is not tracked by git
```

With `--min-delta` as well, the same fact becomes a gate reason and the run exits `1`:

```
Gate FAILED:
  - skill 'greeting' has no resolvable baseline: SKILL.md is not tracked by git
```

**What it means.** `--baseline previous` asks git for the newest earlier version of
`SKILL.md`, so it can run the same cases twice and report the difference. Sometimes git
cannot answer. That is a fact about the repository, not about the skill, so it is reported
rather than raised.

Without `--min-delta` this is only a note: the candidate arm still ran and the gate still
reads it. With `--min-delta` it fails the gate, because "we could not check" must never be
recorded as "nothing changed" — otherwise a repository could pass the delta gate forever by
deleting its git history. See
[A baseline that cannot be resolved is reported, never assumed to be "no change"](invariants.md#a-baseline-that-cannot-be-resolved-is-reported-never-assumed-to-be-no-change).

**What to do.** The reason is the last part of the line — after `baseline unavailable —` in
the note, after the colon in the gate reason. Each one has a different fix.

| Reason | What to do |
| --- | --- |
| `git is not installed` | Install git, or drop `--baseline previous`. In a container image, add git to the image. |
| `<path> is not inside a git repository` | The skill directory has no `.git` at or above it. Run from a checkout, and in continuous integration make sure the checkout step actually ran. |
| `SKILL.md is not tracked by git` | The file exists but was never committed. Commit it. There is no earlier version until there is a first one. |
| `cannot read <path>/SKILL.md: <the OS or decoding error>` | The working copy cannot be read: file permissions, or a file that is not UTF-8 text. |
| `no earlier version of SKILL.md found in the last 50 commits` | Every one of the last 50 commits touching this `SKILL.md` carries the same `version:`, or the same content where there is no `version:`. Bump `version:` in the same commit as the edit. Quote it (`version: "1.2"`), because YAML reads an unquoted `1.20` and `1.2` as the same number — an unquoted two-part version is itself an exit-2 authoring error. |
| `cannot archive commit <sha>` | `git archive` failed or passed its 10-second limit while fetching the previous version's bundled files. A very large `assets/` directory can do that. |
| `cannot extract the bundle at commit <sha>: <error>` | The archive would not unpack: a member failed the safe-extraction filter, or the filesystem refused. |

`<sha>` is the first eight characters of the commit hash. Full detail is in
[When resolution fails](comparative-evals.md#when-resolution-fails); the gate rule is in
[`--min-delta`](comparative-evals.md#-min-delta).

## A token limit that was not evaluated

**What you see.** A failing case whose budget detail says the limit was not evaluated:

```
        budget: token budget not evaluated: claude-code did not report token usage
```

**What it means.** Your case declares `max_tokens:`, and the runner could not count tokens.
When a runner cannot count them, it records a short note — `usage_note` in the JSON
report — and `input_tokens` / `output_tokens` stay at `0`.

`0` is not a measurement. If skill-lens compared it anyway, `0 <= max_tokens` would hold for
every limit you could write, and an unverified limit would report as "within budget". So the
check is recorded as a **failing** check instead, carrying the reason. It is excluded from
the `score` fraction, so it neither inflates nor deflates that number, but the case's
`passed` verdict still requires every declared limit to hold — and this one did not hold,
it was never checked. See
[A limit the product cannot measure fails, it never passes](invariants.md#a-limit-the-product-cannot-measure-fails-it-never-passes).

The notes you can meet, verbatim:

| Note | When |
| --- | --- |
| `copilot did not report token usage` | The `copilot` product runner ran, and its trace carried no usage block |
| `claude-code did not report token usage` | The `claude-code` product runner ran, and its trace carried no usage block |
| `the cli product does not report token usage` | The `cli` product runner, which reads plain standard output and has no trace to read usage from. `cli` is the runner's name — one of the three fixed names, beside `copilot` and `claude-code`. The `[runners.cli]` table is *keyed* by that name; it does not set it, and you cannot rename it |

**What to do.** Remove `max_tokens:` from the `budget:` block for cases you run through that
product, or run those cases through `pydantic-ai` or `langchain`, which do report a token
split. Do not leave the limit in place expecting it to be ignored — it is not ignored, it
fails. See [Budget limits and pricing](runners.md#budget-limits-and-pricing).

## A model with no price

**What you see.** In the console totals:

```
Total cost: not priced (see per-case cost_note in the JSON report)
```

or, when only some outcomes were affected, `some costs not priced (see per-case cost_note
in the JSON report)`. In the JSON report, a `cost_note` on the outcome. Under a declared
`max_cost_usd`, a failing budget check:

```
        budget: cost budget not evaluated: no price data for groq:llama (KeyError)
```

**What it means.** Cost is reporting metadata. A price that cannot be looked up must never
be the reason a run errors, so the lookup degrades: `cost_usd` becomes `0.0` and a note
explains why. See [Cost lookup degrades, never raises](invariants.md#cost-lookup-degrades-never-raises).

The notes, verbatim:

| Note | When |
| --- | --- |
| `no price data for <label> (<exception class>)` | The pricing package has no entry for that model. `<label>` is `<provider>:<model>`, or just the model when there is no provider prefix; the name in parentheses is the exception class the lookup raised, for example `KeyError` |
| `genai-prices is not installed; cost not calculated` | The pricing package is absent from the environment |
| `the cli product does not report cost` | The `cli` product runner has no trace to read a cost from |
| `copilot bills per premium request, not per token; <n> premium request(s)` | The `copilot` product runner. Its billing unit is not tokens, so there is no per-token price to apply. This note is set on **every** copilot run, so `max_cost_usd` always fails under `copilot` |

`max_cost_usd` under any of these is a **failing** check, for the same reason `max_tokens`
is under a `usage_note`: `0.0 <= max_cost_usd` is true for every limit you could write, so
"within budget" would be a claim nobody verified.

One consequence is worth stating plainly: **a budget block whose other limits all hold still
fails the case if it also declares an unpriceable `max_cost_usd`.**

**What to do.** Drop `max_cost_usd` from the `budget:` block for that provider. Do not rely
on the skip being silent — it is not.

`max_latency_ms` is always evaluated: it is measured by skill-lens itself, so no provider
can leave it unmeasured. `max_tokens` usually survives too — but not under `cli`, which sets
a `cost_note` and a `usage_note` together, so both limits fail there for the same reason.
See [A token limit that was not evaluated](#a-token-limit-that-was-not-evaluated) above and
[Budget limits and pricing](runners.md#budget-limits-and-pricing).

## NO_RESPONSE_SCRIPTED

**What you see.** This exact text, handed to the agent as a mock tool's return value:

```
no response is scripted for <tool name> with arguments <the call's arguments as JSON>
```

For example, `no response is scripted for get_work_item with arguments {"id": "C"}`. The
arguments are rendered as JSON with the keys sorted, so the line is stable.

skill-lens does not print this itself — the *model* reads it. You usually meet it in the
agent's own output under a failing case, where the agent reports that it could not find
something. A failing case also lists the tool calls it made, as `<name>(<argument>=<value>)`
— the call that triggered this, but not the text it got back.

**What it means.** The tool's `returns:` is a **lookup** — a list of `when:` / `value:`
entries — and the agent called the tool with arguments that no entry answers. The first
entry whose `when:` keys all match wins; when none match, the agent gets this fixed message.
A mock tool never raises, because a model that called a tool in a way you did not anticipate
is an eval signal, and an exception would surface it as broken infrastructure instead. See
[A call no lookup entry answers gets a message, never an exception](invariants.md#a-call-no-lookup-entry-answers-gets-a-message-never-an-exception).

**What to do.** Two options, and which one is right depends on what you meant.

- **The call was reasonable and you missed it.** Add an entry whose `when:` matches it, or
  widen an existing `when:` by removing a key. A `when:` matches as a subset: it needs only
  name the keys that matter, and the call may carry more.
- **You want every other call to get one answer.** Add a final entry with a `value:` and no
  `when:`. That is the fallback, and it matches every call. It has to be last, because an
  entry that an earlier one already answers is an exit-2 authoring error.

Values are compared as YAML and JSON parse them, with no coercion: `when: {id: 1}` matches
the integer `1` and not the string `"1"`, and a `true` matches only a boolean. A mismatch
you did not expect is often this. See
[Answering differently per call](eval-files.md#answering-differently-per-call).

## UndeclaredTool

**What you see.** Exit `2`, before any case runs:

```
runner <runner name>: case '<case name>' of skill '<skill name>' trajectory.<field> names '<tool name>', which is not declared in this case's tools.
```

`<field>` is `called`, `forbidden`, `order` or `call_args`. When the name is one of the six
built-in workspace and bundle tools, a second sentence is appended: ` Built-in workspace and
bundle tools only exist in a case with a 'workspace:' block.`

**What it means.** The case's `trajectory:` block checks a tool the agent was never given.
Such a check can never pass, so it says nothing about the skill — an authoring error, not an
eval signal. It is refused in the runner's preflight, which runs once before the first case,
so no quota is spent proving it.

**What to do.** Either add the tool to the case's `tools:` list, or fix the spelling in
`trajectory:`. If the name is `list_files`, `read_file`, `write_file`, `list_skill_files`,
`read_skill_file` or `run_script`, add a `workspace:` block to the case — those tools only
exist in a case that has one. See
[Declaring tools and scoring the trajectory](runners.md#declaring-tools-and-scoring-the-trajectory).

Two details that explain surprising cases:

- **The runner makes this rule, not the loader.** `skill-lens list` accepts any name, because
  one invocation can run the same case through more than one runner and the loader cannot
  know which. The message names the runner that refused, which is why it starts with
  `runner <runner name>:`.
- **A product runner refuses no name.** `copilot` and `claude-code` bring tools of their own
  (`Bash`, for instance) that skill-lens cannot enumerate, so under them a `trajectory:` name
  is either one of your `tools:` or the product's own, spelled as the product spells it. The
  `cli` runner refuses `trajectory:` outright; see the next section. See
  [Which tools a case has is the runner's to know](invariants.md#which-tools-a-case-has-is-the-runners-to-know-so-the-declared-name-rule-for-trajectory-is-made-in-preflight-per-runner-never-in-the-loader).

## A product runner that cannot run here

A *product runner* starts a real agent product — GitHub Copilot CLI, Claude Code, or a
command you name — instead of calling a model API. Everything it cannot do is refused in
preflight, once, before the first case, so nothing is spent discovering it case by case. See
[Preflight spends nothing](invariants.md#preflight-spends-nothing).

**What you see.** Exit `2` and one of these. `<role>` is `runner`, or `judge` when the same
product grades a `judge:` block.

| Message | What it means | What to do |
| --- | --- | --- |
| `<role> <product>: '<executable>' is not on PATH; install the product, or set [runners.<product>] command in skill-lens.toml` | The preset's executable (`copilot`, or `claude` for `claude-code`) was not found. | Install the product, or point `[runners.<product>] command` at the executable you have. |
| `<role> cli: '<executable>' is not on PATH; set [runners.cli] command in skill-lens.toml to a command on PATH` | Same, for the `cli` runner, which has no preset to install. | Fix the first element of `[runners.cli] command`. |
| `<role> <product>: <command> exited with code <n>: <first line of stderr>` | The version probe ran and failed. The product is on `PATH` but cannot start — a broken install, or one that needs sign-in. | Run `copilot --version` or `claude --version` yourself and fix what it reports. |
| `<role> <product>: <command> could not run: <the OS error>` | The version probe could not be started at all. | Check the file is executable and, on a network filesystem, reachable. |
| `case '<case>' of skill '<skill>' declares tools:, which the <product> runner cannot provide -- <reason>` | This case declares mock `tools:`, and this product cannot be given them. Under `cli` the reason is that it has no flag that takes an MCP (Model Context Protocol) server; under a preset, that `[runners.<product>] command` was pointed at a different executable whose flags are unknown. | Use a preset (`copilot`, `claude-code`) and leave its executable in `command`, adding flags with `args` instead. Or move the case to a framework runner. |
| `case '<case>' of skill '<skill>' declares trajectory:, but the cli runner records no tool calls; use a preset (copilot, claude-code)` | The `cli` runner reads plain standard output, so there are no tool calls to check. | Use a preset, or drop the `trajectory:` block from that case. |
| `case '<case>' of skill '<skill>' is mode: offered, but the cli runner cannot observe whether a skill was loaded; use a preset or mode: loaded` | `mode: offered` measures whether the agent chose the skill. The `cli` runner emits no signal that says so, and guessing `false` would be a silent wrong answer. | Use a preset, or change the case to `mode: loaded`. |

Preflight inspects only the candidate-arm cases that will actually run, once each. See
[Product runners](runners.md#product-runners) and
[Product runners](configuration.md#product-runners) for the `[runners.<name>]` table.

### A `--model` or `--judge-model` nothing reads

**What you see.** Exit `2`:

```
--model is read by pydantic-ai and langchain only, and this run names neither; a product's model is set with [runners.<name>] args = ["--model", "..."] in skill-lens.toml
```

```
--judge-model is read by judge = "pydantic-ai" or "langchain" only; this run's judge is "<judge name>"
```

```
--base-url is read by pydantic-ai and langchain only, and this run names neither; a product reaches its own endpoint
```

**What it means.** A flag that nothing reads is a trap, not a harmless no-op. `--runner
copilot --model gpt-5.2` would look like it chose the model while the product quietly ran
its own default, and you would trust a number measured against something else. So it is a
user error. See
[`--model` with nothing to read it is a user error](invariants.md#-model-with-nothing-to-read-it-is-a-user-error-and-so-is-judge-model).

**What to do.** Drop the flag, or set the product's model where the product reads it:
`args = ["--model", "..."]` in that product's `[runners.<name>]` table. A product reaches
its own endpoint, so `--base-url` has no meaning for one either. See
[`run`](cli.md#run).

## `script_sandbox = "required"` with no backend

**What you see.** Exit `2`, in preflight, before any case runs:

```
script_sandbox = "required" but no sandbox is available: <reason>
```

**What it means.** You turned script execution on and required an operating-system sandbox.
skill-lens probes for one once per run — it *executes* the backend rather than only looking
for it on `PATH`, because present is not the same as working — and found none. `required`
means exactly that: rather than run a skill's bundled scripts with the portable guards
alone, the run stops. Nothing is spent. See
[The sandbox decision is made once per run and appears on the report](invariants.md#the-sandbox-decision-is-made-once-per-run-and-appears-on-the-report).

The reasons, verbatim:

| Reason | What it means |
| --- | --- |
| `sandbox-exec not found on PATH` | macOS, and its backend is missing |
| `bwrap not found on PATH` | Linux, and bubblewrap is not installed |
| `no sandbox backend on <system>` | There is no backend for this platform at all. Windows is the case you will meet |
| `sandbox-exec probe failed: <detail>` | The backend is installed but would not run. `<detail>` is the first line of its standard error, or `exit <n>` when it printed nothing, or the operating-system error when it could not be started at all |
| `bwrap probe failed: <detail>` | The same, in the same three shapes. On Linux the usual cause is that unprivileged user namespaces are disabled on the host |
| `temp directory path contains a double quote, which a sandbox-exec profile cannot express` | The temporary-directory path holds a `"`, which would break the profile's quoting. It fails closed rather than writing a profile it cannot trust |

**What to do.** Pick one, knowingly:

- **Install the backend.** `bwrap` on Linux (`apt-get install bubblewrap`) is the usual fix
  in continuous integration. On macOS `sandbox-exec` ships with the system, so a probe
  failure there points at something else in the message.
- **Move the job to a platform that has one.** Windows has no backend, so `required` can
  never be satisfied there.
- **Lower the setting to `script_sandbox = "auto"`** — only if no provider key is present in
  that environment. `auto` uses a sandbox when one is found and the portable guards alone
  when none is. The portable guards stop a script *inheriting* the harness's environment, but
  a script runs as the same user as skill-lens and an operating system lets a same-user
  process read another's environment. Keep `required` wherever a key is exported.

A neighbouring preflight message has the same shape and a different fix:
`<path to the script> needs <interpreter>, which is not on PATH` means a bundled script's
interpreter is missing. Install it, or map that extension to something you have in
`[script_interpreters]`.

Both checks cover every *discovered* skill, including one that `--tag` or `--case` filters
out, so a missing interpreter for a skill that would never have run still exits `2`. That is
the fail-closed choice. Every run with scripts enabled also prints one line saying what
applied — `scripts: on, sandbox: bwrap`, or `scripts: on, sandbox: none (bwrap not found on
PATH)` — so a log tells you whether the isolation you expected was in force. See
[Running bundled scripts](runners.md#running-bundled-scripts) and
[Bundled scripts](configuration.md#bundled-scripts).
