# Configuration

`skill-eval.toml` is optional. It is located via `--config`, or otherwise discovered by
searching upward from the current directory — the repo root is the conventional home, not a
requirement.

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
| `baseline` | `""` | `""` (off), `"none"` or `"previous"`. Overridden by `--baseline`. |
| `repeat` | `1` | Repetitions per arm. Overridden by `--repeat`. |
| `min_delta` | unset | Required improvement over the baseline. Unset means the delta is reported but not gated; `0.0` is the stricter "must not regress". Overridden by `--min-delta`. |

Resolution order is **CLI flag > config file > built-in default**. API keys come from
environment variables only and are never read from config.

`model`, `retries`, and `retry_backoff_seconds` only matter to components that reach a
provider (`pydantic-ai`, as a runner or a judge); `FakeRunner` and `FakeJudge` ignore them.
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
