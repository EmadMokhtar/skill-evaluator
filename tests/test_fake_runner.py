"""FakeRunner keeps the pipeline testable with no network and no cost."""

from pathlib import Path

from skill_eval.models import EvalCase, RunResult, Skill, ToolCall
from skill_eval.runners.base import Runner
from skill_eval.runners.fake import FakeRunner

SKILL = Skill(name="pdf", description="", instructions="", path=Path("."))


def case(task: str) -> EvalCase:
    return EvalCase(name=task, task=task)


def test_scripted_response_is_keyed_on_the_task():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, case("extract")).output == "used pdfplumber"


def test_same_task_returns_an_equal_result():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, case("extract")) == runner.run(SKILL, case("extract"))


def test_callers_cannot_corrupt_the_scripted_state():
    runner = FakeRunner(responses={"task": RunResult(output="original")})
    result1 = runner.run(SKILL, case("task"))
    result1.output = "mutated"
    result1.tool_calls.append(ToolCall(name="sneaky"))
    result2 = runner.run(SKILL, case("task"))
    assert result2.output == "original"
    assert result2.tool_calls == []


def test_default_covers_unscripted_tasks():
    runner = FakeRunner(default=RunResult(output="fallback"))
    assert runner.run(SKILL, case("anything")).output == "fallback"


def test_unscripted_task_without_a_default_echoes_the_skill_name():
    runner = FakeRunner()
    assert "pdf" in runner.run(SKILL, case("anything")).output


def test_a_scripted_error_is_reported_not_raised():
    runner = FakeRunner(responses={"boom": RunResult(error="provider exploded")})
    assert runner.run(SKILL, case("boom")).errored is True


def test_scripted_tool_calls_survive_the_round_trip():
    runner = FakeRunner(responses={"t": RunResult(tool_calls=[ToolCall(name="read_pdf")])})
    assert runner.run(SKILL, case("t")).tool_calls[0].name == "read_pdf"


def test_the_runner_exposes_its_name():
    assert FakeRunner().name == "fake"


def test_fake_runner_satisfies_the_runner_protocol():
    """Runner is @runtime_checkable; this is the only thing that exercises isinstance
    against it, so a protocol drift (a renamed run, a dropped name) would otherwise
    go undetected."""
    assert isinstance(FakeRunner(), Runner)
