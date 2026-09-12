"""The second real adapter, exercised offline with a scripted model."""

from pathlib import Path

from langchain_fakes import scripted, system_text, text, tool_call, tool_results
from skill_lens.models import EvalCase, Skill, ToolSpec
from skill_lens.runners.base import Runner
from skill_lens.runners.langchain import LangChainRunner

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_final_text_becomes_the_output():
    result = LangChainRunner(model=scripted(text("Order 1234 was delivered."))).run(SKILL, case())
    assert result.output == "Order 1234 was delivered."
    assert result.errored is False


def test_the_runner_registers_its_name_and_declares_that_it_needs_a_key():
    assert LangChainRunner(model=scripted(text("x"))).name == "langchain"
    assert LangChainRunner.needs_api_key is True


def test_langchain_runner_satisfies_the_runner_protocol():
    assert isinstance(LangChainRunner(model=scripted(text("x"))), Runner)


def test_declared_tools_are_offered_to_the_model():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(
        SKILL, case(tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")])
    )
    assert sorted(model.bound_tools[0]) == ["issue_refund", "lookup_order"]


def test_the_skill_instructions_reach_the_model_as_the_system_prompt():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case())
    seen = system_text(model.turns[0])
    assert "Always look up the order first." in seen
    assert "order-support" in seen


def test_the_task_reaches_the_model():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case(task="refund order 1234"))
    assert model.turns[0][-1].content == "refund order 1234"


def test_tool_calls_are_captured_in_order_with_their_arguments():
    runner = LangChainRunner(
        model=scripted(
            tool_call("lookup_order", {"order_id": "1234"}),
            tool_call("check_policy", {}),
            text("Refund declined."),
        )
    )
    result = runner.run(
        SKILL,
        case(
            tools=[
                ToolSpec(name="lookup_order", parameters={"order_id": "string"}),
                ToolSpec(name="check_policy"),
            ]
        ),
    )
    assert [call.name for call in result.tool_calls] == ["lookup_order", "check_policy"]
    assert result.tool_calls[0].arguments == {"order_id": "1234"}
    assert result.output == "Refund declined."


def test_the_canned_return_value_is_handed_back_to_the_model():
    model = scripted(tool_call("lookup_order", {"order_id": "1234"}), text("done"))
    LangChainRunner(model=model).run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", returns='{"status": "shipped"}')])
    )
    assert tool_results(model.turns[1]) == {"lookup_order": '{"status": "shipped"}'}


def test_a_model_omitting_a_required_tool_argument_does_not_error_the_case():
    # The JSON schema is descriptive only: LangChain passes a dict schema's
    # arguments straight through, and every AgentTool accepts any arguments.
    # A model choosing to omit one is captured faithfully, never turned into
    # an infra error.
    runner = LangChainRunner(model=scripted(tool_call("lookup_order", {}), text("done")))
    result = runner.run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})])
    )
    assert result.errored is False
    assert result.tool_calls[0].arguments == {}
    assert result.output == "done"


def test_usage_is_summed_over_every_model_turn():
    runner = LangChainRunner(model=scripted(tool_call("ping", {}), text("done")))
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    # Two turns, 10 in / 5 out each (tests/langchain_fakes.py USAGE).
    assert result.input_tokens == 20
    assert result.output_tokens == 10
    assert result.tokens == 30
    assert result.latency_ms >= 0


def test_the_transcript_is_the_whole_message_list_as_plain_dicts():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    # human task, then the model's reply -- the system prompt is create_agent's,
    # not a message in the state.
    assert [m["type"] for m in result.transcript] == ["human", "ai"]
    assert isinstance(result.transcript[0], dict)


def test_the_served_model_name_is_read_from_the_last_response_that_has_one():
    runner = LangChainRunner(
        model=scripted(tool_call("ping", {}, model_name="gpt-4o-mini-2026-01-01"), text("done"))
    )
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    assert result.model == "gpt-4o-mini-2026-01-01"


def test_a_model_instance_with_no_served_name_reports_an_empty_model():
    # A string model falls back to the configured id; an instance has none.
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.model == ""


def test_an_unpriced_model_reports_zero_cost_with_a_note():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_a_loaded_run_reports_no_triggering_decision():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.skill_triggered is None
