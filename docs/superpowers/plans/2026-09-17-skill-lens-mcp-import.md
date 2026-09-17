# skill-lens `mcp-import` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `skill-lens mcp-import` command that turns a saved MCP `tools/list` response
into mock-tool YAML carrying the server's real schema, plus the `ToolSpec.input_schema`
field that lets a case declare that schema verbatim.

**Architecture:** One new pure module, `src/skill_lens/mcp_import.py` (text in, text out,
no IO), wired to a Typer command in `cli.py` that does the file/stdin reading. `ToolSpec`
gains an optional `input_schema` (exclusive with `parameters`) validated in the case
loader; `build_mock_tool` passes it to the agent verbatim. Everything is offline.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, PyYAML (`yaml.safe_dump`), `jsonschema`
(`Draft202012Validator.check_schema`), pytest with `typer.testing.CliRunner`.

**Spec:** `docs/superpowers/specs/2026-09-17-skill-lens-mcp-import-design.md`

## Global Constraints

- Tool name rule: `^[A-Za-z0-9_-]{1,64}$` — what OpenAI and Anthropic accept. Never rewrite a name.
- `returns:` on an imported mock is always `TODO(skill-lens) the JSON this tool returns`; a missing description is `TODO(skill-lens) what this tool does`. Both sentinels start with `cases.loader.UNFILLED_SENTINEL`.
- `input_schema` reaches the agent verbatim: no `additionalProperties: false`, no reordering. Only the `parameters:` shorthand is closed.
- `mcp-import` never touches the network. `SOURCE` is a file path or `-` (stdin).
- Exit codes: `0` success, `2` any user error. Error messages from `mcp-import` go to **stderr** (`typer.echo(..., err=True)`) so `> tools.yaml` never captures one.
- No agent-framework import anywhere in this change (`tests/test_framework_isolation.py`).
- `skill_lens` (underscore) never appears in user-facing output (`tests/test_naming.py`).
- Every file read pins `encoding="utf-8"`. YAML loading goes through `yaml_loading.safe_load`.
- Ruff line length 100; `S` rules on. Tests are offline and deterministic.
- Conventional Commits on every commit; the PR title is `feat: import mock tools from an MCP server's tools/list listing` and its body carries `Closes #43`.
- Documentation ships in the same PR: `docs/cli.md`, `docs/eval-files.md`, `docs/runners.md`, `skills/writing-skill-evals/references/eval-file-syntax.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `README.md`.
- Run every command from the worktree root: `/Users/emadmokhtar/Projects/skill-evaluator/.claude/worktrees/github-issue-43-624019`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/skill_lens/models.py` (modify) | `ToolSpec.input_schema`; the provider name rule replaces `isidentifier()`. |
| `src/skill_lens/cases/loader.py` (modify) | Load-time checks: `parameters`/`input_schema` exclusive; `input_schema` is a valid JSON Schema of `type: object`. |
| `src/skill_lens/runners/tools.py` (modify) | `build_mock_tool` passes a declared `input_schema` through verbatim (deep copy). |
| `src/skill_lens/mcp_import.py` (create) | `McpImportError`, `ImportedTool`, `parse_tools_list`, `render_tool_mocks`. Pure. |
| `src/skill_lens/cli.py` (modify) | The `mcp-import` command: reads the file or stdin, prints YAML, exits 0/2. |
| `src/skill_lens/scaffold.py` (modify) | One comment line pointing at `mcp-import`. |
| `tests/test_models.py`, `tests/test_case_loader.py`, `tests/test_tools.py`, `tests/test_scaffold.py` (modify) | Tests beside the code they cover. |
| `tests/test_mcp_import.py`, `tests/test_cli_mcp_import.py` (create) | The pure module; the command. |
| Docs listed above (modify) | Ship with the change. |

---

### Task 1: `ToolSpec` — the provider name rule and `input_schema`

**Files:**
- Modify: `src/skill_lens/models.py:204-224`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `ToolSpec.input_schema: dict[str, Any] | None = None`; module constant `TOOL_NAME_PATTERN: re.Pattern[str]` (compiled `^[A-Za-z0-9_-]{1,64}$`). Later tasks import `TOOL_NAME_PATTERN` only for messages; `ToolSpec` validation is what they rely on.

- [ ] **Step 1: Write the failing tests**

Replace `test_tool_spec_rejects_a_name_that_is_not_an_identifier` in `tests/test_models.py` with the four tests below, and add the two `input_schema` tests after `test_tool_spec_accepts_a_full_declaration`.

```python
def test_tool_spec_accepts_a_hyphenated_name():
    # MCP servers routinely name tools `get-pull-request`. A mock must answer to
    # the name the live server uses, and both providers accept a hyphen.
    assert ToolSpec(name="get-pull-request").name == "get-pull-request"


@pytest.mark.parametrize("name", ["look up", "a.b", "café", "", "x" * 65])
def test_tool_spec_rejects_a_name_no_provider_would_register(name):
    # ^[A-Za-z0-9_-]{1,64}$ is the rule OpenAI and Anthropic enforce. A name
    # outside it could never be registered, so it is an authoring error.
    with pytest.raises(ValidationError, match="tool name must match"):
        ToolSpec(name=name)


def test_tool_spec_accepts_a_sixty_four_character_name():
    assert len(ToolSpec(name="x" * 64).name) == 64


def test_tool_spec_carries_an_input_schema():
    schema = {"type": "object", "properties": {"owner": {"type": "string"}}}
    spec = ToolSpec(name="get_pull_request", input_schema=schema)
    assert spec.input_schema == schema
    assert spec.parameters == {}


def test_tool_spec_input_schema_defaults_to_none():
    assert ToolSpec(name="ping").input_schema is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_models.py -k "tool_spec" -v`
Expected: the hyphen test fails with a `ValidationError` (identifier rule); the `input_schema` tests fail with `extra_forbidden`; the parametrized test fails on `match=`.

- [ ] **Step 3: Change the model**

In `src/skill_lens/models.py`, add `import re` as the first stdlib import (before `from pathlib import Path`; ruff's import sorting puts plain `import` lines first), add the pattern beside the other module-level names (after `SandboxBackend = ...`):

```python
# What OpenAI and Anthropic accept as a tool name. A mock is registered under
# its name verbatim, so a name outside this rule could never reach the model.
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
```

and replace the `ToolSpec` class body:

```python
class ToolSpec(BaseModel):
    """A mock tool an eval case makes available to the agent.

    Nothing executes: calling the tool records the call and returns `returns`
    verbatim, so the trajectory is genuinely the model's choice and a run has
    no side effects.

    `parameters` is the shorthand for a tool the author describes by hand: a
    flat name -> primitive type map that `build_mock_tool` closes with
    `additionalProperties: false`. `input_schema` is for a tool that must
    match a real server's declared JSON Schema -- optional arguments, enums,
    arrays, nested objects -- and reaches the agent verbatim. A tool declares
    one or the other; the case loader refuses both.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, ToolParamType] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    returns: str = ""

    @field_validator("name")
    @classmethod
    def _must_be_a_provider_tool_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                f"tool name must match {TOOL_NAME_PATTERN.pattern} (what providers "
                f"accept), got {value!r}"
            )
        return value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_models.py tests/test_tools.py tests/test_case_loader.py -q`
Expected: all pass. (If any existing test built a `ToolSpec` with a non-ASCII or over-long name, it is now wrong per the spec — change the name in that test, not the rule.)

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/models.py tests/test_models.py
git commit -m "feat(models): let a mock tool carry a JSON Schema and a hyphenated name

A mock standing in for a real MCP tool must keep the name and the argument
schema the live server declares. isidentifier() rejected the hyphen every
MCP server uses, and the flat parameters map cannot express an optional
argument or a nested object.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Loader checks for `input_schema`

**Files:**
- Modify: `src/skill_lens/cases/loader.py:131-169` (add a `_validate_tools` function beside `_validate_assertions`) and `:104-105` (call it)
- Test: `tests/test_case_loader.py`

**Interfaces:**
- Consumes: `ToolSpec.input_schema` from Task 1.
- Produces: `CaseParseError` messages of the form `{path}: case {name!r} tool {tool!r} ...` for the three new mistakes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_case_loader.py` (the module already imports `CaseParseError`, `parse_cases_file`, `pytest`, and defines `_write(tmp_path, body)`):

```python
INPUT_SCHEMA_CASE = """cases:
  - name: n
    task: t
    tools:
      - name: get-pull-request
        description: Get a pull request
        input_schema:
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
          required: [owner]
        returns: '{"number": 1}'
    trajectory:
      called: [get-pull-request]
"""


def test_a_tool_may_declare_an_input_schema(tmp_path):
    cases = parse_cases_file(_write(tmp_path, INPUT_SCHEMA_CASE))
    tool = cases[0].tools[0]
    assert tool.name == "get-pull-request"
    assert tool.input_schema["required"] == ["owner"]
    assert tool.parameters == {}


def test_a_tool_may_not_declare_both_parameters_and_an_input_schema(tmp_path):
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        parameters: {q: string}\n"
        "        input_schema: {type: object}\n",
    )
    with pytest.raises(CaseParseError, match="tool 'lookup' declares both parameters and"):
        parse_cases_file(path)


def test_an_input_schema_that_is_not_an_object_is_rejected(tmp_path):
    # Every provider requires a tool's arguments to be an object.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        input_schema: {type: string}\n",
    )
    with pytest.raises(
        CaseParseError, match="tool 'lookup' input_schema must declare type: object"
    ):
        parse_cases_file(path)


def test_a_malformed_input_schema_is_rejected_at_load_time(tmp_path):
    # Caught before any case runs, like an assertion's json_schema.
    path = _write(
        tmp_path,
        "cases:\n  - name: n\n    task: t\n    tools:\n"
        "      - name: lookup\n        input_schema: {type: 5}\n",
    )
    with pytest.raises(CaseParseError, match="tool 'lookup' has an invalid input_schema"):
        parse_cases_file(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_case_loader.py -k "input_schema" -v`
Expected: the first passes already (the model accepts the field); the other three fail because no `CaseParseError` is raised.

- [ ] **Step 3: Add the loader check**

In `src/skill_lens/cases/loader.py`, add after `_validate_assertions`:

```python
def _validate_tools(path: Path, case: EvalCase) -> None:
    """Check each mock tool's declared schema at load time.

    A tool declares its arguments one of two ways -- the `parameters:`
    shorthand or a full `input_schema:` -- never both, and a declared schema
    has to be one a provider would register: valid JSON Schema whose top
    level is an object. All three mistakes are the author's, so they abort
    before any case runs rather than surface as an errored case.
    """
    for tool in case.tools:
        where = f"{path}: case {case.name!r} tool {tool.name!r}"
        if tool.input_schema is None:
            continue
        if tool.parameters:
            raise CaseParseError(
                f"{where} declares both parameters and input_schema; choose one."
            )
        try:
            Draft202012Validator.check_schema(tool.input_schema)
        except SchemaError as exc:
            raise CaseParseError(f"{where} has an invalid input_schema: {exc.message}") from exc
        if tool.input_schema.get("type") != "object":
            raise CaseParseError(
                f"{where} input_schema must declare type: object; a tool's arguments "
                f"are always an object."
            )
```

and call it in `parse_cases_file` after `_validate_assertions(path, case)`:

```python
        _validate_assertions(path, case)
        _validate_tools(path, case)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_case_loader.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/cases/loader.py tests/test_case_loader.py
git commit -m "feat(cases): validate a mock tool's input_schema at load time

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `build_mock_tool` passes a declared schema through verbatim

**Files:**
- Modify: `src/skill_lens/runners/tools.py:37-61`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `ToolSpec.input_schema` (Task 1).
- Produces: `build_mock_tool(spec).json_schema` is a deep copy of `spec.input_schema` when set; the existing closed derivation otherwise.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools.py` after `test_a_tool_with_no_parameters_still_has_a_valid_schema`:

```python
def test_a_declared_input_schema_reaches_the_agent_verbatim():
    # The schema is the point: a mock standing in for a real MCP tool must show
    # the model exactly what the live server would, optional arguments included.
    schema = {
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["owner"],
    }
    tool = build_mock_tool(ToolSpec(name="get-pull-request", input_schema=schema))
    assert tool.json_schema == schema
    assert "additionalProperties" not in tool.json_schema


def test_a_declared_input_schema_is_copied_not_shared():
    schema = {"type": "object", "properties": {}}
    spec = ToolSpec(name="ping", input_schema=schema)
    tool = build_mock_tool(spec)
    tool.json_schema["properties"]["injected"] = {"type": "string"}
    assert spec.input_schema == {"type": "object", "properties": {}}


def test_a_tool_with_an_input_schema_still_returns_the_canned_value():
    tool = build_mock_tool(
        ToolSpec(name="ping", input_schema={"type": "object"}, returns="pong")
    )
    assert tool.call(anything="at all") == "pong"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k "input_schema" -v`
Expected: the first two fail (`json_schema` is the derived closed schema); the third passes already.

- [ ] **Step 3: Change `build_mock_tool`**

Add `import copy` to `src/skill_lens/runners/tools.py` immediately before `from collections.abc import Callable`, and replace the function:

```python
def build_mock_tool(spec: ToolSpec) -> AgentTool:
    """Turn a declared ToolSpec into a callable plus its JSON schema.

    Two shapes, deliberately asymmetric. The `parameters:` shorthand is closed
    (`additionalProperties: false`, every key required) because the author
    wrote every key. A declared `input_schema` is passed verbatim -- deep
    copied so an adapter cannot mutate the case -- because fidelity to the
    server it stands in for is its reason to exist. Types in the shorthand
    are already constrained by `ToolSpec`, and the loader has already checked
    a declared schema, so nothing here can be rejected.
    """
    if spec.input_schema is not None:
        json_schema: dict[str, Any] = copy.deepcopy(spec.input_schema)
    else:
        properties = {name: {"type": type_name} for name, type_name in spec.parameters.items()}
        json_schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    returns = spec.returns

    def call(**_arguments: Any) -> str:
        """Return the canned value, whatever the model passed in."""
        return returns

    return AgentTool(
        name=spec.name,
        description=spec.description,
        json_schema=json_schema,
        call=call,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py tests/test_pydantic_ai_runner.py tests/test_langchain_runner.py -q`
Expected: all pass (the adapter tests prove nothing downstream cared about the schema's shape).

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/runners/tools.py tests/test_tools.py
git commit -m "feat(runners): hand a declared input_schema to the agent verbatim

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `mcp_import.parse_tools_list`

**Files:**
- Create: `src/skill_lens/mcp_import.py`
- Create: `tests/test_mcp_import.py`

**Interfaces:**
- Consumes: `ToolSpec` (Task 1), `cases.loader.UNFILLED_SENTINEL`.
- Produces:
  - `class McpImportError(ValueError)`
  - `@dataclass(frozen=True) class ImportedTool: spec: ToolSpec; output_schema: dict[str, Any] | None = None`
  - `DESCRIPTION_PLACEHOLDER = "TODO(skill-lens) what this tool does"`, `RETURNS_PLACEHOLDER = "TODO(skill-lens) the JSON this tool returns"`
  - `parse_tools_list(text: str, *, source: str) -> list[ImportedTool]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_import.py`:

```python
"""`mcp-import`: an MCP server's tools/list listing becomes mock-tool YAML."""

from __future__ import annotations

import json

import pytest

from skill_lens.cases.loader import UNFILLED_SENTINEL
from skill_lens.mcp_import import (
    DESCRIPTION_PLACEHOLDER,
    RETURNS_PLACEHOLDER,
    McpImportError,
    parse_tools_list,
)

PULL_REQUEST = {
    "name": "get_pull_request",
    "description": "Get details of a specific pull request",
    "inputSchema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repository owner"},
            "pull_number": {"type": "integer"},
        },
        "required": ["owner", "pull_number"],
    },
    "outputSchema": {"type": "object", "properties": {"number": {"type": "integer"}}},
}
ISSUES = {
    "name": "list-issues",
    "description": "List issues",
    "inputSchema": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed"]},
            "labels": {"type": "array", "items": {"type": "string"}},
            "filter": {"type": "object", "properties": {"since": {"type": "string"}}},
        },
    },
}


def _parse(payload: object) -> list:
    return parse_tools_list(json.dumps(payload), source="tools.json")


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [PULL_REQUEST, ISSUES]}},
        {"tools": [PULL_REQUEST, ISSUES]},
        [PULL_REQUEST, ISSUES],
    ],
    ids=["envelope", "result", "array"],
)
def test_the_three_saved_shapes_parse_to_the_same_tools(payload):
    names = [tool.spec.name for tool in _parse(payload)]
    assert names == ["get_pull_request", "list-issues"]


def test_the_input_schema_is_copied_verbatim():
    (tool, _) = _parse([PULL_REQUEST, ISSUES])
    assert tool.spec.input_schema == PULL_REQUEST["inputSchema"]
    assert "additionalProperties" not in tool.spec.input_schema


def test_nested_objects_arrays_and_enums_survive():
    (_, tool) = _parse([PULL_REQUEST, ISSUES])
    assert tool.spec.input_schema == ISSUES["inputSchema"]


def test_the_parsed_schema_shares_no_state_with_the_input():
    payload = json.loads(json.dumps([PULL_REQUEST]))
    (tool,) = parse_tools_list(json.dumps(payload), source="x")
    tool.spec.input_schema["properties"]["injected"] = {}
    assert "injected" not in PULL_REQUEST["inputSchema"]["properties"]


def test_a_hyphenated_name_is_kept():
    (_, tool) = _parse([PULL_REQUEST, ISSUES])
    assert tool.spec.name == "list-issues"


def test_the_description_is_copied():
    (tool, _) = _parse([PULL_REQUEST, ISSUES])
    assert tool.spec.description == "Get details of a specific pull request"


@pytest.mark.parametrize("description", [None, "", "   ", 7], ids=["absent", "empty", "blank", "int"])
def test_a_missing_description_becomes_the_placeholder(description):
    entry = {**PULL_REQUEST, "description": description}
    if description is None:
        del entry["description"]
    (tool,) = _parse([entry])
    assert tool.spec.description == DESCRIPTION_PLACEHOLDER
    assert tool.spec.description.startswith(UNFILLED_SENTINEL)


def test_returns_is_always_the_placeholder():
    # The listing says nothing about what a call returns; inventing a value
    # would be the silent drift the import exists to remove.
    for tool in _parse([PULL_REQUEST, ISSUES]):
        assert tool.spec.returns == RETURNS_PLACEHOLDER
        assert tool.spec.returns.startswith(UNFILLED_SENTINEL)


def test_the_output_schema_is_kept_when_declared():
    (pr, issues) = _parse([PULL_REQUEST, ISSUES])
    assert pr.output_schema == PULL_REQUEST["outputSchema"]
    assert issues.output_schema is None


def test_an_output_schema_that_is_not_an_object_is_ignored():
    (tool,) = _parse([{**PULL_REQUEST, "outputSchema": "nope"}])
    assert tool.output_schema is None


def test_unknown_entry_keys_are_ignored():
    (tool,) = _parse([{**PULL_REQUEST, "title": "PR", "annotations": {"readOnlyHint": True}}])
    assert tool.spec.name == "get_pull_request"


def test_an_empty_listing_parses_to_nothing():
    assert _parse({"tools": []}) == []


def test_invalid_json_names_the_source():
    with pytest.raises(McpImportError, match=r"tools\.json: not valid JSON"):
        parse_tools_list("{not json", source="tools.json")


def test_an_error_response_is_refused_quoting_the_message():
    payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    with pytest.raises(McpImportError, match="Method not found"):
        _parse(payload)


@pytest.mark.parametrize(
    "payload",
    [{"jsonrpc": "2.0"}, {"result": {}}, {"tools": "nope"}, "text", 42, None],
    ids=["no-result", "result-without-tools", "tools-not-a-list", "string", "number", "null"],
)
def test_an_unknown_shape_names_the_accepted_ones(payload):
    with pytest.raises(McpImportError, match=r"expected .*\"tools\": \[\.\.\.\]"):
        _parse(payload)


def test_a_tool_without_an_input_schema_is_refused_by_name():
    entry = {k: v for k, v in PULL_REQUEST.items() if k != "inputSchema"}
    with pytest.raises(McpImportError, match="tool 'get_pull_request' has no inputSchema"):
        _parse([entry])


def test_a_tool_whose_input_schema_is_not_an_object_is_refused():
    with pytest.raises(McpImportError, match="tool 'get_pull_request' has no inputSchema"):
        _parse([{**PULL_REQUEST, "inputSchema": ["nope"]}])


def test_a_tool_without_a_name_is_refused_by_position():
    entry = {k: v for k, v in PULL_REQUEST.items() if k != "name"}
    with pytest.raises(McpImportError, match="tool #2 has no name"):
        _parse([ISSUES, entry])


def test_a_tool_that_is_not_an_object_is_refused_by_position():
    with pytest.raises(McpImportError, match="tool #1 is not an object"):
        _parse(["nope"])


@pytest.mark.parametrize("name", ["a.b", "look up", "x" * 65])
def test_a_name_no_provider_would_register_is_refused_not_rewritten(name):
    with pytest.raises(McpImportError, match=f"tool {name!r} cannot be a mock: .*tool name must match"):
        _parse([{**PULL_REQUEST, "name": name}])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_import.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'skill_lens.mcp_import'`.

- [ ] **Step 3: Write the module**

Create `src/skill_lens/mcp_import.py`:

```python
"""Turn an MCP server's `tools/list` listing into mock-tool YAML.

An MCP (Model Context Protocol) server answers a `tools/list` request with
the tools it exposes: each one's name, description and the JSON Schema of
its arguments. Copying that into a case's `tools:` block by hand is where a
mock drifts from the server it stands in for; this module copies it instead.

Pure: text in, text out. `cli.py` does the IO, and nothing here touches the
network -- the listing is whatever the author saved from an MCP client.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import ValidationError

from skill_lens.cases.loader import UNFILLED_SENTINEL
from skill_lens.models import ToolSpec

# The placeholders an imported block carries. Both start with the loader's
# sentinel, so a pasted block refuses to run until the author fills them --
# the same rule `skill-lens init` relies on.
DESCRIPTION_PLACEHOLDER = f"{UNFILLED_SENTINEL} what this tool does"
RETURNS_PLACEHOLDER = f"{UNFILLED_SENTINEL} the JSON this tool returns"

HEADER = (
    "# Written by `skill-lens mcp-import`. Replace every TODO(skill-lens); until you do,\n"
    "# skill-lens refuses to run the file rather than pass a case that checks nothing.\n"
)
_OUTPUT_SCHEMA_NOTE = "# The server declares this output schema; shape `returns` to match it:"
_ACCEPTED_SHAPES = (
    'a JSON-RPC response ({"result": {"tools": [...]}}), its result ({"tools": [...]}), '
    "or the bare tools array ([...])"
)
# `yaml.safe_dump` folds long plain scalars at 80 columns by default; a folded
# description is valid YAML but reads badly in a file meant to be edited.
_NO_WRAP = 1_000_000


class McpImportError(ValueError):
    """A listing that cannot be imported: bad JSON, an unknown shape, or a
    tool no provider would register. A user error, so the CLI exits 2."""


@dataclass(frozen=True)
class ImportedTool:
    """One tool from the listing: the mock it becomes, and the output schema
    the server declared (if any) for the author to shape `returns` against."""

    spec: ToolSpec
    output_schema: dict[str, Any] | None = None


def parse_tools_list(text: str, *, source: str) -> list[ImportedTool]:
    """Parse a saved `tools/list` response into the mocks it describes.

    `source` names the file (or `<stdin>`) in every error message.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpImportError(f"{source}: not valid JSON: {exc}") from exc
    entries = _unwrap(data, source)
    return [_import_tool(entry, index, source) for index, entry in enumerate(entries)]


def _unwrap(data: object, source: str) -> list[object]:
    """Find the tools array inside any of the three shapes people save."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "error" in data:
            error = data["error"]
            message = error.get("message", error) if isinstance(error, dict) else error
            raise McpImportError(
                f"{source}: the server answered tools/list with an error: {message}"
            )
        if "result" in data:
            data = data["result"]
        if isinstance(data, dict) and isinstance(data.get("tools"), list):
            return data["tools"]
    raise McpImportError(f"{source}: expected {_ACCEPTED_SHAPES}")


def _import_tool(entry: object, index: int, source: str) -> ImportedTool:
    if not isinstance(entry, dict):
        raise McpImportError(f"{source}: tool #{index + 1} is not an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise McpImportError(f"{source}: tool #{index + 1} has no name")
    label = f"tool {name!r}"
    input_schema = entry.get("inputSchema")
    if not isinstance(input_schema, dict):
        raise McpImportError(
            f"{source}: {label} has no inputSchema object; an MCP tools/list entry "
            f"always carries one"
        )
    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        description = DESCRIPTION_PLACEHOLDER
    try:
        spec = ToolSpec(
            name=name,
            description=description,
            input_schema=copy.deepcopy(input_schema),
            returns=RETURNS_PLACEHOLDER,
        )
    except ValidationError as exc:
        reasons = "; ".join(error["msg"] for error in exc.errors())
        raise McpImportError(f"{source}: {label} cannot be a mock: {reasons}") from exc
    output_schema = entry.get("outputSchema")
    return ImportedTool(
        spec=spec,
        output_schema=copy.deepcopy(output_schema) if isinstance(output_schema, dict) else None,
    )
```

(`render_tool_mocks` is Task 5; `yaml`, `Sequence`, `HEADER`, `_OUTPUT_SCHEMA_NOTE` and `_NO_WRAP` are used there. Ruff will flag the unused import until then — that is expected between Tasks 4 and 5; do not remove them.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mcp_import.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/mcp_import.py tests/test_mcp_import.py
git commit -m "feat: parse an MCP tools/list listing into mock tool specs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `mcp_import.render_tool_mocks`

**Files:**
- Modify: `src/skill_lens/mcp_import.py` (append)
- Modify: `tests/test_mcp_import.py` (append)

**Interfaces:**
- Consumes: `ImportedTool`, `parse_tools_list` (Task 4); `build_mock_tool` (Task 3); `load_cases_for_skill` for the round trip.
- Produces: `render_tool_mocks(tools: Sequence[ImportedTool], *, only: Sequence[str] = ()) -> str`; `HEADER: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_import.py`. Extend the import block first:

```python
from pathlib import Path

from skill_lens.cases.loader import UNFILLED_SENTINEL, load_cases_for_skill
from skill_lens.mcp_import import (
    DESCRIPTION_PLACEHOLDER,
    HEADER,
    RETURNS_PLACEHOLDER,
    McpImportError,
    parse_tools_list,
    render_tool_mocks,
)
from skill_lens.models import Skill
from skill_lens.runners.tools import build_mock_tool
from skill_lens.yaml_loading import safe_load
```

then the tests:

```python
def _render(payload: object, **kwargs) -> str:
    return render_tool_mocks(_parse(payload), **kwargs)


PINNED = (
    HEADER
    + """\
tools:
  - name: get_pull_request
    description: Get details of a specific pull request
    input_schema:
      type: object
      properties:
        owner:
          type: string
          description: Repository owner
        pull_number:
          type: integer
      required:
      - owner
      - pull_number
    # The server declares this output schema; shape `returns` to match it:
    #   {"properties": {"number": {"type": "integer"}}, "type": "object"}
    returns: TODO(skill-lens) the JSON this tool returns
"""
)


def test_the_rendered_block_is_pinned():
    assert _render([PULL_REQUEST]) == PINNED


def test_rendering_is_deterministic():
    assert _render([PULL_REQUEST, ISSUES]) == _render([PULL_REQUEST, ISSUES])


def test_the_block_loads_back_to_the_same_schema():
    loaded = safe_load(_render([PULL_REQUEST, ISSUES]))
    assert [tool["name"] for tool in loaded["tools"]] == ["get_pull_request", "list-issues"]
    assert loaded["tools"][0]["input_schema"] == PULL_REQUEST["inputSchema"]
    assert loaded["tools"][1]["input_schema"] == ISSUES["inputSchema"]
    assert loaded["tools"][1]["returns"] == RETURNS_PLACEHOLDER


def test_no_output_schema_means_no_comment():
    assert "output schema" not in _render([ISSUES])


def test_the_output_schema_comment_is_one_line_however_large():
    big = {"type": "object", "properties": {f"k{i}": {"type": "string"} for i in range(40)}}
    text = _render([{**PULL_REQUEST, "outputSchema": big}])
    comment_lines = [line for line in text.splitlines() if line.lstrip().startswith("#   {")]
    assert len(comment_lines) == 1
    assert json.loads(comment_lines[0].split("#   ", 1)[1]) == big


def test_a_description_yaml_would_read_as_a_boolean_survives():
    # PyYAML quotes `yes`; the strict loader would refuse a bare one anyway.
    text = _render([{**PULL_REQUEST, "description": "yes"}])
    assert safe_load(text)["tools"][0]["description"] == "yes"


def test_a_property_named_on_survives():
    schema = {"type": "object", "properties": {"on": {"type": "boolean"}}}
    text = _render([{**PULL_REQUEST, "inputSchema": schema}])
    assert safe_load(text)["tools"][0]["input_schema"] == schema


def test_a_multi_line_description_survives():
    text = _render([{**PULL_REQUEST, "description": "line one\nline two"}])
    assert safe_load(text)["tools"][0]["description"] == "line one\nline two"


def test_a_missing_description_renders_the_placeholder():
    entry = {k: v for k, v in PULL_REQUEST.items() if k != "description"}
    assert f"description: {DESCRIPTION_PLACEHOLDER}" in _render([entry])


def test_only_filters_and_keeps_listing_order():
    text = _render([PULL_REQUEST, ISSUES], only=["list-issues", "get_pull_request"])
    names = [tool["name"] for tool in safe_load(text)["tools"]]
    assert names == ["get_pull_request", "list-issues"]


def test_only_one_tool():
    text = _render([PULL_REQUEST, ISSUES], only=["list-issues"])
    assert [tool["name"] for tool in safe_load(text)["tools"]] == ["list-issues"]


def test_an_unknown_only_name_lists_what_the_listing_declares():
    with pytest.raises(McpImportError) as excinfo:
        _render([PULL_REQUEST, ISSUES], only=["get_issue"])
    message = str(excinfo.value)
    assert "no tool named 'get_issue'" in message
    assert "  get_pull_request\n  list-issues" in message


def test_an_empty_listing_renders_an_empty_tools_list():
    assert _render({"tools": []}) == HEADER + "tools: []\n"


def test_the_block_round_trips_through_the_case_loader_to_the_agent(tmp_path: Path):
    # The whole point: paste the block, fill the placeholders, and the agent
    # sees exactly the schema the server declared.
    skill_dir = tmp_path / "gh"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: gh\ndescription: d\n---\nbody\n", encoding="utf-8")
    # The header comment still says TODO(skill-lens); the loader scans parsed
    # values, not comments, so only the two `returns:` need replacing.
    block = _render([PULL_REQUEST, ISSUES]).replace(RETURNS_PLACEHOLDER, '{"number": 1}')
    indented = "".join(f"    {line}\n" if line else "\n" for line in block.splitlines())
    (skill_dir / "gh.eval.yaml").write_text(
        "cases:\n  - name: n\n    task: t\n" + indented + "    trajectory:\n      called: [list-issues]\n",
        encoding="utf-8",
    )
    skill = Skill(name="gh", description="d", instructions="body", path=skill_dir)
    (case,) = load_cases_for_skill(skill)
    pr, issues = (build_mock_tool(tool) for tool in case.tools)
    assert pr.json_schema == PULL_REQUEST["inputSchema"]
    assert issues.json_schema == ISSUES["inputSchema"]
    assert issues.name == "list-issues"
    assert pr.call(owner="o", pull_number=1) == '{"number": 1}'
```

Note for the round-trip test: the rendered block's `# comment` lines indent fine; the `HEADER` lines start with `#` and are also indented, which YAML ignores.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mcp_import.py -q`
Expected: `ImportError: cannot import name 'render_tool_mocks'`.

- [ ] **Step 3: Write the renderer**

Append to `src/skill_lens/mcp_import.py`:

```python
def render_tool_mocks(tools: Sequence[ImportedTool], *, only: Sequence[str] = ()) -> str:
    """Render the mocks as a `tools:` block ready to paste under a case.

    `only` keeps the named tools, in the order the listing had them. A name the
    listing does not carry is refused with every name it does, because the
    author's next step is to pick from that list.
    """
    if only:
        found = [tool.spec.name for tool in tools]
        missing = [name for name in only if name not in found]
        if missing:
            wanted = ", ".join(repr(name) for name in missing)
            declared = "\n".join(f"  {name}" for name in sorted(found)) or "  (none)"
            raise McpImportError(f"no tool named {wanted} in the listing; it declares:\n{declared}")
        keep = set(only)
        tools = [tool for tool in tools if tool.spec.name in keep]
    if not tools:
        return f"{HEADER}tools: []\n"
    return HEADER + "tools:\n" + "".join(_render_tool(tool) for tool in tools)


def _dump(value: object) -> str:
    # Insertion order preserved; PyYAML quotes any scalar that would resolve
    # to another type (`yes`, `on`, `1.20`), so the block survives the strict
    # loader unchanged.
    return yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, default_flow_style=False, width=_NO_WRAP
    )


def _render_tool(tool: ImportedTool) -> str:
    """One list item: name, description, schema, the output-schema note, `returns`."""
    spec = tool.spec
    lines = _dump(
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
    ).splitlines()
    if tool.output_schema is not None:
        lines.append(_OUTPUT_SCHEMA_NOTE)
        # One line however large, so the note is grep-able and never splits a
        # schema across comment lines an author might half-delete.
        lines.append(f"#   {json.dumps(tool.output_schema, sort_keys=True)}")
    lines.extend(_dump({"returns": spec.returns}).splitlines())
    out = []
    for index, line in enumerate(lines):
        if not line:
            out.append("\n")  # a blank line inside a quoted multi-line scalar
            continue
        out.append(f"{'  - ' if index == 0 else '    '}{line}\n")
    return "".join(out)
```

- [ ] **Step 4: Run the tests and the linter**

Run: `uv run pytest tests/test_mcp_import.py -q && uv run ruff check src/skill_lens/mcp_import.py tests/test_mcp_import.py && uv run ruff format --check src/skill_lens/mcp_import.py tests/test_mcp_import.py`
Expected: all tests pass; ruff clean (run `uv run ruff format` on the two files if `--check` complains, then re-run the tests).

If `test_the_rendered_block_is_pinned` fails on whitespace only, compare `repr()` of both sides — the pinned text is what `yaml.safe_dump` with these settings emits (verified: sequences under a mapping key are not indented, `required:` then `- owner`). Fix the renderer, not the pin, unless the pin has a transcription error.

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/mcp_import.py tests/test_mcp_import.py
git commit -m "feat: render imported MCP tools as a pasteable tools block

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The `mcp-import` command and its `docs/cli.md` section

**Files:**
- Modify: `src/skill_lens/cli.py` (imports at the top; a new command after `init`)
- Create: `tests/test_cli_mcp_import.py`
- Modify: `docs/cli.md` (the usage block at the top; a new `## \`mcp-import\`` section before `## \`--version\``)

**Interfaces:**
- Consumes: `parse_tools_list`, `render_tool_mocks`, `McpImportError` (Tasks 4–5).
- Produces: `skill-lens mcp-import SOURCE [--tool NAME]...`, exit 0/2, YAML on stdout, errors on stderr.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_mcp_import.py`:

```python
"""`skill-lens mcp-import` prints mock-tool YAML for a saved tools/list listing."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from skill_lens.cli import app
from skill_lens.mcp_import import HEADER, RETURNS_PLACEHOLDER
from skill_lens.yaml_loading import safe_load

runner = CliRunner()

LISTING = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "tools": [
            {
                "name": "get_pull_request",
                "description": "Get a pull request",
                "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}}},
            },
            {
                "name": "list-issues",
                "description": "List issues",
                "inputSchema": {"type": "object"},
            },
        ]
    },
}


def _listing(tmp_path, payload=LISTING):
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prints_the_block_for_a_file(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path))])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(HEADER)
    loaded = safe_load(result.stdout)
    assert [tool["name"] for tool in loaded["tools"]] == ["get_pull_request", "list-issues"]
    assert loaded["tools"][0]["returns"] == RETURNS_PLACEHOLDER


def test_stdout_holds_nothing_but_the_block(tmp_path):
    # `> tools.yaml` must capture exactly the block: no banner, no trailer.
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path))])
    assert result.stdout.endswith("returns: TODO(skill-lens) the JSON this tool returns\n")
    assert result.stderr == ""


def test_reads_stdin_for_a_dash():
    result = runner.invoke(app, ["mcp-import", "-"], input=json.dumps(LISTING))
    assert result.exit_code == 0, result.output
    assert "name: list-issues" in result.stdout


def test_tool_filters_and_repeats(tmp_path):
    result = runner.invoke(
        app, ["mcp-import", str(_listing(tmp_path)), "--tool", "list-issues"]
    )
    assert result.exit_code == 0, result.output
    names = [tool["name"] for tool in safe_load(result.stdout)["tools"]]
    assert names == ["list-issues"]


def test_an_unknown_tool_is_a_user_error_on_stderr(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path)), "--tool", "nope"])
    assert result.exit_code == 2
    assert "no tool named 'nope'" in result.stderr
    assert "get_pull_request" in result.stderr
    assert result.stdout == ""


def test_a_missing_file_is_a_user_error(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(tmp_path / "missing.json")])
    assert result.exit_code == 2
    assert "cannot read" in result.stderr
    assert result.stdout == ""


def test_invalid_json_is_a_user_error(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text("{", encoding="utf-8")
    result = runner.invoke(app, ["mcp-import", str(path)])
    assert result.exit_code == 2
    assert "not valid JSON" in result.stderr


def test_an_unknown_shape_is_a_user_error(tmp_path):
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path, {"jsonrpc": "2.0"}))])
    assert result.exit_code == 2
    assert "expected a JSON-RPC response" in result.stderr


def test_a_name_no_provider_accepts_is_a_user_error(tmp_path):
    bad = {"tools": [{"name": "a.b", "inputSchema": {"type": "object"}}]}
    result = runner.invoke(app, ["mcp-import", str(_listing(tmp_path, bad))])
    assert result.exit_code == 2
    assert "tool 'a.b' cannot be a mock" in result.stderr


def test_stdin_errors_name_stdin():
    result = runner.invoke(app, ["mcp-import", "-"], input="{")
    assert result.exit_code == 2
    assert "<stdin>: not valid JSON" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli_mcp_import.py -q`
Expected: every test fails with exit code 2 and `No such command 'mcp-import'`.

- [ ] **Step 3: Add the command**

In `src/skill_lens/cli.py`, add `import sys` directly after `import os`, and add to the project imports (alphabetically, after `from skill_lens.judges.pydantic_ai import PydanticAIJudge`):

```python
from skill_lens.mcp_import import McpImportError, parse_tools_list, render_tool_mocks
```

Then append at the end of the file, after the `init` command (`init` is the last command; the file has no `if __name__` block):

```python
@app.command("mcp-import")
def mcp_import(
    source: Annotated[
        Path, typer.Argument(help="A saved tools/list JSON response, or - for stdin.")
    ],
    tool: Annotated[
        list[str] | None,
        typer.Option("--tool", help="Import only this tool; repeat the flag for several."),
    ] = None,
) -> None:
    """Print mock-tool YAML for the tools an MCP server's tools/list listing declares.

    Offline: the listing is whatever an MCP client saved. Stdout carries
    nothing but the block, so `> tools.yaml` captures exactly it; every error
    goes to stderr.
    """
    if str(source) == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    else:
        label = str(source)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            typer.echo(f"cannot read {source}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    try:
        rendered = render_tool_mocks(parse_tools_list(text, source=label), only=tool or ())
    except McpImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(rendered, nl=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli_mcp_import.py tests/test_cli.py tests/test_cli_init.py -q`
Expected: all pass.

- [ ] **Step 5: Document the command**

`tests/test_docs.py::test_every_cli_command_is_documented` and `::test_every_cli_option_is_documented` now fail until `docs/cli.md` names `` `mcp-import` `` and `--tool`. Run them to see that:

Run: `uv run pytest tests/test_docs.py -k "cli" -q`
Expected: 2 failures naming `mcp-import` and `--tool`.

In `docs/cli.md`, add a line to the usage block at the top, after `skill-lens init <path> [--force]`:

```
skill-lens mcp-import <source> [--tool <name>]...
```

and add this section immediately before `## \`--version\``:

````markdown
## `mcp-import`

```bash
skill-lens mcp-import <source> [--tool <name>]... > tools.yaml
```

Turns a saved MCP `tools/list` response into a `tools:` block you paste under a case.
MCP (Model Context Protocol) is the protocol an agent uses to discover and call the tools
a separate server exposes; `tools/list` is the request that returns each tool's name,
description and the JSON Schema of its arguments. Hand-copying that into a mock is where
the mock drifts from the server it stands in for — this command copies it instead.

`<source>` is a JSON file, or `-` to read stdin. Any of the three shapes people save is
accepted: the whole JSON-RPC response (`{"jsonrpc": ..., "result": {"tools": [...]}}`),
just its result (`{"tools": [...]}`), or the bare array (`[...]`). To capture one, ask any
MCP client for the listing — `npx @modelcontextprotocol/inspector` shows it under
*Tools*, and Claude Code's `/mcp` panel lists the same schemas.

| Flag | Meaning |
| --- | --- |
| `--tool <name>` | Import only this tool. Repeat the flag for several. A name the listing does not carry is a user error, and the message lists the names it does. |

**What is copied, and what is not.** Each tool's `name` and `inputSchema` are copied
verbatim — the schema lands in [`input_schema:`](eval-files.md#mock-tools), so the agent
sees exactly what the live server would declare, optional arguments and all. `description`
is copied when the server gives one. `returns:` is **always** the `TODO(skill-lens)`
placeholder: the listing says nothing about what a call returns, and inventing a value
would be exactly the silent drift the import exists to remove. A missing description gets
the placeholder too. Until you replace them, the file refuses to run as an
[authoring error](eval-files.md#unfilled-scaffolds) rather than passing a case that checks
nothing. When the server declares an `outputSchema`, it is written as a comment above
`returns:` so you can shape the value against it.

```yaml
# Written by `skill-lens mcp-import`. Replace every TODO(skill-lens); until you do,
# skill-lens refuses to run the file rather than pass a case that checks nothing.
tools:
  - name: get_pull_request
    description: Get details of a specific pull request
    input_schema:
      type: object
      properties:
        owner:
          type: string
        pull_number:
          type: integer
      required:
      - owner
      - pull_number
    # The server declares this output schema; shape `returns` to match it:
    #   {"properties": {"number": {"type": "integer"}}, "type": "object"}
    returns: TODO(skill-lens) the JSON this tool returns
```

Tool names are kept as the server spells them — `get-pull-request` stays hyphenated,
because the mock has to answer to the name the skill's prose and the live server use. A
name outside `^[A-Za-z0-9_-]{1,64}$` (the rule every provider enforces) is a user error
naming the tool, never a rewrite.

Stdout carries nothing but the block, so `> tools.yaml` captures exactly it; every error
goes to stderr. The command never touches the network. Exit `0` on success; exit `2` for
an unreadable file, invalid JSON, a shape that is not a `tools/list` listing, a listing the
server answered with an error, a tool without an `inputSchema`, a name no provider would
register, or an unknown `--tool`.
````

Also update the paragraph under the usage block that reads "`init` is the exception: its `<path>` is exactly one skill directory containing `SKILL.md`, never a directory of skills." — append: "`mcp-import` takes no skill path at all; its `<source>` is a saved `tools/list` response."

- [ ] **Step 6: Run the docs tests and the linter**

Run: `uv run pytest tests/test_docs.py -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/skill_lens/cli.py tests/test_cli_mcp_import.py docs/cli.md
git commit -m "feat(cli): add mcp-import to write mock tools from a tools/list listing

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The scaffold points at `mcp-import`

**Files:**
- Modify: `src/skill_lens/scaffold.py:44-48` (the tools comment in `_TEMPLATE`)
- Modify: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `render_scaffold(skill)` mentions `skill-lens mcp-import`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scaffold.py` (the module defines a module-level `SKILL = Skill(...)` fixture and imports `render_scaffold`):

```python
def test_the_scaffold_points_at_mcp_import_for_a_real_server_tool():
    # A tool a real MCP server exposes should be imported, not transcribed.
    assert "skill-lens mcp-import" in render_scaffold(SKILL)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scaffold.py -k mcp_import -v`
Expected: FAIL — the string is absent.

- [ ] **Step 3: Add the comment line**

In `src/skill_lens/scaffold.py`, change the comment on case 2 of `_TEMPLATE` from:

```
  # 2. The edge this skill exists to get right. Mock tools execute nothing:
  #    calling one records the call and returns `returns` verbatim, so the
  #    trajectory is genuinely the model's choice. `trajectory` catches the
  #    failure an output assertion cannot see -- deciding without looking.
```

to:

```
  # 2. The edge this skill exists to get right. Mock tools execute nothing:
  #    calling one records the call and returns `returns` verbatim, so the
  #    trajectory is genuinely the model's choice. `trajectory` catches the
  #    failure an output assertion cannot see -- deciding without looking.
  #    For a tool a real MCP server exposes, `skill-lens mcp-import tools.json`
  #    writes this block from the server's own schema instead.
```

- [ ] **Step 4: Run the scaffold and init tests**

Run: `uv run pytest tests/test_scaffold.py tests/test_cli_init.py -q`
Expected: all pass (the scaffold still loads clean once filled; the new lines are comments).

- [ ] **Step 5: Commit**

```bash
git add src/skill_lens/scaffold.py tests/test_scaffold.py
git commit -m "docs(scaffold): point the tools example at mcp-import

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Reference docs, `ARCHITECTURE.md`, `CLAUDE.md`, `README.md`

**Files:**
- Modify: `docs/eval-files.md` (the `tools` row of the field table; a new `## Mock tools` section before `## Judging output quality`; the "normalised to an identifier" bullet under *Did the agent reach for the skill?*)
- Modify: `docs/runners.md:58-99` (*Declaring tools and scoring the trajectory*)
- Modify: `skills/writing-skill-evals/references/eval-file-syntax.md:83-90`
- Modify: `ARCHITECTURE.md` (module map after the `scaffold.py` row; a new `### Importing MCP tools` subsection at the end of *Invariants, and why*, before `### Security checks`)
- Modify: `CLAUDE.md` (the *What this is* paragraph; the invariants list)
- Modify: `README.md` (one sentence after the YAML example in *A case is a few lines of YAML*)

**Interfaces:** none — prose only. `tests/test_docs.py` and `uv run mkdocs build --strict` are the tests.

- [ ] **Step 1: `docs/eval-files.md`**

Change the `tools` row of the field table to:

```markdown
| `tools` | no | Mock tools the agent may call — see [Mock tools](#mock-tools) |
```

Insert before `## Judging output quality`:

````markdown
## Mock tools

A `tools:` list declares the tools the agent may call. Nothing executes: calling one records
the call and returns `returns` verbatim, so the trajectory is the model's own choice and
the run has no side effects. See [Declaring tools and scoring the
trajectory](runners.md#declaring-tools-and-scoring-the-trajectory) for how the calls are
scored.

A tool declares its arguments one of two ways:

```yaml
    tools:
      - name: lookup_order              # ^[A-Za-z0-9_-]{1,64}$ — what providers accept
        description: Look up an order by its id
        parameters:                     # the shorthand: name -> primitive type
          order_id: string              # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
      - name: get-pull-request
        description: Get details of a specific pull request
        input_schema:                   # a full JSON Schema, passed to the agent verbatim
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
            labels: {type: array, items: {type: string}}
          required: [owner, pull_number]
        returns: '{"number": 1}'
```

`parameters:` is for a tool you describe by hand. Every key is required and the schema is
closed (`additionalProperties: false`), because you wrote every key. `input_schema:` is
for a tool that must match a real server's declared schema — optional arguments, enums,
arrays, nested objects — and reaches the agent exactly as written; nothing is added. It
must be a valid JSON Schema whose top-level `type` is `object`, which is what every
provider requires of a tool. A tool that declares both, or an `input_schema` that is not
a valid object schema, is an authoring error (exit `2`) caught before any case runs.

[`skill-lens mcp-import`](cli.md#mcp-import) writes an `input_schema:` block from an MCP
server's own `tools/list` listing, so a mock for a real server's tool is copied rather
than transcribed.

Tool names follow the rule both OpenAI and Anthropic enforce, `^[A-Za-z0-9_-]{1,64}$`, so
a hyphenated MCP tool name such as `get-pull-request` is kept as the server spells it. A
name outside the rule is an authoring error; skill-lens never rewrites one.
````

In *Did the agent reach for the skill?*, the bullet "The tool name is the skill's name normalised to an identifier (`order-support` becomes `order_support`). A case tool that collides with it is an authoring error." stays as it is — the offered-skill tool's name is still normalised. Add one sentence to it: "This normalisation applies only to the offered-skill tool; a case's own tools keep their names."

- [ ] **Step 2: `docs/runners.md`**

In *Declaring tools and scoring the trajectory*, after the sentence "`order` is a relative subsequence: ..." paragraph and before "Every tool name in `called`...", insert:

```markdown
A tool declares its arguments with the `parameters:` shorthand shown above, or with a full
`input_schema:` when it must match a real server's declared JSON Schema; see [Mock
tools](eval-files.md#mock-tools). The shorthand is closed — every key required,
`additionalProperties: false` — because the author wrote every key. A declared
`input_schema` is handed to the agent verbatim, because fidelity to the server it stands in
for is its reason to exist. [`skill-lens mcp-import`](cli.md#mcp-import) writes one from
the server's own `tools/list` listing.
```

- [ ] **Step 3: `skills/writing-skill-evals/references/eval-file-syntax.md`**

Replace the `tools:` example (lines 83–90) with:

```yaml
    tools:
      - name: lookup_order          # ^[A-Za-z0-9_-]{1,64}$ (what providers accept)
        description: Look up an order by its id
        parameters:
          order_id: string          # string | integer | number | boolean
        returns: '{"id": "1234", "status": "delivered"}'
      - name: get-pull-request      # a real MCP tool: keep the server's name
        description: Get details of a specific pull request
        input_schema:               # the server's JSON Schema, verbatim; never with parameters
          type: object
          properties:
            owner: {type: string}
            pull_number: {type: integer}
          required: [owner, pull_number]
        returns: '{"number": 1}'
```

and add directly below it:

```markdown
`parameters:` is closed (every key required, no extras); `input_schema:` is passed as
written and must be a valid JSON Schema of `type: object`. For a tool a real MCP server
exposes, `skill-lens mcp-import tools.json` writes the `input_schema:` block from the
server's `tools/list` listing — `returns:` still has to be filled in by hand.
```

Run `uv run pytest tests/test_shipped_skill.py -q` afterwards: it may pin the shipped skill's references, and any failure there says what else to keep in step.

- [ ] **Step 4: `ARCHITECTURE.md`**

Module map — add after the `scaffold.py` row:

```markdown
| `mcp_import.py` | Turns a saved MCP `tools/list` response into mock-tool YAML: `parse_tools_list` (three accepted shapes, every refusal a `McpImportError`) and `render_tool_mocks` (a pasteable `tools:` block). Pure: text in, text out; `cli.py` reads the file or stdin. Never touches the network. |
```

The *Core data models* table lists `EvalCase` by field name only and has no `ToolSpec` row; leave it.

Invariants — add at the end of `### Developer experience (M7)`, before `### Security checks`:

```markdown
### Importing MCP tools

**An imported mock is the server's schema verbatim, and `returns` is never invented.**
`mcp-import` copies `name` and `inputSchema` byte-for-byte into `input_schema:` and writes
the `TODO(skill-lens)` sentinel for `returns` (and for a missing `description`), so a
pasted block cannot run until the author has said what the tool returns. The listing says
nothing about return values; a generated one would be exactly the silent drift the import
exists to remove. The server's `outputSchema`, when declared, rides along as a comment.

**`parameters` and `input_schema` are exclusive, and `input_schema` is validated at load
time.** Both set, a schema that fails `check_schema`, or a top-level type other than
`object` is an authoring error (exit 2) in `cases/loader.py`, before any case runs — the
same treatment an assertion's `json_schema` gets.

**The shorthand is closed; a declared schema is open.** `build_mock_tool` adds
`additionalProperties: false` and marks every key required only when it derived the schema
from `parameters:`, where the author wrote every key. A declared `input_schema` is deep
copied and passed as written, because fidelity to the server it stands in for is its reason
to exist.

**A tool name is what providers accept**: `^[A-Za-z0-9_-]{1,64}$`, the rule OpenAI and
Anthropic enforce, in place of `isidentifier()`. MCP tool names are routinely hyphenated
and a mock must answer to the name the skill's prose and the live server use; the import
keeps the name, and a name outside the rule is an error naming the tool, never a rewrite.
`skill_tool_name` — the offered-skill tool — still normalises to an identifier.

**`mcp-import` never touches the network.** `SOURCE` is a file or `-`; a live `--server`
is deliberately deferred (see the design spec).
```

- [ ] **Step 5: `CLAUDE.md`**

In *What this is*, append to the paragraph after the M6 part 2 sentence (before "Milestones are defined in"):

```
`mcp-import` (issue #43) turns a saved MCP `tools/list` response into a pasteable `tools:`
block carrying the server's schema verbatim in the new `ToolSpec.input_schema`, and
`ToolSpec.name` now accepts what providers accept (`^[A-Za-z0-9_-]{1,64}$`). Its design is
in `docs/superpowers/specs/2026-09-17-skill-lens-mcp-import-design.md`.
```

In *Invariants that are easy to break*, add after the `examples/greeting` bullet:

```markdown
- **An imported mock is the server's schema verbatim, and `returns` is never invented.**
  `mcp-import` copies `name` and `inputSchema` into `input_schema:` byte-for-byte and writes
  the `TODO(skill-lens)` sentinel for `returns` (and a missing `description`); a pasted block
  cannot run until the author fills it. `outputSchema` rides along as a comment only.
- **`parameters` and `input_schema` are exclusive, and `input_schema` is validated at load
  time** (`check_schema`, top-level `type: object`) — exit 2 before any case runs.
- **The shorthand is closed; a declared schema is open.** `build_mock_tool` injects
  `additionalProperties: false` only for `parameters:`; an `input_schema` is deep copied
  and passed as written.
- **A tool name is `^[A-Za-z0-9_-]{1,64}$`** — what providers accept — never rewritten.
  `skill_tool_name` (the offered-skill tool) still normalises to an identifier.
- **`mcp-import` never touches the network.** `SOURCE` is a file or `-`.
```

- [ ] **Step 6: `README.md`**

After the YAML example in *A case is a few lines of YAML* (after the closing ```` ``` ````), add:

```markdown
A tool a real MCP server exposes need not be transcribed: `skill-lens mcp-import tools.json`
writes the block from the server's own `tools/list` listing, schema and all.
```

- [ ] **Step 7: Build and test the docs**

Run: `uv sync --group docs && uv run mkdocs build --strict && uv run pytest tests/test_docs.py tests/test_naming.py tests/test_shipped_skill.py tests/test_check_docs_updated.py -q`
Expected: the build succeeds with no warnings; every test passes. A broken anchor (`#mock-tools`, `#mcp-import`) fails `--strict` — check the heading spelling.

- [ ] **Step 8: Commit**

```bash
git add docs/eval-files.md docs/runners.md skills/writing-skill-evals/references/eval-file-syntax.md ARCHITECTURE.md CLAUDE.md README.md
git commit -m "docs: document input_schema, the provider name rule and mcp-import

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Full verification and the pull request

**Files:** none new.

- [ ] **Step 1: The whole suite, lint, format, docs**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mkdocs build --strict && uv run skill-lens list ./examples`
Expected: every test passes (integration tier deselected by default), ruff clean, docs build clean, the examples list.

- [ ] **Step 2: Dogfood the command end to end**

Write a listing to the scratchpad and run the command twice — through a file and through stdin — then load the result:

```bash
cat > /private/tmp/claude-501/-Users-emadmokhtar-Projects-skill-evaluator--claude-worktrees-github-issue-43-624019/d9053e85-8549-4684-93fa-10a119c3987d/scratchpad/tools.json <<'EOF'
{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"get-pull-request","description":"Get a PR","inputSchema":{"type":"object","properties":{"owner":{"type":"string"},"repo":{"type":"string"},"pull_number":{"type":"integer"}},"required":["owner","repo","pull_number"]},"outputSchema":{"type":"object"}}]}}
EOF
uv run skill-lens mcp-import /private/tmp/claude-501/-Users-emadmokhtar-Projects-skill-evaluator--claude-worktrees-github-issue-43-624019/d9053e85-8549-4684-93fa-10a119c3987d/scratchpad/tools.json
uv run skill-lens mcp-import - < /private/tmp/claude-501/-Users-emadmokhtar-Projects-skill-evaluator--claude-worktrees-github-issue-43-624019/d9053e85-8549-4684-93fa-10a119c3987d/scratchpad/tools.json --tool get-pull-request
uv run skill-lens mcp-import /private/tmp/claude-501/-Users-emadmokhtar-Projects-skill-evaluator--claude-worktrees-github-issue-43-624019/d9053e85-8549-4684-93fa-10a119c3987d/scratchpad/tools.json --tool nope; echo "exit=$?"
```

Expected: the block twice (identical), then the "no tool named 'nope'" message on stderr and `exit=2`.

- [ ] **Step 3: Review the diff as a whole**

Run: `git log --oneline main..HEAD && git diff main --stat`
Expected: nine commits (spec + eight tasks), every one Conventional. Read `git diff main -- docs/ ARCHITECTURE.md CLAUDE.md README.md` once as a set: the same rule must be spelled the same way on every page (`^[A-Za-z0-9_-]{1,64}$`; "verbatim"; the two placeholders).

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin worktree-github-issue-43-624019
gh pr create --assignee @EmadMokhtar \
  --title "feat: import mock tools from an MCP server's tools/list listing" \
  --body "$(cat <<'EOF'
Closes #43

## What

- `skill-lens mcp-import SOURCE [--tool NAME]...` turns a saved MCP `tools/list` response (a JSON file, or `-` for stdin; the JSON-RPC envelope, its result, or the bare array) into a pasteable `tools:` block. Offline only.
- `ToolSpec.input_schema`: a full JSON Schema a mock passes to the agent verbatim, exclusive with the `parameters:` shorthand and validated at load time.
- `ToolSpec.name` accepts what OpenAI and Anthropic accept (`^[A-Za-z0-9_-]{1,64}$`) so a hyphenated MCP tool name is kept as the server spells it.

## Why

A mock transcribed by hand from a skill's prose drifts from what the server actually declares, and the flat `parameters:` map could not express an optional argument or a nested object at all. The import copies the server's schema; `returns:` stays a `TODO(skill-lens)` placeholder because the listing says nothing about return values and inventing one would be the same drift by another route.

Design: `docs/superpowers/specs/2026-09-17-skill-lens-mcp-import-design.md`.

## Deferred

A live `--server` (needs the MCP client SDK, transports and auth); an `init`-time flag.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Then read CI through the `ccd_pr` tools; if `docs-freshness` or any job fails, fix it on this branch — do not add `no-docs-needed`, this change documents itself.
