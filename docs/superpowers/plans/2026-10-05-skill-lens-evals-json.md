# evals.json Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `skill-lens run ./skill` runs a skill that already has `evals/evals.json` (the Agent Skills guide / `skill-creator` format) with no YAML written by hand.

**Architecture:** A new module, `cases/evals_json.py`, turns one parsed `evals.json` document into the same raw case mappings the YAML loader produces. Those mappings go through the existing `EvalCase.model_validate` and `_validate_*` chain, so no invariant is bypassed. Discovery and `--evals` learn the `.json` file; no runner, evaluator, reporter or model changes.

**Tech Stack:** Python 3.11+, Pydantic, Typer, pytest, `json` from the standard library. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-05-skill-lens-evals-json-design.md`

## Global Constraints

- Python `>=3.11.4`. Ruff line length is 100; the `S` (security) rules are on for `src/`.
- The user-facing name is `skill-lens` everywhere. `tests/test_naming.py` fails if `skill_lens` appears in docs outside `docs/superpowers/`. (Import paths in code and tests are fine.)
- Authoring errors raise `CaseParseError`; the CLI turns them into **exit 2**. They never score as failures.
- Case name is exactly `eval-<id>`.
- Allowed top-level keys: `skill_name`, `evals`. Allowed per-case keys: `id`, `prompt`, `expected_output`, `files`, `assertions`, `expectations`. Everything else is refused, naming the key.
- Fallback rubric check, when a case has no statements: exactly `The output satisfies: <expected_output>` (the value stripped of surrounding whitespace).
- Load-time size limit for `files` is `DEFAULT_LIMITS.max_file_bytes` (1,000,000).
- No change to any runner, evaluator, reporter, `EvalCase`, or exit code.
- Tests are written first and run offline with `FakeRunner` and a scripted `FakeJudge`.
- Every commit is a Conventional Commit. Commit with `uv run git commit ...` — the pre-commit hook needs `pre-commit` on `PATH`, which `uv run` provides. End each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Work on branch `feat/evals-json` (already created; the spec is its first commit).

## Review Focus

Inputs the spec implies but does not name, most likely to bite first. Each has a test in the task that owns the code.

1. **A UTF-8 byte-order mark (BOM) at the start of the file.** Windows tools write one. A person expects the file to load. (Task 4.)
2. **A bare JSON list, or an empty file, instead of an object.** Expected: a clear authoring error that names the file and the expected shape, not a traceback. (Tasks 2 and 4.)
3. **A first-stage file with only `prompt` and `expected_output`.** The guide tells authors to start there. Expected: it runs through the whole pipeline (`list`, `run`) and is graded by one check. (Task 5.)
4. **A `files` entry that is a symlink loop, a FIFO, or a link out of the skill directory.** Expected: refused at load time, before any run, and never a hang. (Task 3.)
5. **The default `fake` judge.** Expected: the cases are `errored` and the gate fails — never a green run. (Task 5.)

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/skill_lens/cases/errors.py` (create) | `CaseParseError`, alone, so the converter and the loader can both import it without a cycle. |
| `src/skill_lens/cases/evals_json.py` (create) | `evals_json_to_raw_cases`: shape, keys, ids, statements, input files. Knows nothing about YAML. |
| `src/skill_lens/cases/loader.py` (modify) | Dispatch on the `.json` suffix; discovery; the explicit-path rules. YAML behaviour is unchanged. |
| `src/skill_lens/cli.py` (modify) | Help text of the two `--evals` options. |
| `tests/test_evals_json.py` (create) | The converter, unit level. |
| `tests/test_case_loader.py`, `tests/test_orchestrator.py`, `tests/test_cli.py`, `tests/test_cli_init.py` (modify) | Loader, pipeline, CLI behaviour. |
| `docs/eval-files.md`, `docs/cli.md`, `docs/runners.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `skills/writing-skill-evals/SKILL.md` (modify) | Documentation ships with the change. |

---

### Task 1: Move `CaseParseError` to `cases/errors.py`

**Files:**
- Create: `src/skill_lens/cases/errors.py`
- Modify: `src/skill_lens/cases/loader.py:30-31` (the class), `src/skill_lens/cases/loader.py:13` (imports)
- Test: `tests/test_case_loader.py`

**Interfaces:**
- Produces: `skill_lens.cases.errors.CaseParseError`. `skill_lens.cases.loader.CaseParseError` stays importable and is the same class (`cli.py` and the tests import it from the loader).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_case_loader.py`:

```python
def test_case_parse_error_is_one_class_from_both_import_paths():
    from skill_lens.cases import errors, loader

    assert errors.CaseParseError is loader.CaseParseError
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_case_loader.py::test_case_parse_error_is_one_class_from_both_import_paths -v`
Expected: FAIL with `ImportError` (cannot import name `errors`).

- [ ] **Step 3: Implement**

Create `src/skill_lens/cases/errors.py`:

```python
"""The error an eval file raises when it cannot be read or is wrong.

Alone in its own module so the loader and the `evals.json` converter can both
raise it: the loader imports the converter, so the converter cannot import the
loader.
"""

from __future__ import annotations


class CaseParseError(Exception):
    """Raised when an eval file is missing or cannot be parsed."""
```

In `src/skill_lens/cases/loader.py`, delete the class definition (lines 30-31, `class CaseParseError(Exception): ...` and its docstring) and add this import in alphabetical position among the `skill_lens` imports (before `from skill_lens.cases.checks import ...`):

```python
from skill_lens.cases.errors import CaseParseError
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_case_loader.py tests/test_cli.py -q`
Expected: all pass. Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cases/errors.py src/skill_lens/cases/loader.py tests/test_case_loader.py
uv run git commit -m "refactor: move CaseParseError to cases.errors" -m "The evals.json converter must raise this error, and the loader imports the converter. A separate module avoids an import cycle. The loader still exports the name." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The converter — shape, keys, ids, statements

**Files:**
- Create: `src/skill_lens/cases/evals_json.py`
- Create: `tests/test_evals_json.py`

**Interfaces:**
- Consumes: `CaseParseError` (Task 1); `Skill` from `skill_lens.models`.
- Produces: `EVALS_JSON_FILENAME = "evals.json"` and `evals_json_to_raw_cases(path: Path, data: object, skill: Skill | None) -> list[dict[str, Any]]`. Each mapping has `name`, `task`, `judge` (`expected`, `rubric`) and, from Task 3 on, an optional `workspace`. Task 3 fills in `_read_files`; in this task it is a stub that accepts only an absent or empty `files`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_json.py`:

```python
"""The evals.json converter: the shared format in, raw case mappings out."""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_lens.cases.errors import CaseParseError
from skill_lens.cases.evals_json import evals_json_to_raw_cases
from skill_lens.models import Skill

PATH = Path("evals/evals.json")


def _skill(tmp_path, name="csv-analyzer") -> Skill:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def _eval(**overrides):
    base = {"id": 1, "prompt": "do the thing", "assertions": ["it did the thing"]}
    base.update(overrides)
    return base


def _doc(*evals, **top):
    return {"skill_name": "csv-analyzer", "evals": list(evals), **top}


def _convert(data, skill=None):
    return evals_json_to_raw_cases(PATH, data, skill)


# --- the mapping ---------------------------------------------------------------


def test_an_eval_maps_to_a_case_with_a_judge_block():
    data = _doc(_eval(expected_output="A bar chart.", assertions=["axes labelled", "3 bars"]))
    assert _convert(data) == [
        {
            "name": "eval-1",
            "task": "do the thing",
            "judge": {"expected": "A bar chart.", "rubric": ["axes labelled", "3 bars"]},
        }
    ]


def test_expectations_is_accepted_as_the_same_list():
    entry = {"id": 1, "prompt": "p", "expectations": ["a", "b"]}
    [case] = _convert(_doc(entry))
    assert case["judge"]["rubric"] == ["a", "b"]


def test_both_spellings_in_one_eval_are_refused():
    entry = _eval(expectations=["b"])
    with pytest.raises(CaseParseError, match="both 'assertions' and 'expectations'"):
        _convert(_doc(entry))


def test_expected_output_alone_becomes_the_single_check():
    entry = {"id": 2, "prompt": "p", "expected_output": "  A cleaned CSV.  "}
    [case] = _convert(_doc(entry))
    assert case["judge"] == {
        "expected": "  A cleaned CSV.  ",
        "rubric": ["The output satisfies: A cleaned CSV."],
    }


def test_an_empty_statement_list_falls_back_to_expected_output():
    entry = _eval(assertions=[], expected_output="Done.")
    [case] = _convert(_doc(entry))
    assert case["judge"]["rubric"] == ["The output satisfies: Done."]


@pytest.mark.parametrize("expected", [None, "", "   "])
def test_an_eval_with_nothing_to_grade_is_refused(expected):
    entry = {"id": 1, "prompt": "p"}
    if expected is not None:
        entry["expected_output"] = expected
    with pytest.raises(CaseParseError, match="nothing to grade"):
        _convert(_doc(entry))


# --- strict keys ---------------------------------------------------------------


def test_an_unknown_top_level_key_is_refused_and_named():
    with pytest.raises(CaseParseError, match="the top level has unknown key.*'trigger'"):
        _convert(_doc(_eval(), trigger={"positive": []}))


def test_an_unknown_per_case_key_is_refused_and_named():
    with pytest.raises(CaseParseError, match=r"eval #1 has unknown key.*'kind'"):
        _convert(_doc(_eval(kind="dialogue")))


def test_errors_name_the_file():
    with pytest.raises(CaseParseError) as caught:
        _convert(_doc(_eval(kind="dialogue")))
    assert str(PATH) in str(caught.value)


# --- ids -----------------------------------------------------------------------


@pytest.mark.parametrize(("ident", "name"), [(1, "eval-1"), (0, "eval-0"), ("a-b", "eval-a-b")])
def test_an_id_becomes_the_case_name(ident, name):
    [case] = _convert(_doc(_eval(id=ident)))
    assert case["name"] == name


@pytest.mark.parametrize("bad", [True, False, 1.5, "", "  ", None, [], {}])
def test_an_id_must_be_an_integer_or_a_non_empty_string(bad):
    with pytest.raises(CaseParseError, match="'id' must be an integer or a non-empty string"):
        _convert(_doc(_eval(id=bad)))


def test_a_missing_id_is_refused():
    entry = {"prompt": "p", "assertions": ["a"]}
    with pytest.raises(CaseParseError, match="has no 'id'"):
        _convert(_doc(entry))


def test_a_repeated_id_is_refused_even_across_int_and_string():
    with pytest.raises(CaseParseError, match="repeats id"):
        _convert(_doc(_eval(id=1), _eval(id="1")))


# --- prompt, statements, expected_output ----------------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", 3, ["x"]])
def test_a_prompt_must_be_a_non_empty_string(bad):
    entry = _eval()
    entry["prompt"] = bad
    with pytest.raises(CaseParseError, match="'prompt' must be a non-empty string"):
        _convert(_doc(entry))


def test_a_missing_prompt_is_refused():
    with pytest.raises(CaseParseError, match="'prompt' must be a non-empty string"):
        _convert(_doc({"id": 1, "assertions": ["a"]}))


@pytest.mark.parametrize("bad", ["a string", [""], ["ok", "  "], [1], {"a": "b"}])
def test_statements_must_be_a_list_of_non_empty_strings(bad):
    with pytest.raises(CaseParseError, match="must be a list of non-empty strings"):
        _convert(_doc(_eval(assertions=bad)))


def test_expected_output_must_be_a_string():
    with pytest.raises(CaseParseError, match="'expected_output' must be a string"):
        _convert(_doc(_eval(expected_output=3)))


# --- the document's own shape ---------------------------------------------------


def test_a_bare_list_is_refused_with_the_expected_shape():
    with pytest.raises(CaseParseError, match="expected a JSON object with an 'evals' list"):
        _convert([_eval()])


@pytest.mark.parametrize("bad", [None, "x", {"id": 1}])
def test_evals_must_be_a_list(bad):
    with pytest.raises(CaseParseError, match="'evals' must be a list"):
        _convert({"skill_name": "csv-analyzer", "evals": bad})


def test_a_document_without_evals_is_refused():
    with pytest.raises(CaseParseError, match="'evals' must be a list"):
        _convert({"skill_name": "csv-analyzer"})


def test_an_empty_evals_list_is_refused():
    with pytest.raises(CaseParseError, match="'evals' is empty"):
        _convert(_doc())


def test_an_eval_must_be_an_object():
    with pytest.raises(CaseParseError, match=r"eval #2 must be a JSON object"):
        _convert(_doc(_eval(), "not an object"))


# --- skill_name -----------------------------------------------------------------


def test_skill_name_must_match_the_skill(tmp_path):
    skill = _skill(tmp_path, "csv-analyzer")
    assert len(_convert(_doc(_eval()), skill)) == 1
    with pytest.raises(CaseParseError, match="belongs to another skill"):
        _convert(_doc(_eval(), skill_name="pdf"), skill)


def test_skill_name_is_not_compared_without_a_skill():
    assert len(_convert(_doc(_eval(), skill_name="anything"))) == 1


def test_skill_name_is_optional(tmp_path):
    data = {"evals": [_eval()]}
    assert len(_convert(data, _skill(tmp_path))) == 1


def test_skill_name_must_be_a_string():
    with pytest.raises(CaseParseError, match="'skill_name' must be a string"):
        _convert(_doc(_eval(), skill_name=3))


# --- input files (filled in by the next task) ------------------------------------


def test_no_workspace_block_without_files():
    [case] = _convert(_doc(_eval()))
    assert "workspace" not in case
    [case] = _convert(_doc(_eval(files=[])))
    assert "workspace" not in case
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_evals_json.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: skill_lens.cases.evals_json`.

- [ ] **Step 3: Implement the converter**

Create `src/skill_lens/cases/evals_json.py`:

```python
"""Read the shared `evals/evals.json` format into raw case mappings.

Both the Agent Skills evaluation guide (agentskills.io) and Anthropic's
`skill-creator` keep test cases in `evals/evals.json`. This module turns one
parsed document into the same raw mappings the YAML loader produces, so
`EvalCase.model_validate` and the loader's `_validate_*` checks are the only
validators: a JSON case and a YAML case meet identical rules.

The reading is strict. A key this module does not know is an authoring error
that names the key, for the reason every authored model forbids extras: a
misspelled `assertion:` must not drop the checks and pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_lens.cases.errors import CaseParseError
from skill_lens.models import Skill

EVALS_JSON_FILENAME = "evals.json"

_TOP_LEVEL_KEYS = frozenset({"skill_name", "evals"})
_CASE_KEYS = frozenset({"id", "prompt", "expected_output", "files", "assertions", "expectations"})
# The guide says `assertions`; skill-creator says `expectations`. One list, two names.
_STATEMENT_KEYS = ("assertions", "expectations")


def evals_json_to_raw_cases(path: Path, data: object, skill: Skill | None) -> list[dict[str, Any]]:
    """The raw case mappings for one parsed `evals.json`.

    `skill` is optional because a file can be parsed on its own; it is needed
    only for the `skill_name` comparison and to resolve input `files`.
    """
    if not isinstance(data, dict):
        raise CaseParseError(f"{path}: expected a JSON object with an 'evals' list")
    _reject_unknown(path, "the top level", data, _TOP_LEVEL_KEYS)
    if "skill_name" in data:
        _check_skill_name(path, data["skill_name"], skill)
    entries = data.get("evals")
    if not isinstance(entries, list):
        raise CaseParseError(f"{path}: 'evals' must be a list")
    if not entries:
        raise CaseParseError(f"{path}: 'evals' is empty; there is nothing to run")
    seen: set[str] = set()
    return [
        _convert(path, position, entry, skill, seen)
        for position, entry in enumerate(entries, start=1)
    ]


def _reject_unknown(path: Path, where: str, mapping: dict, allowed: frozenset[str]) -> None:
    unknown = sorted(key for key in mapping if key not in allowed)
    if unknown:
        names = ", ".join(repr(key) for key in unknown)
        known = ", ".join(sorted(allowed))
        raise CaseParseError(
            f"{path}: {where} has unknown key(s) {names}; the keys skill-lens reads are {known}"
        )


def _check_skill_name(path: Path, declared: object, skill: Skill | None) -> None:
    if not isinstance(declared, str):
        raise CaseParseError(f"{path}: 'skill_name' must be a string, got {declared!r}")
    if skill is not None and declared != skill.name:
        raise CaseParseError(
            f"{path}: 'skill_name' is {declared!r} but this skill is {skill.name!r}; "
            "the file looks like it belongs to another skill"
        )


def _convert(
    path: Path, position: int, entry: object, skill: Skill | None, seen: set[str]
) -> dict[str, Any]:
    where = f"eval #{position}"
    if not isinstance(entry, dict):
        raise CaseParseError(f"{path}: {where} must be a JSON object")
    _reject_unknown(path, where, entry, _CASE_KEYS)
    name = _case_name(path, where, entry, seen)
    where = f"{where} ({name})"
    prompt = entry.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CaseParseError(f"{path}: {where} 'prompt' must be a non-empty string")
    expected = entry.get("expected_output", "")
    if not isinstance(expected, str):
        raise CaseParseError(f"{path}: {where} 'expected_output' must be a string")
    rubric = _statements(path, where, entry)
    if not rubric:
        if not expected.strip():
            raise CaseParseError(
                f"{path}: {where} has nothing to grade; give it 'assertions' (or "
                "'expectations'), or an 'expected_output' the judge can check"
            )
        rubric = [f"The output satisfies: {expected.strip()}"]
    case: dict[str, Any] = {
        "name": name,
        "task": prompt,
        "judge": {"expected": expected, "rubric": rubric},
    }
    files = _read_files(path, where, entry.get("files", []), skill)
    if files:
        case["workspace"] = {"files": files}
    return case


def _case_name(path: Path, where: str, entry: dict, seen: set[str]) -> str:
    if "id" not in entry:
        raise CaseParseError(f"{path}: {where} has no 'id'")
    ident = entry["id"]
    # `bool` is an `int` in Python; without this, `true` would become id 1.
    valid = isinstance(ident, int | str) and not isinstance(ident, bool)
    if not valid or (isinstance(ident, str) and not ident.strip()):
        raise CaseParseError(
            f"{path}: {where} 'id' must be an integer or a non-empty string, got {ident!r}"
        )
    name = f"eval-{ident}"
    if name in seen:
        raise CaseParseError(f"{path}: {where} repeats id {ident!r}; ids must be unique")
    seen.add(name)
    return name


def _statements(path: Path, where: str, entry: dict) -> list[str]:
    present = [key for key in _STATEMENT_KEYS if key in entry]
    if len(present) == 2:
        raise CaseParseError(
            f"{path}: {where} has both 'assertions' and 'expectations'; they are the same "
            "list under two names, so keep one"
        )
    if not present:
        return []
    key = present[0]
    value = entry[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CaseParseError(f"{path}: {where} '{key}' must be a list of non-empty strings")
    return list(value)


def _read_files(path: Path, where: str, raw: object, skill: Skill | None) -> dict[str, str]:
    """Input files, keyed by the path as written. Filled in by the next task."""
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise CaseParseError(f"{path}: {where} 'files' must be a list of paths")
    if raw:
        raise CaseParseError(f"{path}: {where} input files are not supported yet")
    return {}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_evals_json.py -q`
Expected: all pass.
Run: `uv run ruff check --fix src tests && uv run ruff format src tests`
Expected: clean (the format command may rewrite whitespace; keep its output).

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cases/evals_json.py tests/test_evals_json.py
uv run git commit -m "feat: convert evals.json evals into raw eval cases" -m "The converter reads the shape both the Agent Skills guide and skill-creator use. It is strict about keys, so a misspelled field cannot drop the checks. Input files come in the next commit." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The converter — input files

**Files:**
- Modify: `src/skill_lens/cases/evals_json.py` (`_read_files`, imports)
- Modify: `tests/test_evals_json.py`

**Interfaces:**
- Consumes: `check_relative_path`, `resolve_under`, `stat_regular`, `PathRefused`, `DEFAULT_LIMITS` from `skill_lens.workspace`.
- Produces: `_read_files` returns `{path_as_written: utf8_text}`; every refusal is a `CaseParseError` naming the file, the eval and the path.

- [ ] **Step 1: Write the failing tests**

Replace the last section of `tests/test_evals_json.py` (the `# --- input files (filled in by the next task)` header and its one test) with the block below, and add `import os` and `from promptly import promptly` and `from skill_lens.workspace import DEFAULT_LIMITS` to the imports (keep imports sorted: `os` with the standard library, `promptly` before the `skill_lens` imports, as `tests/test_workspace.py` does).

```python
# --- input files ----------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="FIFOs and symlinks are POSIX features")


def _with_file(tmp_path, relative, content="a,b\n1,2\n"):
    skill = _skill(tmp_path)
    target = skill.path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    return skill


def test_no_workspace_block_without_files():
    [case] = _convert(_doc(_eval()))
    assert "workspace" not in case
    [case] = _convert(_doc(_eval(files=[])))
    assert "workspace" not in case


def test_a_file_is_read_as_text_and_keyed_by_the_path_as_written(tmp_path):
    skill = _with_file(tmp_path, "evals/files/sales.csv")
    [case] = _convert(_doc(_eval(files=["evals/files/sales.csv"])), skill)
    assert case["workspace"] == {"files": {"evals/files/sales.csv": "a,b\n1,2\n"}}


def test_several_files_are_all_read(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.txt", "A")
    (skill.path / "evals/files/b.txt").write_text("B", encoding="utf-8")
    [case] = _convert(_doc(_eval(files=["evals/files/a.txt", "evals/files/b.txt"])), skill)
    assert case["workspace"]["files"] == {"evals/files/a.txt": "A", "evals/files/b.txt": "B"}


def test_files_must_be_a_list_of_paths():
    for bad in ("evals/a.csv", [1], [None]):
        with pytest.raises(CaseParseError, match="'files' must be a list of paths"):
            _convert(_doc(_eval(files=bad)))


def test_files_without_a_skill_are_refused():
    with pytest.raises(CaseParseError, match="no skill directory was given"):
        _convert(_doc(_eval(files=["evals/files/a.csv"])))


def test_a_missing_file_is_refused(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(CaseParseError, match="does not exist"):
        _convert(_doc(_eval(files=["evals/files/nope.csv"])), skill)


def test_a_directory_entry_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.csv")
    with pytest.raises(CaseParseError, match="is a directory"):
        _convert(_doc(_eval(files=["evals/files"])), skill)


def test_a_binary_file_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/chart.png", b"\x89PNG\xff\xfe\x00")
    with pytest.raises(CaseParseError, match="not UTF-8 text"):
        _convert(_doc(_eval(files=["evals/files/chart.png"])), skill)


def test_a_file_over_the_size_limit_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/big.txt", "x" * (DEFAULT_LIMITS.max_file_bytes + 1))
    with pytest.raises(CaseParseError, match="too large"):
        _convert(_doc(_eval(files=["evals/files/big.txt"])), skill)


def test_a_file_exactly_at_the_size_limit_is_read(tmp_path):
    skill = _with_file(tmp_path, "evals/files/ok.txt", "x" * DEFAULT_LIMITS.max_file_bytes)
    [case] = _convert(_doc(_eval(files=["evals/files/ok.txt"])), skill)
    assert len(case["workspace"]["files"]["evals/files/ok.txt"]) == DEFAULT_LIMITS.max_file_bytes


def test_a_parent_directory_path_is_refused(tmp_path):
    skill = _skill(tmp_path)
    (tmp_path / "outside.csv").write_text("x", encoding="utf-8")
    with pytest.raises(CaseParseError, match=r"\.\."):
        _convert(_doc(_eval(files=["../outside.csv"])), skill)


def test_an_absolute_path_is_refused(tmp_path):
    skill = _skill(tmp_path)
    with pytest.raises(CaseParseError, match="not a relative path"):
        _convert(_doc(_eval(files=[str(tmp_path / "outside.csv")])), skill)


def test_the_same_file_listed_twice_is_refused(tmp_path):
    skill = _with_file(tmp_path, "evals/files/a.csv")
    with pytest.raises(CaseParseError, match="twice"):
        _convert(_doc(_eval(files=["evals/files/a.csv", "evals/files/a.csv"])), skill)


@posix_only
def test_a_symlink_out_of_the_skill_directory_is_refused(tmp_path):
    skill = _skill(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (skill.path / "evals").mkdir()
    os.symlink(tmp_path / "secret.txt", skill.path / "evals" / "link.txt")
    with pytest.raises(CaseParseError, match="outside the skill directory"):
        _convert(_doc(_eval(files=["evals/link.txt"])), skill)


@posix_only
def test_a_symlink_loop_is_a_refusal_not_a_crash(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "evals").mkdir()
    os.symlink("loop", skill.path / "evals" / "loop")
    with pytest.raises(CaseParseError, match="evals/loop"):
        _convert(_doc(_eval(files=["evals/loop"])), skill)


@posix_only
def test_a_fifo_is_refused_before_it_is_opened(tmp_path):
    skill = _skill(tmp_path)
    (skill.path / "evals").mkdir()
    os.mkfifo(skill.path / "evals" / "pipe")
    # open() on a FIFO blocks until a writer connects. The refusal has to come
    # from stat(), so a hang here means the file was opened.
    with pytest.raises(CaseParseError, match="not a regular file"):
        promptly(lambda: _convert(_doc(_eval(files=["evals/pipe"])), skill))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_evals_json.py -q`
Expected: the new file tests FAIL (the stub says "input files are not supported yet"); the earlier tests still pass.

- [ ] **Step 3: Implement `_read_files`**

In `src/skill_lens/cases/evals_json.py`, add `import stat` to the standard-library imports, and extend the workspace import:

```python
from skill_lens.workspace import (
    DEFAULT_LIMITS,
    PathRefused,
    check_relative_path,
    resolve_under,
    stat_regular,
)
```

Replace `_read_files` with:

```python
def _read_files(path: Path, where: str, raw: object, skill: Skill | None) -> dict[str, str]:
    """Input files, read now as UTF-8 text and keyed by the path as written.

    Read at load time so a missing or binary file aborts the run before any
    case spends money. The paths are relative to the skill directory, which is
    what both published schemas say.
    """
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise CaseParseError(f"{path}: {where} 'files' must be a list of paths")
    if not raw:
        return {}
    if skill is None:
        raise CaseParseError(
            f"{path}: {where} lists input files, but no skill directory was given to "
            "resolve them against"
        )
    files: dict[str, str] = {}
    for candidate in raw:
        if candidate in files:
            raise CaseParseError(f"{path}: {where} lists the file {candidate!r} twice")
        files[candidate] = _read_one(path, where, skill.path, candidate)
    return files


def _read_one(path: Path, where: str, root: Path, candidate: str) -> str:
    """One input file's text, through the workspace's own containment helpers."""
    try:
        check_relative_path(candidate)
        target = resolve_under(root, candidate)
        if not target.is_relative_to(root.resolve()):
            raise PathRefused(f"refused: {candidate!r} resolves outside the skill directory")
        found = stat_regular(target, candidate)
    except PathRefused as exc:
        raise CaseParseError(f"{path}: {where} file {candidate!r}: {exc}") from exc
    if found is None:
        raise CaseParseError(f"{path}: {where} file {candidate!r} does not exist under {root}")
    if stat.S_ISDIR(found.st_mode):
        raise CaseParseError(
            f"{path}: {where} file {candidate!r} is a directory; list its files one by one"
        )
    limit = DEFAULT_LIMITS.max_file_bytes
    if found.st_size > limit:
        raise CaseParseError(
            f"{path}: {where} file {candidate!r} is too large ({found.st_size:,} bytes; "
            f"the limit is {limit:,})"
        )
    try:
        return target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaseParseError(
            f"{path}: {where} file {candidate!r} is not UTF-8 text; workspace files are text"
        ) from exc
    except OSError as exc:
        raise CaseParseError(f"{path}: {where} file {candidate!r} cannot be read: {exc}") from exc
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_evals_json.py -q`
Expected: all pass (the symlink-loop and FIFO tests are skipped on Windows only).
Run: `uv run ruff check --fix src tests && uv run ruff format src tests`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cases/evals_json.py tests/test_evals_json.py
uv run git commit -m "feat: read input files named by evals.json" -m "Files are read at load time as UTF-8 text through the workspace's own containment helpers, so a missing, binary, oversized or escaping entry aborts the run before any case executes." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Loader — parse, discover, and accept `--evals`

**Files:**
- Modify: `src/skill_lens/cases/loader.py` (`parse_cases_file`, `discover_eval_paths`, `load_cases_for_skill`, module docstring, imports)
- Modify: `src/skill_lens/cli.py` (the two `--evals` help strings)
- Test: `tests/test_case_loader.py`

**Interfaces:**
- Consumes: `evals_json_to_raw_cases`, `EVALS_JSON_FILENAME` (Tasks 2-3).
- Produces: `parse_cases_file(path, skill)` reads a `.json` file; `discover_eval_paths(skill)` includes `evals/evals.json`; `load_cases_for_skill(skill, evals_path=...)` accepts a `.json` file, and in a directory reads only a file named `evals.json` among the JSON files. The YAML path is behaviourally unchanged.

- [ ] **Step 1: Write the failing tests**

Add `import json` to the imports of `tests/test_case_loader.py`, then append:

```python
EVALS_JSON = {
    "skill_name": "pdf",
    "evals": [
        {"id": 1, "prompt": "Extract the text from report.pdf", "assertions": ["names pdfplumber"]},
        {"id": 2, "prompt": "Extract from nope.pdf", "expected_output": "A polite error."},
    ],
}


def _json_skill(tmp_path, document=EVALS_JSON):
    skill = _skill(tmp_path)
    evals = skill.path / "evals"
    evals.mkdir()
    (evals / "evals.json").write_text(json.dumps(document), encoding="utf-8")
    return skill


def test_parses_cases_from_an_evals_json_file(tmp_path):
    skill = _json_skill(tmp_path)
    cases = parse_cases_file(skill.path / "evals" / "evals.json", skill)
    assert [c.name for c in cases] == ["eval-1", "eval-2"]
    assert cases[0].task == "Extract the text from report.pdf"
    assert cases[0].judge.rubric == ["names pdfplumber"]
    assert cases[1].judge.expected == "A polite error."
    assert cases[1].judge.rubric == ["The output satisfies: A polite error."]
    assert cases[0].workspace is None


def test_a_json_case_with_files_gets_a_workspace(tmp_path):
    document = {
        "evals": [
            {"id": 1, "prompt": "p", "assertions": ["a"], "files": ["evals/files/in.csv"]},
        ]
    }
    skill = _json_skill(tmp_path, document)
    (skill.path / "evals" / "files").mkdir()
    (skill.path / "evals" / "files" / "in.csv").write_text("x,y\n", encoding="utf-8")
    [case] = load_cases_for_skill(skill)
    assert case.workspace is not None
    assert case.workspace.files == {"evals/files/in.csv": "x,y\n"}


def test_discovers_evals_json_in_the_evals_directory(tmp_path):
    assert len(load_cases_for_skill(_json_skill(tmp_path))) == 2


def test_evals_json_loads_beside_yaml_files(tmp_path):
    skill = _json_skill(tmp_path)
    (skill.path / "evals" / "basic.yaml").write_text(CASES_YAML)
    names = [c.name for c in load_cases_for_skill(skill)]
    assert names == ["extracts text", "handles missing file", "eval-1", "eval-2"]


def test_other_json_files_in_the_evals_directory_are_not_eval_files(tmp_path):
    skill = _json_skill(tmp_path)
    (skill.path / "evals" / "schema.json").write_text("{not json", encoding="utf-8")
    assert len(load_cases_for_skill(skill)) == 2


def test_an_explicit_json_file_is_read_whatever_it_is_called(tmp_path):
    skill = _skill(tmp_path)
    other = tmp_path / "cases.json"
    other.write_text(json.dumps(EVALS_JSON), encoding="utf-8")
    assert len(load_cases_for_skill(skill, evals_path=other)) == 2


def test_an_explicit_directory_reads_only_a_file_named_evals_json(tmp_path):
    skill = _skill(tmp_path)
    directory = tmp_path / "somewhere"
    directory.mkdir()
    (directory / "evals.json").write_text(json.dumps(EVALS_JSON), encoding="utf-8")
    (directory / "other.json").write_text("{not json", encoding="utf-8")
    assert len(load_cases_for_skill(skill, evals_path=directory)) == 2


def test_invalid_json_names_the_file(tmp_path):
    skill = _skill(tmp_path)
    path = tmp_path / "evals.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CaseParseError, match=r"invalid JSON in .*evals\.json"):
        parse_cases_file(path, skill)


def test_an_empty_file_is_invalid_json_not_a_crash(tmp_path):
    skill = _skill(tmp_path)
    path = tmp_path / "evals.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(CaseParseError, match="invalid JSON"):
        parse_cases_file(path, skill)


def test_a_byte_order_mark_is_skipped(tmp_path):
    # Windows editors write one, and json.loads refuses a document that starts with it.
    skill = _skill(tmp_path)
    path = tmp_path / "evals.json"
    path.write_text("\ufeff" + json.dumps(EVALS_JSON), encoding="utf-8")
    assert len(parse_cases_file(path, skill)) == 2


def test_a_bare_json_list_is_refused_with_the_expected_shape(tmp_path):
    skill = _skill(tmp_path)
    path = tmp_path / "evals.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CaseParseError, match="expected a JSON object with an 'evals' list"):
        parse_cases_file(path, skill)


def test_a_yaml_file_still_needs_a_cases_list(tmp_path):
    path = tmp_path / "x.eval.yaml"
    path.write_text("evals: []\n", encoding="utf-8")
    with pytest.raises(CaseParseError, match="expected a top-level 'cases' list"):
        parse_cases_file(path)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_case_loader.py -q`
Expected: the new JSON tests FAIL (a `.json` file is read as YAML and rejected for lacking `cases`); `test_a_yaml_file_still_needs_a_cases_list` and every older test pass.

- [ ] **Step 3: Implement**

In `src/skill_lens/cases/loader.py`:

1. Change the module docstring to `"""Discover and parse eval case files: YAML, and the shared evals.json."""`.
2. Add `import json` beside `import copy`, and this import beside the other `skill_lens.cases` imports:

```python
from skill_lens.cases.evals_json import EVALS_JSON_FILENAME, evals_json_to_raw_cases
```

3. Add this helper above `parse_cases_file`, and split the YAML reading out of it so the YAML path is unchanged but shares the loop:

```python
def _is_eval_file(path: Path) -> bool:
    """A YAML file, or the one JSON file the shared format uses.

    Other JSON in an `evals/` directory (fixtures, schemas) is not an eval
    file, so it is never parsed as one.
    """
    return path.suffix in {".yaml", ".yml"} or path.name == EVALS_JSON_FILENAME


def _raw_cases_from_yaml(path: Path, text: str) -> tuple[list[object], ToolLibrary]:
    """The `cases:` list of a YAML file, and the tool library it imports."""
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise CaseParseError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict) or "cases" not in data:
        raise CaseParseError(f"{path}: expected a top-level 'cases' list")
    raw_cases = data["cases"]
    if not isinstance(raw_cases, list):
        raise CaseParseError(f"{path}: 'cases' must be a list")
    # The scaffold scan stays the first check after parsing, ahead of any
    # library import: a half-filled file is told what to fill in, not that a
    # library it will import is missing.
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
    return raw_cases, _load_tool_libraries(path, data)


def _raw_cases_from_json(path: Path, text: str, skill: Skill | None) -> list[object]:
    """The raw case mappings of an `evals.json` file."""
    # A byte-order mark is not part of the document, and json.loads refuses it.
    try:
        data = json.loads(text.removeprefix("\ufeff"))
    except json.JSONDecodeError as exc:
        raise CaseParseError(f"invalid JSON in {path}: {exc}") from exc
    return list(evals_json_to_raw_cases(path, data, skill))
```

4. Replace the body of `parse_cases_file` so it reads:

```python
def parse_cases_file(path: Path, skill: Skill | None = None) -> list[EvalCase]:
    """Parse one YAML or evals.json file into EvalCase models.

    `skill` is optional because a case file can be parsed on its own; it is
    only needed for the checks that depend on what the skill would be offered
    as (see `_validate_cross_references`), and, for an `evals.json`, to resolve
    its input files and check its `skill_name`.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CaseParseError(f"cannot read {path}: {exc}") from exc
    if path.suffix == ".json":
        raw_cases = _raw_cases_from_json(path, text, skill)
        library = EMPTY_LIBRARY
    else:
        raw_cases, library = _raw_cases_from_yaml(path, text)
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases):
        raw = _resolve_tool_refs(path, index, raw, library)
        try:
            case = EvalCase.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(f"{path}: case #{index + 1} invalid ({fields}): {exc}") from exc
        _validate_cross_references(path, case, skill)
        _validate_workspace(path, case)
        _validate_assertions(path, case)
        _validate_tools(path, case)
        cases.append(case)
    return cases
```

5. In `discover_eval_paths` and `load_cases_for_skill`, replace the two `p.suffix in {".yaml", ".yml"}` filters with `_is_eval_file(p)`, and update the docstring of `discover_eval_paths` to `"""Find eval files beside a skill: an evals/ dir (YAML and evals.json), then *.eval.yaml."""`.

In `src/skill_lens/cli.py`, change both `help="Explicit eval file or directory."` strings (in `run` near line 174 and `list` near line 417) to `help="Explicit eval file (YAML or evals.json) or directory."`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_case_loader.py tests/test_cli.py tests/test_cli_init.py tests/test_orchestrator.py tests/test_framework_isolation.py -q`
Expected: all pass.
Run: `uv run ruff check --fix src tests && uv run ruff format src tests`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cases/loader.py src/skill_lens/cli.py tests/test_case_loader.py
uv run git commit -m "feat: read evals.json eval files" -m "Discovery finds evals/evals.json beside any YAML files, and --evals accepts a .json file. In a directory only a file named evals.json is read, so fixtures and schemas are not parsed as evals. Converted cases go through the same validation as YAML cases." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Pipeline and CLI behaviour (and a spec correction)

**Files:**
- Test: `tests/test_orchestrator.py`, `tests/test_cli.py`, `tests/test_cli_init.py`
- Modify: `docs/superpowers/specs/2026-10-05-skill-lens-evals-json-design.md`

**Interfaces:**
- Consumes: the loader changes from Task 4. No source change is expected in this task; if a test below fails for a reason other than a typo in the test, stop and report it — it means an earlier task is wrong.

- [ ] **Step 1: Write the tests**

`tests/test_orchestrator.py` — add `import json` with the other standard-library imports, then append:

```python
def _evals_json_skill(tmp_path, evals, *, name="s"):
    skill_dir = tmp_path / name
    (skill_dir / "evals").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n", encoding="utf-8")
    document = {"skill_name": name, "evals": evals}
    (skill_dir / "evals" / "evals.json").write_text(json.dumps(document), encoding="utf-8")
    return skill_dir


def test_an_evals_json_case_is_graded_by_the_judge(tmp_path):
    skill_dir = _evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "t", "assertions": ["is polite"]}]
    )
    judge = FakeJudge(
        default=JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="polite tone")])
    )
    report = run_evals(load_skills(skill_dir), [FakeRunner()], judge=judge)
    assert [outcome.status for outcome in report.outcomes] == ["passed"]


def test_an_evals_json_case_errors_under_the_default_judge(tmp_path):
    skill_dir = _evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "t", "assertions": ["is polite"]}]
    )
    report = run_evals(load_skills(skill_dir), [FakeRunner()])
    assert [outcome.status for outcome in report.outcomes] == ["errored"]


def test_an_evals_json_input_file_reaches_the_workspace(tmp_path):
    class ReadsTheInput(FakeRunner):
        def run(self, skill, case, workspace=None, scripts=None):
            return RunResult(output=workspace.read("evals/files/in.csv"))

    skill_dir = _evals_json_skill(
        tmp_path,
        [{"id": 1, "prompt": "t", "assertions": ["a"], "files": ["evals/files/in.csv"]}],
    )
    (skill_dir / "evals" / "files").mkdir()
    (skill_dir / "evals" / "files" / "in.csv").write_text("x,y\n", encoding="utf-8")
    judge = FakeJudge(
        default=JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="ok")])
    )
    report = run_evals(load_skills(skill_dir), [ReadsTheInput()], judge=judge)
    assert report.outcomes[0].status == "passed"
    assert report.outcomes[0].result.output == "x,y\n"


def test_an_unknown_evals_json_key_aborts_the_run(tmp_path):
    skill_dir = _evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "t", "assertions": ["a"], "kind": "dialogue"}]
    )
    with pytest.raises(CaseParseError, match="'kind'"):
        run_evals(load_skills(skill_dir), [FakeRunner()])
```

`tests/test_cli.py` — add `import json` if missing, then append (the module already has `SKILL_MD` for a skill named `pdf`, `runner`, `app` and `plain`):

```python
def _make_evals_json_skill(tmp_path, evals):
    skill_dir = tmp_path / "pdf"
    (skill_dir / "evals").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    document = {"skill_name": "pdf", "evals": evals}
    (skill_dir / "evals" / "evals.json").write_text(json.dumps(document), encoding="utf-8")
    return skill_dir


def test_list_counts_the_cases_in_an_evals_json(tmp_path):
    skill_dir = _make_evals_json_skill(
        tmp_path,
        [
            {"id": 1, "prompt": "a", "assertions": ["x"]},
            {"id": 2, "prompt": "b", "assertions": ["y"]},
        ],
    )
    result = runner.invoke(app, ["list", str(skill_dir)])
    assert result.exit_code == 0, result.output
    assert "2 case(s)" in result.stdout


def test_a_first_stage_evals_json_lists_and_runs(tmp_path):
    # The guide starts authors with only a prompt and an expected output.
    skill_dir = _make_evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "extract", "expected_output": "The text."}]
    )
    listed = runner.invoke(app, ["list", str(skill_dir)])
    assert listed.exit_code == 0, listed.output
    assert "1 case(s)" in listed.stdout
    ran = runner.invoke(app, ["run", str(skill_dir)])
    # No judge is configured, so the one check cannot be graded: errored, never green.
    assert ran.exit_code == 1, ran.output
    assert "1 errored" in ran.output


def test_run_over_an_evals_json_errors_under_the_default_judge(tmp_path):
    skill_dir = _make_evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "extract", "assertions": ["names the library"]}]
    )
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 1, result.output
    assert "1 errored" in result.output


def test_an_unknown_evals_json_key_is_a_user_error(tmp_path):
    skill_dir = _make_evals_json_skill(
        tmp_path, [{"id": 1, "prompt": "a", "assertions": ["x"], "kind": "dialogue"}]
    )
    result = runner.invoke(app, ["run", str(skill_dir)])
    assert result.exit_code == 2
    assert "'kind'" in plain(result.output)


def test_evals_accepts_an_explicit_json_file(tmp_path):
    skill_dir = _make_evals_json_skill(tmp_path, [{"id": 1, "prompt": "a", "assertions": ["x"]}])
    other = tmp_path / "more.json"
    other.write_text(
        json.dumps({"evals": [{"id": 7, "prompt": "b", "assertions": ["y"]}]}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["list", str(skill_dir), "--evals", str(other)])
    assert result.exit_code == 0, result.output
    assert "1 case(s)" in result.stdout
```

`tests/test_cli_init.py` — append:

```python
def test_batch_init_skips_a_skill_that_has_an_evals_json(tmp_path):
    root = tmp_path / "skills"
    skill = root / "csv"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: csv\ndescription: csv things\n---\n\nbody\n", encoding="utf-8"
    )
    original = '{"evals": []}'
    (skill / "evals" / "evals.json").write_text(original, encoding="utf-8")

    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output
    assert "Skipped csv: already has 1 eval file(s)" in result.output
    assert [p.name for p in (skill / "evals").iterdir()] == ["evals.json"]
    assert (skill / "evals" / "evals.json").read_text(encoding="utf-8") == original


def test_single_init_writes_beside_an_evals_json_and_leaves_it_alone(tmp_path):
    path = _skill_dir(tmp_path)
    (path / "evals").mkdir()
    original = '{"evals": []}'
    (path / "evals" / "evals.json").write_text(original, encoding="utf-8")

    result = runner.invoke(app, ["init", str(path)])
    assert result.exit_code == 0, result.output
    assert (path / "evals" / "order-support.eval.yaml").is_file()
    assert (path / "evals" / "evals.json").read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_orchestrator.py tests/test_cli.py tests/test_cli_init.py -q`
Expected: all pass. (They exercise code finished in Task 4, so there is no red phase here; the red phase was in Tasks 2-4.) If `"1 errored"` is not in the output, read the summary line the CLI prints (the existing assertion `"0 errored" in result.output` in `tests/test_cli.py` shows the phrasing) and fix the test's string, not the code.

- [ ] **Step 3: Correct the spec**

In `docs/superpowers/specs/2026-10-05-skill-lens-evals-json-design.md`:

1. In the §2 table row beginning `| **Discovery: \`evals/evals.json\` is found together with`, replace the sentence ``` `init` and `list` already call it, so a skill with an `evals.json` is not scaffolded over. ``` with ``` `list` and batch `init` already call it, so batch `init` skips a skill that has an `evals.json`; single-skill `init` writes its scaffold beside the JSON file and never touches it. ```.
2. In §5, in the first bullet, after the clause about `json.loads`, add: ``` A leading byte-order mark is removed first, because Windows editors write one and `json.loads` refuses it. ```
3. In §8, replace the bullet that begins ``- `tests/test_cli.py` — `run` on a skill whose only eval file is `evals.json`, with a scripted judge`` (it ends with ``and `init` does not scaffold over an `evals.json`.``) with these three bullets. A scripted judge cannot be injected through the CLI, so the passing case is pinned at the orchestrator level:

```markdown
- `tests/test_orchestrator.py` — an `evals.json` case is graded by a scripted judge and passes;
  under the default `fake` judge it is `errored`; an input file reaches the workspace; an
  unknown key aborts the run.
- `tests/test_cli.py` — `list` counts the cases; a first-stage file (only `prompt` and
  `expected_output`) lists and runs; `run` over an `evals.json` exits 1 with `1 errored` under
  the default judge; an unknown key exits 2 naming it; `--evals` accepts a `.json` file.
- `tests/test_cli_init.py` — batch `init` skips a skill that has an `evals.json`; single
  `init` writes beside it and leaves it unchanged.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrator.py tests/test_cli.py tests/test_cli_init.py docs/superpowers/specs/2026-10-05-skill-lens-evals-json-design.md
uv run git commit -m "test: pin evals.json through the pipeline and the CLI" -m "Covers grading by the judge, the errored result under the default judge, input files reaching the workspace, list counts, and init leaving an evals.json alone. The spec now says batch init skips such a skill and single init writes beside it." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/eval-files.md`, `docs/cli.md`, `docs/runners.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `skills/writing-skill-evals/SKILL.md`

**Interfaces:**
- Consumes: the behaviour from Tasks 1-5. The mkdocs build runs with `--strict`, so every link must resolve.

- [ ] **Step 1: `docs/eval-files.md`**

Insert this section immediately before `## Where eval files are found`:

````markdown
## Reading `evals.json`

A skill that already has an `evals/evals.json` — the file the [Agent Skills evaluation
guide](https://agentskills.io/skill-creation/evaluating-skills) describes and Anthropic's
`skill-creator` writes — runs with no YAML:

```bash
skill-lens run ./my-skill
```

```json
{
  "skill_name": "csv-analyzer",
  "evals": [
    {
      "id": 1,
      "prompt": "Find the top 3 months by revenue in the CSV and chart them.",
      "expected_output": "A bar chart of the three highest months.",
      "files": ["evals/files/sales.csv"],
      "assertions": ["The chart shows exactly 3 months", "Both axes are labelled"]
    }
  ]
}
```

| `evals.json` | Becomes | Rule |
| --- | --- | --- |
| `skill_name` | — | Optional. When present it must equal the skill's `name`. |
| `id` | the case name `eval-<id>` | Required. An integer or a non-empty string, never a boolean, unique in the file. |
| `prompt` | `task` | Required, non-empty. |
| `assertions` or `expectations` | `judge.rubric` | A list of non-empty strings. The guide says `assertions`; `skill-creator` says `expectations`. Both in one eval is an error. |
| `expected_output` | `judge.expected` | Context for the judge. With no statements it becomes the single check `The output satisfies: <expected_output>`. |
| `files` | `workspace.files` | Paths relative to the skill directory, keyed in the workspace by the path as written. |

What to know:

- **A real judge grades it.** The statements are plain English, so `judge = "fake"` (the
  default) reports the cases as `errored`, never as passed. Pick a judge — see
  [Judging output quality](#judging-output-quality) and [Configuration](configuration.md).
- **Nothing to grade is an error.** An eval with no statements and no `expected_output` stops
  the run (exit 2).
- **Keys are strict.** An unknown key at the top level or in an eval stops the run (exit 2)
  and is named in the message. A file in another layout, such as one with a `trigger` block
  or `files` that are folder names, needs rewriting first.
- **`files` are text.** Each is read when the file loads and must be a UTF-8 text file inside
  the skill directory, no larger than 1,000,000 bytes. A missing file, a folder, a binary file
  or a path that leaves the skill directory stops the run (exit 2). A case with no `files`
  gets no workspace.
- **The judge reads the agent's reply.** `evals.json` does not name output files, so a file the
  agent writes is not shown to the judge. Move to a YAML case with `judge.artifacts` when the
  skill's product is a file.
- **Move to YAML for more.** Mock `tools`, `trajectory`, `budget` and `mode: offered` exist only
  in YAML. Both kinds of file can sit in one `evals/` directory and are loaded together.
````

In the list under `## Where eval files are found`, change item 1 to:

```markdown
1. an `evals/` directory beside `SKILL.md` — every `.yaml` / `.yml` file in it, plus
   `evals.json` ([Reading `evals.json`](#reading-evalsjson)), or
```

and change the sentence beginning `` `--evals <path>` overrides discovery `` to:

```markdown
`--evals <path>` overrides discovery with an explicit file or directory. A file ending in
`.json` is read as `evals.json`; in a directory, only a file named `evals.json` is read among
the JSON files, so fixtures and schemas are left alone. Skills with no eval
files are reported as **skipped** — visible in the output, never silently ignored.
```

- [ ] **Step 2: `docs/cli.md`**

In both table rows (`skill-lens run` options near line 31 and `skill-lens list` near line 103), change the meaning to: `An explicit eval file (YAML or `evals.json`) or directory, overriding discovery`.

- [ ] **Step 3: `docs/runners.md`**

At the end of the first paragraph of `## The workspace`, add one sentence: `A case read from an `evals.json` gets a workspace only when it lists input `files`; see [Reading `evals.json`](eval-files.md#reading-evalsjson).`

- [ ] **Step 4: `ARCHITECTURE.md`**

(a) In the module map, replace the final `.` of the `cases/loader.py` row with `; an `evals.json` is read through `cases/evals_json.py`.`, and add these two rows immediately after it:

```markdown
| `cases/errors.py` | `CaseParseError`, alone, so the converter below can raise it without importing the loader that imports the converter. |
| `cases/evals_json.py` | Turns one parsed `evals/evals.json` — the shared format of the Agent Skills guide and `skill-creator` — into the raw case mappings the YAML loader produces: strict keys, `eval-<id>` names, `assertions` / `expectations` as `judge.rubric`, `expected_output` as `judge.expected` (or the one check when there are no statements), and `files` read as UTF-8 text through the workspace's containment helpers. It validates nothing the loader already validates. |
```

(b) In the data-flow diagram line that reads `cases/loader (evals/ dir or *.eval.yaml; tool_libraries: → cases/tool_libraries; ref: resolved)`, insert `evals.json → cases/evals_json; ` immediately after `*.eval.yaml; `.

(c) Add this subsection immediately before `### Security checks`:

```markdown
### Reading `evals.json`

**`evals.json` is converted, never interpreted twice.** `cases/evals_json.py` produces raw
case mappings and stops. `EvalCase.model_validate` and the loader's `_validate_*` functions are
the only validators, so a JSON case and a YAML case meet identical rules, and no runner,
evaluator, reporter, model or exit code knows which file a case came from.

**Keys are strict.** An unknown top-level or per-case key is an authoring error naming the
key. `assertions` and `expectations` are one list under two names (the guide and
`skill-creator` disagree), and both in one eval is refused as ambiguous. There is no leniency
flag: the only thing that stops a misspelled `assertion:` from dropping every check is that it
is refused.

**A case with nothing to grade is refused, never passed.** No statements and no
`expected_output` is an authoring error. `expected_output` alone becomes one rubric check,
because the guide defines it as a description of success and tells authors to start with only
a prompt and an expected output.

**`id` is never a boolean and is unique.** `bool` is an `int` in Python, so `true` would
otherwise become id 1. The case name is `eval-<id>`, and `1` and `"1"` collide.

**Input files are text inside the skill directory, read at load time.** The converter uses
`check_relative_path`, `resolve_under` and `stat_regular` from `workspace.py`, so a FIFO, a
symlink loop, a link out of the directory, a folder, a binary file or one over
`max_file_bytes` aborts the run before any case executes. The load-time limit is the default
cap because the loader has no configuration; the configured cap still applies when the
workspace is seeded. A `workspace:` block exists only when `files` is non-empty.

**`CaseParseError` lives in `cases/errors.py`** and the loader re-exports it, because the
loader imports the converter and the converter raises the error.
```

- [ ] **Step 5: `CLAUDE.md`**

(a) In "What this is", after the sentence ending `` `docs/superpowers/specs/2026-09-21-skill-lens-call-args-design.md`. `` insert:

```
`evals.json` support lets a skill that already has `evals/evals.json` — the file the Agent
Skills guide and `skill-creator` use — run with no YAML: `cases/evals_json.py` converts it to
the raw mappings the YAML loader produces, so the same validation applies. Its design is in
`docs/superpowers/specs/2026-10-05-skill-lens-evals-json-design.md`.
```

(b) In the invariants list, insert these bullets immediately before the bullet that begins ``- **`EvalCase.tools` holds only `ToolSpec`;``:

```
- **`evals.json` is converted to raw case mappings and validated by the existing pipeline.**
  `cases/evals_json.py` has no validator of its own beyond shape; `EvalCase` and the loader's
  `_validate_*` functions are the only rules, so a JSON case and a YAML case are identical
  downstream. No runner, evaluator, reporter, model or exit code changed.
- **`evals.json` keys are strict, and nothing to grade is refused.** An unknown key at either
  level is an authoring error naming it; `assertions` and `expectations` are one list (both in
  one eval is refused); no statements and no `expected_output` is refused, while
  `expected_output` alone becomes the single check `The output satisfies: <expected_output>`.
- **`evals.json` `id` is never a boolean and the case name is `eval-<id>`.** Ids are unique
  across `1` and `"1"`.
- **`evals.json` input files are text inside the skill directory, read at load time** through
  the workspace's containment helpers, capped at the default `max_file_bytes`; a `workspace:`
  block exists only when `files` is non-empty.
```

- [ ] **Step 6: `skills/writing-skill-evals/SKILL.md`**

Insert this section immediately after the `## Choosing the check` table:

```markdown
## A skill that already has `evals/evals.json`

skill-lens reads that file directly (the shape the Agent Skills guide and `skill-creator` use):
`skill-lens run ./the-skill` needs no YAML. Its `assertions` / `expectations` become a judge
rubric, so a real judge must be configured — under the default `judge = "fake"` the cases come
back `errored`. Keep the JSON when plain-English checks on the reply are enough. Add a YAML
file in the same `evals/` directory when the skill needs mock `tools`, a `trajectory`, a
`budget`, `mode: offered`, or a judge that reads a file the agent wrote; both load together.
```

- [ ] **Step 7: Check the docs**

Run: `uv run pytest tests/test_docs.py tests/test_naming.py tests/test_shipped_skill.py tests/test_check_docs_updated.py -q`
Expected: all pass.
Run: `uv sync --group docs && uv run mkdocs build --strict`
Expected: the build finishes with no warnings. A broken anchor warning means a heading slug differs: `## Reading \`evals.json\`` slugs to `reading-evalsjson`.

- [ ] **Step 8: Commit**

```bash
git add docs/eval-files.md docs/cli.md docs/runners.md ARCHITECTURE.md CLAUDE.md skills/writing-skill-evals/SKILL.md
uv run git commit -m "docs: document reading evals.json eval files" -m "Adds the mapping table, the strict-key and text-file rules, the judge requirement, the module-map rows and the invariants." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Whole-branch verification

**Files:** none (verification only; fix and commit only if something fails).

- [ ] **Step 1: The full gate, as CI runs it**

Run each and expect success:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mkdocs build --strict
uv run skill-lens list ./examples
```

Expected: pytest all green (the `integration` marker is deselected by default); ruff clean; mkdocs clean; `list` prints the example skills with their case counts unchanged.

- [ ] **Step 2: Dogfood on a real file**

Create a scratch skill outside the repo and run it:

```bash
mkdir -p /tmp/evals-json-demo/evals/files
printf -- '---\nname: demo\ndescription: demo skill\n---\n\nSay hello politely.\n' > /tmp/evals-json-demo/SKILL.md
printf 'name\nAda\n' > /tmp/evals-json-demo/evals/files/names.csv
printf '{"skill_name":"demo","evals":[{"id":1,"prompt":"Greet the first person in names.csv","expected_output":"A polite greeting to Ada.","files":["evals/files/names.csv"]}]}' > /tmp/evals-json-demo/evals/evals.json
uv run skill-lens list /tmp/evals-json-demo
uv run skill-lens run /tmp/evals-json-demo
```

Expected: `list` prints `demo	1 case(s)`. `run` exits 1 with `1 errored`, because the default `fake` judge cannot grade the check — the correct, loud outcome. Then rewrite `evals.json` with an unknown key (`"kind": "x"` in the eval) and confirm `run` exits 2 naming `'kind'`. Delete `/tmp/evals-json-demo` afterwards.

- [ ] **Step 3: Hand off**

Do not push. Report to the user: the branch `feat/evals-json`, the commit list (`git log --oneline main..HEAD`), and the verification results. Remind them that the project requires, before the pull request: a linked issue (`Closes #N` in the body), a Conventional Commit title (`feat: read evals.json eval files`), and `--assignee EmadMokhtar`. Ask whether to file the issue, push, and open the pull request.
