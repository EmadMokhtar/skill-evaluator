# Runners

The default runner is `fake` (offline, scripted, free). To evaluate a skill with a
real agent you need one of the two framework extras, a key in the environment, and a
model:

```bash
uv tool install "skill-lens[pydantic-ai]"       # or "skill-lens[langchain]", or both
export OPENAI_API_KEY=...
skill-lens run ./skills --runner pydantic-ai --model openai:gpt-4o-mini
skill-lens run ./skills --runner langchain --model openai:gpt-4o-mini
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
(`judge = "pydantic-ai"` or `"langchain"`), and one judge grades every runner's output.

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

Every tool name in `called`, `forbidden`, or `order` must be declared in that case's
`tools:` — including `forbidden`, since forbidding a tool the agent was never offered in
the first place is a check that can never fire. A name that isn't declared is an
authoring error (the run aborts, exit `2`), not a failing case, because a check that can
never pass tells you nothing about the skill.

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
  it.
- `TMPDIR`/`TMP`/`TEMP` point at a scratch directory outside the workspace, deleted after
  the call, so a script's temporary files never appear in `list_files`, `file-produced` or
  the judge's artifacts.
- A script runs with `shell=False`, its arguments as argv. Nothing the model sends is ever
  joined into a command line.
- A wall-clock timeout (`script_timeout_seconds`) kills the whole process group, not only
  the direct child, so a script that starts `sleep 1000` and exits leaves nothing behind.
  One honest limit: the kill is `os.killpg`, so a script that calls `os.setsid()` leaves
  that group and survives it on every POSIX platform; only the `bwrap` backend closes that
  gap (`--unshare-pid` puts the script in its own PID namespace and `--die-with-parent`
  takes it down with the sandbox) — and the stock Ubuntu runner has no working `bwrap`.
- stdout and stderr are written to files in the scratch directory, not held in memory, and
  read back capped at `max_script_output_bytes` each; a cut ends with `... [truncated, N
  bytes omitted]` stating the exact count. The harness reads them through the descriptors
  it opened before the process started, never by re-opening the path, so a script cannot
  swap a symlink or a FIFO into their place.
- A script writes to disk directly, so the workspace caps cannot refuse it beforehand.
  After the call the directory is measured, and if it is over `max_files` or
  `max_total_bytes` the tool result ends with a `warning:` line (`warning: the working
  directory now holds 12,345,678 bytes; max_total_bytes is 5,000,000`) and every later
  `write_file` is refused. The timeout is the real bound on what one script can write.

**The OS sandbox**, where one exists (`script_sandbox = "auto"`, the default), is a second
layer on top. Under either backend a script cannot open a network connection; cannot write
to the host filesystem outside the workspace and its scratch directory (under `bwrap`,
writes under the temporary directory and `/dev/shm` land in an in-memory mount that is
discarded when the script exits — bounded, like output, only by the timeout); and cannot
read anything under the system temporary directory except the workspace, the scratch
directory and the skill's own bundle — so under `--concurrency N` a script cannot read the
baseline arm's workspace or another case's scratch directory. The bundle is allowed back
explicitly because a `--baseline previous` bundle is extracted under that temporary
directory. Reads anywhere else are allowed (see below).

| | Backend | How |
| --- | --- | --- |
| macOS | `sandbox-exec` | A profile that allows everything, then denies `network*` and `file-write*`, re-allows writes under the workspace and the scratch directory (plus `file-write-data` on `/dev/null`), denies `file-read*` under the temporary directory, then re-allows reads under the workspace, the scratch directory and the bundle. Later rules win. `sandbox-exec` is marked deprecated in Apple's documentation, is present and working on current macOS, and is what Bazel, Chromium and Claude Code use. |
| Linux | `bwrap` (bubblewrap) | `--ro-bind / /`, `--dev /dev`, `--proc /proc`, an empty `--tmpfs` over the temporary directory so sibling workspaces vanish, `--bind` for the workspace and the scratch directory, `--ro-bind` for the bundle, then `--unshare-net --unshare-pid --die-with-parent --new-session`. Needs unprivileged user namespaces or a setuid install; a stock Ubuntu CI image refuses the former, so the probe reports `none` there and says why. |
| Windows | none | The portable guards only. `"required"` refuses to run. |

The probe runs once per run, after discovery and before any case. The backend is
*executed* — `sandbox-exec -p '(version 1)(allow default)(deny network*)' /usr/bin/true`, or
`bwrap --ro-bind / / --dev /dev --proc /proc --unshare-net --unshare-pid --die-with-parent
-- /bin/true` — not merely found on `PATH`, because present is not the same as working: an
Ubuntu 24.04 runner refuses user namespaces under AppArmor, and the first line of that
refusal is what the report shows. The result is on every report that enabled scripts —
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
