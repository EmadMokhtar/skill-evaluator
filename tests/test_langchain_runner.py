"""The second real adapter, exercised offline with a scripted model."""

from pathlib import Path

import pytest

from langchain_fakes import (
    FunctionChatModel,
    StatusError,
    scripted,
    system_text,
    text,
    tool_call,
    tool_results,
)
from skill_lens.models import EvalCase, Skill, ToolSpec
from skill_lens.runners.base import Runner
from skill_lens.runners.langchain import LangChainRunner
from skill_lens.runners.prompting import BASELINE_PREAMBLE, OFFERED_PREAMBLE, WORKSPACE_PREAMBLE
from skill_lens.runners.tools import BUILTIN_TOOL_NAMES, skill_tool_name
from skill_lens.workspace import Workspace

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


def test_the_served_model_name_is_also_read_from_the_anthropic_shaped_key():
    # langchain-openai writes `model_name`; langchain-anthropic has written the
    # API's own `model` field. Either must be read, or an Anthropic run falls
    # back to the configured string and is reported and priced wrongly.
    runner = LangChainRunner(model=scripted(text("done", model="claude-sonnet-4-6-20260301")))
    assert runner.run(SKILL, case()).model == "claude-sonnet-4-6-20260301"


def test_model_name_wins_over_model_when_both_are_present():
    runner = LangChainRunner(
        model=scripted(text("done", model_name="served-snapshot", model="raw-field"))
    )
    assert runner.run(SKILL, case()).model == "served-snapshot"


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


# --- failures and retries -------------------------------------------------


def raising(exc: Exception) -> FunctionChatModel:
    def reply(messages, turn):
        raise exc

    return FunctionChatModel(reply=reply)


def flaky(exc: Exception, then: str) -> FunctionChatModel:
    """Raises `exc` on the first turn, answers `then` afterwards."""

    def reply(messages, turn):
        if turn == 0:
            raise exc
        return text(then)

    return FunctionChatModel(reply=reply)


def test_a_provider_failure_is_reported_not_raised():
    result = LangChainRunner(model=raising(StatusError(500)), retries=0).run(SKILL, case())
    assert result.errored is True
    assert result.error == "StatusError: status 500"
    assert result.output == ""


def test_a_transient_failure_is_retried_and_can_succeed():
    slept = []
    runner = LangChainRunner(
        model=flaky(StatusError(429), "recovered"),
        retries=2,
        retry_backoff_seconds=0.01,
        sleep=slept.append,
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_a_5xx_failure_is_retried_and_can_succeed():
    slept = []
    runner = LangChainRunner(
        model=flaky(StatusError(503), "recovered"),
        retries=2,
        retry_backoff_seconds=0.01,
        sleep=slept.append,
    )
    assert runner.run(SKILL, case()).output == "recovered"
    assert slept == [0.01]


def test_backoff_grows_exponentially_between_attempts():
    slept = []
    runner = LangChainRunner(
        model=raising(StatusError(429)), retries=3, retry_backoff_seconds=1.0, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert slept == [1.0, 2.0, 4.0]
    assert result.errored is True


def test_a_permanent_failure_is_not_retried():
    model = raising(StatusError(401))
    slept = []
    result = LangChainRunner(model=model, retries=3, sleep=slept.append).run(SKILL, case())
    assert len(model.turns) == 1
    assert slept == []
    assert result.errored is True


class APIConnectionError(Exception):
    """Named like the openai/anthropic SDKs' network error: no status code."""


def test_an_sdk_connection_error_is_transient_by_name():
    # The SDKs' connection and timeout errors carry no status_code and do not
    # subclass the builtins; the class name is the only provider-neutral signal.
    slept = []
    runner = LangChainRunner(
        model=flaky(APIConnectionError("reset"), "recovered"), retries=1, sleep=slept.append
    )
    assert runner.run(SKILL, case()).output == "recovered"
    assert slept == [1.0]


def test_a_builtin_timeout_is_transient():
    runner = LangChainRunner(
        model=flaky(TimeoutError(), "recovered"), retries=1, sleep=lambda _: None
    )
    assert runner.run(SKILL, case()).output == "recovered"


def test_an_unrelated_exception_is_not_retried():
    model = raising(ValueError("bad request shape"))
    result = LangChainRunner(model=model, retries=3, sleep=lambda _: None).run(SKILL, case())
    assert len(model.turns) == 1
    assert result.error == "ValueError: bad request shape"


def test_the_configured_model_is_reported_on_a_failure():
    # A model string LangChain cannot resolve raises during construction,
    # before any network call, so this stays offline -- and the report row
    # must still say which model was attempted.
    result = LangChainRunner(model="not-a-real-provider:some-model").run(SKILL, case())
    assert result.errored is True
    assert result.model == "not-a-real-provider:some-model"


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    import skill_lens.runners.langchain as adapter

    def boom(messages):
        raise RuntimeError("transcript exploded")

    monkeypatch.setattr(adapter, "_transcript", boom)
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.errored is True
    assert "transcript exploded" in result.error


def test_a_missing_optional_extra_propagates_rather_than_becoming_an_errored_case(monkeypatch):
    import skill_lens.runners.langchain as adapter

    def explode() -> None:
        raise adapter.RunnerDependencyError("the 'langchain' runner needs its optional extra")

    monkeypatch.setattr(adapter, "_require_langchain", explode)
    with pytest.raises(adapter.RunnerDependencyError):
        LangChainRunner(model=scripted(text("done"))).run(SKILL, case())


def test_the_dependency_message_names_the_extra(monkeypatch):
    import builtins

    import skill_lens.runners.langchain as adapter

    real_import = builtins.__import__

    def no_langchain(name, *args, **kwargs):
        if name == "langchain":
            raise ImportError("No module named 'langchain'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_langchain)
    with pytest.raises(adapter.RunnerDependencyError, match=r"skill-lens\[langchain\]"):
        adapter._require_langchain()


# --- temperature ----------------------------------------------------------


def test_a_numeric_temperature_reaches_the_chat_model(monkeypatch):
    import skill_lens.runners.langchain as adapter

    seen = {}

    def fake_chat_model(model, temperature):
        seen["model"], seen["temperature"] = model, temperature
        return scripted(text("done"))

    monkeypatch.setattr(adapter, "_chat_model", fake_chat_model)
    LangChainRunner(model="openai:gpt-4o-mini", temperature=0.7).run(SKILL, case())
    assert seen == {"model": "openai:gpt-4o-mini", "temperature": 0.7}


def test_init_chat_model_receives_the_temperature_and_omits_it_when_unset(monkeypatch):
    # ChatOpenAI refuses to construct without a key even though nothing is
    # sent; a placeholder keeps this offline.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-construction")
    from skill_lens.runners.langchain import _chat_model

    warm = _chat_model("openai:gpt-4o-mini", 0.7)
    assert warm.temperature == 0.7
    unset = _chat_model("openai:gpt-4o-mini", "unset")
    # ChatOpenAI leaves temperature None when none was given.
    assert unset.temperature is None


# --- offered mode ---------------------------------------------------------


def offered_case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    kwargs["mode"] = "offered"
    return EvalCase(**kwargs)


def test_an_offered_skill_is_registered_as_a_tool_named_after_it():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case(tools=[ToolSpec(name="lookup_order")]))
    assert sorted(model.bound_tools[0]) == ["lookup_order", "order_support"]


def test_an_offered_skill_is_not_forced_into_the_system_prompt():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case())
    assert system_text(model.turns[0]) == OFFERED_PREAMBLE


def test_an_offered_skill_that_is_declined_reports_false():
    result = LangChainRunner(model=scripted(text("done"))).run(SKILL, offered_case())
    assert result.skill_triggered is False


def test_an_offered_skill_that_is_chosen_reports_true_and_appears_in_the_trajectory():
    runner = LangChainRunner(model=scripted(tool_call("order_support", {}), text("done")))
    result = runner.run(SKILL, offered_case())
    assert result.skill_triggered is True
    assert [call.name for call in result.tool_calls] == ["order_support"]


def test_choosing_the_skill_delivers_its_instructions_to_the_model():
    model = scripted(tool_call("order_support", {}), text("done"))
    LangChainRunner(model=model).run(SKILL, offered_case())
    assert "Always look up the order first." in tool_results(model.turns[1])["order_support"]


def test_registration_and_detection_agree_on_a_name_replace_would_get_wrong():
    weird = Skill(name="123 weird name!", description="odd", instructions="Weird.", path=Path("."))
    expected = skill_tool_name(weird.name)
    model = scripted(tool_call(expected, {}), text("done"))
    result = LangChainRunner(model=model).run(weird, offered_case())
    assert expected in model.bound_tools[0]
    assert result.skill_triggered is True


# --- baseline arm and workspace ------------------------------------------

EMPTY_SKILL = Skill(
    name="order-support", description="", instructions="", variant="baseline", path=Path(".")
)


def test_a_baseline_prompt_never_leaks_the_skill_name():
    model = scripted(text("done"))
    LangChainRunner(model=model).run(EMPTY_SKILL, case())
    assert system_text(model.turns[0]) == BASELINE_PREAMBLE
    assert "order-support" not in system_text(model.turns[0])


def test_the_builtin_tools_are_registered_only_when_a_workspace_is_given(tmp_path):
    with_workspace = scripted(text("done"))
    LangChainRunner(model=with_workspace).run(
        SKILL, case(), workspace=Workspace(root=tmp_path.resolve())
    )
    assert set(BUILTIN_TOOL_NAMES) <= set(with_workspace.bound_tools[0])

    without = scripted(text("done"))
    LangChainRunner(model=without).run(SKILL, case())
    # With no tools at all create_agent never binds, so flatten rather than index.
    bound = {name for group in without.bound_tools for name in group}
    assert not set(BUILTIN_TOOL_NAMES) & bound


def test_the_workspace_preamble_is_delivered_with_a_workspace(tmp_path):
    model = scripted(text("done"))
    LangChainRunner(model=model).run(SKILL, case(), workspace=Workspace(root=tmp_path.resolve()))
    assert system_text(model.turns[0]).endswith(WORKSPACE_PREAMBLE)


def test_a_model_writing_a_file_lands_it_in_the_workspace(tmp_path):
    runner = LangChainRunner(
        model=scripted(
            tool_call("write_file", {"path": "report.md", "content": "north 120"}), text("done")
        )
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.read("report.md") == "north 120"
    assert [call.name for call in result.tool_calls] == ["write_file"]


def test_a_model_writing_outside_the_root_is_refused_not_errored(tmp_path):
    runner = LangChainRunner(
        model=scripted(
            tool_call("write_file", {"path": "../escape.txt", "content": "x"}), text("done")
        )
    )
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, case(), workspace=workspace)
    assert result.error is None
    assert workspace.listing() == []
