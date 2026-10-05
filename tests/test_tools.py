"""Mock tools: the agent's environment, declared by the eval case."""

from pathlib import Path

import skill_lens.runners.tools as tools_module
from skill_lens.models import TOOL_NAME_PATTERN, Skill, ToolSpec
from skill_lens.runners.tools import (
    NO_RESPONSE_SCRIPTED,
    build_mock_tool,
    build_skill_tool,
    skill_tool_name,
)


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
    tool = build_mock_tool(ToolSpec(name="ping", input_schema={"type": "object"}, returns="pong"))
    assert tool.call(anything="at all") == "pong"


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


# --- a sequence of return values, consumed in call order -----------------------


def test_a_sequence_is_handed_back_one_entry_per_call_in_order():
    tool = build_mock_tool(
        ToolSpec(name="get_work_item", returns=['{"id": "A", "parent": "B"}', '{"id": "B"}'])
    )
    assert tool.call(id="A") == '{"id": "A", "parent": "B"}'
    assert tool.call(id="B") == '{"id": "B"}'


def test_a_sequence_repeats_its_last_entry_once_exhausted():
    # The last entry is the steady state: a skill that keeps calling gets the
    # same answer it stopped on, and `trajectory.max_calls` is what catches a
    # loop that should have ended.
    tool = build_mock_tool(ToolSpec(name="poll", returns=["running", "done"]))
    assert [tool.call() for _ in range(4)] == ["running", "done", "done", "done"]


def test_a_sequence_ignores_the_arguments_it_is_handed():
    tool = build_mock_tool(ToolSpec(name="poll", returns=["one", "two"]))
    assert tool.call(unexpected="x") == "one"
    assert tool.call() == "two"


def test_each_built_tool_counts_its_own_calls():
    # Two builds of the same spec -- two arms, two attempts, two work items --
    # must never share a counter: the second run would start mid-sequence.
    spec = ToolSpec(name="poll", returns=["one", "two"])
    first, second = build_mock_tool(spec), build_mock_tool(spec)
    assert first.call() == "one"
    assert second.call() == "one"
    assert first.call() == "two"


def test_a_sequence_is_consumed_exactly_once_under_concurrent_calls():
    # A framework may run several calls from one model turn in parallel, so
    # the counter is locked: every entry is handed out exactly once before
    # the last one repeats, whichever thread gets there first.
    from concurrent.futures import ThreadPoolExecutor

    entries = [str(n) for n in range(50)]
    tool = build_mock_tool(ToolSpec(name="poll", returns=entries))
    with ThreadPoolExecutor(max_workers=8) as pool:
        seen = list(pool.map(lambda _: tool.call(), range(50)))
    assert sorted(seen, key=int) == entries


# --- a lookup keyed by the call's arguments ------------------------------------


def test_a_lookup_hands_back_the_first_entry_whose_when_matches():
    tool = build_mock_tool(
        ToolSpec(
            name="get_work_item",
            returns=[
                {"when": {"id": "A"}, "value": '{"id": "A", "parent": "B"}'},
                {"when": {"id": "B"}, "value": '{"id": "B", "parent": null}'},
            ],
        )
    )
    assert tool.call(id="B") == '{"id": "B", "parent": null}'
    assert tool.call(id="A") == '{"id": "A", "parent": "B"}'
    # Order-independent: the argument decides, not the call count.
    assert tool.call(id="B") == '{"id": "B", "parent": null}'


def test_a_lookup_matches_on_a_subset_of_the_arguments():
    tool = build_mock_tool(
        ToolSpec(name="search", returns=[{"when": {"repo": "a/b"}, "value": "found"}])
    )
    assert tool.call(repo="a/b", query="anything", page=2) == "found"


def test_a_lookup_entry_without_when_is_the_fallback():
    tool = build_mock_tool(
        ToolSpec(
            name="get_work_item",
            returns=[{"when": {"id": "A"}, "value": "A"}, {"value": "not found"}],
        )
    )
    assert tool.call(id="A") == "A"
    assert tool.call(id="Z") == "not found"
    assert tool.call() == "not found"


def test_a_call_matching_no_lookup_entry_gets_a_readable_message_not_an_exception():
    # An unscripted argument is an eval signal -- the skill asked for
    # something the author did not anticipate -- so the model reads a message
    # and the transcript shows it, rather than the run erroring.
    tool = build_mock_tool(
        ToolSpec(name="get_work_item", returns=[{"when": {"id": "A"}, "value": "A"}])
    )
    message = tool.call(id="Z", expand=True)
    assert message == NO_RESPONSE_SCRIPTED.format(
        name="get_work_item", arguments='{"expand": true, "id": "Z"}'
    )
    assert tool.call() == NO_RESPONSE_SCRIPTED.format(name="get_work_item", arguments="{}")


def test_the_no_match_message_survives_arguments_json_cannot_encode():
    tool = build_mock_tool(ToolSpec(name="t", returns=[{"when": {"id": "A"}, "value": "A"}]))
    assert isinstance(tool.call(id=object()), str)
    assert isinstance(tool.call(id=float("nan"), blob=b"\x00"), str)


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
    # them, but they are not legal in a Python identifier in any position, and
    # no provider accepts them either. The cleaning pass is plain ASCII
    # membership, so they cannot survive it. Assert the property, not one
    # output string, since totality is the actual thing being guaranteed.
    assert skill_tool_name("²").isidentifier()
    assert skill_tool_name("①").isidentifier()
    assert skill_tool_name("Level²").isidentifier()


def test_a_non_ascii_name_becomes_one_a_provider_accepts():
    # 'café' is a valid Python identifier, but OpenAI and Anthropic reject the
    # accented letter. The offered tool has to be registrable, so the rule is
    # the provider's, not Python's.
    assert skill_tool_name("café") == "caf_"
    assert TOOL_NAME_PATTERN.fullmatch(skill_tool_name("café"))
    assert TOOL_NAME_PATTERN.fullmatch(skill_tool_name("résumé-writer"))


def test_a_long_name_is_cut_to_the_provider_limit():
    long_name = "x" * 70
    assert skill_tool_name(long_name) == "x" * 64
    assert TOOL_NAME_PATTERN.fullmatch(skill_tool_name(long_name))


def test_the_prefix_never_pushes_a_name_over_the_limit():
    # The leading-digit prefix is added before the cut, so the result is still
    # 64 characters and still starts with the prefix.
    name = skill_tool_name("9" * 70)
    assert len(name) == 64
    assert name.startswith("skill_")
    assert TOOL_NAME_PATTERN.fullmatch(name)


def test_every_output_matches_the_provider_rule():
    # Totality: whatever the skill is called, the tool name is one a provider
    # will register.
    for skill_name in ["", "---", "²", "①", "Level²", "a b", "🎉 party", "-lead", "_x"]:
        assert TOOL_NAME_PATTERN.fullmatch(skill_tool_name(skill_name)), skill_name


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
    # each other. AgentTool being frozen only blocks reassigning the attribute,
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
