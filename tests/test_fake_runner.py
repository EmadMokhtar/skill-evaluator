from pathlib import Path

from skill_eval.models import RunResult, Skill, ToolCall
from skill_eval.runners.base import Runner
from skill_eval.runners.fake import FakeRunner

SKILL = Skill(name="pdf", description="", instructions="Use pdfplumber.", path=Path("/s/pdf"))


def test_fake_runner_returns_scripted_response_for_task():
    runner = FakeRunner(responses={"extract": RunResult(output="used pdfplumber")})
    assert runner.run(SKILL, "extract").output == "used pdfplumber"


def test_fake_runner_is_deterministic():
    runner = FakeRunner(responses={"extract": RunResult(output="stable")})
    assert runner.run(SKILL, "extract") == runner.run(SKILL, "extract")


def test_fake_runner_returns_defensive_copies():
    """Mutating a result should not corrupt the runner's internal state."""
    original_result = RunResult(output="immutable", tool_calls=[])
    runner = FakeRunner(responses={"task": original_result})

    # First call: get result and mutate it
    result1 = runner.run(SKILL, "task")
    assert result1.output == "immutable"
    result1.tool_calls.append(ToolCall(name="mutated"))
    result1.output = "corrupted"

    # Second call: should still be the original scripted values
    result2 = runner.run(SKILL, "task")
    assert result2.output == "immutable"
    assert len(result2.tool_calls) == 0


def test_unknown_task_returns_default():
    runner = FakeRunner(default=RunResult(output="fallback"))
    assert runner.run(SKILL, "anything").output == "fallback"


def test_unknown_task_without_default_echoes_skill_name():
    result = FakeRunner().run(SKILL, "anything")
    assert "pdf" in result.output
    assert result.errored is False


def test_scripted_error_result_is_errored():
    runner = FakeRunner(responses={"boom": RunResult(error="API down")})
    assert runner.run(SKILL, "boom").errored is True


def test_carries_tool_calls_through():
    runner = FakeRunner(
        responses={"t": RunResult(output="x", tool_calls=[ToolCall(name="read_pdf")])}
    )
    assert runner.run(SKILL, "t").tool_calls[0].name == "read_pdf"


def test_runner_exposes_name():
    assert FakeRunner().name == "fake"


def test_fake_runner_satisfies_the_runner_protocol():
    """Item 8: Runner is @runtime_checkable but nothing exercised isinstance
    against it, so protocol drift would not be caught when M2's adapters
    land. Lock in that FakeRunner actually satisfies the protocol.
    """
    assert isinstance(FakeRunner(), Runner)
