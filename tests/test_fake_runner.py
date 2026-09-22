"""FakeRunner keeps the pipeline testable with no network and no cost."""

from pathlib import Path

import pytest

from skill_lens.models import EvalCase, RunResult, Skill, ToolCall
from skill_lens.runners.base import Runner
from skill_lens.runners.fake import FakeRunner
from skill_lens.workspace import PathRefused, Workspace

SKILL = Skill(name="pdf", description="", instructions="", path=Path("."))
BASELINE = Skill(name="pdf", description="", instructions="", path=Path("."), variant="baseline")


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


def test_callers_cannot_corrupt_the_default_scripted_state():
    runner = FakeRunner(default=RunResult(output="fallback"))
    result1 = runner.run(SKILL, case("anything"))
    result1.output = "mutated"
    result1.tool_calls.append(ToolCall(name="sneaky"))
    result2 = runner.run(SKILL, case("anything"))
    assert result2.output == "fallback"
    assert result2.tool_calls == []


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


def test_the_baseline_arm_can_be_scripted_separately():
    runner = FakeRunner(
        responses={"t": RunResult(output="with skill")},
        baseline_responses={"t": RunResult(output="without skill")},
    )
    case = EvalCase(name="c", task="t")
    candidate = Skill(name="s", path=Path("."))
    baseline = Skill(name="s", path=Path("."), variant="baseline")

    assert runner.run(candidate, case).output == "with skill"
    assert runner.run(baseline, case).output == "without skill"


def test_an_unscripted_baseline_arm_falls_back_to_the_shared_script():
    runner = FakeRunner(responses={"t": RunResult(output="shared")})
    baseline = Skill(name="s", path=Path("."), variant="baseline")
    assert runner.run(baseline, EvalCase(name="c", task="t")).output == "shared"


def test_fake_runner_still_satisfies_the_protocol():
    assert isinstance(FakeRunner(), Runner)


def test_running_without_a_workspace_is_unchanged():
    runner = FakeRunner(responses={"t": RunResult(output="scripted")})
    assert runner.run(SKILL, EvalCase(name="n", task="t")).output == "scripted"


def test_scripted_writes_land_in_the_workspace(tmp_path):
    runner = FakeRunner(writes={"t": {"report.md": "body"}})
    workspace = Workspace(root=tmp_path.resolve())
    runner.run(SKILL, EvalCase(name="n", task="t"), workspace=workspace)
    assert workspace.read("report.md") == "body"


def test_the_baseline_arm_can_be_scripted_to_write_differently(tmp_path):
    # The only way a zero-cost test can express "this skill helps" for an
    # artifact, mirroring how responses / baseline_responses already work.
    runner = FakeRunner(
        writes={"t": {"report.md": "thorough"}},
        baseline_writes={"t": {"report.md": "thin"}},
    )
    candidate = Workspace(root=(tmp_path / "c").resolve())
    baseline = Workspace(root=(tmp_path / "b").resolve())
    candidate.root.mkdir()
    baseline.root.mkdir()
    runner.run(SKILL, EvalCase(name="n", task="t"), workspace=candidate)
    runner.run(BASELINE, EvalCase(name="n", task="t"), workspace=baseline)
    assert candidate.read("report.md") == "thorough"
    assert baseline.read("report.md") == "thin"


def test_the_baseline_falls_back_to_the_candidate_script(tmp_path):
    runner = FakeRunner(writes={"t": {"report.md": "shared"}})
    workspace = Workspace(root=tmp_path.resolve())
    runner.run(BASELINE, EvalCase(name="n", task="t"), workspace=workspace)
    assert workspace.read("report.md") == "shared"


def test_an_unscripted_task_writes_nothing(tmp_path):
    runner = FakeRunner(writes={"other": {"report.md": "body"}})
    workspace = Workspace(root=tmp_path.resolve())
    runner.run(SKILL, EvalCase(name="n", task="t"), workspace=workspace)
    assert workspace.listing() == []


def test_a_scripted_write_that_escapes_raises(tmp_path):
    # A test helper, not a provider: an escaping path here is a bug in the
    # test that scripted it, and must be loud rather than silently skipped.
    runner = FakeRunner(writes={"t": {"../escape.txt": "x"}})
    workspace = Workspace(root=tmp_path.resolve())
    with pytest.raises(PathRefused):
        runner.run(SKILL, EvalCase(name="n", task="t"), workspace=workspace)


def test_scripted_state_cannot_be_corrupted_by_a_caller(tmp_path):
    # The existing deep-copy invariant, re-checked now that a second scripted
    # mapping exists.
    scripted = {"t": {"report.md": "body"}}
    runner = FakeRunner(writes=scripted, responses={"t": RunResult(output="o")})
    workspace = Workspace(root=tmp_path.resolve())
    result = runner.run(SKILL, EvalCase(name="n", task="t"), workspace=workspace)
    result.output = "mutated"
    assert runner.run(SKILL, EvalCase(name="n", task="t"), workspace=workspace).output == "o"


def test_the_fake_runner_accepts_and_ignores_the_scripts_keyword():
    from skill_lens.scripts import SandboxStatus, ScriptPolicy, ScriptRuntime

    runtime = ScriptRuntime(
        policy=ScriptPolicy(), sandbox=SandboxStatus(backend="none", detail="test")
    )
    runner = FakeRunner(default=RunResult(output="ok"))
    skill = Skill(name="pdf", path=Path("/tmp/pdf"))
    case = EvalCase(name="x", task="t")
    assert runner.run(skill, case, scripts=runtime).output == "ok"


def test_preflight_refuses_a_trajectory_naming_a_tool_the_case_does_not_declare():
    # The fake runner offers a case's mock tools and nothing else, so it makes
    # the same check the framework runners do -- before any case runs.
    from skill_lens.models import TrajectorySpec
    from skill_lens.runners.preflight import UndeclaredTool

    case = EvalCase(name="c", task="t", trajectory=TrajectorySpec(called=["Bash"]))
    with pytest.raises(UndeclaredTool, match=r"runner fake: case 'c' of skill 's'"):
        FakeRunner().preflight([], {"s": [case]})


def test_preflight_returns_nothing_for_a_clean_plan():
    from skill_lens.models import ToolSpec, TrajectorySpec

    case = EvalCase(
        name="c",
        task="t",
        tools=[ToolSpec(name="lookup_order")],
        trajectory=TrajectorySpec(called=["lookup_order"]),
    )
    assert FakeRunner().preflight([], {"s": [case]}) is None
