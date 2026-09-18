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


def test_a_self_referential_list_does_not_recurse_forever():
    raw: list = ["n"]
    raw.append(raw)
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
