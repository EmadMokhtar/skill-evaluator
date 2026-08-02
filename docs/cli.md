# CLI

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <name>] [--tag <tag>]
                      [--min-pass-rate <float>] [--json-output <path>] [--config <file>]
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
| `--tag <tag>` | none | Only run cases carrying this tag |
| `--min-pass-rate <float>` | `1.0` | Required overall pass rate, `0.0`–`1.0` |
| `--json-output <path>` | none | Write a machine-readable report here |
| `--config <file>` | upward discovery | Path to `skill-eval.toml` |

Each flag overrides the corresponding key in [configuration](configuration.md).
Exit codes are documented in [Gating](gating.md).

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
order-support	2 case(s)	examples/order-support
```

## `--version`

Print the installed version and exit.
