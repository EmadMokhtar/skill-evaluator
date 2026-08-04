# CLI

```
skill-eval run <path> [--evals <path>] [--runner <name>] [--model <name>]
                      [--judge-model <name>] [--tag <tag>] [--min-pass-rate <float>]
                      [--json-output <path>] [--config <file>]
skill-eval list <path> [--evals <path>]
skill-eval init <path> [--force]
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

Each flag overrides the corresponding key in [configuration](configuration.md).
Exit codes are documented in [Gating](gating.md).

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

## `init`

```bash
skill-eval init <skill-dir> [--force]
```

Writes a starter eval suite to `<skill-dir>/evals/<skill-name>.eval.yaml`: a common-case
case, a policy-edge case carrying `tools:` and `trajectory:`, and both halves of the
`mode: offered` triggering pair.

Every field you have to supply holds the placeholder `TODO(skill-eval)`, and a case still
containing one aborts the run as an [authoring error](eval-files.md#unfilled-scaffolds).
The generated file is therefore never a green suite that checks nothing.

| Flag | Meaning |
| --- | --- |
| `--force` | Overwrite an existing eval file. Without it, an existing file is a user error. |

Exit `0` on success. Exit `2` when the path holds no `SKILL.md`, when the output file
exists and `--force` was not given, or when the file cannot be written.

## `--version`

Print the installed version and exit.
