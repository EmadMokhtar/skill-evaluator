"""The LangChain judge, exercised offline with a scripted model."""

from langchain_core.messages import AIMessage, ToolCall

from langchain_fakes import USAGE, FunctionChatModel, StatusError
from skill_lens.judges.base import Judge
from skill_lens.judges.langchain import LangChainJudge
from skill_lens.models import JudgeRequest, RubricCheck

REQUEST = JudgeRequest(
    task="Why can't I return this?",
    output="The return window is 30 days.",
    checks=[RubricCheck(id="r1", text="states the 30-day window")],
)


def verdict(checks: list[dict], **metadata) -> AIMessage:
    """What a model answers through the judge's structured-output tool.

    LangChain's default `with_structured_output` binds the schema as a tool
    named after the class and parses that tool call's arguments.
    """
    return AIMessage(
        content="",
        tool_calls=[ToolCall(name="JudgeOutput", args={"checks": checks}, id="call-judge")],
        usage_metadata={"input_tokens": 50, "output_tokens": 9, "total_tokens": 59},
        response_metadata=dict(metadata),
    )


def structured(checks: list[dict], **metadata) -> FunctionChatModel:
    return FunctionChatModel(reply=lambda messages, turn: verdict(checks, **metadata))


def raising(exc: Exception) -> FunctionChatModel:
    def reply(messages, turn):
        raise exc

    return FunctionChatModel(reply=reply)


def test_it_satisfies_the_judge_protocol_and_registers_its_name():
    judge = LangChainJudge(model=structured([]))
    assert isinstance(judge, Judge)
    assert judge.name == "langchain"
    assert LangChainJudge.needs_api_key is True


def test_the_model_verdicts_become_check_results():
    judge = LangChainJudge(
        model=structured([{"id": "r1", "passed": True, "evidence": "'30 days'"}])
    )
    result = judge.judge(REQUEST)
    assert result.errored is False
    assert [(c.id, c.passed, c.evidence) for c in result.checks] == [("r1", True, "'30 days'")]


def test_usage_and_the_served_model_are_read_from_the_raw_response():
    judge = LangChainJudge(
        model=structured(
            [{"id": "r1", "passed": True, "evidence": "x"}], model_name="dated-2026-08-01"
        )
    )
    result = judge.judge(REQUEST)
    assert (result.input_tokens, result.output_tokens) == (50, 9)
    assert result.model == "dated-2026-08-01"


def test_the_system_prompt_and_the_rendered_request_reach_the_model():
    model = structured([])
    LangChainJudge(model=model).judge(REQUEST)
    system, human = model.turns[0][0], model.turns[0][-1]
    assert system.type == "system" and "evidence" in str(system.content)
    assert "r1: states the 30-day window" in str(human.content)
    assert "The return window is 30 days." in str(human.content)


def test_the_schema_is_bound_as_the_structured_output_tool():
    model = structured([])
    LangChainJudge(model=model).judge(REQUEST)
    assert model.bound_tools == [["JudgeOutput"]]


def test_a_malformed_verdict_is_errored_not_failed():
    # PydanticAI retries a malformed structured output internally; LangChain
    # hands it back as parsing_error. An unreadable verdict is an infra
    # signal, never a low score.
    model = FunctionChatModel(
        reply=lambda messages, turn: AIMessage(
            content="",
            tool_calls=[ToolCall(name="JudgeOutput", args={"checks": "not a list"}, id="c")],
            usage_metadata=dict(USAGE),
        )
    )
    result = LangChainJudge(model=model, retries=2, sleep=lambda _: None).judge(REQUEST)
    assert result.errored is True
    assert result.error.startswith("JudgeOutputInvalid:")
    assert len(model.turns) == 1  # not transient: one attempt


def test_a_provider_failure_is_reported_not_raised():
    result = LangChainJudge(model=raising(StatusError(500)), retries=0).judge(REQUEST)
    assert result.errored is True
    assert result.error == "StatusError: status 500"


def test_a_transient_failure_is_retried_before_giving_up():
    attempts = {"n": 0}

    def reply(messages, turn):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise StatusError(429)
        return verdict([{"id": "r1", "passed": True, "evidence": "x"}])

    judge = LangChainJudge(model=FunctionChatModel(reply=reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is False
    assert attempts["n"] == 3


def test_a_permanent_failure_is_not_retried():
    model = raising(StatusError(401))
    result = LangChainJudge(model=model, retries=2, sleep=lambda _: None).judge(REQUEST)
    assert result.errored is True
    assert len(model.turns) == 1


def test_an_unpriceable_model_degrades_to_a_note_rather_than_erroring():
    judge = LangChainJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    result = judge.judge(REQUEST)
    assert result.errored is False
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_the_model_is_reported_on_a_provider_failure():
    result = LangChainJudge(model="not-a-real-provider:some-model").judge(REQUEST)
    assert result.errored is True
    assert result.model == "not-a-real-provider:some-model"


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    # Patched on skill_lens.judges.langchain: the judge imports _cost by name.
    import skill_lens.judges.langchain as judge_module

    def boom(*args):
        raise RuntimeError("cost calc exploded")

    monkeypatch.setattr(judge_module, "_cost", boom)
    judge = LangChainJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    result = judge.judge(REQUEST)
    assert result.errored is True
    assert "cost calc exploded" in result.error
