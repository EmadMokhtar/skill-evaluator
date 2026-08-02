---
applyTo: "src/skill_eval/models.py,src/skill_eval/config.py,src/skill_eval/yaml_loading.py,src/skill_eval/cases/**,src/skill_eval/skills/**"
---

# Reviewing models and loaders

This is where user-authored files become typed objects. Every mistake here is silent.

- **`extra="forbid"` on every user-authored model** — `EvalCase`, `AssertionSpec`,
  `ToolSpec`, `TrajectorySpec`, `BudgetSpec`, `Config`. Without it a typo like `assertion:`
  yields a case that passes vacuously, the worst failure mode an eval tool has. Flag any
  removal or any new user-authored model that omits it.
- **`models.py` holds every data shape.** Other modules import from it; they do not define
  their own.
- **All file IO pins `encoding="utf-8"`** and re-raises `OSError`/`UnicodeDecodeError` as a
  typed parse error (`SkillParseError`, `CaseParseError`, `ConfigError`) naming the file and
  the field. A raw traceback reaching the user is a bug.
- **YAML goes through `yaml_loading.safe_load`.** PyYAML's `SafeLoader` is YAML 1.1 and
  turns bare `yes`/`no`/`on`/`off` into booleans; an assertion `value: yes` is meant as the
  string.
- **Secrets never come from `skill-eval.toml`.** A config file is committed; a key must not
  be. Flag any new config field that would hold a credential.
- **Derived values are properties, not stored fields** — `RunResult.tokens`, `errored`, and
  the `RunReport` aggregates. A stored copy can disagree with its source.
- Skills with no eval files are reported as **skipped**, visibly — never silently dropped.
