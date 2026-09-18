## v0.9.1 (2026-09-18)

### Fix

- **copilot**: read the skill tool request as the offered-mode load signal (#51)

## v0.9.0 (2026-09-17)

### Feat

- grade rubrics through an agent product's CLI (#48)

## v0.8.0 (2026-09-17)

### Feat

- run cases through an agent product's CLI (#46)

## v0.7.0 (2026-09-17)

### Feat

- import mock tools from an MCP server's tools/list listing (#45)

## v0.6.0 (2026-09-13)

### Feat

- run every case through more than one runner (#35)

### Fix

- give every version spelling in docs/ci.md a line of its own (#36)

## v0.5.0 (2026-09-13)

### Feat

- run the scripts a skill bundles, under an opt-in and a sandbox (#34)
- run cases and grade rubrics through LangChain (#32)

## v0.4.0 (2026-09-11)

### Feat

- show why a case failed, rerun one case, and scaffold file-producing skills (#21)

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
