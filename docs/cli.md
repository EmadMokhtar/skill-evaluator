# CLI

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <name>]
                      [--judge-model <name>] [--tag <tag>] [--min-pass-rate <float>]
                      [--json-output <path>] [--config <file>] [--baseline <kind>]
                      [--repeat <int>] [--min-delta <float>]
skill-eval list <path> [--evals <path>]
skill-eval --version
```

`<path>` is a skill directory or a directory of skill directories. Discovery is
recursive.

## `run`

Discover skills, run their eval cases, score them, and gate on the results.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |
| `--runner <name>` | `fake` | `fake` or `pydantic-ai` — see [Runners](runners.md) |
| `--model <name>` | `openai:gpt-4o-mini` | Model id, passed to runners that use one |
| `--judge-model <name>` | falls back to `--model` | Model id for the LLM judge |
| `--tag <tag>` | none | Only run cases carrying this tag |
| `--min-pass-rate <float>` | `1.0` | Required overall pass rate, `0.0`–`1.0` |
| `--json-output <path>` | none | Write a machine-readable report here |
| `--config <file>` | upward discovery | Path to `skill-eval.toml` |
| `--baseline <kind>` | off | Run a second, baseline arm: `none` (no skill loaded) or `previous` (the prior version, from git). Omit for a single-arm run. |
| `--repeat <int>` | `1` | Sample each arm this many times. Each repetition is its own outcome. |
| `--min-delta <float>` | unset | Require the candidate arm to beat the baseline by at least this much. Requires `--baseline`. |

Each flag overrides the corresponding key in [configuration](configuration.md).
Exit codes are documented in [Gating](gating.md). `--baseline`, `--repeat` and `--min-delta`
are covered in full in [Comparative evals](comparative-evals.md).

`--repeat` and `--baseline` multiply spend: `--repeat 5 --baseline previous` runs 10x as many
cases as a plain run (5 repetitions x 2 arms). Before a run on a runner that needs an API key,
the CLI prints a run plan:

```
Plan: up to 2 arm(s) x 3 repeat(s) x 4 case(s) = 24 runs
```

This is deliberately a **ceiling, not a forecast** — "up to", not "exactly". It applies the
`--tag` filter, but it does not resolve baselines or evaluate per-case arm rules (a `mode:
offered` case skips the baseline arm under `--baseline none`; a skill whose previous version
cannot be resolved skips it for that whole skill). Both of those only ever *reduce* the real
count from what the plan line shows.

`--judge-model` names the model the judge grades with, but it does not turn judging on: the
judge is selected by the `judge` key in [`skill-eval.toml`](configuration.md#judging), which
defaults to `"fake"` so that upgrading never starts spending money on its own. A blank model
id is rejected as a user error (exit 2) rather than reaching a provider.

## `list`

Show the skills that would be evaluated and how many cases each has. Discovers and
validates every eval file without calling a runner — free, and no API key required.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--evals <path>` | discovery | An explicit eval file or directory, overriding discovery |

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	5 case(s)	examples/order-support
```

## `--version`

Print the installed version and exit.
