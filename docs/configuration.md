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
| `temperature` | `0.0` | — |
| `retries` | `2` | — |
| `retry_backoff_seconds` | `1.0` | — |
| `judge` | `"fake"` | — |
| `judge_model` | `""` (falls back to `model`) | `--judge-model` |
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

`keep_workspace` keeps each case's temporary directory instead of deleting it after the run,
so you can inspect what a case actually wrote. `--keep-workspace` / `--no-keep-workspace`
override it in either direction; leaving both unset keeps the config file's value. Every kept
directory is printed under a `Kept workspaces` section, whichever of the flag or the config
turned keeping on — a setting that silently filled a disk with no on-screen explanation would
be a trap.

`full_output` lifts the 500-character cap on the agent output printed under a non-passing
case, in every reporter. `--full-output` / `--no-full-output` override it in either
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
and the product runners ignore them — a product's model and timeout live in its own
[`[runners.<name>]` table](#product-runners).
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

## Product runners

One `[runners.<name>]` table per product runner — `copilot`, `claude-code`, or `cli`. Each
table is optional for a preset (the preset supplies the verified argv) and required for
`cli`, which has no preset:

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
| `command` | the preset's argv (required for `cli`) | The whole argv. Exactly one element must be `{prompt}`, and it cannot be the first (the executable); the prompt is substituted as that one element, never through a shell. |
| `args` | `[]` | Appended after `command`. Must not contain `{prompt}`. |
| `timeout_seconds` | `600.0` | Wall clock per case, finite and positive; the process group is killed at expiry. |
| `max_output_bytes` | `8000000` | Cap on the trace, positive; a longer one is an errored case naming this key. |
| `skills_dir` | `".agents/skills"` | `cli` only. Where the skill is written, relative to the working directory; no absolute path and no `..`. |
| `invoke` | `"{task}"` | `cli` only. The prompt in `mode: loaded`; `{name}` and `{task}` are substituted, and `{task}` must appear. |

Two keys with two meanings: drop an isolation flag with `command`, add a model with
`args`. `skills_dir` or `invoke` under a preset, an unknown table name, a `command` without
exactly one `{prompt}` element, `{prompt}` in `args`, and `cli` named as a runner with no
`command` are all config errors (exit 2) naming the key. No API key or token belongs here:
the product reads its own auth. The tables are config-only, with no CLI flag: which product
a repository evaluates under, and how, is repository policy. See
[Runners](runners.md#product-runners) for what each product runner measures and
[Security](security.md#product-runners) for what naming one means.

## Bundled scripts

`allow_scripts` is the trust switch. A skill may ship code under `scripts/` beside
`SKILL.md`; with this key `true` (or `--allow-scripts`) the agent gets a `run_script` tool
that executes it. It is off by default because a `SKILL.md` under evaluation is, by
construction, code nobody has vetted, and skill-lens is built to run in CI — running that
code is a decision the operator of the run states, never something an eval file can turn
on. Reading the bundle (`references/`, `assets/`, `scripts/`) needs no opt-in.

With execution off, every discovered skill that bundles scripts is named on the report
(`skill log-triage bundles 1 script; execution is off (allow_scripts = true or
--allow-scripts)`), so a script that silently never runs cannot look like a skill that does
not need one. With execution on, a preflight runs once, after discovery and before any
case: every bundled script's interpreter must be on `PATH`, and `script_sandbox =
"required"` must find a sandbox, or the run exits 2 before any money is spent. Both the
preflight and the notes cover every *discovered* skill — including one that `--tag` or
`--case` filters out, or that has no cases — so a missing interpreter for a skill that
would never run still exits 2. That is the fail-closed choice: a check that quietly
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
judge_temperature = 0.0      # or "unset" for a reasoning judge model
```

The default `judge = "fake"` does not grade. Rather than passing a rubric it never checked,
it reports the case as **errored** — nothing was verified, so nothing is reported as
verified. A consequence worth knowing: `--judge-model` does not turn judging on, because the
judge is selected by `judge`, not by naming a model — and since the flag is read only by
`judge = "pydantic-ai"` or `"langchain"`, passing it under any other judge is a user error
(exit 2) rather than a flag that silently did nothing. `--model` follows the same rule: a
run where nothing reads it — `--runner fake`, or only product runners, with no keyed judge
falling back to it — refuses it; see [CLI](cli.md#run).

`judge_temperature` is deliberately **separate from** `temperature` and defaults to `0`.
Sampling the skill under test is a normal thing to want; sampling the grader is not, because
an unstable judge makes the same output pass one day and fail the next, which is
indistinguishable from a real regression.
