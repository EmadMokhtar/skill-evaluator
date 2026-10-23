# Configuration

`skill-lens.toml` is optional. It is located via `--config`, or otherwise discovered by
searching upward from the current directory — the repo root is the conventional home, not a
requirement. [`examples/skill-lens.toml`](https://github.com/EmadMokhtar/skill-evaluator/blob/main/examples/skill-lens.toml)
is a complete, annotated example: every key listed, the realistic ones live, the rest
commented out at their defaults.

```toml
default_runner = "fake"
min_pass_rate = 1.0
fail_on_error = true

[per_skill_min]
greeting = 0.9
```

| Key | Default | CLI override |
| --- | --- | --- |
| `default_runner` | `"fake"` (a string, or a list of names) | `--runner` |
| `model` | `"openai:gpt-4o-mini"` | `--model` |
| `base_url` | `""` (the provider's own variable, then its default) | `--base-url` |
| `temperature` | `0.0` | — |
| `retries` | `2` | — |
| `retry_backoff_seconds` | `1.0` | — |
| `judge` | `"fake"` | — |
| `judge_model` | `""` (falls back to `model`) | `--judge-model` |
| `judge_base_url` | `""` (inherits `base_url` with `model`) | — |
| `judge_temperature` | `0.0` | — |
| `min_pass_rate` | `1.0` | `--min-pass-rate` |
| `fail_on_error` | `true` | — |
| `per_skill_min` | `{}` | — |
| `baseline` | `""` | `--baseline` |
| `repeat` | `1` | `--repeat` |
| `min_delta` | unset | `--min-delta` |
| `concurrency` | `1` | `--concurrency` |
| `keep_workspace` | `false` | `--keep-workspace` / `--no-keep-workspace` |
| `full_output` | `false` | `--full-output` / `--no-full-output` |
| `max_file_bytes` | `1000000` | — |
| `max_files` | `200` | — |
| `max_total_bytes` | `5000000` | — |
| `allow_scripts` | `false` | `--allow-scripts` / `--no-allow-scripts` |
| `script_sandbox` | `"auto"` | — |
| `script_timeout_seconds` | `30.0` | — |
| `max_script_output_bytes` | `20000` | — |
| `script_interpreters` | `{ py = ["python3"], sh = ["bash"] }` | — |
| `runners` | `{}` | — |
| `product_sandbox` | `"auto"` | `--product-sandbox` |
| `max_spend_usd` | unset (no ceiling) | `--max-spend` |

Resolution order is **CLI flag > config file > built-in default**. API keys come from
environment variables only and are never read from config.

`default_runner` names `fake`, a framework runner (`pydantic-ai`, `langchain`) or a
[product runner](#product-runners) (`copilot`, `claude-code`, `cli`). It may be a list, in
which case every case runs through each runner named and the report shows one outcome per
`(skill, case, runner)`:

```toml
default_runner = ["pydantic-ai", "langchain"]
```

An empty list, or a name given twice, is a config error (exit 2) naming the field. A
`--runner` flag on the command line — repeatable — replaces the whole list; it never appends
to it. See [Runners](runners.md) for what the matrix measures and
[Gating](gating.md) for how it is gated.

`baseline` is `""` (off), `"none"` (compare against an empty skill) or `"previous"` (compare
against the prior version resolved from git). `repeat` is how many times each arm is sampled
per case. `min_delta` has no default — leaving it unset means the delta is reported but not
gated, and `0.0` is a real, stricter choice ("must not regress") rather than the same as
unset. All three are detailed in [Comparative evals](comparative-evals.md), including why
`min_delta` requires `baseline` to be set.

`concurrency` bounds how many cases run at once. It defaults to `1`, which runs everything
sequentially and behaves exactly as it did before the option existed. The work is
network-bound — one provider round trip per case against sub-millisecond of local work — so
raising it overlaps waiting, not computation; the practical ceiling is your provider's rate
limit, not your CPU. Runners and evaluators are shared across threads, so a custom one must
have no mutable state its `run`/`evaluate` touches.

`max_spend_usd` is the run's spend ceiling in US dollars. Once the spend the run has recorded
reaches it, no new case starts: cases already running finish and are scored, every case left
is reported as not run, and the gate fails. Unset, the default, means no ceiling, so
upgrading never stops a run on its own. It must be a positive, finite amount; `0`, a negative
number, `inf` and `nan` are config errors (exit 2), because a ceiling of zero would run
nothing and one of infinity is no ceiling. A ceiling must be able to see what is spent, so a
run with a `copilot` or `cli` runner or judge, or a keyed one whose model has no price, is
refused before it starts (exit 2), and spend that turns out unpriced during a run stops it.
The whole rule, and how it meets `--concurrency`, is in
[The spend ceiling](gating.md#the-spend-ceiling).

```toml
max_spend_usd = 5.00
```

`keep_workspace` keeps each case's temporary directory instead of deleting it after the run,
so you can inspect what a case actually wrote. `--keep-workspace` / `--no-keep-workspace`
override it in either direction; leaving both unset keeps the config file's value. Every kept
directory is printed under a `Kept workspaces` section, whichever of the flag or the config
turned keeping on — a setting that silently filled a disk with no on-screen explanation would
be a trap.

`full_output` lifts the 500-character cap on the agent output printed under a non-passing
case, and the 200-character cap on each stream of a failed hook, in every reporter. `--full-output` / `--no-full-output` override it in either
direction; leaving both unset keeps the config file's value. The cap is never silent — a cut
output always states exactly how many characters were removed — so the default is safe to
leave in CI, where a long red log helps nobody, and `true` is the right committed value for
a repository that reads its failures locally.

`max_file_bytes`, `max_files`, and `max_total_bytes` are runaway guards on what one case's
workspace may write, not something you tune per run — they get no CLI flag because they are
policy set once per repository rather than a per-run decision. Roughly 100x a realistic
artifact, so they only bind when a case is genuinely stuck (writing the same file repeatedly,
or writing many small ones) rather than when it legitimately produces something large.

`max_file_bytes` also caps what is *read*: the agent's `read_file` refuses a larger file
before opening it, because a bundled script can leave a sparse file of any apparent size
behind. The configured value applies to the agent's tools; a `file:` assertion, a judge
artifact and `read_skill_file` apply the built-in default of `1000000`, so a produced file
larger than 1 MB is scored as unreadable (a failed check) even where this setting is raised.
See [The workspace](runners.md#the-workspace).

`model`, `retries`, and `retry_backoff_seconds` only matter to components that reach a
provider (`pydantic-ai` or `langchain`, as a runner or a judge); `FakeRunner`, `FakeJudge`
and the product runners and judges ignore them — a product's model and timeout live in its
own [`[runners.<name>]` table](#product-runners).
`temperature` accepts a float or the literal string `"unset"`, for reasoning models that
reject any explicit temperature:

```toml
default_runner = "pydantic-ai"
model = "openai:gpt-4o-mini"
temperature = 0.0            # or "unset" for reasoning models, which reject it
retries = 2
retry_backoff_seconds = 1.0
```

A blank model id is rejected as a user error (exit 2) rather than being passed to a provider,
whether it arrives from `model`, `judge_model`, or the matching flag.

### Self-hosted endpoints

`base_url` points the runner's provider at a self-hosted, OpenAI-compatible server (Ollama,
vLLM, LM Studio, a gateway) instead of the provider's own endpoint. It is exactly the kind
of value a repository commits beside `model`: not a secret, and the same for every
contributor who runs the suite against the same machine.

```toml
default_runner = "pydantic-ai"
model = "ollama:gpt-oss:latest"
base_url = "http://localhost:11434/v1"
```

Resolution follows the usual order — `--base-url` > `base_url` > the provider's own
environment variable (`OPENAI_BASE_URL`, `OLLAMA_BASE_URL`, read by the framework when
skill-lens passes nothing) > the provider's default. The variable is a fallback, not an
override: to point a committed file at a different machine for one run, pass `--base-url`,
as `--model` does for the model.

The endpoint travels with the model. `judge_base_url` is empty by default, and an empty
value means the judge uses `base_url` **exactly when it uses `model`** — `judge_model` unset
and no `--judge-model`. A judge with a model of its own gets its provider's default
endpoint unless `judge_base_url` names one, so a cloud judge under a local runner is never
pointed at `localhost`, and two local models on one server are one line each:

```toml
judge = "pydantic-ai"
judge_model = "ollama:qwen3"                 # its own model, so ...
judge_base_url = "http://localhost:11434/v1" # ... its own endpoint, spelled out
```

What is checked, and when:

- **At load time (exit 2):** a value must be a bare `http://` or `https://` URL naming a
  host. A URL carrying a user name or password (`http://user:secret@host/v1`) is refused
  as a secret in a committed file — credentials go in the provider's environment variable.
- **In preflight, before any spend (exit 2):** a provider whose client takes no endpoint.
  Under `pydantic-ai`, `openai:`, `ollama:` and `anthropic:` take one; `deepseek:`,
  `azure:`, `openrouter:` and the other hosted-only prefixes do not, and the refusal names
  the provider. Under `langchain` the value reaches the chat model as `base_url=`, which the
  OpenAI and Anthropic families accept; a chat model that rejects it is refused the same
  way. Without a `base_url` nothing is checked and nothing changes.
- **Never relaxed:** the API-key check. `ollama:` needs no key. `openai:` pointed at a local
  server still needs `OPENAI_API_KEY` exported — to any value — because the provider's
  client requires one; the docs for your server say whether it reads it.

`ollama:` under `pydantic-ai` has no default endpoint: with neither `base_url` nor
`OLLAMA_BASE_URL` set, every case errors with the provider's own message. Committing
`base_url` is what makes that prefix work from a fresh clone.

Product runners and judges (`copilot`, `claude-code`, `cli`) reach their own endpoint and
ignore both keys, as they ignore `model`; `--base-url` under a run where nothing reads it is
a user error (exit 2), the rule `--model` follows. `base_url` is never printed in a report.

## Product runners

One `[runners.<name>]` table per product — `copilot`, `claude-code`, or `cli` — whether the
product runs cases, grades rubrics as the [judge](#judging), or both: the same table serves
the judge. `args` in particular reaches both seats at once: a flag added there, such as a
hypothetical `--deny-tool`, changes what the product can do as the runner and as the
judge — there is no separate key to restrict one without the other. Each table is optional
for a preset (the preset supplies the verified argv) and required for `cli`, which has no
preset:

```toml
default_runner = ["copilot", "claude-code"]

[runners.copilot]
args = ["--model", "gpt-5.2"]      # appended to the preset's argv
timeout_seconds = 900              # default 600; finite and positive

[runners.claude-code]
# Replaces the preset's argv entirely; keep the output-format flag or the
# trace cannot be read.
command = ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]

[runners.cli]
command = ["my-agent", "--prompt", "{prompt}"]   # required for cli
skills_dir = ".agents/skills"                     # default
invoke = "/{name} {task}"                         # default "{task}"
max_output_bytes = 8000000                        # default
```

| Key | Default | Meaning |
| --- | --- | --- |
| `command` | the preset's argv (required for `cli`) | The whole argv. Exactly one element must be `{prompt}`, and it cannot be the first (the executable); the prompt is substituted as that one element, never through a shell. When the first element is not the preset's own executable, preflight skips the `--version` probe (a wrapper might treat `--version` as a prompt), the judge's tool restriction is dropped, and a case with `tools:` is refused — a wrapper is not known to take the flag that hands the product the [MCP bridge](runners.md#mock-tools-under-a-product)'s config. |
| `args` | `[]` | Appended after `command`. Must not contain `{prompt}`. |
| `timeout_seconds` | `600.0` | Wall clock per case (per judge call, for the judge), finite and positive; the process group is killed at expiry. |
| `max_output_bytes` | `8000000` | Cap on the trace, positive; a longer one is an errored case naming this key. |
| `skills_dir` | `".agents/skills"` | `cli` only. Where the skill is written, relative to the working directory; no absolute path and no `..`. Not read by the judge, which delivers no skill. |
| `invoke` | `"{task}"` | `cli` only. The prompt in `mode: loaded`; only `{name}` and `{task}` may appear (no other field name, no conversion, no format spec), and `{task}` must appear. Not read by the judge. |
| `pass_env` | unset | Variable *names* (never values) the product also gets under the [product sandbox](#product-sandbox)'s home isolation, beyond the allowlist and the product's own auth variables. Each must be a variable name; one the isolation sets itself (`HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TMPDIR`, `TMP`, `TEMP`, the four `XDG_*_HOME`, `CLAUDE_CONFIG_DIR`, `COPILOT_HOME`, `CLAUDE_CODE_TMPDIR`, `CLAUDE_TMPDIR`) is a config error. Under home isolation `cli` must set it, `[]` when the command needs nothing. Not read under `product_sandbox = "off"`. |

Two keys with two meanings: drop an isolation flag with `command`, add a model with
`args`. `skills_dir` or `invoke` under a preset, an unknown table name, a `command` without
exactly one `{prompt}` element, `{prompt}` in `args`, and `cli` named as a runner or judge
with no `command` are all config errors (exit 2) naming the key. No API key or token belongs
here: the product reads its own auth. The tables are config-only, with no CLI flag: which
product a repository evaluates under, and how, is repository policy. When the product
judges, `claude-code` gets `--tools ""` and `copilot` gets
`--available-tools=skill-lens-none` appended after `args`, so each grades with no tools — as
long as `command` still names the preset's executable: a wrapper is not known to accept the
flag, so a `command` naming anything else drops it along with the `--version` probe. `cli`
gets nothing extra. The same table's `args` (or `command`) may not carry the product's own
tool-selection flag (`--tools` under `claude-code`, `--available-tools` under `copilot`) when
the product judges: both products accumulate a repeated flag instead of taking the last
one, so the entry would hand the judge tools back, and the judge's preflight refuses it
(exit 2) naming the entry. Narrow the runner's tools under a different judge instead. When
a case declares `tools:`, the runner's preflight refuses `--available-tools` in
`[runners.copilot]` the same way: the flag keeps only the tools it names, MCP tools
included, so it would hide the case's mock tools from the model (Claude Code's `--tools`
governs its built-in set only and is not refused). A case's `tools:` reach a preset through
the [MCP bridge](runners.md#mock-tools-under-a-product), whose config is appended after
`args` as the last argument; the bridge itself has no key here. See
[Runners](runners.md#product-runners) for what
each product runner measures, [Judging with a product](runners.md#judging-with-a-product)
for the judge, and [Security](security.md#product-runners) for what naming one means.

### Product sandbox

`product_sandbox` decides, once per run and before any case, how much a product runner and
the product judge are isolated from your machine. One decision serves every product in the
run, and every product line on the report says what applied.

| `product_sandbox` | Home isolation | OS sandbox | When no OS sandbox works |
| --- | --- | --- | --- |
| `"auto"` (default) | yes | `sandbox-exec` on macOS, `bwrap` on Linux, when its probe succeeds | home isolation only, and the report says why |
| `"required"` | yes | yes | exit 2 before any case |
| `"off"` | no — the whole environment and your real home | no | — |

**Home isolation** gives each case (and each judge call) a fresh temporary home directory,
deleted when the case ends — `--keep-workspace` keeps the workspace, never the home. `HOME`,
the four `XDG_*_HOME` directories and `TMPDIR` point into it, so the product's own config
(`~/.claude`, `~/.claude.json`, `~/.copilot`) starts empty and personal skills, plugins,
settings, caches and logins stay out of the run. The environment is built from an allowlist:
`PATH`, the locale, `TZ`, `SHELL`, `USER`, `LOGNAME`, the proxy and CA variables
(`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `ALL_PROXY` and their lower-case forms,
`SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS`), the product's own variables, and the
names in its table's `pass_env`:

| Product | Its own variables |
| --- | --- |
| `claude-code` | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `IS_SANDBOX` (which Claude Code needs to run as root with its permission prompts off); `DISABLE_AUTOUPDATER=1` is set, so a fresh home does not start an update, and `CLAUDE_CODE_TMPDIR` points at the temporary directory, because Claude Code keeps its own temporary files there rather than under `TMPDIR` |
| `copilot` | the first of `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` that is set — the one Copilot would use — and no other; `GH_TOKEN` and `GITHUB_TOKEN` are general GitHub tokens, in a workflow often the job's own |
| `cli` | none — name what the command needs in `pass_env` |

Home isolation is **not a sandbox**: a process can still read and write any file you can by
its absolute path. A login kept in your home directory or the OS keychain is out of reach
too, so the product authenticates from the environment — `CLAUDE_CODE_OAUTH_TOKEN` (from
`claude setup-token`) or `ANTHROPIC_API_KEY`, and `COPILOT_GITHUB_TOKEN`. Preflight refuses a
preset with none of its auth variables set and non-empty, or a credential named in `pass_env`
(a name containing `TOKEN`, `KEY`, `SECRET`, `PASSWORD` or `CREDENTIAL`; a region, a profile name or a provider switch does not count), with exit 2 and the fix; a `command` that names another executable (a wrapper that brings its own auth)
is not checked. A Bedrock or Vertex setup names its variables in `pass_env`:

```toml
[runners.claude-code]
pass_env = ["CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]

[runners.cli]
command = ["my-agent", "--prompt", "{prompt}"]
pass_env = []                    # the command needs nothing from your environment
```

**The OS sandbox** adds a boundary around the product's whole process tree: it cannot itself
change files outside its working directory and its own temporary directories (the home, and
the [MCP bridge](runners.md#mock-tools-under-a-product)'s directory for a case with
`tools:`), and cannot read other skill-lens temporary directories, such as the other arm's
workspace. It can read everything else you can — the products and their runtimes are
installed in your home directory — it can reach the network, because the product must reach
its provider, and it can ask a system service (`launchd`, a user systemd, Docker) to act
outside the sandbox for it.
The product's `--version` (and the bridge, when a case declares `tools:`) is started once
inside the sandbox before any case, so a product that will not start there is exit 2 up
front. See [Security](security.md#product-runners) for what each mode guarantees.

`--product-sandbox` overrides the key. `skill-lens.toml` is inside the trust boundary — in a
`pull_request` workflow a pull request can set `product_sandbox = "off"` for itself — so a
workflow that runs untrusted pull requests should pass `product-sandbox: required` in the
action (see [CI integration](ci.md#running-under-a-product)).

## Bundled scripts

`allow_scripts` is the trust switch. A skill may ship code under `scripts/` beside
`SKILL.md`; with this key `true` (or `--allow-scripts`) the agent gets a `run_script` tool
that executes it. It is off by default because a `SKILL.md` under evaluation is, by
construction, code nobody has vetted, and skill-lens is built to run in CI — running that
code is a decision the operator of the run states, never something an eval file can turn
on. Reading the rest of what the skill ships (`references/`, `templates/`, …) needs no opt-in.

With execution off, every discovered skill that bundles scripts is named on the report
(`skill log-triage bundles 1 script; execution is off (allow_scripts = true or
--allow-scripts)`), so a script that silently never runs cannot look like a skill that does
not need one. This key governs only skill-lens's `run_script` tool, and a product runner can
still run the scripts through its own tools: when the run includes one, that `bundles 1
script` line names `run_script` as what is off and says so (see [Product
runners](security.md#product-runners)). With execution on, a preflight runs once, after
discovery and before any case: every bundled script's interpreter must be on `PATH`, and
`script_sandbox = "required"` must find a sandbox, or the run exits 2 before any money is
spent. Both the preflight and the notes cover every *discovered* skill — including one that
`--tag` or `--case` filters out, or that has no cases — so a missing interpreter for a skill
that would never run still exits 2. That is the fail-closed choice: a check that quietly
skipped some skills would not be one.

The four `script_*` keys are repository policy, config-only:

- `script_sandbox` — `"auto"` uses an OS sandbox when the once-per-run probe finds one
  (`sandbox-exec` on macOS, `bwrap` on Linux) and the portable guards alone otherwise;
  `"required"` refuses to run scripts at all without one (exit 2, before any case runs);
  `"off"` never probes. See [Running bundled scripts](runners.md#running-bundled-scripts)
  for what each guarantees.
- `script_timeout_seconds` — wall clock per call; the whole process group is killed at
  expiry and the model is told the script was stopped. Must be a finite number greater
  than zero: TOML accepts a bare `inf`, and a timeout of infinity would be no timeout.
- `max_script_output_bytes` — per stream (stdout, stderr); anything beyond is cut with a
  marker stating exactly how many bytes were omitted. Equal to the judge's per-artifact
  cap on purpose: one number for how much untrusted output reaches a model.
- `script_interpreters` — a table from file extension to the argv prefix that runs it,
  each looked up on `PATH` (so a `uv tool install` of skill-lens, whose venv has none of
  the skill's dependencies, is not what runs the script). Keys are normalised to a bare
  lower-case extension (`".PY"` and `"py"` are the same key); an empty argv is a config
  error. A script with any other extension is refused with a message that lists the
  allowed ones. A script's own dependencies are the eval's problem: install them in the
  CI job.

```toml
allow_scripts = true
script_sandbox = "required"      # never run unsandboxed on this runner
script_timeout_seconds = 10

[script_interpreters]
py = ["python3", "-X", "utf8"]
sh = ["bash"]
```

Setting `script_*` keys while `allow_scripts` is `false` is fine — it is the normal state of
a repository that turns execution on only in one CI job.

## Judging

`judge` selects the judge the same way `default_runner` selects the runner, and defaults to
`"fake"` for the same reason: **upgrading must never start spending money on its own.**

`judge = "pydantic-ai"` and `judge = "langchain"` each need their extra installed; a
repository that installs only `skill-lens[langchain]` can both run and grade.

```toml
judge = "pydantic-ai"
judge_model = ""             # empty falls back to `model`
judge_base_url = ""          # empty inherits `base_url` exactly when `judge_model` is empty
judge_temperature = 0.0      # or "unset" for a reasoning judge model
```

`judge_base_url` follows the model: see [Self-hosted endpoints](#self-hosted-endpoints).

`judge` also accepts `copilot`, `claude-code` and `cli`: the rubric is then graded through
that product, started from its [`[runners.<name>]` table](#product-runners) — the same table
that configures it as a runner — with no API key. `judge_model` and `judge_temperature` are
then not read (a product's model is set with `[runners.<name>] args`, and no product
exposes a temperature), and `--judge-model` is a user error (exit 2). See
[Judging with a product](runners.md#judging-with-a-product) for how the verdict is read.

```toml
judge = "claude-code"        # grades with no tools (so does copilot)

[runners.claude-code]
args = ["--model", "sonnet"]  # the judge's model, and the runner's if it runs here too
```

The default `judge = "fake"` does not grade. Rather than passing a rubric it never checked,
it reports the case as **errored** — nothing was verified, so nothing is reported as
verified. A consequence worth knowing: `--judge-model` does not turn judging on, because the
judge is selected by `judge`, not by naming a model — and since the flag is read only by
`judge = "pydantic-ai"` or `"langchain"`, passing it under any other judge (`"fake"` or a
product) is a user error (exit 2) rather than a flag that silently did nothing. `--model`
follows the same rule: a run where nothing reads it — `--runner fake`, or only product
runners, with no keyed judge falling back to it — refuses it; see [CLI](cli.md#run).

`judge_temperature` is deliberately **separate from** `temperature` and defaults to `0`.
Sampling the skill under test is a normal thing to want; sampling the grader is not, because
an unstable judge makes the same output pass one day and fail the next, which is
indistinguishable from a real regression.
