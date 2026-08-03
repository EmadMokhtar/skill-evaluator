# Eval-writing skill and `init` scaffolder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `skill-eval init` (a deterministic eval-suite scaffolder whose placeholders the loader refuses to run) and `writing-skill-evals` (an Agent Skill that supplies the judgment about which cases to write and what a red case means).

**Architecture:** `scaffold.py` renders a starter suite as a pure function over a loaded `Skill`; `cli.py` does the file IO. Unfilled placeholders carry the literal `TODO(skill-eval)`, which `cases/loader.py` rejects *before* Pydantic validation — an authoring error, exit 2, never a scored failure. The skill lives at `skills/writing-skill-evals/`, symlinked into `.claude/skills/` so the repo runs it on itself, and ships its own eval suite.

**Tech Stack:** Python 3.13, Pydantic v2, Typer, PyYAML (through `skill_eval.yaml_loading.safe_load`), pytest, ruff, uv.

## Global Constraints

- Line length 100; `uv run ruff check .` and `uv run ruff format --check .` must pass.
- `from __future__ import annotations` at the top of every new Python module.
- All file IO pins `encoding="utf-8"`.
- YAML is read through `skill_eval.yaml_loading.safe_load`, never `yaml.safe_load`.
- `skill_eval` (underscore) never appears in user-facing output; the user-facing name is `skill-eval`.
- Exit codes are the CI contract: gate pass `0`, gate fail `1`, user/authoring error `2`.
- Authoring errors abort the run and never score as case failures.
- Every test passes offline with no API key; the `FakeRunner` tier stays deterministic.
- Conventional Commits on every commit — enforced by a `commit-msg` hook and by CI.
- Documentation ships with the change, per the table in `CLAUDE.md`.
- The sentinel string is exactly `TODO(skill-eval)`, defined once as
  `skill_eval.cases.loader.UNFILLED_SENTINEL` and imported everywhere else.

---

### Task 1: The loader refuses an unfilled scaffold

**Files:**
- Modify: `src/skill_eval/cases/loader.py:14-52`
- Modify: `docs/eval-files.md` (new section before "## Assertion kinds")
- Modify: `ARCHITECTURE.md` (§Invariants, and why)
- Modify: `CLAUDE.md` (§Invariants that are easy to break)
- Test: `tests/test_case_loader.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `skill_eval.cases.loader.UNFILLED_SENTINEL: str` (the literal
  `"TODO(skill-eval)"`) and the guarantee that `parse_cases_file` raises
  `CaseParseError` for any case containing it. Tasks 2 and 3 import the constant.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_case_loader.py`:

```python
def test_a_sentinel_in_a_case_is_an_authoring_error(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: handles the common case\n"
        "    task: TODO(skill-eval) the prompt a user would type\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    message = str(exc.value)
    assert "TODO(skill-eval)" in message
    assert "task" in message
    assert str(path) in message


def test_a_sentinel_nested_in_a_tool_names_the_field(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: takes the right path\n"
        "    task: refund order 1234\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "        description: look an order up\n"
        "        returns: 'TODO(skill-eval) the JSON this tool returns'\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "tools[0].returns" in str(exc.value)


def test_a_sentinel_in_a_rubric_entry_names_its_position(tmp_path):
    path = tmp_path / "unfilled.eval.yaml"
    path.write_text(
        "cases:\n"
        "  - name: explains itself\n"
        "    task: refund order 1234\n"
        "    judge:\n"
        "      rubric:\n"
        "        - The reply names order 1234\n"
        "        - TODO(skill-eval) what else a good answer does\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path)
    assert "judge.rubric[1]" in str(exc.value)


def test_a_sentinel_in_a_comment_is_not_a_sentinel(tmp_path):
    # Comments are discarded by the YAML parser before the scan sees the data,
    # which is what lets the generated file explain the token it uses.
    path = tmp_path / "filled.eval.yaml"
    path.write_text(
        "# Replace every TODO(skill-eval) before running this file.\n"
        "cases:\n"
        "  - name: handles the common case\n"
        "    task: greet Ada\n"
        "    assertions:\n"
        "      - kind: contains\n"
        "        value: Ada\n",
        encoding="utf-8",
    )
    cases = parse_cases_file(path)
    assert [case.name for case in cases] == ["handles the common case"]
```

If `tests/test_case_loader.py` does not already import `pytest`, `parse_cases_file`, and
`CaseParseError`, add:

```python
import pytest

from skill_eval.cases.loader import CaseParseError, parse_cases_file
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_case_loader.py -k sentinel -v
```

Expected: FAIL. The first three fail with `DID NOT RAISE <class 'CaseParseError'>` (the
sentinel is a legal string today); the comment test passes already.

- [ ] **Step 3: Implement the scan**

In `src/skill_eval/cases/loader.py`, add the constant beside the existing ones (after
`EVAL_SUFFIX = ".eval.yaml"`, line 15):

```python
# The placeholder `skill-eval init` writes into every field the author has to
# fill in. Living here rather than in scaffold.py makes it the *loader's*
# guarantee: a hand-written stub is refused exactly like a generated one.
UNFILLED_SENTINEL = "TODO(skill-eval)"
```

Add the scan function immediately after the `CaseParseError` class:

```python
def _reject_unfilled(path: Path, index: int, raw: object, trail: str = "") -> None:
    """Refuse a case still carrying scaffold placeholders.

    Runs before schema validation so the message names the field to fill in
    rather than complaining about the type of a value nobody meant to keep.
    An unfilled scaffold says something about the author's progress, not about
    the skill, so it aborts the run as an authoring error instead of scoring
    as a failure.
    """
    if isinstance(raw, str):
        if UNFILLED_SENTINEL in raw:
            raise CaseParseError(
                f"{path}: case #{index + 1} still has the scaffold placeholder "
                f"{UNFILLED_SENTINEL} at {trail or 'case'}. Fill it in -- an "
                f"unfinished eval cannot say anything about the skill."
            )
    elif isinstance(raw, dict):
        for key, value in raw.items():
            _reject_unfilled(path, index, value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(raw, list):
        for position, value in enumerate(raw):
            _reject_unfilled(path, index, value, f"{trail}[{position}]")
```

In `parse_cases_file`, call it first in the loop (currently line 44-46):

```python
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
        try:
            case = EvalCase.model_validate(raw)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_case_loader.py -v
```

Expected: PASS, including the pre-existing tests in the file.

- [ ] **Step 5: Document the rule**

In `docs/eval-files.md`, insert this section immediately before `## Assertion kinds`:

```markdown
## Unfilled scaffolds

`skill-eval init` writes placeholder fields holding the literal `TODO(skill-eval)`.
Loading a case that still contains one is an **authoring error**: the run aborts with
exit `2` naming the file, the case, and the field.

That is deliberate, and it is the loader's rule rather than the scaffolder's — a
hand-written stub is refused the same way. An unfinished eval that ran would either pass
while checking nothing or fail while saying nothing about the skill, and both are worse
than a run that stops and tells you which field to fill in.

Comments are discarded before the check, so a file may discuss the token freely.
```

In `ARCHITECTURE.md`, under `## Invariants, and why`, add a bullet in the style of the
surrounding ones:

```markdown
- **An unfilled scaffold is an authoring error, not a failure.** `skill-eval init` writes
  `TODO(skill-eval)` into every field the author must supply, and `cases/loader.py`
  rejects any case still containing it — before schema validation, so the message names
  the field rather than its type. Enforcing this in the loader rather than the generator
  makes it unconditional: hand-written stubs get it too, and no CI configuration can opt
  out of it.
```

In `CLAUDE.md`, under `## Invariants that are easy to break`, add the condensed form:

```markdown
- **An unfilled scaffold aborts the run.** A case still containing `TODO(skill-eval)` is
  an authoring error (exit 2), checked in `cases/loader.py` before validation so the
  message names the field. The rule is the loader's, so hand-written stubs get it too.
```

- [ ] **Step 6: Verify the whole suite and the linters**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
```

Expected: all tests pass, no lint or format diagnostics.

- [ ] **Step 7: Commit**

```bash
git add src/skill_eval/cases/loader.py tests/test_case_loader.py docs/eval-files.md ARCHITECTURE.md CLAUDE.md
git commit -m "feat: reject eval cases that still hold scaffold placeholders"
```

---

### Task 2: Render the starter suite

**Files:**
- Create: `src/skill_eval/scaffold.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `skill_eval.cases.loader.UNFILLED_SENTINEL` (Task 1);
  `skill_eval.models.Skill` (fields: `name: str`, `description: str`,
  `instructions: str`, `path: Path`).
- Produces: `skill_eval.scaffold.render_scaffold(skill: Skill) -> str` — the full text of
  a starter eval file. Pure: no filesystem access. Task 3 writes the returned string.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scaffold.py`:

```python
"""The generated suite must be real YAML, refuse to run, and run once filled in."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_eval.cases.loader import UNFILLED_SENTINEL, CaseParseError, parse_cases_file
from skill_eval.models import Skill
from skill_eval.scaffold import render_scaffold
from skill_eval.yaml_loading import safe_load

SKILL = Skill(
    name="order-support",
    description="Handle customer refund requests against the 30-day return policy",
    instructions="Always call lookup_order first.",
    path=Path("order-support"),
)


def test_the_scaffold_names_the_skill_and_quotes_its_description():
    text = render_scaffold(SKILL)
    assert "order-support" in text
    assert "Handle customer refund requests against the 30-day return policy" in text


def test_the_scaffold_is_valid_yaml_with_four_cases():
    data = safe_load(render_scaffold(SKILL))
    assert len(data["cases"]) == 4


def test_the_scaffold_ships_both_halves_of_the_triggering_pair():
    data = safe_load(render_scaffold(SKILL))
    triggered = [
        case["trajectory"]["skill_triggered"]
        for case in data["cases"]
        if case.get("mode") == "offered"
    ]
    assert sorted(triggered) == [False, True]


def test_every_scaffold_case_carries_a_placeholder():
    data = safe_load(render_scaffold(SKILL))
    for case in data["cases"]:
        assert UNFILLED_SENTINEL in str(case), case["name"]


def test_a_fresh_scaffold_refuses_to_load(tmp_path):
    path = tmp_path / "order-support.eval.yaml"
    path.write_text(render_scaffold(SKILL), encoding="utf-8")
    with pytest.raises(CaseParseError) as exc:
        parse_cases_file(path, SKILL)
    assert UNFILLED_SENTINEL in str(exc.value)


def test_a_filled_scaffold_loads_clean(tmp_path):
    # Substituting any real text for the placeholder must be all it takes: if
    # the generated file were malformed in some other way -- a trajectory
    # naming an undeclared tool, `skill_triggered` on a loaded case -- the
    # cross-reference checks would catch it here.
    path = tmp_path / "order-support.eval.yaml"
    filled = render_scaffold(SKILL).replace(UNFILLED_SENTINEL, "refund order 1234:")
    path.write_text(filled, encoding="utf-8")
    cases = parse_cases_file(path, SKILL)
    assert len(cases) == 4
    assert [case.mode for case in cases] == ["loaded", "loaded", "offered", "offered"]


def test_no_scaffold_case_is_assertion_free_unless_it_checks_triggering():
    # A case with no assertions passes vacuously; the generated loaded cases
    # must never model that.
    data = safe_load(render_scaffold(SKILL))
    for case in data["cases"]:
        if case.get("mode") == "offered":
            continue
        assert case["assertions"], case["name"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_scaffold.py -v
```

Expected: FAIL at collection — `ModuleNotFoundError: No module named 'skill_eval.scaffold'`.

- [ ] **Step 3: Write the module**

Create `src/skill_eval/scaffold.py`:

```python
"""Render a starter eval suite for a skill.

Structure is deterministic, so it belongs in the CLI; judgment about *which*
cases a given skill needs belongs to the writing-skill-evals skill. Rendering
is a pure function over a loaded `Skill` so it can be tested as a string, with
the file IO left to cli.py.
"""

from __future__ import annotations

from skill_eval.cases.loader import UNFILLED_SENTINEL
from skill_eval.models import Skill

# A judge block is deliberately absent: the default judge does not grade, so a
# generated rubric would error every case until the author configures one.
# references/eval-file-syntax.md in the writing-skill-evals skill covers it.
_TEMPLATE = """\
# Eval suite for the {name} skill, written by `skill-eval init`.
#
# Replace every {sentinel} below. Until you do, this file refuses to run:
# skill-eval treats an unfilled scaffold as an authoring error (exit 2) rather
# than reporting cases that check nothing as passes.
#
# Reference: https://emadmokhtar.github.io/skill-evaluator/eval-files/
cases:
  # 1. The common case. Keep at least one assertion -- a case with none passes
  #    without checking anything.
  - name: handles the common case
    task: {sentinel} the prompt a user would type
    tags: [smoke]
    assertions:
      - kind: contains
        value: {sentinel} a string every good answer contains

  # 2. The edge this skill exists to get right. Mock tools execute nothing:
  #    calling one records the call and returns `returns` verbatim, so the
  #    trajectory is genuinely the model's choice. `trajectory` catches the
  #    failure an output assertion cannot see -- deciding without looking.
  - name: takes the right path on the hard case
    task: {sentinel} the prompt that reaches the policy edge
    tools:
      - name: lookup_something
        description: {sentinel} what this tool does
        parameters:
          query: string
        returns: '{sentinel} the JSON this tool returns'
    trajectory:
      called: [lookup_something]
      max_calls: 3
    assertions:
      - kind: contains
        value: {sentinel} a string every good answer contains

  # 3 and 4. Does the agent reach for the skill at all? `mode: offered` stops
  #    force-loading it and registers it as a tool described by its frontmatter
  #    description, which for this skill reads:
  #
  #      {description}
  #
  #    Ship both halves. Positives alone score a skill that fires on
  #    everything at 100%.
  - name: reaches for the skill when it should
    mode: offered
    task: {sentinel} a prompt this skill is for
    tags: [triggering]
    trajectory:
      skill_triggered: true

  - name: leaves unrelated work alone
    mode: offered
    task: {sentinel} a prompt this skill is NOT for
    tags: [triggering]
    trajectory:
      skill_triggered: false
"""


def render_scaffold(skill: Skill) -> str:
    """Return the text of a starter eval suite for `skill`."""
    # Collapsed to one line: the description is interpolated into a YAML
    # comment, and a newline in it would end the comment mid-sentence and
    # leave the remainder as syntax.
    description = " ".join(skill.description.split()) or "(this skill has no description)"
    return _TEMPLATE.format(
        name=skill.name,
        description=description,
        sentinel=UNFILLED_SENTINEL,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_scaffold.py -v
```

Expected: PASS, all eight tests.

- [ ] **Step 5: Verify the suite and the linters**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
```

Expected: all tests pass, clean lint and format.

- [ ] **Step 6: Commit**

```bash
git add src/skill_eval/scaffold.py tests/test_scaffold.py
git commit -m "feat: render a starter eval suite from a loaded skill"
```

---

### Task 3: `skill-eval init`

**Files:**
- Modify: `src/skill_eval/cli.py` (imports at 10-24; new command after `list_skills`)
- Modify: `docs/cli.md`
- Modify: `docs/roadmap.md:12`
- Modify: `ARCHITECTURE.md` (§Module map)
- Test: `tests/test_cli_init.py`

**Interfaces:**
- Consumes: `skill_eval.scaffold.render_scaffold(skill) -> str` (Task 2);
  `skill_eval.cases.loader.UNFILLED_SENTINEL`, `EVALS_DIRNAME` (`"evals"`),
  `EVAL_SUFFIX` (`".eval.yaml"`); `skill_eval.skills.loader.parse_skill_file(path) -> Skill`,
  `SkillParseError`, `SKILL_FILENAME` (`"SKILL.md"`).
- Produces: the `init` command. Task 5's CI step and the skill's own instructions call it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_init.py`:

```python
"""`skill-eval init` writes a starter suite, and refuses rather than clobbers."""

from __future__ import annotations

from typer.testing import CliRunner

from skill_eval.cases.loader import UNFILLED_SENTINEL
from skill_eval.cli import app

runner = CliRunner()

SKILL_MD = """---
name: order-support
description: Handle customer refund requests against the 30-day return policy
---

Always call lookup_order before deciding anything.
"""


def _skill_dir(tmp_path, text: str = SKILL_MD):
    path = tmp_path / "order-support"
    path.mkdir()
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def test_init_writes_the_suite_into_an_evals_directory(tmp_path):
    path = _skill_dir(tmp_path)
    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    target = path / "evals" / "order-support.eval.yaml"
    assert target.is_file()
    assert UNFILLED_SENTINEL in target.read_text(encoding="utf-8")


def test_init_refuses_to_overwrite_without_force(tmp_path):
    path = _skill_dir(tmp_path)
    runner.invoke(app, ["init", str(path)])
    target = path / "evals" / "order-support.eval.yaml"
    target.write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "--force" in result.output
    assert target.read_text(encoding="utf-8") == "cases: []\n"


def test_force_overwrites(tmp_path):
    path = _skill_dir(tmp_path)
    target = path / "evals" / "order-support.eval.yaml"
    target.parent.mkdir()
    target.write_text("cases: []\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path), "--force"])
    assert result.exit_code == 0, result.output
    assert UNFILLED_SENTINEL in target.read_text(encoding="utf-8")


def test_a_path_with_no_skill_md_is_a_user_error(tmp_path):
    empty = tmp_path / "not-a-skill"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty)])
    assert result.exit_code == 2
    assert "SKILL.md" in result.output


def test_a_skill_name_with_a_separator_cannot_escape_the_evals_directory(tmp_path):
    path = _skill_dir(
        tmp_path,
        "---\nname: ../../etc/passwd\ndescription: hostile\n---\n\nbody\n",
    )
    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    written = list((path / "evals").iterdir())
    assert len(written) == 1
    assert written[0].parent == path / "evals"


def test_an_unwritable_target_is_a_user_error(tmp_path):
    # `evals` already exists as a *file*, so creating the directory fails.
    path = _skill_dir(tmp_path)
    (path / "evals").write_text("not a directory\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 2
    assert "cannot write" in result.output


def test_an_unfilled_scaffold_fails_a_run_as_an_authoring_error(tmp_path):
    # The end-to-end contract: init, then run, exits 2 with the field named --
    # not a green gate, and not a failure reported against the skill.
    path = _skill_dir(tmp_path)
    assert runner.invoke(app, ["init", str(path)]).exit_code == 0

    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 2
    assert UNFILLED_SENTINEL in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_cli_init.py -v
```

Expected: FAIL — every test exits `2` with "No such command 'init'".

- [ ] **Step 3: Add the command**

In `src/skill_eval/cli.py`, extend the existing imports:

```python
import re
```

goes with the stdlib imports at the top (beside `import os`), and these join the
`skill_eval` imports:

```python
from skill_eval.cases.loader import (
    EVALS_DIRNAME,
    EVAL_SUFFIX,
    UNFILLED_SENTINEL,
    CaseParseError,
    load_cases_for_skill,
)
from skill_eval.scaffold import render_scaffold
from skill_eval.skills.loader import SKILL_FILENAME, SkillParseError, load_skills, parse_skill_file
```

(the two existing `from skill_eval.cases.loader import ...` and
`from skill_eval.skills.loader import ...` lines are replaced by these; keep the
alphabetical ordering ruff's `I` rule enforces).

Append the command after `list_skills`:

```python
def _eval_filename(name: str) -> str:
    """A safe file name for a skill's eval suite.

    The name comes from user-supplied frontmatter, so it is not automatically
    a safe path component: `name: ../../x` would otherwise write outside the
    directory init was pointed at.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "skill"
    return f"{safe}{EVAL_SUFFIX}"


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="A skill directory containing SKILL.md.")],
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing eval file.")
    ] = False,
) -> None:
    """Write a starter eval suite beside a skill."""
    skill_md = path / SKILL_FILENAME
    if not skill_md.is_file():
        typer.echo(f"no {SKILL_FILENAME} in {path}; point init at a skill directory")
        raise typer.Exit(code=2)
    try:
        skill = parse_skill_file(skill_md)
    except SkillParseError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    target = path / EVALS_DIRNAME / _eval_filename(skill.name)
    if target.exists() and not force:
        typer.echo(f"{target} already exists; pass --force to overwrite it")
        raise typer.Exit(code=2)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_scaffold(skill), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"cannot write {target}: {exc}")
        raise typer.Exit(code=2) from exc

    typer.echo(f"Wrote {target}")
    typer.echo(f"Fill in every {UNFILLED_SENTINEL}, then run: skill-eval run {path}")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_cli_init.py -v
```

Expected: PASS, all seven tests.

- [ ] **Step 5: Document the command**

In `docs/cli.md`, add a section for `init` matching the shape of the existing command
sections:

```markdown
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
```

In `docs/roadmap.md`, replace the M7 row (line 12) with:

```markdown
| M7 | DX: `skill-eval init` scaffolder, more examples | `init` shipped; examples planned |
```

In `ARCHITECTURE.md`, under `## Module map`, add a row/bullet in the file's existing
style for:

```markdown
- `scaffold.py` — renders the starter eval suite `skill-eval init` writes. Pure: a `Skill`
  in, the file text out, with the IO left to `cli.py`.
```

- [ ] **Step 6: Verify the suite, the docs tests, and the linters**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check .
```

Expected: PASS — including `tests/test_docs.py::test_every_cli_command_is_documented` and
`::test_every_cli_option_is_documented`, which now cover `init` and `--force`.

- [ ] **Step 7: Commit**

```bash
git add src/skill_eval/cli.py tests/test_cli_init.py docs/cli.md docs/roadmap.md ARCHITECTURE.md
git commit -m "feat: add skill-eval init to scaffold an eval suite"
```

---

### Task 4: The `writing-skill-evals` skill

**Files:**
- Create: `skills/writing-skill-evals/SKILL.md`
- Create: `skills/writing-skill-evals/references/eval-file-syntax.md`
- Create: `skills/writing-skill-evals/references/case-design.md`
- Create: `skills/writing-skill-evals/references/auditing.md`
- Create: `.claude/skills/writing-skill-evals` (symlink to `../../skills/writing-skill-evals`)
- Create: `docs/writing-evals.md`
- Modify: `mkdocs.yml` (nav)
- Modify: `.gitignore`
- Test: `tests/test_shipped_skill.py`

**Interfaces:**
- Consumes: `skill_eval.evaluators.assertion.ASSERTION_KINDS: tuple[str, ...]`,
  `skill_eval.models.EvalCase.model_fields`, `skill_eval.skills.loader.parse_skill_file`.
- Produces: the skill directory. Task 5 adds `evals/` inside it and extends
  `tests/test_shipped_skill.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shipped_skill.py`:

```python
"""The shipped skill must parse, and its syntax reference must track the code.

The reference restates material docs/eval-files.md also carries, so it is
pinned to the code rather than to the prose: a new assertion kind or case field
fails here until the skill mentions it.
"""

from __future__ import annotations

from pathlib import Path

from skill_eval.evaluators.assertion import ASSERTION_KINDS
from skill_eval.models import EvalCase
from skill_eval.skills.loader import parse_skill_file

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "writing-skill-evals"
SYNTAX = SKILL_DIR / "references" / "eval-file-syntax.md"


def test_the_skill_parses():
    skill = parse_skill_file(SKILL_DIR / "SKILL.md")
    assert skill.name == "writing-skill-evals"
    assert skill.description


def test_the_syntax_reference_lists_every_assertion_kind():
    text = SYNTAX.read_text(encoding="utf-8")
    for kind in ASSERTION_KINDS:
        assert f"`{kind}`" in text, f"assertion kind {kind!r} is missing from {SYNTAX.name}"


def test_the_syntax_reference_lists_every_case_field():
    text = SYNTAX.read_text(encoding="utf-8")
    for field in EvalCase.model_fields:
        assert f"`{field}`" in text, f"case field {field!r} is missing from {SYNTAX.name}"


def test_the_skill_is_linked_into_dot_claude():
    link = REPO_ROOT / ".claude" / "skills" / "writing-skill-evals"
    assert link.is_dir(), "the skill is not linked into .claude/skills/"
    assert (link / "SKILL.md").is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_shipped_skill.py -v
```

Expected: FAIL — `SkillParseError: cannot read .../skills/writing-skill-evals/SKILL.md`
and `FileNotFoundError` on the reference.

- [ ] **Step 3: Write SKILL.md**

Create `skills/writing-skill-evals/SKILL.md`:

```markdown
---
name: writing-skill-evals
description: Use when writing, running, or auditing skill-eval eval suites for an Agent Skill — deciding which cases a skill needs, choosing between assertions, judge rubrics and trajectory checks, and reading a failing case correctly
---

# Writing skill evals

An eval suite is worth exactly what its cases check. The two failure modes are a suite
that is green because it checks nothing, and a red case that gets the skill edited when
the eval was wrong. Everything here exists to prevent one of those.

## Workflow

1. **Orient.** Read the target `SKILL.md`. Look for existing evals in `evals/` or
   `*.eval.yaml` beside it, and run `skill-eval list <path>` to see what the tool already
   discovers.
2. **Scaffold.** Run `skill-eval init <skill-dir>`. Do not hand-roll the file structure —
   the generated file already carries the triggering pair and the placeholders that stop
   an unfinished suite from running.
3. **Mine the skill for claims.** Every "always", "never" and "must" in the instructions
   is a candidate case. The frontmatter `description` is a claim too, and it is precisely
   what the `mode: offered` cases test.
4. **Propose the case list and confirm it.** Show the user the cases you intend to write,
   one line each, before writing any YAML. Ask only for what `SKILL.md` cannot tell you:
   which tools exist and what they return, which policy edges are real, what a good answer
   sounds like. Do not ask for what the file already says.
5. **Write the cases.** Replace every `TODO(skill-eval)`. Read
   `references/eval-file-syntax.md` for the fields, and `references/case-design.md` for
   patterns to draw from.
6. **Run it.** `skill-eval run <path>` — the fake runner first, which proves the file
   parses and the checks are well formed; then a real runner if the user has a key
   configured.
7. **Triage every red case** by the rule below before editing anything.

## Choosing the check

| Use | When |
| --- | --- |
| `assertions` | The check is mechanical and stable: an id appears, a traceback does not. |
| `judge` | The claim is about quality, tone or reasoning. "Explains it plainly" is not a substring. |
| `trajectory` | The failure is invisible in the output — deciding without looking the order up, calling the tool that was forbidden. |
| `budget` | Guarding against a regression into a tool-call loop or a runaway answer. |

## Triage: the eval or the skill?

A red case means one of two different things, and naming which comes before any edit.

**The eval is wrong** when the output was actually fine: a regex tight enough to feel
rigorous but that rejects phrasing a model may legitimately vary, an assertion on wording
the skill never promised, a budget below what the task honestly costs.
`examples/greeting/greeting.eval.yaml` in the skill-eval repo documents a real instance —
a single-sentence regex relaxed after real model output failed it for no good reason.

**The skill is wrong** when the output genuinely was not what the skill claims: the
instruction is missing, or is present but too weak to survive a plausible prompt.

Fixing the eval is yours to do. **Changing the target `SKILL.md` is proposed and
confirmed, never silent** — the user is the author of their skill, and an eval that gets
its own subject rewritten to match it has stopped measuring anything.

## Rules that are not negotiable

- **Ship the negative control.** A suite of triggering positives scores a skill that fires
  on everything at 100%. `mode: offered` cases come in pairs.
- **Never leave a case with no assertions.** It passes without checking anything. If there
  is nothing to assert, the case is not ready.
- **Every rubric entry must be independently checkable and evidenced.** skill-eval records
  a check that passes without citing evidence as a failure, so a vague entry costs a case
  rather than buying coverage.
- **Never leave a `TODO(skill-eval)` behind.** The run will refuse it, which is the point,
  but a suite that cannot run is not a suite.

## Auditing an existing suite

Read `references/auditing.md` and work its checklist. Report findings; do not rewrite the
user's suite unasked.
```

- [ ] **Step 4: Write the syntax reference**

Create `skills/writing-skill-evals/references/eval-file-syntax.md`. It must mention every
`EvalCase` field and every assertion kind in backticks, or `tests/test_shipped_skill.py`
fails:

```markdown
# Eval file syntax

A file has one top-level `cases:` list. Unknown keys inside a case or an assertion are
rejected — without that, a typo like `assertion:` would yield a case that passes
vacuously.

## Case fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Case name, shown in reports |
| `task` | yes | The prompt handed to the runner |
| `assertions` | no | Output checks; a case with none passes vacuously |
| `tools` | no | Mock tools the agent may call |
| `trajectory` | no | Which tools must and must not have been called, and in what order |
| `budget` | no | Ceilings on tokens, cost, and latency |
| `judge` | no | A rubric for an LLM judge |
| `mode` | no | `loaded` (default) or `offered` |
| `tags` | no | Labels for `--tag` filtering |

## Assertion kinds

| `kind` | Passes when |
| --- | --- |
| `contains` | `value` appears in the output |
| `not_contains` | `value` does not appear in the output |
| `regex` | `value` matches anywhere in the output (`re.search`) |
| `equals` | the stripped output equals `value` exactly |

Every assertion must hold for the case to pass. An unknown kind or a malformed regex
aborts the run as an authoring error rather than being reported as a skill failure.

## Tools

Nothing executes. Calling a mock tool records the call and returns `returns` verbatim, so
the trajectory is genuinely the model's choice and a run has no side effects. Mock tools
accept any arguments — a hallucinated argument must not surface as an infra error.

```yaml
    tools:
      - name: lookup_order          # must be a valid identifier
        description: Look up an order by its id
        parameters:
          order_id: string          # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
```

## Trajectory

```yaml
    trajectory:
      called: [lookup_order]        # must have been called
      forbidden: [issue_refund]     # must not have been
      order: [lookup_order, issue_refund]   # relative order, not exhaustive
      max_calls: 3
      skill_triggered: true         # mode: offered only
```

Every name in `called`, `forbidden` and `order` must be a tool the case itself declares.

## Budget

```yaml
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
      max_latency_ms: 20000
```

An unpriced model makes a cost limit unverifiable, so that check is skipped rather than
counted as passed — a cost limit as the only budget check then fails the case, because
nothing was verified.

## Judge

```yaml
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
```

One verdict per rubric entry, each with its evidence; skill-eval derives pass and score
from those. A check that passes without evidence is recorded as a failure. An empty
rubric, or a blank entry, is an authoring error. Judging costs money and is opted into
with `judge = "pydantic-ai"` in `skill-eval.toml`; the default `judge = "fake"` reports a
judged case as **errored** rather than passing a rubric nobody checked.

## Triggering (`mode: offered`)

The skill is not force-loaded; it is registered as a tool named after the skill
(`order-support` becomes `order_support`) and described by its frontmatter description.
Calling it delivers the instructions. Check it with `skill_triggered`, not by naming the
tool in `called:`. Setting `skill_triggered` on a `loaded` case is an authoring error.

## Placeholders

`skill-eval init` writes `TODO(skill-eval)` into every field you must supply. A case still
containing one aborts the run with exit 2, naming the field.
```

- [ ] **Step 5: Write the case-design reference**

Create `skills/writing-skill-evals/references/case-design.md`:

```markdown
# Designing the cases

## Deriving cases from the skill

Read the instructions and list every claim. Claims look like: "always call X first",
"never do Y after 30 days", "answer in one short sentence", "ask before writing files".
Each claim gets at least one case; the interesting ones get two, one either side of the
line.

The frontmatter `description` is the claim that triggering tests: it is the only text an
agent sees when deciding whether to reach for the skill.

## A minimum suite

1. **The common case** — the prompt this skill exists for, with an assertion on something
   every good answer contains.
2. **The edge** — the prompt that reaches the policy line, with `trajectory` if getting
   there requires a tool call.
3. **The other side of the edge** — the near-identical prompt where the answer flips. A
   skill that refuses everything passes case 2 alone.
4. **The triggering pair** — `mode: offered`, positive and negative.

## Patterns by skill archetype

| Archetype | What to check |
| --- | --- |
| Policy skill (refunds, approvals) | `trajectory` proving it looked before deciding; `forbidden` on the destructive tool; both sides of the policy line |
| Tool-using skill | `order` for a required sequence; `max_calls` against loops; a case where the tool returns an error string |
| Formatting skill | `regex` — but only on structure the skill actually promised; a judge for "reads plainly" |
| Knowledge skill | `contains` on the fact; `not_contains` on the plausible wrong answer; a judge for reasoning |

## Writing rubric entries

Each entry is checked independently and must be evidenced by quoting the output. That
makes the test for a good entry mechanical: **could you point at the sentence that proves
it?**

- Good: "The reply names order 1234." "The reply states the return window has closed."
- Bad: "The reply is helpful." "The response is well structured." Nothing can be quoted
  as proof, and an unsupported pass is an LLM judge's characteristic failure mode.

Split compound entries. "Names the order and explains the policy" hides which half failed.

## Assertions that age badly

- A regex pinning phrasing the skill never promised. Check the structure the skill
  committed to, nothing more.
- `equals` on anything a model generates freely.
- Asserting on a number the mock tool returns — that tests the fixture, not the skill.
- A budget set at the current spend. Leave headroom, or every prompt improvement is a
  red case.
```

- [ ] **Step 6: Write the auditing reference**

Create `skills/writing-skill-evals/references/auditing.md`:

```markdown
# Auditing an existing suite

Work the checklist, then report findings with the file and case named. Do not rewrite the
user's suite unasked.

## Checklist

- [ ] **Vacuous cases.** Any `loaded` case with no `assertions`, no `trajectory`, no
      `judge`? It passes without checking anything.
- [ ] **Missing negative control.** Any `mode: offered` positive with no negative
      counterpart? The suite cannot distinguish a well-targeted skill from one that fires
      on everything.
- [ ] **Claims with no case.** List the skill's "always/never/must" statements and find
      the case for each. Name the ones with none.
- [ ] **One-sided edges.** A policy case that only proves the refusal, never the approval.
- [ ] **Over-tight assertions.** `equals` or a `regex` pinning phrasing the skill never
      promised; an assertion that would fail on a legitimately different good answer.
- [ ] **Fixture assertions.** An assertion whose value comes from a mock tool's `returns`
      rather than from the skill's behavior.
- [ ] **Unevidenceable rubric entries.** Anything you could not prove by quoting the
      output ("is helpful", "is well structured"), or compound entries hiding which half
      failed.
- [ ] **Budgets that never bind, or bind too tightly.** A ceiling far above any plausible
      run checks nothing; one at the current spend turns every prompt change red.
- [ ] **A cost limit as the only budget check** on a model with no pricing entry — the
      check is skipped, so the case fails for having verified nothing.
- [ ] **Leftover placeholders.** `TODO(skill-eval)` anywhere.
- [ ] **Tags.** Is there a `smoke` subset a fast CI job could run?

## Reporting

For each finding: the file and case, what is wrong, and the smallest change that fixes it.
Rank by what would let a broken skill through, not by what is easiest to fix.
```

- [ ] **Step 7: Link the skill into `.claude/skills/` and ignore worktrees**

```bash
mkdir -p .claude/skills
ln -s ../../skills/writing-skill-evals .claude/skills/writing-skill-evals
```

`.claude/` is otherwise untracked and holds local worktrees, so add to `.gitignore`:

```
# Local Claude Code state; the skills/ symlinks below it are tracked.
.claude/*
!.claude/skills/
```

- [ ] **Step 8: Document the skill for users**

Create `docs/writing-evals.md`:

```markdown
# The eval-writing skill

`skill-eval init` gives you the structure of a suite. It cannot tell you which cases
*this* skill needs, or what a red case means. That judgment ships as an Agent Skill.

## What it does

Given a skill to evaluate, it reads the `SKILL.md` for its claims, proposes a case list
and confirms it with you, scaffolds and fills in the suite, runs it, and triages the
failures — distinguishing an eval that is wrong from a skill that is wrong, and proposing
changes to your `SKILL.md` rather than making them silently. It also audits suites you
already have: missing negative controls, cases that assert nothing, rubric entries no
evidence could support.

## Installing it

The skill lives in [`skills/writing-skill-evals/`](https://github.com/EmadMokhtar/skill-evaluator/tree/main/skills/writing-skill-evals).
Copy or symlink it into the skills directory your agent reads:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator
ln -s "$PWD/skill-evaluator/skills/writing-skill-evals" ~/.claude/skills/writing-skill-evals
```

Then ask for it by name, or describe the task — "write evals for my order-support skill".

## Using it

It expects `skill-eval` on `PATH` (`uv tool install skill-eval`, or run it inside a
project that depends on it). Everything it writes is an ordinary eval file: nothing about
the suite depends on the skill afterwards.
```

In `mkdocs.yml`, add the page to the nav, under the existing `Reference:` block after
`Eval files`:

```yaml
      - Writing evals: writing-evals.md
```

- [ ] **Step 9: Run the tests**

```bash
uv run pytest tests/test_shipped_skill.py tests/test_docs.py -v
```

Expected: PASS — including `test_every_page_is_reachable_from_the_nav` and
`test_relative_links_resolve` for the new page.

- [ ] **Step 10: Verify discovery, the docs build, and the linters**

```bash
uv run skill-eval list ./skills
```

Expected: `writing-skill-evals` listed with `0 case(s)` — Task 5 adds them.

```bash
uv sync --group docs && uv run mkdocs build --strict
uv run pytest && uv run ruff check . && uv run ruff format --check .
```

Expected: a clean strict build and a green suite.

- [ ] **Step 11: Commit**

```bash
git add skills .claude/skills docs/writing-evals.md mkdocs.yml .gitignore tests/test_shipped_skill.py
git commit -m "feat: add the writing-skill-evals skill"
```

---

### Task 5: Evals for the shipped skill

**Files:**
- Create: `skills/writing-skill-evals/evals/writing-skill-evals.eval.yaml`
- Modify: `tests/test_shipped_skill.py`
- Modify: `.github/workflows/ci.yml` (the self-check step)

**Interfaces:**
- Consumes: `skill_eval.cases.loader.load_cases_for_skill(skill, evals_path=None) -> list[EvalCase]`;
  `skill_eval.skills.loader.load_skills(path) -> list[Skill]`; the skill directory from Task 4.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shipped_skill.py`:

```python
from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.skills.loader import load_skills

SKILLS_DIR = REPO_ROOT / "skills"


def test_the_shipped_skill_has_cases_that_parse():
    # This call also exercises the loader's cross-reference validation, so an
    # undeclared trajectory tool or a leftover placeholder surfaces here.
    (skill,) = load_skills(SKILLS_DIR)
    cases = load_cases_for_skill(skill)
    assert cases


def test_the_shipped_suite_ships_both_halves_of_the_triggering_pair():
    (skill,) = load_skills(SKILLS_DIR)
    triggered = [
        case.trajectory.skill_triggered
        for case in load_cases_for_skill(skill)
        if case.mode == "offered"
    ]
    assert sorted(triggered) == [False, True]
```

(Move the two new imports up beside the existing imports at the top of the file.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_shipped_skill.py -k shipped -v
```

Expected: FAIL — `assert []` / `assert [] == [False, True]`, since the skill has no eval
files yet.

- [ ] **Step 3: Write the suite**

Create `skills/writing-skill-evals/evals/writing-skill-evals.eval.yaml`:

```yaml
# Evals for the skill that writes evals. Four cases, three concerns:
#   - triggering: does it fire on an eval-writing request, and stay quiet
#     otherwise (the negative control this skill insists on for everyone else).
#   - authoring: does it read the skill before writing the file, and is what it
#     writes a suite rather than a shape -- judged, because "includes a negative
#     control" is a property of the YAML, not a substring of the reply.
#   - auditing: handed a deliberately weak suite, does it name what is missing.
cases:
  - name: reaches for the skill on an eval-writing request
    mode: offered
    task: Write evals for my order-support skill in ./skills/order-support
    tags: [triggering]
    trajectory:
      skill_triggered: true

  - name: leaves unrelated work alone
    mode: offered
    task: Rename the `parse` function in utils.py to `parse_input`
    tags: [triggering]
    trajectory:
      skill_triggered: false

  - name: reads the skill before writing the suite
    task: >-
      Write an eval suite for the skill at ./skills/order-support. Read the
      skill first, then write the file.
    tags: [authoring, judged]
    tools:
      - name: read_file
        description: Read a file from disk
        parameters:
          path: string
        returns: |
          ---
          name: order-support
          description: Handle customer refund requests against the 30-day return policy
          ---
          Always call lookup_order before saying anything about an order's state.
          Never issue a refund for an order delivered more than 30 days ago.
      - name: write_file
        description: Write text to a file on disk
        parameters:
          path: string
          content: string
        returns: '{"ok": true}'
      - name: run_command
        description: Run a shell command and return its output
        parameters:
          command: string
        returns: 'order-support  4 case(s)  skills/order-support'
    trajectory:
      order: [read_file, write_file]
      max_calls: 8
    judge:
      expected: >-
        An eval suite covering both sides of the 30-day policy, with a
        mode: offered pair including the negative control, and no placeholders
        left in it.
      rubric:
        - The suite it wrote includes a case where a refund is refused
        - The suite it wrote includes a case where a refund is allowed
        # Quoted: a plain YAML scalar cannot contain ": ".
        - "The suite it wrote includes a mode: offered case expecting skill_triggered true"
        - "The suite it wrote includes a mode: offered case expecting skill_triggered false"
        - No case in the suite it wrote still contains the text TODO
        - Every non-triggering case it wrote has at least one assertion, judge or trajectory check

  - name: names what a weak suite is missing
    task: Review the eval file at ./skills/greeting/evals/greeting.eval.yaml and tell me what is wrong with it
    tags: [auditing, judged]
    tools:
      - name: read_file
        description: Read a file from disk
        parameters:
          path: string
        returns: |
          cases:
            - name: greets someone
              task: greet Ada
            - name: fires on a greeting request
              mode: offered
              task: say hello to Ada
              trajectory:
                skill_triggered: true
    trajectory:
      called: [read_file]
    judge:
      expected: >-
        A review naming the assertion-free first case and the missing negative
        control for the triggering case.
      rubric:
        - The review says the first case checks nothing because it has no assertions
        - The review says the triggering case has no negative control
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_shipped_skill.py -v
```

Expected: PASS, all six tests.

- [ ] **Step 5: Extend the CI self-check**

In `.github/workflows/ci.yml`, replace the self-check step (currently
`run: uv run skill-eval list ./examples`) with:

```yaml
      - name: Self-check (dogfood the CLI on examples/ and skills/)
        # The examples and the shipped skill's suite assert real model
        # behaviour and include judged cases, so they cannot be run with
        # FakeRunner. `list` still exercises discovery, YAML parsing and
        # tool/trajectory/budget schema validation on real files at zero cost;
        # the full run path is covered every PR by the cassette tier.
        run: |
          uv run skill-eval list ./examples
          uv run skill-eval list ./skills
```

- [ ] **Step 6: Verify the self-check locally**

```bash
uv run skill-eval list ./skills
```

Expected: `writing-skill-evals	4 case(s)	skills/writing-skill-evals`

- [ ] **Step 7: Run everything**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mkdocs build --strict
```

Expected: green throughout.

- [ ] **Step 8: Commit**

```bash
git add skills/writing-skill-evals/evals tests/test_shipped_skill.py .github/workflows/ci.yml
git commit -m "test: evaluate the writing-skill-evals skill with its own suite"
```

---

## Notes for the reviewer

- Tasks 1–3 are the CLI half and stand on their own: after Task 3, `init` writes a suite
  that refuses to run until it is filled in.
- Tasks 4–5 are the skill half and depend on Task 1 only for the sentinel the SKILL.md
  refers to.
- The PR title must be conventional — it becomes the commit on `main` after squash-merge.
  Suggested: `feat: add an eval-writing skill and the init scaffolder`.
