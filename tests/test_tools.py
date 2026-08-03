"""Mock tools: the agent's environment, declared by the eval case."""

from pathlib import Path

import skill_eval.runners.tools as tools_module
from skill_eval.models import Skill, ToolSpec
from skill_eval.runners.tools import build_mock_tool, build_skill_tool, skill_tool_name


def test_schema_describes_every_declared_parameter():
    tool = build_mock_tool(
        ToolSpec(
            name="lookup_order",
            description="Look up an order",
            parameters={"order_id": "string", "verbose": "boolean"},
        )
    )
    assert tool.name == "lookup_order"
    assert tool.description == "Look up an order"
    assert tool.json_schema["type"] == "object"
    assert tool.json_schema["properties"] == {
        "order_id": {"type": "string"},
        "verbose": {"type": "boolean"},
    }
    assert sorted(tool.json_schema["required"]) == ["order_id", "verbose"]
    assert tool.json_schema["additionalProperties"] is False


def test_a_tool_with_no_parameters_still_has_a_valid_schema():
    tool = build_mock_tool(ToolSpec(name="ping"))
    assert tool.json_schema["properties"] == {}
    assert tool.json_schema["required"] == []


def test_calling_the_tool_returns_the_canned_value_verbatim():
    tool = build_mock_tool(ToolSpec(name="lookup_order", returns='{"id": "1234"}'))
    assert tool.call(order_id="1234") == '{"id": "1234"}'


def test_calling_the_tool_ignores_whatever_arguments_it_is_handed():
    # The model can hallucinate an argument; a mock must not explode on it,
    # because that would surface as an infra error instead of an eval signal.
    tool = build_mock_tool(ToolSpec(name="ping", returns="pong"))
    assert tool.call(unexpected="x", another=2) == "pong"


def test_a_tool_with_no_return_value_yields_an_empty_string():
    assert build_mock_tool(ToolSpec(name="ping")).call() == ""


def test_module_does_not_import_an_agent_framework():
    # No agent-framework type may appear outside runners/pydantic_ai.py.
    source = Path(tools_module.__file__).read_text(encoding="utf-8")
    assert "pydantic_ai" not in source


def test_a_skill_name_becomes_a_valid_identifier():
    assert skill_tool_name("order-support") == "order_support"
    assert skill_tool_name("order support") == "order_support"
    assert skill_tool_name("already_fine") == "already_fine"


def test_a_name_that_cannot_start_an_identifier_is_prefixed():
    assert skill_tool_name("123-go").isidentifier()
    assert skill_tool_name("123-go").startswith("skill_")


def test_a_name_with_nothing_usable_falls_back_to_a_stable_default():
    assert skill_tool_name("---") == "skill"
    assert skill_tool_name("") == "skill"


def test_a_name_with_a_category_no_character_is_still_a_valid_identifier():
    # '²' (superscript two) and '①' (circled digit one) are Unicode category
    # "Other Number" (No): char.isalnum() and char.isdigit() both say True for
    # them, but they are not legal in a Python identifier in any position.
    # A cleaning pass built on isalnum()/isdigit() lets them survive and
    # defeats the digit-prefix guard, breaking the function's totality
    # contract. Assert the property (isidentifier()), not one output string,
    # since totality is the actual thing being guaranteed.
    assert skill_tool_name("²").isidentifier()
    assert skill_tool_name("①").isidentifier()
    assert skill_tool_name("Level²").isidentifier()


def test_distinct_names_may_collapse_to_the_same_identifier():
    # Deliberate, accepted behavior: normalization is lossy, so names that
    # differ only in separator survive as the same identifier. This is fine
    # because only one skill is offered as a tool per run, so there is never
    # a collision to resolve within a single run.
    assert skill_tool_name("a-b") == skill_tool_name("a_b") == skill_tool_name("a b") == "a_b"


def test_the_offered_tool_describes_the_skill_and_takes_no_arguments():
    tool = build_skill_tool(
        Skill(
            name="order-support",
            description="Handle refund requests",
            instructions="Always look up the order first.",
            path=Path("."),
        )
    )
    assert tool.name == "order_support"
    assert tool.description == "Handle refund requests"
    assert tool.json_schema["properties"] == {}
    assert tool.json_schema["required"] == []


def test_built_skill_tools_do_not_share_mutable_schema_state():
    # Regression: build_skill_tool used to do `json_schema=dict(_EMPTY_SCHEMA)`,
    # a shallow copy. Every built tool's `properties` dict and `required` list
    # were the *same* objects, shared with the module-level template and with
    # each other. MockTool being frozen only blocks reassigning the attribute,
    # not mutating its contents, so mutating one tool's schema would leak into
    # every other tool built from the template.
    first = build_skill_tool(Skill(name="a", description="d", instructions="i", path=Path(".")))
    second = build_skill_tool(Skill(name="b", description="d", instructions="i", path=Path(".")))
    assert first.json_schema["properties"] is not second.json_schema["properties"]
    assert first.json_schema["required"] is not second.json_schema["required"]
    first.json_schema["properties"]["leaked"] = {"type": "string"}
    first.json_schema["required"].append("leaked")
    assert second.json_schema["properties"] == {}
    assert second.json_schema["required"] == []


def test_calling_the_offered_tool_delivers_the_skill_instructions():
    # Offered mode has to be honest: an agent that picks the skill must
    # actually receive it, or every later assertion is about an agent acting on
    # instructions it never saw.
    tool = build_skill_tool(
        Skill(name="s", description="d", instructions="Always look it up.", path=Path("."))
    )
    assert tool.call() == "Always look it up."
