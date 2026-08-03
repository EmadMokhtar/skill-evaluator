---
applyTo: "src/skill_eval/cli.py,src/skill_eval/gating.py,src/skill_eval/orchestrator.py,src/skill_eval/reporters/**"
---

# Reviewing the CLI, gate, orchestrator and reporters

This is the contract surface. CI depends on the exit code; humans depend on the output.

- **Exit codes are the contract:** gate passed `0`, gate failed `1`, user or authoring error
  `2`. A JSON-write failure escalates to 2 **only when the gate itself passed** — it must
  never mask an already-failing gate. Flag any change that widens or reorders these.
- **A run executing zero cases fails the gate.** "Nothing ran" is a broken run, not a pass —
  otherwise a mistyped path reports success forever. The reason must name the cause: no
  skills found, all skills skipped for having no cases, or every case filtered out by
  `--tag`.
- **Errored cases fail the gate by default**, so CI never goes green on a run that did not
  happen.
- **Authoring errors abort the run.** `orchestrator.run_evals` deliberately lets them
  propagate; `cli.py` catches them via `_AUTHORING_ERRORS` and prints the message without a
  traceback. Flag any `try`/`except` in the orchestrator that would swallow one into a
  failing score.
- **`skill_eval` (underscore) never appears in user-facing output.** The name is
  `skill-eval`: command, config file, distribution, prose.
- A new runner must be registered in `cli._RUNNERS`, and if it spends money it needs the
  preflight key check before construction so a missing key costs nothing and exits 2.
- Gate reasons are read by whoever is staring at a red pipeline. Each should say what failed
  and against which threshold.
