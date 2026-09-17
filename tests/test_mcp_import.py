"""`mcp-import`: an MCP server's tools/list listing becomes mock-tool YAML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "description", [None, "", "   ", 7], ids=["absent", "empty", "blank", "int"]
)
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


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [PULL_REQUEST], "nextCursor": "p2"}},
        {"tools": [PULL_REQUEST], "nextCursor": "p2"},
    ],
    ids=["envelope", "bare-result"],
)
def test_a_paginated_listing_is_refused_naming_next_cursor(payload):
    # A page-one capture would otherwise import a silent subset of the
    # server's tools, and --tool would only ever be able to list that page's
    # names.
    with pytest.raises(McpImportError, match=r"nextCursor 'p2'"):
        _parse(payload)


@pytest.mark.parametrize("cursor", [None, ""])
def test_a_null_or_empty_next_cursor_is_the_last_page(cursor):
    payload = {"tools": [PULL_REQUEST], "nextCursor": cursor}
    names = [tool.spec.name for tool in _parse(payload)]
    assert names == ["get_pull_request"]


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
    pattern = f"tool {name!r} cannot be a mock: .*tool name must match"
    with pytest.raises(McpImportError, match=pattern):
        _parse([{**PULL_REQUEST, "name": name}])


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
    (skill_dir / "SKILL.md").write_text(
        "---\nname: gh\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    # The header comment still says TODO(skill-lens); the loader scans parsed
    # values, not comments, so only the two `returns:` need replacing. Quoted,
    # like every other JSON `returns:` value in this repo (e.g.
    # examples/order-support/order-support.eval.yaml) -- unquoted, `{...}`
    # is YAML flow-mapping syntax, not a string, and the case loader would
    # reject it (`returns` must be a string).
    block = _render([PULL_REQUEST, ISSUES]).replace(RETURNS_PLACEHOLDER, "'{\"number\": 1}'")
    indented = "".join(f"    {line}\n" if line else "\n" for line in block.splitlines())
    (skill_dir / "gh.eval.yaml").write_text(
        "cases:\n  - name: n\n    task: t\n"
        + indented
        + "    trajectory:\n      called: [list-issues]\n",
        encoding="utf-8",
    )
    skill = Skill(name="gh", description="d", instructions="body", path=skill_dir)
    (case,) = load_cases_for_skill(skill)
    pr, issues = (build_mock_tool(tool) for tool in case.tools)
    assert pr.json_schema == PULL_REQUEST["inputSchema"]
    assert issues.json_schema == ISSUES["inputSchema"]
    assert issues.name == "list-issues"
    assert pr.call(owner="o", pull_number=1) == '{"number": 1}'
