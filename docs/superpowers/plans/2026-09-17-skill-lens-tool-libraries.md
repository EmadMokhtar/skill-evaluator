# Tool Libraries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one YAML file declare a mock tool once and let any eval file import it with
`tool_libraries:` and name it with `- ref: <name>`, so nine skills fronting one MCP server
share one contract instead of nine hand-copied blocks.

**Architecture:** A new `cases/tool_libraries.py` parses library files (a top-level
`tools:` list, checked exactly as inline tools are) and merges an eval file's imports into
a `ToolLibrary` that refuses ambiguity. `cases/loader.py` resolves every `ref:` on the raw
case mapping before `EvalCase.model_validate`, so `EvalCase.tools` keeps holding only
`ToolSpec` and nothing downstream changes. The checks both file kinds share (the scaffold
sentinel walk, the tool-schema check) move to `cases/checks.py`, below both.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML through `yaml_loading.safe_load`,
jsonschema, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-17-skill-lens-tool-libraries-design.md`

## Global Constraints

- All file IO pins `encoding="utf-8"` and re-raises as a typed error naming the file.
- YAML goes through `skill_lens.yaml_loading.safe_load`, never `yaml.safe_load`.
- `skill_lens` (underscore) never appears in user-facing text; the name is `skill-lens`.
- Every data shape lives in `models.py`; `extra="forbid"` on every user-facing model.
- Authoring errors abort the run (exit 2) and never score as failures; they are raised as
  `CaseParseError` from the case loader, which `cli._AUTHORING_ERRORS` already covers.
- The pipeline test tier is zero-cost, offline and deterministic.
- Conventional Commits on every commit; PRs are squash-merged, so the PR title is
  conventional too.
- Documentation ships with the change: `docs/`, `ARCHITECTURE.md`, `CLAUDE.md`, the bundled
  skill's syntax reference. `uv run mkdocs build --strict` and `uv run pytest
  tests/test_docs.py` must pass.
- `tests/conftest.py` moves every test into a fresh `tmp_path`; a path-relativity test must
  place the eval file and the library in different directories to prove anything.

---

### Task 1: Move the shared checks into `cases/checks.py`

**Files:**
- Create: `src/skill_lens/cases/checks.py`
- Modify: `src/skill_lens/cases/loader.py` (the `UNFILLED_SENTINEL` constant,
  `_reject_unfilled`, `_validate_tools`)
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces: `UNFILLED_SENTINEL: str`; `find_unfilled(raw: object, trail: str = "") ->
  str | None`; `check_tool_schema(tool: ToolSpec) -> None` (raises `ValueError`).
  `cases.loader.UNFILLED_SENTINEL` keeps working as an import path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_checks.py
"""The checks an eval file and a tool library share."""

import pytest

from skill_lens.cases.checks import UNFILLED_SENTINEL, check_tool_schema, find_unfilled
from skill_lens.models import ToolSpec


def test_no_placeholder_returns_none():
    assert find_unfilled({"name": "n", "tools": [{"returns": "x"}]}) is None


def test_a_placeholder_value_is_found_with_its_trail():
    raw = {"name": "n", "tools": [{"name": "t", "returns": f"{UNFILLED_SENTINEL} fill"}]}
    assert find_unfilled(raw) == "tools[0].returns"


def test_a_placeholder_key_is_found_at_the_parent_trail():
    raw = {"workspace": {"files": {f"{UNFILLED_SENTINEL}.txt": "content"}}}
    assert find_unfilled(raw) == "workspace.files"


def test_a_top_level_placeholder_string_has_an_empty_trail():
    assert find_unfilled(UNFILLED_SENTINEL) == ""


def test_a_self_referential_anchor_does_not_recurse_forever():
    raw: dict = {"name": "n"}
    raw["self"] = raw
    assert find_unfilled(raw) is None


def test_a_tool_with_parameters_only_passes():
    check_tool_schema(ToolSpec(name="t", parameters={"q": "string"}))


def test_both_parameters_and_input_schema_are_refused():
    tool = ToolSpec(name="t", parameters={"q": "string"}, input_schema={"type": "object"})
    with pytest.raises(ValueError, match="declares both parameters and input_schema"):
        check_tool_schema(tool)


def test_a_malformed_input_schema_is_refused():
    with pytest.raises(ValueError, match="has an invalid input_schema"):
        check_tool_schema(ToolSpec(name="t", input_schema={"type": 5}))


def test_a_non_object_input_schema_is_refused():
    with pytest.raises(ValueError, match="must declare type: object"):
        check_tool_schema(ToolSpec(name="t", input_schema={"type": "string"}))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.cases.checks'`

- [ ] **Step 3: Create the module**

```python
# src/skill_lens/cases/checks.py
"""Checks every eval-authoring file kind shares.

An eval file and a tool library are both the author's input, and the same
mistakes are refused in both: a scaffold placeholder left in place, and a
mock tool whose declared schema no provider would register. `cases/loader.py`
and `cases/tool_libraries.py` both import from here and this module imports
neither, so the loader can import the library module without a cycle.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from skill_lens.models import ToolSpec

# The placeholder `skill-lens init` and `skill-lens mcp-import` write into
# every field the author has to fill in. Living here rather than in
# scaffold.py makes it the *loader's* guarantee: a hand-written stub is
# refused exactly like a generated one.
UNFILLED_SENTINEL = "TODO(skill-lens)"


def find_unfilled(
    raw: object, trail: str = "", seen: frozenset[int] = frozenset()
) -> str | None:
    """The trail ("tools[0].returns") of the first scaffold placeholder in
    `raw`, or None when there is none.

    Mapping keys are checked as well as values: `workspace.files` is keyed by
    filename, and a key hit reports the mapping's own trail. `seen` holds the
    `id()` of every dict/list currently being walked, so a self-referential
    YAML anchor (a node that contains itself) is skipped instead of recursing
    forever -- still a malformed file, but one that must exit cleanly rather
    than crash with a RecursionError.
    """
    if isinstance(raw, str):
        return trail if UNFILLED_SENTINEL in raw else None
    if isinstance(raw, dict):
        if id(raw) in seen:
            return None
        seen = seen | {id(raw)}
        for key, value in raw.items():
            found = find_unfilled(key, trail, seen)
            if found is None:
                found = find_unfilled(value, f"{trail}.{key}" if trail else str(key), seen)
            if found is not None:
                return found
        return None
    if isinstance(raw, list):
        if id(raw) in seen:
            return None
        seen = seen | {id(raw)}
        for position, value in enumerate(raw):
            found = find_unfilled(value, f"{trail}[{position}]", seen)
            if found is not None:
                return found
    return None


def check_tool_schema(tool: ToolSpec) -> None:
    """Raise ValueError unless the tool's declared schema is one a provider
    would register.

    A tool declares its arguments one of two ways -- the `parameters:`
    shorthand or a full `input_schema:` -- never both, and a declared schema
    has to be valid JSON Schema whose top level is an object. All three
    mistakes are the author's; the caller prefixes the message with where the
    tool was declared, so the same text serves a case and a library.
    """
    if tool.input_schema is None:
        return
    if tool.parameters:
        raise ValueError("declares both parameters and input_schema; choose one.")
    try:
        Draft202012Validator.check_schema(tool.input_schema)
    except SchemaError as exc:
        raise ValueError(f"has an invalid input_schema: {exc.message}") from exc
    if tool.input_schema.get("type") != "object":
        raise ValueError(
            "input_schema must declare type: object; a tool's arguments are always an object."
        )
```

- [ ] **Step 4: Make the loader delegate**

In `src/skill_lens/cases/loader.py`:

Replace the import block's `from jsonschema import Draft202012Validator` /
`from jsonschema.exceptions import SchemaError` usage for tools (keep them: `_validate_assertions`
still uses both) and add:

```python
from skill_lens.cases.checks import UNFILLED_SENTINEL, check_tool_schema, find_unfilled
```

Delete the `UNFILLED_SENTINEL = "TODO(skill-lens)"` constant and its comment (the import
above keeps `loader.UNFILLED_SENTINEL` importable for `scaffold.py`, `cli.py`,
`mcp_import.py` and the tests).

Replace the whole `_reject_unfilled` function with:

```python
def _reject_unfilled(path: Path, index: int, raw: object) -> None:
    """Refuse a case still carrying scaffold placeholders.

    Runs before schema validation so the message names the field to fill in
    rather than complaining about the type of a value nobody meant to keep.
    An unfilled scaffold says something about the author's progress, not
    about the skill, so it aborts the run as an authoring error instead of
    scoring as a failure. The walk itself is `checks.find_unfilled`, shared
    with tool libraries.
    """
    trail = find_unfilled(raw)
    if trail is not None:
        raise CaseParseError(
            f"{path}: case #{index + 1} still has the scaffold placeholder "
            f"{UNFILLED_SENTINEL} at {trail or 'case'}. Fill it in -- an "
            f"unfinished eval cannot say anything about the skill."
        )
```

Replace the whole `_validate_tools` function with:

```python
def _validate_tools(path: Path, case: EvalCase) -> None:
    """Check each mock tool's declared schema at load time.

    The rules are `checks.check_tool_schema`'s, shared with tool libraries;
    here each refusal names the file, the case and the tool. All three
    mistakes are the author's, so they abort before any case runs rather
    than surface as an errored case.
    """
    for tool in case.tools:
        try:
            check_tool_schema(tool)
        except ValueError as exc:
            raise CaseParseError(f"{path}: case {case.name!r} tool {tool.name!r} {exc}") from exc
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_checks.py tests/test_case_loader.py tests/test_scaffold.py tests/test_mcp_import.py tests/test_cli_init.py -q`
Expected: all PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/checks.py src/skill_lens/cases/loader.py tests/test_checks.py
git commit -m "refactor: move the checks eval files and tool libraries share into cases/checks.py"
```

---

### Task 2: The `ToolRef` model

**Files:**
- Modify: `src/skill_lens/models.py` (after `ToolSpec`)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `ToolRef(BaseModel)` with `ref: str`, `returns: str | None = None`,
  `extra="forbid"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
def test_a_tool_ref_carries_a_name_and_optionally_returns():
    from skill_lens.models import ToolRef

    assert ToolRef(ref="lookup_order").returns is None
    assert ToolRef(ref="lookup_order", returns="{}").returns == "{}"


def test_a_tool_ref_refuses_any_other_key():
    from pydantic import ValidationError

    from skill_lens.models import ToolRef

    with pytest.raises(ValidationError, match="description"):
        ToolRef(ref="lookup_order", description="rewritten")
    with pytest.raises(ValidationError):
        ToolRef(returns="{}")
```

(Use the file's existing top-level imports if `pytest`, `ValidationError` or the models
are already imported there; move the new imports to the top.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k tool_ref -v`
Expected: FAIL with `ImportError: cannot import name 'ToolRef'`

- [ ] **Step 3: Add the model**

In `src/skill_lens/models.py`, directly after the `ToolSpec` class:

```python
class ToolRef(BaseModel):
    """A case's reference to a tool a library declares: `- ref: lookup_order`.

    The library owns the contract (name, description, schema); the case may
    set only `returns`, the scenario. The case loader resolves every ref into
    the `ToolSpec` it names before the case is validated, so `EvalCase.tools`
    never holds one and no runner ever sees one.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    returns: str | None = None
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/models.py tests/test_models.py
git commit -m "feat: add the ToolRef model a case uses to name a library tool"
```

---

### Task 3: Parse one library file

**Files:**
- Create: `src/skill_lens/cases/tool_libraries.py`
- Test: `tests/test_tool_libraries.py`

**Interfaces:**
- Consumes: `checks.UNFILLED_SENTINEL`, `checks.find_unfilled`, `checks.check_tool_schema`.
- Produces: `ToolLibraryError(Exception)`; `parse_tool_library(path: Path) ->
  list[ToolSpec]`; constants `TOOL_LIBRARIES_KEY = "tool_libraries"`,
  `LIBRARY_SUFFIXES = frozenset({".yaml", ".yml"})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tool_libraries.py
"""Tool libraries: mock tools declared once and imported by many eval files."""

from pathlib import Path

import pytest

from skill_lens.cases.checks import UNFILLED_SENTINEL
from skill_lens.cases.tool_libraries import ToolLibraryError, parse_tool_library

LIBRARY = """# the order API
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000"}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
"""


def _write(tmp_path: Path, body: str, name: str = "lib.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_a_library_parses_to_tool_specs_in_file_order(tmp_path):
    specs = parse_tool_library(_write(tmp_path, LIBRARY))
    assert [s.name for s in specs] == ["lookup_order", "issue_refund"]
    assert specs[0].parameters == {"order_id": "string"}
    assert specs[0].returns == '{"id": "0000"}'


def test_an_empty_tools_list_is_a_library_with_nothing_in_it(tmp_path):
    assert parse_tool_library(_write(tmp_path, "tools: []\n")) == []


def test_a_missing_file_is_refused_naming_it(tmp_path):
    with pytest.raises(ToolLibraryError, match="cannot read tool library .*missing.yaml"):
        parse_tool_library(tmp_path / "missing.yaml")


def test_invalid_yaml_is_refused_naming_the_file(tmp_path):
    with pytest.raises(ToolLibraryError, match="invalid YAML in tool library .*lib.yaml"):
        parse_tool_library(_write(tmp_path, "tools: [unclosed\n"))


@pytest.mark.parametrize(
    "body",
    ["- just\n- a list\n", "cases: []\n", "tools: {not: a list}\n", "just a string\n"],
    ids=["list", "no-tools-key", "tools-not-a-list", "scalar"],
)
def test_anything_but_a_top_level_tools_list_is_refused(tmp_path, body):
    with pytest.raises(ToolLibraryError, match="expected a top-level 'tools' list"):
        parse_tool_library(_write(tmp_path, body))


def test_a_second_top_level_key_is_refused_naming_it(tmp_path):
    with pytest.raises(ToolLibraryError, match="expected only a top-level 'tools' list.*'cases'"):
        parse_tool_library(_write(tmp_path, "tools: []\ncases: []\n"))


def test_a_placeholder_value_is_refused_naming_the_tool_and_the_field(tmp_path):
    body = f"tools:\n  - name: t\n    returns: {UNFILLED_SENTINEL} fill me\n"
    with pytest.raises(ToolLibraryError, match=r"tool #1 still has the scaffold placeholder"):
        parse_tool_library(_write(tmp_path, body))
    with pytest.raises(ToolLibraryError, match=r"at returns\."):
        parse_tool_library(_write(tmp_path, body))


def test_a_placeholder_key_is_refused_too(tmp_path):
    body = f"tools:\n  - name: t\n    parameters:\n      '{UNFILLED_SENTINEL}': string\n"
    with pytest.raises(ToolLibraryError, match=r"tool #1 still has the scaffold placeholder"):
        parse_tool_library(_write(tmp_path, body))


def test_a_tool_the_case_loader_would_refuse_is_refused_here_naming_its_position(tmp_path):
    with pytest.raises(ToolLibraryError, match=r"tool #2 invalid \(name\)"):
        parse_tool_library(
            _write(tmp_path, "tools:\n  - name: ok\n  - name: 'not ok'\n")
        )
    with pytest.raises(ToolLibraryError, match=r"tool #1 invalid \(colour\)"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: ok\n    colour: blue\n"))


def test_a_tool_that_is_not_a_mapping_is_refused_naming_its_position(tmp_path):
    with pytest.raises(ToolLibraryError, match=r"tool #1 invalid"):
        parse_tool_library(_write(tmp_path, "tools:\n  - just a string\n"))


def test_the_schema_checks_apply_naming_the_tool(tmp_path):
    both = "tools:\n  - name: t\n    parameters: {q: string}\n    input_schema: {type: object}\n"
    with pytest.raises(ToolLibraryError, match="tool #1 't' declares both parameters and"):
        parse_tool_library(_write(tmp_path, both))
    with pytest.raises(ToolLibraryError, match="tool #1 't' input_schema must declare type"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: t\n    input_schema: {type: string}\n"))
    with pytest.raises(ToolLibraryError, match="tool #1 't' has an invalid input_schema"):
        parse_tool_library(_write(tmp_path, "tools:\n  - name: t\n    input_schema: {type: 5}\n"))


def test_a_name_declared_twice_in_one_file_is_refused_naming_both_positions(tmp_path):
    body = "tools:\n  - name: t\n  - name: other\n  - name: t\n"
    with pytest.raises(ToolLibraryError, match=r"declares tool 't' twice \(tools #1 and #3\)"):
        parse_tool_library(_write(tmp_path, body))


def test_a_description_of_yes_survives_the_strict_bool_loader(tmp_path):
    (spec,) = parse_tool_library(_write(tmp_path, "tools:\n  - name: t\n    description: yes\n"))
    assert spec.description == "yes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tool_libraries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skill_lens.cases.tool_libraries'`

- [ ] **Step 3: Create the module with `parse_tool_library`**

```python
# src/skill_lens/cases/tool_libraries.py
"""Tool libraries: mock tools declared once and imported by many eval files.

A library is a YAML file holding one top-level `tools:` list -- the block a
case's `tools:` already takes, and exactly what `skill-lens mcp-import`
prints. An eval file names the libraries it imports under `tool_libraries:`
(paths relative to the eval file) and a case pulls a tool in with
`- ref: <name>`. The library owns the tool's contract; the case may set only
`returns`, the scenario.

Everything here raises `ToolLibraryError` naming the library file; the case
loader wraps it with the eval file that did the importing, so a message
always names both the file to look at and the file to fix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from skill_lens.cases.checks import UNFILLED_SENTINEL, check_tool_schema, find_unfilled
from skill_lens.models import ToolSpec
from skill_lens.yaml_loading import safe_load

TOOL_LIBRARIES_KEY = "tool_libraries"
LIBRARY_SUFFIXES = frozenset({".yaml", ".yml"})


class ToolLibraryError(Exception):
    """A library that cannot be imported: a missing or malformed file, a tool
    the loader would refuse inline, a name declared twice, or a ref no library
    declares. An authoring error, so the run exits 2."""


def parse_tool_library(path: Path) -> list[ToolSpec]:
    """Parse one library file into the tools it declares, in file order.

    Every tool is checked as an inline one would be -- the scaffold sentinel
    first, so the message names the field to fill in; then `ToolSpec` itself
    (the name rule, unknown keys); then the schema rules -- and each refusal
    names this file and the tool's position, because this is the file to fix.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolLibraryError(f"cannot read tool library {path}: {exc}") from exc
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ToolLibraryError(f"invalid YAML in tool library {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise ToolLibraryError(
            f"tool library {path}: expected a top-level 'tools' list -- the block "
            f"`skill-lens mcp-import` prints"
        )
    extra = sorted(str(key) for key in data if key != "tools")
    if extra:
        raise ToolLibraryError(
            f"tool library {path}: expected only a top-level 'tools' list, but found "
            f"{', '.join(repr(key) for key in extra)}"
        )
    specs: list[ToolSpec] = []
    positions: dict[str, int] = {}
    for index, raw in enumerate(data["tools"]):
        where = f"tool library {path}: tool #{index + 1}"
        trail = find_unfilled(raw)
        if trail is not None:
            raise ToolLibraryError(
                f"{where} still has the scaffold placeholder {UNFILLED_SENTINEL} at "
                f"{trail or 'tool'}. Fill it in -- an unfinished mock cannot stand in "
                f"for anything."
            )
        try:
            spec = ToolSpec.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise ToolLibraryError(f"{where} invalid ({fields}): {exc}") from exc
        try:
            check_tool_schema(spec)
        except ValueError as exc:
            raise ToolLibraryError(f"{where} {spec.name!r} {exc}") from exc
        if spec.name in positions:
            raise ToolLibraryError(
                f"tool library {path} declares tool {spec.name!r} twice "
                f"(tools #{positions[spec.name] + 1} and #{index + 1})"
            )
        positions[spec.name] = index
        specs.append(spec)
    return specs
```

(`Mapping`, `dataclass` and `field` are imported now for Task 4; ruff will flag them
unused until then — add them in Task 4 instead if you commit between the tasks.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tool_libraries.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/tool_libraries.py tests/test_tool_libraries.py
git commit -m "feat: parse a tool library file into the mocks it declares"
```

---

### Task 4: Import an eval file's libraries into one `ToolLibrary`

**Files:**
- Modify: `src/skill_lens/cases/tool_libraries.py`
- Test: `tests/test_tool_libraries.py`

**Interfaces:**
- Produces: `ToolLibrary` (frozen dataclass: `specs: Mapping[str, ToolSpec]`, `sources:
  Mapping[str, Path]`, `declared: bool`; method `resolve(ref: str) -> ToolSpec`);
  `EMPTY_LIBRARY: ToolLibrary`; `load_tool_libraries(entries: object, *, relative_to:
  Path) -> ToolLibrary`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_libraries.py` (extend the import line with `EMPTY_LIBRARY`,
`ToolLibrary`, `load_tool_libraries`):

```python
def test_a_file_entry_is_imported_relative_to_the_given_directory(tmp_path):
    _write(tmp_path / "shared", LIBRARY, "api.yaml")
    evals = tmp_path / "skills" / "a" / "evals"
    evals.mkdir(parents=True)
    library = load_tool_libraries(["../../../shared/api.yaml"], relative_to=evals)
    assert library.declared is True
    assert set(library.specs) == {"lookup_order", "issue_refund"}
    assert library.sources["lookup_order"] == (tmp_path / "shared" / "api.yaml").resolve()


def test_a_directory_entry_imports_its_yaml_files_sorted_and_nothing_else(tmp_path):
    shared = tmp_path / "shared"
    _write(shared, "tools:\n  - name: b_tool\n", "b.yml")
    _write(shared, "tools:\n  - name: a_tool\n", "a.yaml")
    _write(shared, "tools:\n  - name: ignored\n", "notes.txt")
    (shared / "nested").mkdir()
    _write(shared / "nested", "tools:\n  - name: deeper\n", "c.yaml")
    library = load_tool_libraries(["shared"], relative_to=tmp_path)
    assert list(library.specs) == ["a_tool", "b_tool"]


def test_an_empty_directory_is_refused(tmp_path):
    (tmp_path / "shared").mkdir()
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[0\] 'shared' names a directory with no YAML"):
        load_tool_libraries(["shared"], relative_to=tmp_path)


def test_a_missing_path_is_refused_saying_where_it_looked(tmp_path):
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[0\] 'nope.yaml' does not exist \(looked at"):
        load_tool_libraries(["nope.yaml"], relative_to=tmp_path)


def test_an_absolute_path_is_refused(tmp_path):
    # The check mirrors workspace.check_relative_path (absolute, drive or
    # root); the Windows spellings only trip on Windows, and that function's
    # own tests cover them.
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[0\] .* is not a relative path"):
        load_tool_libraries(["/etc/tools.yaml"], relative_to=tmp_path)


@pytest.mark.parametrize("entries", ["shared/api.yaml", {"path": "x"}, 3])
def test_a_value_that_is_not_a_list_is_refused(tmp_path, entries):
    with pytest.raises(ToolLibraryError, match="tool_libraries must be a list of paths"):
        load_tool_libraries(entries, relative_to=tmp_path)


@pytest.mark.parametrize("entry", [3, None, "", "   ", ["nested"]])
def test_an_entry_that_is_not_a_path_is_refused_naming_its_position(tmp_path, entry):
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[1\] must be a path"):
        load_tool_libraries(["lib.yaml", entry], relative_to=tmp_path)


def test_the_same_file_listed_twice_is_refused(tmp_path):
    _write(tmp_path, LIBRARY)
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[1\] 'lib.yaml' imports .*lib.yaml again; tool_libraries\[0\] already did"):
        load_tool_libraries(["lib.yaml", "lib.yaml"], relative_to=tmp_path)


def test_a_directory_plus_a_file_inside_it_is_refused(tmp_path):
    _write(tmp_path / "shared", LIBRARY, "api.yaml")
    with pytest.raises(ToolLibraryError, match=r"tool_libraries\[1\] .* imports .* again"):
        load_tool_libraries(["shared", "shared/api.yaml"], relative_to=tmp_path)


def test_a_name_two_files_declare_is_refused_naming_both(tmp_path):
    _write(tmp_path, "tools:\n  - name: lookup_order\n", "one.yaml")
    _write(tmp_path, "tools:\n  - name: lookup_order\n", "two.yaml")
    with pytest.raises(ToolLibraryError) as info:
        load_tool_libraries(["one.yaml", "two.yaml"], relative_to=tmp_path)
    message = str(info.value)
    assert "tool 'lookup_order' is declared by both" in message
    assert "one.yaml" in message and "two.yaml" in message


def test_a_library_error_names_the_library_file(tmp_path):
    _write(tmp_path, "tools: [unclosed\n")
    with pytest.raises(ToolLibraryError, match="invalid YAML in tool library .*lib.yaml"):
        load_tool_libraries(["lib.yaml"], relative_to=tmp_path)


def test_resolve_returns_the_named_tool():
    library = ToolLibrary(specs={"t": ToolSpec(name="t")}, sources={"t": Path("x")}, declared=True)
    assert library.resolve("t").name == "t"


def test_resolve_without_a_tool_libraries_key_says_to_add_one():
    with pytest.raises(ToolLibraryError, match="references tool 'x' but the file declares no tool_libraries:"):
        EMPTY_LIBRARY.resolve("x")


def test_resolve_of_an_unknown_name_lists_the_declared_names_sorted():
    library = ToolLibrary(
        specs={"zeta": ToolSpec(name="zeta"), "alpha": ToolSpec(name="alpha")},
        sources={"zeta": Path("x"), "alpha": Path("x")},
        declared=True,
    )
    with pytest.raises(ToolLibraryError, match="which no imported library declares; the imports declare: alpha, zeta"):
        library.resolve("x")


def test_resolve_against_empty_imports_says_nothing_is_declared():
    library = ToolLibrary(specs={}, sources={}, declared=True)
    with pytest.raises(ToolLibraryError, match="the imports declare: nothing"):
        library.resolve("x")
```

Add `from skill_lens.models import ToolSpec` to the imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tool_libraries.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'load_tool_libraries'`

- [ ] **Step 3: Add `ToolLibrary`, `EMPTY_LIBRARY` and `load_tool_libraries`**

Add to `src/skill_lens/cases/tool_libraries.py`, after `ToolLibraryError`:

```python
@dataclass(frozen=True)
class ToolLibrary:
    """Every tool an eval file's imports declare, by name.

    `declared` is False when the eval file has no `tool_libraries:` key at
    all, so the first `ref:` can say "add the key" rather than "unknown name".
    """

    specs: Mapping[str, ToolSpec] = field(default_factory=dict)
    sources: Mapping[str, Path] = field(default_factory=dict)
    declared: bool = False

    def resolve(self, ref: str) -> ToolSpec:
        """The tool `ref` names, or a ToolLibraryError saying what to fix."""
        if not self.declared:
            raise ToolLibraryError(
                f"references tool {ref!r} but the file declares no {TOOL_LIBRARIES_KEY}:"
            )
        try:
            return self.specs[ref]
        except KeyError:
            names = ", ".join(sorted(self.specs)) or "nothing"
            raise ToolLibraryError(
                f"references tool {ref!r}, which no imported library declares; "
                f"the imports declare: {names}"
            ) from None


EMPTY_LIBRARY = ToolLibrary()
```

And after `parse_tool_library`:

```python
def load_tool_libraries(entries: object, *, relative_to: Path) -> ToolLibrary:
    """Import every library an eval file's `tool_libraries:` names.

    `entries` is the key's raw YAML value; `relative_to` is the eval file's
    directory, the one location the file can rely on -- so the same file
    resolves identically under discovery, `--evals`, `list` and from any
    working directory. An absolute path would break on the next checkout and
    is refused. A file entry is used as written; a directory entry imports
    its `.yaml` / `.yml` files, sorted, without descending further, like
    `evals/`. Every tool name must be declared exactly once across the
    imports: ambiguity is never resolved by position.
    """
    if not isinstance(entries, list):
        raise ToolLibraryError(
            f"{TOOL_LIBRARIES_KEY} must be a list of paths relative to the eval file, "
            f"got {type(entries).__name__}"
        )
    files: list[Path] = []
    origin: dict[Path, str] = {}
    for position, entry in enumerate(entries):
        where = f"{TOOL_LIBRARIES_KEY}[{position}]"
        if not isinstance(entry, str) or not entry.strip():
            raise ToolLibraryError(
                f"{where} must be a path relative to the eval file, got {entry!r}"
            )
        candidate = Path(entry)
        # Three checks, not one, as in workspace.check_relative_path: on
        # Windows "C:foo" is drive-relative but not absolute, and "\\foo" has
        # a root and no drive.
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise ToolLibraryError(
                f"{where} {entry!r} is not a relative path; a library path is relative "
                f"to the eval file so the suite loads from any checkout"
            )
        resolved = (relative_to / candidate).resolve()
        if resolved.is_dir():
            expanded = sorted(
                p for p in resolved.iterdir() if p.is_file() and p.suffix in LIBRARY_SUFFIXES
            )
            if not expanded:
                raise ToolLibraryError(
                    f"{where} {entry!r} names a directory with no YAML files in it ({resolved})"
                )
        elif resolved.is_file():
            expanded = [resolved]
        else:
            raise ToolLibraryError(f"{where} {entry!r} does not exist (looked at {resolved})")
        for file in expanded:
            if file in origin:
                raise ToolLibraryError(
                    f"{where} {entry!r} imports {file} again; {origin[file]} already did"
                )
            origin[file] = where
            files.append(file)
    specs: dict[str, ToolSpec] = {}
    sources: dict[str, Path] = {}
    for file in files:
        for spec in parse_tool_library(file):
            if spec.name in sources:
                raise ToolLibraryError(
                    f"tool {spec.name!r} is declared by both {sources[spec.name]} and "
                    f"{file}; a ref can only mean one of them"
                )
            specs[spec.name] = spec
            sources[spec.name] = file
    return ToolLibrary(specs=specs, sources=sources, declared=True)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tool_libraries.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/tool_libraries.py tests/test_tool_libraries.py
git commit -m "feat: import an eval file's tool libraries into one unambiguous set"
```

---

### Task 5: Resolve `ref:` in the case loader

**Files:**
- Modify: `src/skill_lens/cases/loader.py` (`parse_cases_file`, two new helpers)
- Test: `tests/test_case_loader.py`, `tests/test_product_preflight.py`,
  `tests/test_mcp_import.py`

**Interfaces:**
- Consumes: `ToolRef` (Task 2); `TOOL_LIBRARIES_KEY`, `EMPTY_LIBRARY`, `ToolLibrary`,
  `ToolLibraryError`, `load_tool_libraries` (Tasks 3–4).
- Produces: `parse_cases_file` honouring `tool_libraries:` and `ref:`; nothing else
  changes its signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_case_loader.py`:

```python
LIBRARY_YAML = """tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000"}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
"""

REF_CASES = """tool_libraries:
  - ../../shared-tools/order-api.yaml
cases:
  - name: refuses
    task: refund 1234
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "days_since_delivery": 45}'
      - ref: issue_refund
    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
"""


def _layout(tmp_path):
    """An eval file two directories below the library, so a path relative to
    the eval file and a path relative to the working directory differ."""
    shared = tmp_path / "shared-tools"
    shared.mkdir()
    (shared / "order-api.yaml").write_text(LIBRARY_YAML, encoding="utf-8")
    evals = tmp_path / "skills" / "orders" / "evals"
    evals.mkdir(parents=True)
    path = evals / "orders.yaml"
    path.write_text(REF_CASES, encoding="utf-8")
    return path


def test_a_ref_resolves_to_the_library_tool_with_the_case_returns(tmp_path, monkeypatch):
    path = _layout(tmp_path)
    # conftest already moved us into tmp_path; move further so a path
    # resolved against the working directory could not find the library.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (case,) = parse_cases_file(path)
    lookup, refund = case.tools
    assert type(lookup) is ToolSpec and type(refund) is ToolSpec
    assert lookup.description == "Look up an order by its id"
    assert lookup.parameters == {"order_id": "string"}
    assert lookup.returns == '{"id": "1234", "days_since_delivery": 45}'
    assert refund.returns == '{"ok": true}'
    assert case.trajectory.called == ["lookup_order"]


def test_refs_resolve_under_an_explicit_evals_path_too(tmp_path):
    path = _layout(tmp_path)
    skill = _skill(tmp_path / "unrelated")
    (case,) = load_cases_for_skill(skill, evals_path=path)
    assert [t.name for t in case.tools] == ["lookup_order", "issue_refund"]


def test_a_ref_may_sit_beside_an_inline_tool(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace(
            "      - ref: issue_refund\n",
            "      - name: escalate\n        returns: ok\n      - ref: issue_refund\n",
        ),
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    assert [t.name for t in case.tools] == ["lookup_order", "escalate", "issue_refund"]


@pytest.mark.parametrize("extra", ["description: rewritten", "name: other", "parameters: {}"])
def test_a_ref_carrying_anything_but_returns_is_refused_naming_the_key(tmp_path, extra):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace("      - ref: issue_refund\n", f"      - ref: issue_refund\n        {extra}\n"),
        encoding="utf-8",
    )
    key = extra.split(":")[0]
    with pytest.raises(CaseParseError, match=rf"orders.yaml: case #1 tool #2: invalid ref: entry \({key}\)"):
        parse_cases_file(path)


def test_a_placeholder_in_a_ref_returns_is_caught_before_any_library_is_read(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").unlink()
    path.write_text(
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - ref: lookup_order\n        returns: TODO(skill-lens) fill\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match=r"placeholder TODO\(skill-lens\) at tools\[0\]\.returns"):
        parse_cases_file(path)


def test_a_ref_with_no_tool_libraries_key_says_to_add_one(tmp_path):
    path = _write(tmp_path, "cases:\n  - name: n\n    task: t\n    tools:\n      - ref: lookup_order\n")
    with pytest.raises(CaseParseError, match=r"cases.eval.yaml: case #1 tool #1 references tool 'lookup_order' but the file declares no tool_libraries:"):
        parse_cases_file(path)


def test_an_unknown_ref_lists_what_the_imports_declare(tmp_path):
    path = _layout(tmp_path)
    path.write_text(REF_CASES.replace("ref: issue_refund", "ref: cancel_order"), encoding="utf-8")
    with pytest.raises(CaseParseError, match=r"case #1 tool #2 references tool 'cancel_order', which no imported library declares; the imports declare: issue_refund, lookup_order"):
        parse_cases_file(path)


def test_a_library_error_names_the_eval_file_and_the_library(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text("tools: [unclosed\n", encoding="utf-8")
    with pytest.raises(CaseParseError) as info:
        parse_cases_file(path)
    assert "orders.yaml: invalid YAML in tool library" in str(info.value)
    assert "order-api.yaml" in str(info.value)


def test_a_missing_library_names_the_entry_and_the_eval_file(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").unlink()
    with pytest.raises(CaseParseError, match=r"orders.yaml: tool_libraries\[0\] '../../shared-tools/order-api.yaml' does not exist"):
        parse_cases_file(path)


def test_a_ref_twice_in_one_case_hits_the_duplicate_check(tmp_path):
    path = _layout(tmp_path)
    path.write_text(REF_CASES.replace("ref: issue_refund", "ref: lookup_order"), encoding="utf-8")
    with pytest.raises(CaseParseError, match="declares tool 'lookup_order' more than once"):
        parse_cases_file(path)


def test_a_ref_beside_an_inline_tool_of_the_same_name_hits_the_duplicate_check(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        REF_CASES.replace("      - ref: issue_refund\n", "      - name: lookup_order\n"),
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match="declares tool 'lookup_order' more than once"):
        parse_cases_file(path)


def test_a_ref_to_a_builtin_name_collides_in_a_workspace_case(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text(
        "tools:\n  - name: read_file\n", encoding="utf-8"
    )
    path.write_text(
        "tool_libraries: [../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n    workspace: {}\n    tools:\n      - ref: read_file\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match="collides with a built-in workspace tool"):
        parse_cases_file(path)


def test_a_ref_collides_with_the_offered_skill_name(tmp_path):
    path = _layout(tmp_path)
    (tmp_path / "shared-tools" / "order-api.yaml").write_text(
        "tools:\n  - name: orders\n", encoding="utf-8"
    )
    path.write_text(
        "tool_libraries: [../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n    mode: offered\n    tools:\n      - ref: orders\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseParseError, match="collides with the name skill 'orders' is offered under"):
        parse_cases_file(path, Skill(name="orders", description="", instructions="", path=tmp_path))


def test_two_cases_sharing_an_anchored_tools_list_both_resolve(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        "tool_libraries: [../../shared-tools/order-api.yaml]\n"
        "cases:\n"
        "  - name: one\n    task: t\n    tools: &shared\n      - ref: lookup_order\n"
        "  - name: two\n    task: t\n    tools: *shared\n",
        encoding="utf-8",
    )
    one, two = parse_cases_file(path)
    assert one.tools[0].description == two.tools[0].description == "Look up an order by its id"


def test_a_file_with_tool_libraries_but_no_refs_loads_unchanged(tmp_path):
    path = _layout(tmp_path)
    path.write_text(
        "tool_libraries: [../../shared-tools/order-api.yaml]\n"
        "cases:\n  - name: n\n    task: t\n",
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    assert case.tools == []


@pytest.mark.parametrize("value", ["shared-tools/x.yaml", "{a: b}"])
def test_a_tool_libraries_value_that_is_not_a_list_is_refused(tmp_path, value):
    path = _write(tmp_path, f"tool_libraries: {value}\ncases: []\n")
    with pytest.raises(CaseParseError, match="cases.eval.yaml: tool_libraries must be a list of paths"):
        parse_cases_file(path)
```

Add `ToolSpec` to the file's `from skill_lens.models import ...` line.

Append to `tests/test_product_preflight.py`:

```python
def test_a_referenced_tool_is_refused_like_an_inline_one(tmp_path):
    # `ref:` resolves in the case loader, so preflight sees a ToolSpec and
    # refuses it with the same message -- nothing product-specific to add.
    from skill_lens.cases.loader import parse_cases_file

    (tmp_path / "lib.yaml").write_text("tools:\n  - name: lookup\n", encoding="utf-8")
    path = tmp_path / "ping.eval.yaml"
    path.write_text(
        "tool_libraries: [lib.yaml]\ncases:\n  - name: uses tools\n    task: t\n"
        "    tools:\n      - ref: lookup\n",
        encoding="utf-8",
    )
    (case,) = parse_cases_file(path)
    with pytest.raises(ProductSetupError, match="case 'uses tools' of skill 'ping' declares tools:"):
        ProductRunner(_product()).preflight([_skill()], {"ping": [case]})
```

Append to `tests/test_mcp_import.py`:

```python
def test_the_block_is_a_library_an_eval_file_can_import(tmp_path: Path):
    # `mcp-import tools.json > shared-tools/gh.yaml` is the whole import
    # step: the rendered block is a tool library as printed, once the
    # placeholders are filled.
    shared = tmp_path / "shared-tools"
    shared.mkdir()
    block = _render([PULL_REQUEST, ISSUES]).replace(RETURNS_PLACEHOLDER, "'{\"number\": 1}'")
    (shared / "gh.yaml").write_text(block, encoding="utf-8")
    skill_dir = tmp_path / "gh"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: gh\n---\nbody\n", encoding="utf-8")
    (skill_dir / "gh.eval.yaml").write_text(
        "tool_libraries: [../shared-tools/gh.yaml]\n"
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - ref: get_pull_request\n      - ref: list-issues\n"
        "    trajectory:\n      called: [list-issues]\n",
        encoding="utf-8",
    )
    skill = Skill(name="gh", description="d", instructions="body", path=skill_dir)
    (case,) = load_cases_for_skill(skill)
    pr, issues = (build_mock_tool(tool) for tool in case.tools)
    assert pr.json_schema == PULL_REQUEST["inputSchema"]
    assert issues.json_schema == ISSUES["inputSchema"]
```

(Check the fixture names `PULL_REQUEST`, `ISSUES`, `_render`, `RETURNS_PLACEHOLDER` and
the tool names they carry against the top of that file; `get_pull_request` and
`list-issues` are what the existing round-trip test uses.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_case_loader.py -k "ref or librar or anchored" tests/test_product_preflight.py -k referenced tests/test_mcp_import.py -k library -v`
Expected: FAIL — `EvalCase.model_validate` refuses `ref` as an extra field on `ToolSpec`
("tools.0.name Field required" / "ref Extra inputs are not permitted").

- [ ] **Step 3: Implement**

In `src/skill_lens/cases/loader.py`, add the imports:

```python
import copy

from pydantic import ValidationError  # already imported

from skill_lens.cases.tool_libraries import (
    EMPTY_LIBRARY,
    TOOL_LIBRARIES_KEY,
    ToolLibrary,
    ToolLibraryError,
    load_tool_libraries,
)
from skill_lens.models import EvalCase, Skill, ToolRef
```

In `parse_cases_file`, after the `raw_cases` list check and before the loop, load the
libraries; in the loop, resolve refs after the sentinel check:

```python
    raw_cases = data["cases"]
    if not isinstance(raw_cases, list):
        raise CaseParseError(f"{path}: 'cases' must be a list")
    library = _load_tool_libraries(path, data)
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases):
        _reject_unfilled(path, index, raw)
        raw = _resolve_tool_refs(path, index, raw, library)
        try:
            case = EvalCase.model_validate(raw)
        ...
```

Add the two helpers after `_reject_unfilled`:

```python
def _load_tool_libraries(path: Path, data: dict) -> ToolLibrary:
    """The tools this file's `tool_libraries:` imports; `EMPTY_LIBRARY` when
    the key is absent, so a `ref:` can say "add the key".

    Paths resolve against the eval file's own directory, never the working
    directory. A library error is re-raised naming this file too: the
    library is the file to fix, this is the file that imported it.
    """
    if TOOL_LIBRARIES_KEY not in data:
        return EMPTY_LIBRARY
    try:
        return load_tool_libraries(data[TOOL_LIBRARIES_KEY], relative_to=path.parent)
    except ToolLibraryError as exc:
        raise CaseParseError(f"{path}: {exc}") from exc


def _resolve_tool_refs(path: Path, index: int, raw: object, library: ToolLibrary) -> object:
    """Replace every `- ref: <name>` in the case's `tools:` with the tool the
    library declares, keeping the case's own `returns:` when it set one.

    Runs on the raw mapping, before `EvalCase.model_validate`: `ToolSpec`
    forbids unknown keys and requires a name, so a ref is not a ToolSpec and
    must never become one half-built. Resolving here is what keeps
    `EvalCase.tools` a list of `ToolSpec` for every runner, evaluator and
    preflight downstream. Nothing is mutated: a YAML anchor can alias one
    `tools:` list into several cases, so the result is a new mapping with a
    new list. Anything that is not a mapping with a `ref` key is kept as
    written for the model to judge.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
        return raw
    tools: list[object] = []
    for position, entry in enumerate(raw["tools"]):
        if not isinstance(entry, dict) or "ref" not in entry:
            tools.append(entry)
            continue
        where = f"{path}: case #{index + 1} tool #{position + 1}"
        try:
            ref = ToolRef.model_validate(entry)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise CaseParseError(
                f"{where}: invalid ref: entry ({fields}): a ref: may carry only returns: "
                f"beside it; the library declares the tool's name, description and schema."
            ) from exc
        try:
            spec = library.resolve(ref.ref)
        except ToolLibraryError as exc:
            raise CaseParseError(f"{where} {exc}") from exc
        resolved = copy.deepcopy(spec.model_dump())
        if ref.returns is not None:
            resolved["returns"] = ref.returns
        tools.append(resolved)
    return {**raw, "tools": tools}
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/skill_lens/cases/loader.py tests/test_case_loader.py tests/test_product_preflight.py tests/test_mcp_import.py
git commit -m "feat: resolve ref: tools from the libraries an eval file imports"
```

---

### Task 6: Dogfood in the example and point the scaffold at it

**Files:**
- Create: `examples/shared-tools/order-api.yaml`
- Modify: `examples/order-support/order-support.eval.yaml`, `src/skill_lens/scaffold.py`
  (`_TEMPLATE`, the tools comment at lines 43–48)
- Test: `tests/test_examples.py`, `tests/test_scaffold.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_examples.py`:

```python
def test_order_support_pulls_its_tools_from_the_shared_library():
    # The four tool-bearing cases share two contracts and set their own
    # scenario through `returns:` -- the case the library feature exists for.
    skill = next(s for s in load_skills(EXAMPLES / "order-support"))
    cases = {case.name: case for case in load_cases_for_skill(skill)}
    refuses = cases["refuses a refund outside the return window"]
    refunds = cases["refunds an order inside the return window"]
    assert [t.name for t in refuses.tools] == ["lookup_order", "issue_refund"]
    assert refuses.tools[0].description == refunds.tools[0].description
    assert '"days_since_delivery": 45' in refuses.tools[0].returns
    assert '"days_since_delivery": 3' in refunds.tools[0].returns
    assert (EXAMPLES / "shared-tools" / "order-api.yaml").is_file()
```

Append to `tests/test_scaffold.py`:

```python
def test_the_scaffold_points_at_tool_libraries_for_a_shared_tool():
    assert "tool_libraries" in render_scaffold(SKILL)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_examples.py tests/test_scaffold.py -q`
Expected: the two new tests FAIL (no library file; no `tool_libraries` in the scaffold)

- [ ] **Step 3: Create the library and convert the example**

`examples/shared-tools/order-api.yaml`:

```yaml
# The order API the order-support skill fronts. A tool library: the block a
# case's `tools:` takes, declared once and imported by any eval file with
#
#   tool_libraries:
#     - ../shared-tools/order-api.yaml
#
# Each case names a tool with `- ref: lookup_order` and sets its own
# `returns:` -- the library is the contract, the case is the scenario.
# `skill-lens mcp-import tools.json > shared-tools/<server>.yaml` writes a
# file of this shape from a real MCP server's listing.
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000", "status": "delivered", "days_since_delivery": 0}'
  - name: issue_refund
    description: Issue a refund for an order
    parameters:
      order_id: string
    returns: '{"ok": true}'
```

`examples/order-support/order-support.eval.yaml` — add the import after the header
comment and replace each inline block:

```yaml
# Five cases on the same skill, covering three concerns:
#   - trajectory: did it look the order up before deciding -- one case where
#     the policy forbids a refund, one where it allows it. Invisible to an
#     output assertion.
#   - judge: "explains it plainly" is not a substring, so a rubric-based
#     LLM judge grades it instead.
#   - triggering (`mode: offered`): does the agent even reach for the skill,
#     including the negative control that proves it isn't firing on everything.
#
# The two mock tools are declared once, in ../shared-tools/order-api.yaml, and
# each case sets only what its scenario returns.
tool_libraries:
  - ../shared-tools/order-api.yaml

cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tags: [smoke, refund]
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
      - ref: issue_refund
    trajectory:
      called: [lookup_order]
      forbidden: [issue_refund]
      max_calls: 3
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
    assertions:
      - kind: contains
        value: "1234"

  - name: refunds an order inside the return window
    task: Please refund order 5678
    tags: [refund]
    tools:
      - ref: lookup_order
        returns: '{"id": "5678", "status": "delivered", "days_since_delivery": 3}'
      - ref: issue_refund
    trajectory:
      order: [lookup_order, issue_refund]
      max_calls: 4
    budget:
      max_tokens: 2000
      max_cost_usd: 0.01
    assertions:
      - kind: contains
        value: "5678"

  # The judge earns its keep where an assertion cannot: "explains it plainly"
  # is not a substring.
  - name: explains the refusal in plain language
    task: I want a refund for order 1234
    tags: [refund, judged]
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
    judge:
      expected: A short, plain-language refusal that names the order id.
      rubric:
        - The reply names order 1234
        - The reply explains that the return window has closed
        - The reply does not promise a refund

  # Triggering, both directions. Run only the positive and a skill that fires
  # on everything scores 100% -- which is why the negative control ships too.
  - name: reaches for the skill on a refund question
    mode: offered
    task: I want a refund for order 1234
    tags: [triggering]
    tools:
      - ref: lookup_order
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
    trajectory:
      skill_triggered: true

  - name: leaves an unrelated question alone
    mode: offered
    task: What's the capital of Egypt?
    tags: [triggering]
    trajectory:
      skill_triggered: false
```

In `src/skill_lens/scaffold.py`, extend the tools comment in `_TEMPLATE`:

```
  #    For a tool a real MCP server exposes, `skill-lens mcp-import tools.json`
  #    writes this block from the server's own schema instead. A tool several
  #    skills share can live once in a library file each eval file imports
  #    with `tool_libraries:` and names with `- ref: <name>`.
```

- [ ] **Step 4: Run the tests and the CI self-check**

Run: `uv run pytest tests/test_examples.py tests/test_scaffold.py tests/test_cassettes.py -q && uv run skill-lens list ./examples`
Expected: PASS; `list` prints four skills with `order-support 5 case(s)`.

- [ ] **Step 5: Commit**

```bash
git add examples/shared-tools/order-api.yaml examples/order-support/order-support.eval.yaml src/skill_lens/scaffold.py tests/test_examples.py tests/test_scaffold.py
git commit -m "feat: share the order-support example's mock tools through a library"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/eval-files.md`, `docs/cli.md`, `docs/runners.md`,
  `skills/writing-skill-evals/references/eval-file-syntax.md`, `ARCHITECTURE.md`,
  `CLAUDE.md`, `README.md`

- [ ] **Step 1: `docs/eval-files.md`**

Change the opening paragraph's last sentence to:

> Extra keys alongside `cases:` at the top level of the file are ignored, with one
> exception skill-lens reads: `tool_libraries:` — see [Sharing tools across eval
> files](#sharing-tools-across-eval-files).

Add after the "Mock tools" section's last paragraph (the tool-name rule), before
"## Judging output quality":

```markdown
### Sharing tools across eval files

Several skills often front one API — one MCP server behind nine skills — and every eval
file that mocks `get_pull_request` would otherwise carry the same block. A **tool
library** declares a tool once. It is a YAML file with one top-level `tools:` list, the
same block a case takes, and exactly what [`skill-lens mcp-import`](cli.md#mcp-import)
prints — so `skill-lens mcp-import tools.json > shared-tools/server.yaml` writes one:

```yaml
# shared-tools/order-api.yaml
tools:
  - name: lookup_order
    description: Look up an order by its id
    parameters:
      order_id: string
    returns: '{"id": "0000", "status": "delivered", "days_since_delivery": 0}'
```

An eval file imports libraries with `tool_libraries:` beside `cases:`, and a case names a
tool with `ref:`:

```yaml
tool_libraries:
  - ../../shared-tools/order-api.yaml    # relative to this file; a directory imports every .yaml/.yml in it

cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tools:
      - ref: lookup_order                # the library's name, description and schema
        returns: '{"id": "1234", "status": "delivered", "days_since_delivery": 45}'
```

The library owns the tool's **contract** — name, description, `parameters:` or
`input_schema:` — and the case owns the **scenario**: a `ref:` may set `returns:` and
nothing else. Any other key beside `ref:` is an authoring error naming it; a case that
needs a different contract declares the tool inline, and a `ref:` may sit in the same list
as inline tools. After loading, the case is exactly what it would have been with the
library's block pasted in: `trajectory:` names, the six reserved built-in names and the
`mode: offered` collision rule all read the resolved tool, and every runner sees a plain
mock.

Library paths are relative to the eval file — never to the working directory, never
absolute — so the same file loads identically under discovery, under `--evals`, under
`skill-lens list` and from any checkout. A library is checked as an eval file is: an
unfilled `TODO(skill-lens)`, a tool name outside the rule, an unknown key, or an invalid
schema is an authoring error (exit `2`) naming the library file and the tool's position.
So is an unresolvable `ref:` — no `tool_libraries:` key, a name no import declares (the
message lists the names they do), a missing file — a name two imported files both declare
(never resolved by position), or one file imported twice.
```

In "Where eval files are found", append a paragraph:

> A `tool_libraries:` entry resolves against the eval file's own directory whichever way
> the file was found, so `--evals` does not change where a library is looked for.

- [ ] **Step 2: `docs/cli.md`**

In the `mcp-import` section, after the "Stdout carries nothing but the block" paragraph:

> The block is also a [tool library](eval-files.md#sharing-tools-across-eval-files) as
> printed: `skill-lens mcp-import tools.json > shared-tools/server.yaml`, fill the
> placeholders, then import it from each eval file with `tool_libraries:` and name a tool
> with `- ref: <name>` — one contract for every skill that fronts the server.

- [ ] **Step 3: `docs/runners.md`**

In "Declaring tools and scoring the trajectory", after the paragraph ending "writes one
from the server's own `tools/list` listing.":

> A tool declared in a [tool library](eval-files.md#sharing-tools-across-eval-files) and
> named with `ref:` is resolved by the case loader before any runner is involved, so a
> runner never sees a reference — only the `ToolSpec` it named, with the case's own
> `returns:`.

- [ ] **Step 4: `skills/writing-skill-evals/references/eval-file-syntax.md`**

In "## Tools", after the paragraph ending "`returns:` still has to be filled in by hand.":

```markdown
A tool several skills share is declared once in a **tool library** — a YAML file with a
top-level `tools:` list, the block above, which is also what `mcp-import` prints. The eval
file imports it with `tool_libraries:` (paths relative to the eval file; a directory
imports every `.yaml`/`.yml` in it) and a case names a tool with `ref:`, setting only
its own `returns:`:

```yaml
tool_libraries:
  - ../../shared-tools/order-api.yaml
cases:
  - name: refuses a refund outside the return window
    task: I want a refund for order 1234
    tools:
      - ref: lookup_order           # name, description, schema from the library
        returns: '{"id": "1234", "days_since_delivery": 45}'   # this case's scenario
```

A `ref:` may carry `returns:` and nothing else. An unknown name, a missing library, a name
two libraries both declare, or an absolute path is an authoring error (exit 2).
```

Do **not** add `tool_libraries` to the "Case fields" table: a test pins that table to
`EvalCase`'s fields, and this is a file-level key.

- [ ] **Step 5: `ARCHITECTURE.md`**

Module map — add two rows after `cases/loader.py`:

```markdown
| `cases/checks.py` | The checks an eval file and a tool library share: the `TODO(skill-lens)` sentinel walk (`find_unfilled`, keys and values, cycle-safe) and `check_tool_schema` (`parameters`/`input_schema` exclusive, `check_schema`, top-level `type: object`). Below both loaders so neither imports the other. |
| `cases/tool_libraries.py` | A tool library is a YAML file with one top-level `tools:` list — the block `mcp-import` prints. `parse_tool_library` checks each tool as the case loader would and names the file and position in every refusal; `load_tool_libraries` resolves an eval file's `tool_libraries:` entries against the eval file's directory (file or directory, never absolute) into one `ToolLibrary` that refuses a name declared twice; `ToolLibrary.resolve` turns a `ref:` into its `ToolSpec` or says what to fix. Raises `ToolLibraryError`; the case loader wraps it with the importing file. |
```

Update the `cases/loader.py` row: "Finds and parses eval YAML for a skill into `EvalCase`
models; imports the file's `tool_libraries:` and resolves every `- ref:` into the
library's `ToolSpec` on the raw mapping, before validation."

Data flow — change the per-skill line to:

```
        └─ per skill: cases/loader (evals/ dir or *.eval.yaml; tool_libraries: → cases/tool_libraries; ref: resolved) ──► [EvalCase]
```

Add a section before "### Security checks":

```markdown
### Tool libraries

**`EvalCase.tools` holds only `ToolSpec`.** A `ref:` is resolved by `cases/loader.py` on
the raw case mapping, before `EvalCase.model_validate`: `ToolSpec` forbids unknown keys
and requires a name, so a reference is not a ToolSpec and must never become one
half-built. Resolving there is what keeps every runner, evaluator, reporter and product
preflight unchanged — the product runners' `tools:` refusal applies to a referenced tool
exactly as to an inline one, and the duplicate-name, built-in-name, offered-skill and
trajectory checks all read the resolved name.

**A `ref:` may set `returns:` and nothing else.** The library owns the contract (name,
description, schema); the case owns the scenario. Overriding the contract per case would
reintroduce the drift the library exists to remove; `ToolRef` (`extra="forbid"`) is what
refuses it.

**An unresolvable `ref:` is an authoring error at load time**, exit 2, before any case
runs: no `tool_libraries:` key (the message says to add one), an unknown name (the message
lists the names the imports declare), a missing, unreadable or malformed library. Never
an errored or failed case.

**A name two imported libraries declare is refused naming both files** — never resolved
by position — and so are one file declaring a name twice and one file imported twice
(listed twice, or a directory plus a file inside it). A runner named twice is refused
rather than de-duplicated for the same reason.

**Library paths are relative to the eval file, never the working directory, and never
absolute** (`is_absolute()`, `drive` or `root`, the three checks `check_relative_path`
makes — but `..` is allowed, because the library sits above the skill by design). The
same eval file resolves identically under discovery, `--evals`, `list` and from any
checkout.

**A library is checked as an eval file is**: the sentinel (keys and values), the
tool-name rule, unknown keys at the tool and at the top level, schema validity — each
refusal naming the library file and the tool's position, because that is the file to fix;
the case loader then prefixes the eval file that imported it.

**A library is a top-level `tools:` list and nothing else** — the block `mcp-import`
prints, so `mcp-import tools.json > shared-tools/server.yaml` is the whole import step.

**The resolver never mutates parsed YAML.** An anchored `tools:` list aliased into two
cases resolves in both; the resolver returns a new mapping with a new list.
```

- [ ] **Step 6: `CLAUDE.md`**

In "What this is", after the `mcp-import` sentence ending "in the new `ToolSpec.input_schema`,
and `ToolSpec.name` now accepts what providers accept (`^[A-Za-z0-9_-]{1,64}$`).":

> Tool libraries (issue #42) let a YAML file with one top-level `tools:` list — the block
> `mcp-import` prints — be imported by any eval file with `tool_libraries:` (paths relative
> to the eval file) and named in a case with `- ref: <name>`, which may set only `returns:`;
> the loader resolves every ref before validation, so `EvalCase.tools` still holds only
> `ToolSpec`. Its design is in
> `docs/superpowers/specs/2026-09-17-skill-lens-tool-libraries-design.md`.

Add to the invariants list, after the `mcp-import never touches the network` bullet:

```markdown
- **`EvalCase.tools` holds only `ToolSpec`; a `ref:` is resolved by the case loader on the
  raw mapping before validation.** No runner, evaluator, reporter or product preflight ever
  sees a reference; the product `tools:` refusal, the duplicate-name, built-in-name,
  offered-skill and trajectory checks all read the resolved tool.
- **A `ref:` may set `returns:` and nothing else** — the library owns the contract, the
  case owns the scenario. `ToolRef` is `extra="forbid"`.
- **An unresolvable `ref:` is an authoring error at load time** (exit 2): no
  `tool_libraries:` key, an unknown name (the message lists the declared names), a missing,
  unreadable or malformed library. A name two imported files declare is refused naming
  both; so is one file declaring a name twice or imported twice.
- **Library paths are relative to the eval file, never the working directory, never
  absolute**; `..` is allowed. A library is a top-level `tools:` list and nothing else, and
  is checked as an eval file is (sentinel, name rule, unknown keys, schema), each refusal
  naming the library file and the tool's position. The resolver never mutates parsed YAML.
```

- [ ] **Step 7: `README.md`**

After "A tool a real MCP server exposes need not be transcribed: ... schema and all.":

> A tool several skills share is declared once, in a tool library any eval file imports
> with `tool_libraries:` and names with `- ref: <name>`.

- [ ] **Step 8: Verify the docs**

```bash
uv sync --group docs
uv run mkdocs build --strict
uv run pytest tests/test_docs.py tests/test_shipped_skill.py tests/test_naming.py -q
```

Expected: build clean, tests PASS.

- [ ] **Step 9: Commit**

```bash
git add docs/eval-files.md docs/cli.md docs/runners.md skills/writing-skill-evals/references/eval-file-syntax.md ARCHITECTURE.md CLAUDE.md README.md
git commit -m "docs: document tool libraries and the ref: form of a mock tool"
```

---

### Task 8: Final verification and the pull request

- [ ] **Step 1: Full suite, lint, format check, audit**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run skill-lens list ./examples
```

Expected: everything green; `list` shows four skills.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin HEAD
gh pr create --assignee EmadMokhtar \
  --title "feat: share mock tools across eval files through tool libraries" \
  --body "$(cat <<'EOF'
## Summary

- A **tool library** is a YAML file with one top-level `tools:` list — the block `skill-lens mcp-import` prints — so `mcp-import tools.json > shared-tools/server.yaml` writes one.
- An eval file imports libraries with `tool_libraries:` (paths relative to the eval file; a file or a directory) and a case names a tool with `- ref: <name>`, which may set only `returns:`: the library owns the contract, the case owns the scenario.
- `cases/loader.py` resolves every `ref:` on the raw mapping before `EvalCase.model_validate`, so `EvalCase.tools` still holds only `ToolSpec` and no runner, evaluator, reporter or product preflight changes.
- Every mistake is an authoring error (exit 2) before any case runs: an unknown name lists what the imports declare, a name two files declare names both, an absolute path is refused, a library is checked as an eval file is.
- The order-support example now shares its two mocks through `examples/shared-tools/order-api.yaml`.

Design: `docs/superpowers/specs/2026-09-17-skill-lens-tool-libraries-design.md`.

Closes #42

## Test plan

- [ ] `uv run pytest` — new `tests/test_checks.py`, `tests/test_tool_libraries.py`, and cases in the loader, models, product-preflight, mcp-import, examples and scaffold suites
- [ ] `uv run mkdocs build --strict` and `uv run pytest tests/test_docs.py`
- [ ] `uv run skill-lens list ./examples`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
