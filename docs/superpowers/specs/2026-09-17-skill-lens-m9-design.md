# skill-lens M9 — Product runners — Design

**Date:** 2026-09-17
**Status:** Approved (design), pending implementation plan
**Parent:** `2026-07-30-skill-eval-design.md` (§9); `2026-09-11-skill-lens-m8-design.md`
(the runner matrix this milestone plugs into)
**Issue:** [#44](https://github.com/EmadMokhtar/skill-evaluator/issues/44)

## 1. Scope

The two shipped runners drive a generic agent framework with skill-lens's own system
prompt and tool conventions. A skill written for a named product — GitHub Copilot CLI,
Claude Code — is deployed into that product's skill directory and loaded by that
product's own mechanics, which the framework runners can only approximate. Two things
follow. A green run under `pydantic-ai` says "a reasonable agent does the right thing
with these instructions", not "the product we ship to does". And an engineer whose
organisation grants a Copilot seat but no provider API key cannot run skill-lens at
all today: every runner and judge except the fakes needs a key.

M9 adds **product runners**: a runner that starts the product's own command-line
interface in non-interactive mode, with the skill under test placed where that product
discovers skills, and reads the result back from the product's machine-readable trace.
Two PRs:

**Part 1 — the runner** (`feat: run cases through an agent product's CLI`):

- **`ProductRunner`** (`runners/product.py`) behind the unchanged `Runner` protocol,
  parameterised by a `Product` value: the argv template, the skill directory, the
  invocation spelling and the trace parser. Three runner names: **`copilot`** and
  **`claude-code`** are built-in presets; **`cli`** is a generic product built from
  `skill-lens.toml` with no trace parser (stdout is the output).
- **`runners/traces.py`**: two pure parsers, Copilot's JSONL (JSON Lines, one JSON
  object per line) and Claude Code's `stream-json`, each producing one `Trace` value.
- **`Skill.markdown`**: the `SKILL.md` text verbatim, so the product sees the file the
  author wrote.
- **`[runners.<name>]`** tables in `skill-lens.toml`; a preflight hook that fails
  before any case runs; `RunReport.products` so every reporter says which product,
  which version, and under which trust model the run happened.
- **`RunResult.usage_note`** and the matching `BudgetEvaluator` rule: a token limit the
  product cannot measure fails, it never passes.
- A `--model` / `--judge-model` flag that nothing reads is a user error.

**Part 2 — the judge** (`feat: grade rubrics through an agent product's CLI`):

- **`ProductJudge`** (`judges/product.py`): `judge = "copilot"`, `"claude-code"` or
  `"cli"`, the existing judge prompt sent through the same product, the per-check JSON
  verdict parsed from its response.

Everything downstream — evaluators, gating, comparison, reporters — keys on `RunResult`
and `CaseOutcome.runner`, and is untouched except where this spec says otherwise.

### What the probes established

Recorded on 2026-09-17 against Copilot CLI 1.0.37 and the installed Claude Code, in a
scratch directory holding a one-line `ping` skill.

| | Copilot CLI | Claude Code |
| --- | --- | --- |
| Non-interactive flag | `-p <prompt> --allow-all-tools` (required for `-p`) | `-p <prompt> --dangerously-skip-permissions` |
| Trace flag | `--output-format json` (JSONL) | `--output-format stream-json --verbose` |
| Skill discovered from | `.agents/skills/<name>/SKILL.md` (also `.github/skills`, `.claude/skills`), event `session.skills_loaded` | `.claude/skills/<name>/SKILL.md`, `system`/`init` event, `skills` list |
| Skill-load signal | `skill.invoked` event, `data.name` | `assistant` message with a `tool_use` block named `Skill`, `input.skill` |
| Output text | last `assistant.message` with non-empty `data.content` | `result` event, `result` field |
| Tool calls | `assistant.message` `data.toolRequests[]` (`name`, `arguments`) | `assistant` `tool_use` blocks (`name`, `input`) |
| Tokens | `session.shutdown` `data.modelMetrics[<model>].usage` (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`); not yet observed in a `-p` stream | `result.usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`) |
| Cost | none; `result.usage.premiumRequests` | `result.total_cost_usd`, list price |
| Model | `session.tools_updated` `data.model`; `session.shutdown` `data.currentModel` | `system`/`init` `model` |
| Failure | `session.error` (`errorType`, `message`, `statusCode`); `result.exitCode` | `result.is_error`, `result.subtype != "success"` |
| Isolation from the user's own setup | `--no-custom-instructions`; personal skills and plugins under `~/.copilot` still load | `--setting-sources project --strict-mcp-config --no-session-persistence`: no user hooks, plugins or MCP servers; the project skill still discovered; OAuth auth still works (`--bare` would not: it forces an API key) |
| Version | `copilot --version` | `claude --version` |

Also established: Claude Code's print mode waits three seconds for piped stdin unless
stdin is `/dev/null`; it runs from inside another Claude Code session without unsetting
anything. Copilot's monthly quota was exhausted during the probes, so its successful-run
events were read from the product's local session logs, which use the same event schema;
a real `-p` recording is the first thing Part 1's implementer does when quota returns.

### Explicitly deferred

| Deferred | Why |
| --- | --- |
| Mock tools under a product runner, through an MCP bridge (skill-lens starts a stdio MCP server exposing the case's `tools:` and passes it with `--additional-mcp-config` / `--mcp-config`) | A subsystem of its own, and it belongs with the mock-tool work in #40–#43. Until then `tools:` with a product runner is a preflight authoring error. |
| A per-case timeout | Carried over from M6 part 2; `timeout_seconds` is per product, per repository. |
| A hermetic Copilot run (`COPILOT_HOME` pointing at an empty directory plus `COPILOT_GITHUB_TOKEN`) | Documented as the recipe; not automated, because it would move the user's auth. |
| `--disable-builtin-mcps` in the Copilot preset | The GitHub MCP server is the product as shipped. `command` replaces the argv for anyone who wants it off. |
| A model name shared between framework and product runners | Products spell models their own way (`gpt-5.2`, `sonnet`); the model string is provider-prefixed. `[runners.<name>] args = ["--model", "…"]` is explicit and never reaches the wrong runner. |
| Tool-name normalisation across products (`bash` vs `Bash`) | A `trajectory:` under a product runner names that product's tools. A translation table would be a third moving target. |
| Restricting Copilot's tools while judging | No verified flag; Claude Code's `--tools ""` is verified. Documented. |
| Feeding the prompt through stdin | Neither product documents reading the prompt from stdin; argv is verified. A prompt over 100 KiB is refused with a message, because Linux caps one argument at 128 KiB. |

## 2. Decisions

| Decision | Why |
| --- | --- |
| **One `ProductRunner` parameterised by a `Product` value**, not one class per product. | Products differ in argv and trace grammar — data — where frameworks differ in API. One subprocess path means one timeout, one process-group kill, one capped read and one test surface. A generic-only runner (`trace = "copilot"` as a config key) was rejected because every user would have to reproduce the isolation flags the probes verified, and a wrong argv would fail at run time instead of being a preset a test pins. |
| **The product sees `SKILL.md` byte for byte** (`Skill.markdown`), never a re-rendering from the parsed fields. | Products honour frontmatter keys skill-lens does not model (`allowed-tools`, `disable-model-invocation`, `license`). A re-rendering would change the product's behaviour and the eval would measure the re-rendering. |
| **No skill directory when `Skill.markdown` is empty.** | The `--baseline none` skill is built with empty fields and no text; keying on emptiness rather than on the arm keeps the rule the same one `BASELINE_PREAMBLE` uses — a runner that could branch on the arm could cheat. |
| **The prompt is the task, verbatim; no preamble.** | The product owns its system prompt. Anything skill-lens added would be part of what `--min-delta` measures. |
| **`mode: loaded` invokes the skill through the product's own mechanism** (`/<name> <task>`); **`mode: offered` sends the bare task.** | Loaded means "the instructions are in effect"; under a product that is an explicit invocation. Offered means "does the product choose it from the description"; that is the bare task. Under `--baseline none` the baseline arm gets the bare task in both modes — it has no skill to invoke, and the name never reaches it. |
| **`skill_triggered` comes only from the product's own load event.** | Inferring it from the output would let a model that guessed the answer read as a triggered skill. Where a product has no load event (`cli`), `mode: offered` is an authoring error under that runner — never a silent `false`, which would make every negative control pass. |
| **`tools:` with a product runner is a preflight authoring error.** | The product cannot be given mock tools yet (see deferred). Ignoring the block would make `trajectory: called:` fail for a reason that says nothing about the skill and `forbidden:` pass vacuously. |
| **A token limit the product cannot measure is a failing check** (`RunResult.usage_note`). | `0 <= max_tokens` is always true. The rule already exists for cost (`cost_note`); this milestone makes it symmetric. |
| **Copilot cost is `0.0` with a `cost_note`; Claude Code cost is `total_cost_usd` with none.** | Copilot bills per premium request, so no dollar figure exists; the note says so and `max_cost_usd` fails as "not evaluated". Claude Code reports a list-price figure the product itself computed; a subscription user is not billed it, but it is a real, comparable number and the docs say what it is. |
| **Tokens include cache reads and writes.** | They were processed; a `max_tokens` that ignored them would measure the cache, not the skill. PydanticAI's `input_tokens` counts them too. |
| **Tool calls come from what the model requested**, not from what executed. | `toolRequests` / `tool_use` are the message history; a refused or failed call was still the model's choice. Same rule as `all_messages()` in the framework runners. |
| **Full environment inheritance; permission prompts disabled.** | The product needs its own auth (a keychain, `~/.copilot`, `GH_TOKEN`, OAuth) and cannot run non-interactively otherwise. This is the opposite of `run_script`'s allowlist, on purpose: the product is the harness here, not the subject. Naming a product runner is the trust decision, and the report says so on every run. |
| **`allow_scripts` keeps governing only `run_script`.** | The product's shell tool can run anything under its cwd, bundled scripts included, whatever `allow_scripts` says. Gating the runner on `allow_scripts` would also switch on `run_script` for every framework runner in the same matrix. The report states that bundled scripts are reachable through the product's own tools and that no skill-lens sandbox applies. |
| **A preflight hook, optional on the protocol.** | The executable check, the version probe and the per-case compatibility checks must fail before any quota is spent — the rule every other authoring error follows. The framework runners define no hook and are unaffected. |
| **The version probe is executed, not merely found.** | Same rule as the sandbox probe. A `copilot` on `PATH` that cannot start is exit 2 up front, not thirty errored cases. |
| **A truncated trace is `RunResult.error`, never a partial parse.** | The final event is the last line; losing it loses the output. The message names the cap so the fix is one config line. |
| **`--model` with no runner that reads it, and `--judge-model` with a judge that does not, are user errors.** | Today `--runner fake --model x` is silently ignored. With `--runner copilot` that silence becomes a common trap: the flag looks honoured and the product runs its default model. |
| **`command` replaces the argv; `args` appends.** | Two knobs, two meanings: drop an isolation flag with `command`, add a model with `args`. `command` must contain exactly one element equal to `{prompt}`, substituted as a whole argv element — never through a shell. |
| **Presets forbid `skills_dir` and `invoke`.** | A preset is the verified spelling for that product. A repository that needs different values is describing a different product and should say so with `cli`. |
| **`transcript` drops Copilot's `ephemeral` events.** | They are discovery chatter (the user's every installed plugin skill, with paths) that says nothing about the run. |

## 3. Models

```python
class Skill(BaseModel):
    ...
    markdown: str = ""      # the SKILL.md text verbatim; "" when there is no file

class RunResult(BaseModel):
    ...
    usage_note: str = ""    # why input/output tokens are 0 when the runner could not count them

class ProductStatus(BaseModel):
    """One product a run executed, as preflight found it."""
    name: str               # "copilot", "claude-code", "cli"
    executable: str         # resolved path
    version: str            # first line of `--version`; "" for cli
    trust: str              # fixed text: permission prompts disabled, no skill-lens sandbox,
                            # bundled scripts reachable through the product's own tools

class RunReport(BaseModel):
    ...
    products: list[ProductStatus] = Field(default_factory=list)
```

`Skill.markdown` is set by `parse_skill_text` (which has the text) and therefore by both
the loader and the baseline resolver; `_baseline_skill` for `none` leaves it empty. It is
not serialised anywhere: `CaseOutcome` carries the skill's name only.

`ProductStatus.trust` is fixed harness text, not a per-run finding, so a reporter can
render it without knowing about products; it is on the model rather than in each
reporter so the three reporters cannot drift.

## 4. `runners/product.py`

**`Product`** — a frozen dataclass:

| Field | `copilot` | `claude-code` | `cli` |
| --- | --- | --- | --- |
| `name` | `copilot` | `claude-code` | `cli` |
| `argv` | `copilot -p {prompt} --allow-all-tools --output-format json --no-custom-instructions --no-auto-update` | `claude -p {prompt} --output-format stream-json --verbose --dangerously-skip-permissions --setting-sources project --strict-mcp-config --no-session-persistence` | `command` from config |
| `skills_dir` | `.agents/skills` | `.claude/skills` | `skills_dir` from config, default `.agents/skills` |
| `invoke` | `/{name} {task}` | `/{name} {task}` | `invoke` from config, default `{task}` |
| `parse` | `traces.parse_copilot` | `traces.parse_claude_code` | `None` |
| `version_argv` | `["--version"]` | `["--version"]` | `None` |
| `timeout_seconds` | 600 | 600 | 600 |
| `max_output_bytes` | 8 000 000 | 8 000 000 | 8 000 000 |

`.agents/skills` is chosen for Copilot over `.github/skills` because it is the
cross-product convention Copilot also reads, so a `cli` product for another agent that
follows the convention works with the default. `PRESETS` maps the two names to their
values; `Product.from_settings(name, settings)` applies `command`, `args`,
`timeout_seconds`, `max_output_bytes`, and for `cli` also `skills_dir` and `invoke`.

**`ProductRunner`** — `name` is the product's name; `needs_api_key = False`;
constructor takes the `Product`.

`run(skill, case, workspace=None, scripts=None)` — `scripts` accepted for protocol
symmetry and ignored, as `FakeRunner` does; the product brings its own tools.

1. **Working directory.** `workspace.root` when the case declares a workspace, else a
   private `tempfile.mkdtemp(prefix="skill-lens-product-")`, resolved, removed in a
   `finally`. A product's file tools write into its cwd, and the assertion and judge
   evaluators read the workspace root, so the two must be one directory.
2. **Skill delivery.** When `skill.markdown` is non-empty: write
   `<cwd>/<skills_dir>/<skill.name>/SKILL.md` with the text, then copy `scripts/`,
   `references/` and `assets/` from `skill.bundle_root` when it is set — those three and
   nothing else, so an eval file beside `SKILL.md` is never visible to the product, and
   `--baseline previous` carries the bundle the resolver extracted. The directory is
   written before the process starts and is not removed afterwards when the case has a
   workspace: under `--keep-workspace` the kept directory then shows exactly what the
   product saw. `skill.name` was checked in preflight to be a single path segment.
3. **Prompt.** `product.invoke.format(name=skill.name, task=case.task)` when
   `case.mode == "loaded"` and a skill directory was written; `case.task` otherwise
   (offered mode, or the baseline-none arm, which has nothing to invoke). A prompt over
   100 KiB (UTF-8) is `RunResult.error` naming the size.
4. **Process.** `subprocess.Popen(argv, cwd=cwd, stdin=DEVNULL, stdout=<file>,
   stderr=<file>, env=os.environ, **_group_kwargs())` — `shell=False`, the prompt
   substituted as one argv element. Wait with `product.timeout_seconds`; the process group
   is killed after every exit, timeout or not; stdout and stderr are read through the
   harness's own handles, capped at `max_output_bytes`. These are `_group_kwargs`,
   `_reap_and_kill_group` and `_read_capped_handle` from `scripts.py`, moved to a new
   `process.py` that `scripts.py` imports; behaviour unchanged, one implementation.
5. **Errors → `RunResult.error`, never raised**: the executable missing at run time (it
   passed preflight, so this is a race, reported as such); a timeout (`timed out after
   Ns`); a non-zero exit with no parseable failure event (exit code plus the last stderr
   lines); a truncated stdout (`product output exceeded N bytes; raise
   [runners.<name>] max_output_bytes`); a trace with no final event; a product-reported
   failure (`session.error` message, or `result.is_error`). `latency_ms` and `model`
   are filled in on the error path where known, as the framework runners do.
6. **Result.** With a parser: `RunResult(output, tool_calls, transcript, input_tokens,
   output_tokens, latency_ms, cost_usd, cost_note, usage_note, model, skill_triggered)`
   from the `Trace`; `skill_triggered = skill.name in trace.invoked_skills` in offered
   mode, `None` otherwise. Without one (`cli`): `output` is stdout with one trailing
   newline stripped, no tool calls, `usage_note = "the cli runner does not report token
   usage"`, `cost_note = "the cli runner does not report cost"`, `model = ""`.

**`ProductRunner.preflight(skills, cases_by_skill)`** raises `ProductSetupError` (added to
`cli.py`'s `_AUTHORING_ERRORS`, exit 2) for the first of:

- the executable (`argv[0]`) not found by `shutil.which`, naming the runner, the
  executable, and where it is set (`copilot` / `claude-code`: install the product;
  `cli`: `[runners.cli] command`);
- `version_argv` set and `<executable> --version` failing to run or exiting non-zero
  (executed with a 30 s timeout, output captured, the first line kept as the version);
- a skill whose `name` is not a single path segment (empty, `.`, `..`, or containing a
  separator);
- a case declaring `tools:`;
- under `cli` only, a case with `trajectory:` or `mode: offered`.

It inspects the cases that will actually run — the skills and cases the orchestrator
discovered after `--tag` and `--case` — because compatibility is a property of (case,
runner), unlike the sandbox decision, which is run-wide. It returns a `ProductStatus`.

## 5. `runners/traces.py`

```python
@dataclass(frozen=True)
class Trace:
    output: str
    tool_calls: list[ToolCall]
    transcript: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float
    cost_note: str
    usage_note: str
    invoked_skills: frozenset[str]
    error: str | None
```

Both parsers take the captured stdout as text, split it into lines, `json.loads` each
non-empty line, skip a line that is not a JSON object (a product may print a warning to
stdout), and return `Trace`. Neither raises for content; a structural problem is
`Trace.error`.

**`parse_copilot`**
- `output`: `data.content` of the last `assistant.message` whose content is non-empty.
- `tool_calls`: every `data.toolRequests[]` of every `assistant.message`, in order,
  `ToolCall(name, arguments)`; arguments that are not a dict are wrapped as `{"_raw":
  …}`, the existing `_arguments` rule.
- `invoked_skills`: `data.name` of every `skill.invoked`.
- tokens: summed over `data.modelMetrics[*].usage` of the `session.shutdown` event —
  `inputTokens + cacheReadTokens + cacheWriteTokens` and `outputTokens`; absent, both
  are 0 and `usage_note = "copilot did not report token usage"`.
- `model`: `data.currentModel` of `session.shutdown`, else `data.model` of the last
  `session.tools_updated`, else `""`.
- `cost_usd = 0.0`, `cost_note = "copilot bills per premium request, not per token; N
  premium request(s)"` from `result.usage.premiumRequests`.
- `error`: `data.message` of the last `session.error` when present; else `"copilot exited
  with code N"` when `result.exitCode` is non-zero; else `"no result event in the copilot
  trace"` when there is no `result` line.
- `transcript`: every parsed line whose top-level `ephemeral` is not `true`.

**`parse_claude_code`**
- `output`: `result` of the `result` event.
- `tool_calls`: every `tool_use` block of every `assistant` message's `content`, in
  order, `ToolCall(name, arguments=input)`.
- `invoked_skills`: `input.skill` of every `tool_use` named `Skill`.
- tokens: `result.usage` — `input_tokens + cache_creation_input_tokens +
  cache_read_input_tokens` and `output_tokens`.
- `model`: `model` of the `system`/`init` event, else `""`.
- `cost_usd`: `result.total_cost_usd`; `cost_note = ""`.
- `error`: `result.result` when `is_error` is true or `subtype != "success"` (the text is
  the product's own explanation); `"no result event in the claude-code trace"` when
  there is no `result` line.
- `transcript`: every parsed line.

## 6. `judges/product.py` (Part 2)

**`ProductJudge`** — `name` is the product's name; `needs_api_key = False`; takes the
same `Product`, built from the same `[runners.<name>]` table (`args`, `timeout_seconds`,
`max_output_bytes`, `command`).

`judge(request)`:

1. Prompt: `SYSTEM_PROMPT`, a blank line, `render_request(request)`, and one closing
   line — "Reply with the JSON object only; no prose, no code fence." The product's
   system prompt is its own; the judge instructions travel in the user turn.
2. Working directory: a private empty temp directory, no skill directory. The judge
   grades text; it must not discover the skill under test.
3. Argv: the product's, plus `--tools ""` for `claude-code` (no tools while grading —
   verified in the product's help). Copilot keeps its tools; documented as a known gap.
4. The same subprocess helper as the runner; the same error cases become
   `JudgeVerdict.error`.
5. Verdict: the trace's `output` (or stdout under `cli`) is searched for the first
   balanced `{ … }` after stripping a surrounding code fence; it is validated as
   `JudgeOutput`. Anything else — no object, invalid JSON, a shape that is not
   `JudgeOutput` — is `JudgeVerdict(error="JudgeOutputInvalid: …")`, one attempt.
   `input_tokens`, `output_tokens`, `cost_usd`, `cost_note`, `model` come from the trace
   as they do for the runner.

`judge_temperature` is not consulted — no product exposes it — and the docs say so.
`JudgeEvaluator` is untouched: it still derives `passed` and `score` from the checks and
still records an evidence-free pass as a failure.

## 7. Config, CLI, orchestrator, reporters

**`config.py`**

```python
class ProductSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=600.0, gt=0, allow_inf_nan=False)
    max_output_bytes: int = Field(default=8_000_000, gt=0)
    skills_dir: str | None = None
    invoke: str | None = None

class Config(BaseModel):
    ...
    runners: dict[str, ProductSettings] = Field(default_factory=dict)
```

Validation (`ConfigError` naming the key, exit 2): a key not in `{copilot, claude-code,
cli}`; `command` without exactly one element equal to `{prompt}`; `skills_dir` or `invoke` under a preset;
a `skills_dir` that is not a relative path within the working directory (checked with the
workspace's `check_relative_path`); an `invoke` template that does not contain `{task}`.
`timeout_seconds` has the same finite-and-positive rule as `script_timeout_seconds`, for
the same reason. All config-only: repository policy with no per-run reason to vary.
`cli` named with no `[runners.cli] command` is checked where the name is resolved — the
runner factory in `cli.py` — because the name can arrive from `default_runner`, `judge`
or the `--runner` flag, and the config model sees only the first two; it is a
`ConfigError` naming the key either way.

**`cli.py`**

- `_RUNNERS` and `_JUDGES` become factories taking `(settings, model_name)`, so a product
  runner is built from its `[runners.<name>]` table and the keyed runners exactly as
  today. `_resolve_runners` is unchanged.
- `--model` given (the flag, not the config key, which always has a value) when no
  runner in the matrix has `needs_api_key` → `BadParameter`: "`--model` applies to
  pydantic-ai and langchain; set a product's model with `[runners.<name>] args`".
  `--judge-model` with a judge that does not read it → the same.
- `ProductSetupError` joins `_AUTHORING_ERRORS`.
- The `Plan:` line prints when any runner needs a key **or** is a product runner —
  product runs spend quota.

**`orchestrator.py`** — after discovery and before execution, for every runner with a
`preflight` attribute, `runner.preflight(skills, cases_by_skill)`; the returned
`ProductStatus` values go on `RunReport.products`. Order: after the script preflight,
so a `ScriptSetupError` and a `ProductSetupError` cannot race.

**`runners/base.py`** — the `Runner` docstring documents `preflight` as an optional
member: "a runner may define `preflight(skills, cases_by_skill) -> ProductStatus |
None`; the orchestrator calls it once per run, after discovery and before any case
runs; it raises an authoring error to abort the run."

**Reporters** — console prints one line per product: `copilot 1.0.37
(/opt/homebrew/bin/copilot): <trust>`; Markdown the same under the header; JUnit adds
`skill-lens.products` properties; JSON adds `"products": [...]`. `ScriptNote` rendering
is unchanged — the product line already says scripts are reachable.

**`action.yml`** — no change; `runner: copilot` already reaches `--runner` through the
comma split. `docs/ci.md` gains a workflow that installs the product and passes its
token, and the warning that a `pull_request` checkout can set `default_runner` for
itself, so untrusted-PR workflows pin `runner:` explicitly.

## 8. Documentation

| File | Change |
| --- | --- |
| `docs/runners.md` | New "Product runners" section: what a product runner measures and what it cannot (the table in §1), the presets' argv and why each flag, `command` vs `args`, the skill directory, loaded vs offered prompts, the `--model` rule, the trust paragraph. |
| `docs/configuration.md` | `[runners.<name>]`, every key, the validation rules, the annotated example. |
| `docs/cli.md` | `--runner copilot` / `claude-code` / `cli`; `--model` and `--judge-model` errors. |
| `docs/eval-files.md` | A table of which case features each runner supports (`tools:`, `trajectory:`, `offered`, `budget.max_tokens`, `budget.max_cost_usd`). |
| `docs/gating.md` | `products` in the JSON report; the two new exit-2 causes. |
| `docs/security.md` | The product trust model: prompts disabled, full environment, no sandbox, `allow_scripts` scope, `skill-lens.toml` in the trust boundary, the hermetic-Copilot recipe. |
| `docs/ci.md` | The product workflow example; `runner:` pinning for untrusted PRs. |
| `ARCHITECTURE.md`, `CLAUDE.md` | Module map (`runners/product.py`, `runners/traces.py`, `judges/product.py`, `process.py`); the invariants in §10. |
| `docs/roadmap.md` | M9 row and "What M9 shipped". |
| `examples/skill-lens.toml` | Annotated `[runners.copilot]` and `[runners.cli]` blocks. |

## 9. Testing

**Zero-cost tier (CI, no network).**

- **A fake product** (`tests/fake_product.py`, written into `tmp_path` and put on `PATH`
  by a fixture): a Python script that asserts its cwd contains the expected skill
  directory, writes the prompt it received to a file the test reads back, then prints
  a fixture trace — or sleeps, exits non-zero, prints a truncated trace, or prints more
  than the cap, as the test directs through an environment variable. This drives the
  real subprocess path: cwd, delivery, invocation spelling, timeout and group kill,
  capped read, non-zero exit, truncated trace, the version probe.
- **`tests/test_product_runner.py`**: the skill directory holds `SKILL.md` verbatim
  plus the three bundle directories and nothing else; no directory under
  `--baseline none`; the previous baseline's bundle delivered; loaded mode sends
  `/<name> <task>` and offered mode the bare task; the private temp cwd is removed and
  a workspace cwd is kept; every error case above lands in `RunResult.error`, never
  raised; `skill_triggered` both ways in offered mode and `None` in loaded; `scripts=`
  accepted and ignored.
- **`tests/test_traces.py`**: both parsers against scrubbed fixtures under
  `tests/fixtures/products/` (Claude Code from the probes; Copilot from the session-log
  schema and the errored probe, replaced by a real `-p` recording when quota returns):
  output, tool calls with arguments, invoked skills, tokens with and without
  `session.shutdown`, cost and both notes, model, every error shape, `ephemeral`
  dropped, a non-JSON line skipped.
- **`tests/test_product_preflight.py`**: each `ProductSetupError` cause; only the
  cases that will run are inspected; the `ProductStatus` fields.
- **`tests/test_config.py`**: every `ProductSettings` rule; `runners` keys validated.
- **`tests/test_cli.py`**: `--runner copilot` builds from config; `--model` with only
  product runners is exit 2 and with a mixed matrix is not; `--judge-model` likewise;
  the `Plan:` line for a product matrix.
- **`tests/test_budget.py`**: `max_tokens` under `usage_note` is a failing check
  excluded from the score, symmetric with the cost rule.
- **`tests/test_skill_loader.py` / `test_baseline.py`**: `Skill.markdown` round-trips
  through both; the `none` baseline has none.
- **Reporters**: `products` rendered by all four.
- **`tests/test_product_judge.py`** (Part 2): a fenced object, a bare object, prose
  around an object, no object, invalid JSON, a wrong shape; no skill directory in the
  judge's cwd; `--tools ""` present for `claude-code` only.
- **Guards that must keep passing:** `test_framework_isolation` (the new modules import
  no framework), `test_naming`, `test_docs`, `test_supply_chain`, `test_release_config`,
  `test_security_checks`.

**Integration tier** (`-m integration`, opt-in): one real `claude -p` and one real
`copilot -p` run of `examples/greeting`, each skipped when its executable is missing.
Quota-bearing; never in CI.

## 10. Invariants this milestone adds or must not break

1. **The product sees `SKILL.md` byte for byte.** `Skill.markdown` is the file, never a
   re-rendering; only the loader and the baseline resolver set it.
2. **The prompt is the task verbatim, and the baseline-none arm never sees the skill's
   name.** Loaded mode invokes the skill through the product's own spelling; the baseline
   arm, having nothing to invoke, gets the bare task.
3. **`skill_triggered` comes only from the product's load event.** A product without one
   makes `offered` an authoring error, never a silent `false`.
4. **A limit the product cannot measure fails, it never passes.** `usage_note` for
   tokens, `cost_note` for cost, both handled by `BudgetEvaluator` the same way.
5. **A truncated trace is `RunResult.error`, never a partial parse.**
6. **Naming a product runner is the trust decision, and the report says so.** The
   product runs with permission prompts disabled and the full environment; no skill-lens
   sandbox applies; bundled scripts are reachable through the product's own tools;
   `allow_scripts` governs only `run_script`.
7. **Preflight spends nothing.** Executable found and executed, names checked, cases
   checked — all before the first case, exit 2 on the first problem.
8. **The process group is killed after every exit, and output is read through the
   harness's own handle, capped** — one implementation in `process.py`, shared with
   `run_script`.
9. **`--model` with nothing to read it is a user error.**
10. **`tools:` with a product runner is an authoring error**, not a silently emptier run.
11. **Runners and judges never raise for a product failure.** `ProductSetupError` is
    the one exception, and it is raised only from preflight.
12. **No agent-framework type appears outside the four adapter modules.** The new
    modules import `subprocess` and `json`, nothing else.
13. **Secrets come from environment variables only.** `[runners.<name>]` holds no token;
    the product reads its own.
14. **`skill_lens` never appears in user-facing output.**

## 11. Release shape

Two pull requests, each squash-merged, each `feat:`, each bumping the minor version:

1. `feat: run cases through an agent product's CLI` — Part 1. Additive: three runner
   names, a `[runners.<name>]` table, `Skill.markdown`, `RunResult.usage_note`,
   `RunReport.products`, one optional protocol member, one moved helper module. One
   behaviour change, documented: `--model` with no runner that reads it is now exit 2
   instead of silently ignored.
2. `feat: grade rubrics through an agent product's CLI` — Part 2. Additive: three judge
   names over the same table.

The Copilot `-p` recording lands in Part 1 when quota allows before it merges, or in a
`test:` follow-up; the schema-derived fixture keeps CI green either way.
