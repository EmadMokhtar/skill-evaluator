# skill-eval

Run evaluations on Agent Skills (`SKILL.md`) — in CI/CD or on demand.

**📖 Full documentation: <https://emadmokhtar.github.io/skill-evaluator/>**

Skills and their eval cases are **inputs** to the tool. Nothing about a skill under test
is vendored here, so any skill repo can adopt `skill-eval` without embedding it.

> **Status:** M2. The full pipeline — discovery, scoring, reporting, gating — runs offline
> against `FakeRunner` (the default, scripted, free) and against real agents through
> `pydantic-ai` (provider-flexible), scoring output text, tool-use trajectories, and
> efficiency budgets.

## Install

```bash
uv sync
```

## Quickstart

A skill is a directory containing `SKILL.md`. Its eval cases live beside it:

```
examples/
  greeting/
    SKILL.md
    greeting.eval.yaml
```

```yaml
# greeting.eval.yaml
cases:
  - name: greets the named person in one sentence
    task: greet Ada
    tags: [smoke]
    budget:
      max_tokens: 500
    assertions:
      - kind: contains
        value: Ada
      - kind: not_contains
        value: Traceback
```

Point the CLI at a single skill directory or at a parent directory of many — discovery is
recursive:

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	2 case(s)	examples/order-support
```

`list` discovers skills and validates every eval file without calling a runner — free, and
no API key required.

## Documentation

| Topic | Page |
| --- | --- |
| First eval, end to end | [Getting started](https://emadmokhtar.github.io/skill-evaluator/getting-started/) |
| Eval YAML reference | [Eval files](https://emadmokhtar.github.io/skill-evaluator/eval-files/) |
| Commands and flags | [CLI](https://emadmokhtar.github.io/skill-evaluator/cli/) |
| `skill-eval.toml` | [Configuration](https://emadmokhtar.github.io/skill-evaluator/configuration/) |
| Real agents, tools, budgets | [Runners](https://emadmokhtar.github.io/skill-evaluator/runners/) |
| Exit codes and CI | [Gating](https://emadmokhtar.github.io/skill-evaluator/gating/) |
| How it is built | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Contributing | [Contributing](https://emadmokhtar.github.io/skill-evaluator/contributing/) |

## License

MIT — see [LICENSE](LICENSE).
