from pathlib import Path

from skill_eval.models import (
    CaseOutcome,
    EvalScore,
    RunReport,
    RunResult,
    Skill,
    ToolCall,
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
