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
