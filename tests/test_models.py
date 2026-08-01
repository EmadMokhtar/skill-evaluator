from pathlib import Path

import pytest
from pydantic import ValidationError

from skill_eval.models import (
    BudgetSpec,
    CaseOutcome,
    EvalCase,
    EvalScore,
    RunReport,
    RunResult,
    Skill,
    ToolCall,
    ToolSpec,
    TrajectorySpec,
)


def _result(output="ok", error=None):
    return RunResult(output=output, error=error)


def test_skill_holds_metadata_and_body():
    skill = Skill(
        name="pdf",
        description="Work with PDFs",
        instructions="Do the thing.",
        path=Path("/skills/pdf"),
    )
    assert skill.name == "pdf"
    assert skill.instructions == "Do the thing."


def test_run_result_defaults_are_empty_and_not_errored():
    result = _result()
    assert result.tool_calls == []
    assert result.cost_usd == 0.0
    assert result.errored is False


def test_run_result_with_error_is_errored():
    assert _result(error="boom").errored is True


def test_tool_call_roundtrips_arguments():
    call = ToolCall(name="search", arguments={"q": "x"})
    assert call.arguments["q"] == "x"


def _outcome(skill, case, status):
    return CaseOutcome(
        skill_name=skill,
        case_name=case,
        runner="fake",
        status=status,
        scores=[EvalScore(evaluator="assertion", passed=status == "passed", score=1.0, detail="")],
        result=_result(),
    )


def test_run_report_aggregates_counts_and_pass_rate():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "errored"),
            _outcome("b", "c4", "passed"),
        ]
    )
    assert report.total == 4
    assert report.passed == 2
    assert report.failed == 1
    assert report.errored == 1
    assert report.pass_rate == 0.5


def test_pass_rate_by_skill_groups_correctly():
    report = RunReport(
        outcomes=[
            _outcome("a", "c1", "passed"),
            _outcome("a", "c2", "failed"),
            _outcome("b", "c3", "passed"),
        ]
    )
    assert report.pass_rate_by_skill() == {"a": 0.5, "b": 1.0}


def test_empty_report_has_zero_pass_rate():
    report = RunReport()
    assert report.total == 0
    assert report.pass_rate == 0.0


def test_tool_spec_rejects_an_unsupported_parameter_type():
    # An unknown type is an authoring mistake in the user's YAML, so it must be
    # rejected at parse time rather than reaching the runner.
    with pytest.raises(ValidationError):
        ToolSpec(name="lookup", parameters={"order_id": "uuid"})


def test_tool_spec_rejects_a_name_that_is_not_an_identifier():
    with pytest.raises(ValidationError):
        ToolSpec(name="look up")


def test_tool_spec_accepts_a_full_declaration():
    spec = ToolSpec(
        name="lookup_order",
        description="Look up an order by its id",
        parameters={"order_id": "string", "verbose": "boolean"},
        returns='{"id": "1234"}',
    )
    assert spec.parameters["order_id"] == "string"
    assert spec.returns == '{"id": "1234"}'


def test_case_carries_tools_trajectory_and_budget():
    case = EvalCase(
        name="refund",
        task="refund order 1234",
        tools=[ToolSpec(name="lookup_order", parameters={"order_id": "string"})],
        trajectory=TrajectorySpec(called=["lookup_order"], forbidden=["issue_refund"]),
        budget=BudgetSpec(max_tokens=4000),
    )
    assert case.tools[0].name == "lookup_order"
    assert case.trajectory.called == ["lookup_order"]
    assert case.budget.max_tokens == 4000


def test_case_without_the_new_blocks_keeps_working():
    case = EvalCase(name="plain", task="hello")
    assert case.tools == []
    assert case.trajectory is None
    assert case.budget is None


def test_trajectory_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        TrajectorySpec(calls=["lookup_order"])


def test_budget_spec_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        BudgetSpec(max_token=10)


def test_run_result_tokens_is_the_sum_of_input_and_output():
    result = RunResult(input_tokens=112, output_tokens=15)
    assert result.tokens == 127


def test_run_result_rejects_writing_tokens_directly():
    # `tokens` is derived. Accepting it silently would let a runner report a
    # total that disagrees with the split it was priced from.
    with pytest.raises(ValidationError):
        RunResult(tokens=127)
