## v0.3.0 (2026-09-11)

### Feat

- give eval cases a contained filesystem and score the files they produce (#12)

## v0.2.0 (2026-09-09)

### BREAKING CHANGE

- the distribution, command, config filename and Python package
are renamed from skill-eval to skill-lens.

### Feat

- rename to skill-lens and release to PyPI on merge to main
- report runs as JUnit and Markdown, run them concurrently, and ship a GitHub Action (#8)
- compare each eval case against a baseline and gate on the improvement (#7)
- add an eval-writing skill and the init scaffolder (#6)
- add LLM-as-judge scoring and skill-triggering evals (#5)
- evaluate skills against real agents with trajectory and budget scoring (#3)
- add skill-eval design and zero-cost eval engine (M0+M1) (#1)
