"""The first real adapter, exercised offline with a scripted model."""

from pathlib import Path

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skill_eval.models import EvalCase, Skill, ToolSpec
from skill_eval.runners.base import Runner
from skill_eval.runners.pydantic_ai import PydanticAIRunner

SKILL = Skill(
    name="order-support",
    description="Handle refund requests",
    instructions="Always look up the order first.",
    path=Path("."),
)


def scripted(*responses: ModelResponse) -> FunctionModel:
    """A model that replays `responses` in order, then repeats the last one."""
    calls = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    return FunctionModel(reply)


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def tool_call(name: str, args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])


def case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "refund order 1234")
    return EvalCase(**kwargs)


def test_the_final_text_becomes_the_output():
    runner = PydanticAIRunner(model=scripted(text("Order 1234 was delivered.")))
    result = runner.run(SKILL, case())
    assert result.output == "Order 1234 was delivered."
    assert result.errored is False


def test_the_runner_registers_its_name():
    assert PydanticAIRunner(model=scripted(text("x"))).name == "pydantic-ai"


def test_declared_tools_are_offered_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(tool.name for tool in info.function_tools)
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(
        SKILL,
        case(tools=[ToolSpec(name="lookup_order"), ToolSpec(name="issue_refund")]),
    )
    assert seen["tools"] == ["issue_refund", "lookup_order"]


def test_the_skill_instructions_reach_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply)).run(SKILL, case())
    assert "Always look up the order first." in seen["instructions"]
    assert "order-support" in seen["instructions"]


def test_tool_calls_are_captured_in_order():
    runner = PydanticAIRunner(
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


def test_json_string_arguments_are_normalised_to_a_dict():
    # Real providers hand back a JSON string; FunctionModel hands back a dict.
    # Both must land in RunResult as a dict.
    runner = PydanticAIRunner(
        model=scripted(tool_call("lookup_order", '{"order_id": "1234"}'), text("done"))
    )
    result = runner.run(
        SKILL, case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})])
    )
    assert result.tool_calls[0].arguments == {"order_id": "1234"}


def test_unparseable_arguments_are_preserved_rather_than_dropped():
    runner = PydanticAIRunner(model=scripted(tool_call("ping", "not json at all"), text("done")))
    result = runner.run(SKILL, case(tools=[ToolSpec(name="ping")]))
    assert result.tool_calls[0].arguments == {"_raw": "not json at all"}


def test_the_canned_return_value_is_handed_back_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if type(part).__name__ == "ToolReturnPart":
                    seen["content"] = part.content
        if "content" not in seen:
            return tool_call("lookup_order", {})
        return text("done")

    runner = PydanticAIRunner(model=FunctionModel(reply))
    runner.run(SKILL, case(tools=[ToolSpec(name="lookup_order", returns='{"status": "shipped"}')]))
    assert seen["content"] == '{"status": "shipped"}'


def test_usage_latency_and_transcript_are_captured():
    runner = PydanticAIRunner(model=scripted(text("done")))
    result = runner.run(SKILL, case())
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tokens == result.input_tokens + result.output_tokens
    assert result.latency_ms >= 0
    assert len(result.transcript) >= 2
    assert isinstance(result.transcript[0], dict)


def test_an_unpriced_model_reports_zero_cost_with_a_note():
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.cost_usd == 0.0
    assert "no price data" in result.cost_note


def test_a_provider_failure_is_reported_not_raised():
    def explode(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=500, model_name="scripted", body=None)

    result = PydanticAIRunner(model=FunctionModel(explode), retries=0).run(SKILL, case())
    assert result.errored is True
    assert "500" in result.error
    assert result.output == ""


def test_a_transient_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelHTTPError(status_code=429, model_name="scripted", body=None)
        return text("recovered")

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(flaky), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_backoff_grows_exponentially_between_attempts():
    def always_429(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=429, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(always_429), retries=3, retry_backoff_seconds=1.0, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert slept == [1.0, 2.0, 4.0]
    assert result.errored is True


def test_a_permanent_failure_is_not_retried():
    attempts = {"n": 0}

    def unauthorized(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="scripted", body=None)

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(unauthorized), retries=3, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert attempts["n"] == 1
    assert slept == []
    assert result.errored is True


def test_the_runner_declares_that_it_needs_a_key():
    assert PydanticAIRunner.needs_api_key is True


def test_a_model_omitting_a_required_tool_argument_does_not_error_the_case():
    # build_mock_tool marks every declared parameter "required" with
    # additionalProperties: false in the JSON schema shown to the model. That
    # schema is descriptive only: PydanticAI's `Tool.from_schema` does not
    # generate a validator from it, so a call that omits a required argument
    # (or sends an undeclared one) is executed exactly like any other call --
    # no ModelRetry prompt back to the model, no exception out of run_sync.
    # A model choosing to omit an argument is therefore captured faithfully
    # in the trajectory rather than being turned into an infra error.
    runner = PydanticAIRunner(
        model=scripted(tool_call("lookup_order", {}), text("done")),
    )
    result = runner.run(
        SKILL,
        case(tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})]),
    )
    assert result.errored is False
    assert result.tool_calls[0].name == "lookup_order"
    assert result.tool_calls[0].arguments == {}
    assert result.output == "done"
    # Pin the actual claim: no retry-prompt part anywhere in the transcript.
    # If PydanticAI HAD rejected the call, a RetryPromptPart (serialised with
    # part_kind == "retry-prompt") would show up in a request message's parts.
    retry_parts = [
        part
        for message in result.transcript
        for part in message.get("parts", [])
        if part.get("part_kind") == "retry-prompt"
    ]
    assert retry_parts == []


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    # The never-raise contract covers our own capture code, not just the
    # provider. A real provider can emit message shapes FunctionModel never
    # does, and a serialisation failure must not crash the run.
    import skill_eval.runners.pydantic_ai as adapter

    def boom(messages):
        raise RuntimeError("transcript exploded")

    monkeypatch.setattr(adapter, "_transcript", boom)
    result = PydanticAIRunner(model=scripted(text("done"))).run(SKILL, case())
    assert result.errored is True
    assert "transcript exploded" in result.error


def test_a_numeric_temperature_is_sent_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply), temperature=0.7).run(SKILL, case())
    assert seen["settings"] == {"temperature": 0.7}


def test_an_unset_temperature_sends_no_temperature_at_all():
    # Reasoning models reject any temperature but 1, so "unset" must mean no
    # model_settings at all rather than an omitted key inside one.
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        return text("done")

    PydanticAIRunner(model=FunctionModel(reply), temperature="unset").run(SKILL, case())
    assert seen["settings"] is None


def test_a_5xx_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelHTTPError(status_code=503, model_name="scripted", body=None)
        return text("recovered")

    slept = []
    runner = PydanticAIRunner(
        model=FunctionModel(flaky), retries=2, retry_backoff_seconds=0.01, sleep=slept.append
    )
    result = runner.run(SKILL, case())
    assert result.output == "recovered"
    assert result.errored is False
    assert slept == [0.01]


def test_pydantic_ai_runner_satisfies_the_runner_protocol():
    """Runner is @runtime_checkable; this guards the seam the whole design rests
    on (Runner.run(skill, case) -> RunResult) against signature drift."""
    assert isinstance(PydanticAIRunner(model=scripted(text("x"))), Runner)
