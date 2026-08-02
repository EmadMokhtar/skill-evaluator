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


def test_calling_the_offered_tool_delivers_the_skill_instructions():
    # Offered mode has to be honest: an agent that picks the skill must
    # actually receive it, or every later assertion is about an agent acting on
    # instructions it never saw.
    tool = build_skill_tool(
        Skill(name="s", description="d", instructions="Always look it up.", path=Path("."))
    )
    assert tool.call() == "Always look it up."
