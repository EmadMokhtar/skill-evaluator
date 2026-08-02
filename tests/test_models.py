from pathlib import Path

import pytest
from pydantic import ValidationError

from skill_eval.models import (
    BudgetSpec,
    CaseOutcome,
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeRequest,
    JudgeSpec,
    JudgeVerdict,
    RubricCheck,
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


def test_a_case_defaults_to_loaded_mode_with_no_judge():
    case = EvalCase(name="c", task="t")
    assert case.mode == "loaded"
    assert case.judge is None


def test_a_case_can_declare_offered_mode_and_a_rubric():
    case = EvalCase(
        name="c",
        task="t",
        mode="offered",
        judge=JudgeSpec(expected="a plain answer", rubric=["names the order id"]),
        trajectory=TrajectorySpec(skill_triggered=True),
    )
    assert case.mode == "offered"
    assert case.judge.rubric == ["names the order id"]
    assert case.trajectory.skill_triggered is True


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        EvalCase(name="c", task="t", mode="offerred")


def test_a_judge_spec_forbids_unknown_keys():
    # Without extra="forbid" a typo like `rubrics:` yields a vacuously-passing case.
    with pytest.raises(ValidationError):
        JudgeSpec(rubrics=["oops"])


def test_a_result_reports_no_triggering_decision_by_default():
    # None means "this run was not an offered run", which is distinct from False.
    assert RunResult().skill_triggered is None


def test_a_verdict_knows_when_it_errored():
    assert JudgeVerdict().errored is False
    assert JudgeVerdict(error="boom").errored is True


def test_a_request_carries_the_checks_it_wants_graded():
    request = JudgeRequest(
        task="why?",
        output="because",
        checks=[RubricCheck(id="r1", text="explains why")],
    )
    assert [check.id for check in request.checks] == ["r1"]


def test_an_errored_score_cannot_also_be_passed():
    # The two must never disagree: an infra failure is not a green case.
    with pytest.raises(ValidationError):
        EvalScore(evaluator="judge", passed=True, errored=True)


def test_a_score_carries_per_check_evidence():
    score = EvalScore(
        evaluator="judge",
        passed=False,
        checks=[CheckResult(id="r1", passed=False, evidence="never mentions the window")],
    )
    assert score.checks[0].evidence == "never mentions the window"
    assert score.cost_usd == 0.0


def test_judge_cost_is_summed_across_outcomes_and_kept_off_the_run_cost():
    report = RunReport(
        outcomes=[
            CaseOutcome(
                skill_name="s",
                case_name="c",
                runner="fake",
                status="passed",
                scores=[EvalScore(evaluator="judge", passed=True, cost_usd=0.002)],
                result=RunResult(cost_usd=0.01),
            )
        ]
    )
    assert report.judge_cost_usd == pytest.approx(0.002)
    assert report.outcomes[0].result.cost_usd == pytest.approx(0.01)
