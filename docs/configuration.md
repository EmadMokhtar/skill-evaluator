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
| `default_runner` | `"fake"` | `--runner` |
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

Resolution order is **CLI flag > config file > built-in default**. API keys come from
environment variables only and are never read from config.

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

`model`, `retries`, and `retry_backoff_seconds` only matter to components that reach a
provider (`pydantic-ai` or `langchain`, as a runner or a judge); `FakeRunner` and `FakeJudge`
ignore them.
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
verified. A consequence worth knowing: `--judge-model` does nothing on its own, because the
judge is selected by `judge`, not by naming a model.

`judge_temperature` is deliberately **separate from** `temperature` and defaults to `0`.
Sampling the skill under test is a normal thing to want; sampling the grader is not, because
an unstable judge makes the same output pass one day and fail the next, which is
indistinguishable from a real regression.
