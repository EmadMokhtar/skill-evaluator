"""The real judge, exercised offline with a scripted model."""

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skill_eval.judges.base import Judge
from skill_eval.judges.pydantic_ai import PydanticAIJudge
from skill_eval.models import JudgeRequest, RubricCheck

REQUEST = JudgeRequest(
    task="Why can't I return this?",
    output="The return window is 30 days.",
    checks=[RubricCheck(id="r1", text="states the 30-day window")],
)


def structured(checks: list[dict]) -> FunctionModel:
    """A model that answers with the judge's structured-output tool."""

    def reply(messages, info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": checks})])

    return FunctionModel(reply)


def raising(exc: Exception) -> FunctionModel:
    def reply(messages, info: AgentInfo) -> ModelResponse:
        raise exc

    return FunctionModel(reply)


def test_it_satisfies_the_judge_protocol():
    assert isinstance(PydanticAIJudge(model=structured([])), Judge)


def test_it_registers_its_name():
    assert PydanticAIJudge(model=structured([])).name == "pydantic-ai"


def test_the_model_verdicts_become_check_results():
    judge = PydanticAIJudge(
        model=structured([{"id": "r1", "passed": True, "evidence": "'30 days'"}])
    )
    verdict = judge.judge(REQUEST)
    assert verdict.errored is False
    assert [(c.id, c.passed, c.evidence) for c in verdict.checks] == [("r1", True, "'30 days'")]


def test_usage_is_captured():
    judge = PydanticAIJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    verdict = judge.judge(REQUEST)
    assert verdict.input_tokens > 0
    assert verdict.output_tokens > 0


def test_the_rendered_request_reaches_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["instructions"] = messages[0].instructions or ""
        seen["prompt"] = str(messages[0].parts[-1].content)
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": []})])

    PydanticAIJudge(model=FunctionModel(reply)).judge(REQUEST)
    assert "evidence" in seen["instructions"]
    assert "r1: states the 30-day window" in seen["prompt"]
    assert "The return window is 30 days." in seen["prompt"]


def test_a_provider_failure_is_reported_not_raised():
    # Judges must never raise: the evaluator turns the error into an errored
    # case, which is an infra signal, not a skill that got worse.
    judge = PydanticAIJudge(model=raising(ModelHTTPError(status_code=500, model_name="m")))
    verdict = judge.judge(REQUEST)
    assert verdict.errored is True
    assert "500" in verdict.error


def test_a_transient_failure_is_retried_before_giving_up():
    attempts = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ModelHTTPError(status_code=429, model_name="m")
        name = info.output_tools[0].name
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=name, args={"checks": [{"id": "r1", "passed": True, "evidence": "x"}]}
                )
            ]
        )

    judge = PydanticAIJudge(model=FunctionModel(reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is False
    assert attempts["n"] == 3


def test_a_permanent_failure_is_not_retried():
    attempts = {"n": 0}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="m")

    judge = PydanticAIJudge(model=FunctionModel(reply), retries=2, sleep=lambda _: None)
    assert judge.judge(REQUEST).errored is True
    assert attempts["n"] == 1


def test_an_unpriceable_model_degrades_to_a_note_rather_than_erroring():
    judge = PydanticAIJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    verdict = judge.judge(REQUEST)
    assert verdict.errored is False
    assert verdict.cost_usd == 0.0
    assert verdict.cost_note != ""
