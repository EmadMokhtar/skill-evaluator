"""The checks an eval file and a tool library share."""

import pytest

from skill_lens.cases.checks import (
    UNFILLED_SENTINEL,
    check_tool,
    check_tool_returns,
    check_tool_schema,
    find_unfilled,
)
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


# --- returns: a lookup's when: keys and reachability ----------------------------


def _lookup(*entries, **spec):
    spec.setdefault("name", "t")
    return ToolSpec(returns=list(entries), **spec)


def test_a_string_and_a_sequence_have_nothing_to_check():
    check_tool_returns(ToolSpec(name="t", returns="x"))
    check_tool_returns(ToolSpec(name="t", returns=["x", "y"]))


def test_a_lookup_on_declared_parameters_passes():
    check_tool_returns(
        _lookup(
            {"when": {"id": "A"}, "value": "a"},
            {"when": {"id": "B", "expand": True}, "value": "b"},
            {"value": "fallback"},
            parameters={"id": "string", "expand": "boolean"},
        )
    )


def test_a_when_key_the_shorthand_never_carries_is_refused():
    # `parameters:` is closed, so a call can never carry `item_id`; an entry
    # keyed on it could never match, and a check that can never fire is an
    # authoring error, not a scenario.
    with pytest.raises(ValueError, match=r"returns\[1\]\.when names 'item_id'.*declares id"):
        check_tool_returns(
            _lookup(
                {"when": {"id": "A"}, "value": "a"},
                {"when": {"item_id": "B"}, "value": "b"},
                parameters={"id": "string"},
            )
        )


def test_a_when_key_a_closed_input_schema_never_carries_is_refused():
    schema = {
        "type": "object",
        "properties": {"owner": {"type": "string"}},
        "additionalProperties": False,
    }
    with pytest.raises(ValueError, match=r"returns\[0\]\.when names 'repo'"):
        check_tool_returns(_lookup({"when": {"repo": "x"}, "value": "a"}, input_schema=schema))


def test_a_when_key_an_open_input_schema_might_carry_is_allowed():
    # Without `additionalProperties: false` the server accepts keys it does
    # not list, so the author may key on one.
    schema = {"type": "object", "properties": {"owner": {"type": "string"}}}
    check_tool_returns(_lookup({"when": {"repo": "x"}, "value": "a"}, input_schema=schema))
    check_tool_returns(
        _lookup({"when": {"repo": "x"}, "value": "a"}, input_schema={"type": "object"})
    )


def test_a_tool_with_no_parameters_at_all_refuses_every_when_key():
    with pytest.raises(ValueError, match=r"returns\[0\]\.when names 'id'.*declares no parameters"):
        check_tool_returns(_lookup({"when": {"id": "A"}, "value": "a"}))


def test_an_entry_after_a_fallback_is_unreachable():
    with pytest.raises(ValueError, match=r"returns\[2\] can never be reached: returns\[1\]"):
        check_tool_returns(
            _lookup(
                {"when": {"id": "A"}, "value": "a"},
                {"value": "fallback"},
                {"when": {"id": "B"}, "value": "b"},
                parameters={"id": "string"},
            )
        )


def test_an_entry_repeating_an_earlier_when_is_unreachable():
    with pytest.raises(ValueError, match=r"returns\[1\] can never be reached: returns\[0\]"):
        check_tool_returns(
            _lookup(
                {"when": {"id": "A"}, "value": "a"},
                {"when": {"id": "A"}, "value": "b"},
                parameters={"id": "string"},
            )
        )


def test_an_entry_narrower_than_an_earlier_one_is_unreachable():
    # Anything carrying id=A and expand=true already matched the first entry.
    with pytest.raises(ValueError, match=r"returns\[1\] can never be reached: returns\[0\]"):
        check_tool_returns(
            _lookup(
                {"when": {"id": "A"}, "value": "a"},
                {"when": {"id": "A", "expand": True}, "value": "b"},
                parameters={"id": "string", "expand": "boolean"},
            )
        )


def test_a_narrower_entry_before_a_wider_one_is_fine():
    check_tool_returns(
        _lookup(
            {"when": {"id": "A", "expand": True}, "value": "b"},
            {"when": {"id": "A"}, "value": "a"},
            parameters={"id": "string", "expand": "boolean"},
        )
    )


def test_an_empty_when_is_refused_in_favour_of_dropping_the_key():
    with pytest.raises(ValueError, match=r"returns\[0\]\.when is empty"):
        check_tool_returns(_lookup({"when": {}, "value": "a"}, parameters={"id": "string"}))


def test_check_tool_runs_both_the_schema_and_the_returns_rules():
    with pytest.raises(ValueError, match="declares both parameters and input_schema"):
        check_tool(ToolSpec(name="t", parameters={"q": "string"}, input_schema={"type": "object"}))
    with pytest.raises(ValueError, match="can never be reached"):
        check_tool(_lookup({"value": "a"}, {"value": "b"}))
    check_tool(ToolSpec(name="t", parameters={"q": "string"}, returns=["a", "b"]))
