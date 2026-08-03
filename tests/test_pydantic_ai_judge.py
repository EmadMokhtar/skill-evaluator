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
    # Distinguish the two counts rather than just asserting both are > 0: a
    # request/response swap would misreport spend silently otherwise. The
    # rendered request (system prompt + rubric) is far larger than the
    # structured-output reply, so input must exceed output here.
    assert verdict.input_tokens > verdict.output_tokens > 0


def test_the_verdict_reports_the_model_the_provider_actually_served():
    # A provider may serve a dated snapshot rather than the id requested, so
    # the verdict must reflect what _model_name reads off the response.
    def reply(messages, info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": []})])

    judge = PydanticAIJudge(model=FunctionModel(reply, model_name="dated-snapshot-2026-08-01"))
    verdict = judge.judge(REQUEST)
    assert verdict.model == "dated-snapshot-2026-08-01"


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
    assert "no price data" in verdict.cost_note


def test_the_model_is_reported_on_a_provider_failure():
    # The error branch must set model=configured, same as the sibling
    # runner's `except` in `run` -- a report row for a failed judge call must
    # still say which model was attempted. A model string pydantic_ai cannot
    # resolve raises during agent construction, before any network call, so
    # this stays offline.
    judge = PydanticAIJudge(model="not-a-real-provider:some-model")
    verdict = judge.judge(REQUEST)
    assert verdict.errored is True
    assert verdict.model == "not-a-real-provider:some-model"


def test_a_numeric_temperature_is_sent_to_the_model():
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": []})])

    PydanticAIJudge(model=FunctionModel(reply), temperature=0.7).judge(REQUEST)
    assert seen["settings"] == {"temperature": 0.7}


def test_an_unset_temperature_sends_no_temperature_at_all():
    # Reasoning models reject any temperature but 1, so "unset" must mean no
    # model_settings at all rather than an omitted key inside one.
    seen = {}

    def reply(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = info.model_settings
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args={"checks": []})])

    PydanticAIJudge(model=FunctionModel(reply), temperature="unset").judge(REQUEST)
    assert seen["settings"] is None


def test_a_failure_while_capturing_the_result_is_reported_not_raised(monkeypatch):
    # The never-raise contract covers our own capture code, not just the
    # provider. A real provider can emit shapes the judge's own capture code
    # can't serialise, and that must not crash the whole eval run -- only the
    # one case. Patched on skill_eval.judges.pydantic_ai, not
    # skill_eval.runners.pydantic_ai: the judge imports calculate_cost by
    # name at import time, so patching the runner's module would silently
    # miss.
    import skill_eval.judges.pydantic_ai as judge_module

    def boom(usage, model_name, provider):
        raise RuntimeError("cost calc exploded")

    monkeypatch.setattr(judge_module, "calculate_cost", boom)
    judge = PydanticAIJudge(model=structured([{"id": "r1", "passed": True, "evidence": "x"}]))
    verdict = judge.judge(REQUEST)
    assert verdict.errored is True
    assert "cost calc exploded" in verdict.error
