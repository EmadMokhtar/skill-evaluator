"""A case run as several sessions that share one workspace (issue #95).

Every test here is in the zero-cost tier: `FakeRunner`, small recording
runners, or PydanticAI's offline `FunctionModel`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from typer.testing import CliRunner

from langchain_fakes import scripted, text
from skill_lens.cases.loader import CaseParseError, parse_cases_file
from skill_lens.cli import app
from skill_lens.evaluators.judge import build_request
from skill_lens.models import (
    AssertionSpec,
    BudgetSpec,
    EvalCase,
    HookRun,
    JudgeSpec,
    RunResult,
    Skill,
    ToolCall,
    ToolSpec,
    TrajectorySpec,
    WorkspaceSpec,
)
from skill_lens.orchestrator import RunOptions, run_evals
from skill_lens.runners.fake import FakeRunner
from skill_lens.runners.langchain import LangChainRunner
from skill_lens.runners.product import Product, ProductRunner
from skill_lens.runners.pydantic_ai import PydanticAIRunner
from skill_lens.runners.traces import parse_claude_code
from skill_lens.sessions import merge_results, run_sessions
from skill_lens.workspace import create_workspace


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "resume")
    if "task" not in kwargs:
        kwargs.setdefault("sessions", ["start", "continue"])
    if kwargs.get("sessions") is not None:
        kwargs.setdefault("workspace", WorkspaceSpec())
    return EvalCase(**kwargs)


def _skill(tmp_path: Path) -> Skill:
    return Skill(name="planner", description="d", instructions="i", path=tmp_path)


def _evals(tmp_path: Path, *cases: EvalCase) -> Path:
    """Write cases to a YAML file, so each test goes through the real loader."""
    path = tmp_path / "generated.eval.yaml"
    payload = {"cases": [case.model_dump(exclude_none=True) for case in cases]}
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


class _Recorder:
    """Records each call: the task it was given, its case and its workspace."""

    name = "recording"

    def __init__(self, results: dict[str, RunResult] | None = None) -> None:
        self.calls: list[tuple[str, EvalCase, object]] = []
        self._results = results or {}

    def run(self, skill, case, workspace=None):
        self.calls.append((case.task, case, workspace))
        return self._results.get(case.task, RunResult(output=f"did {case.task}")).model_copy(
            deep=True
        )


# -- the model ---------------------------------------------------------------


def test_a_case_may_declare_sessions_instead_of_a_task():
    case = _case()
    assert case.sessions == ["start", "continue"]
    assert case.task is None


def test_a_task_case_has_no_sessions():
    case = EvalCase(name="one", task="do it")
    assert case.task == "do it"
    assert case.sessions is None


def test_task_and_sessions_together_are_refused():
    with pytest.raises(ValidationError, match="both task: and sessions:"):
        EvalCase(name="c", task="t", sessions=["a", "b"], workspace=WorkspaceSpec())


def test_a_case_with_neither_task_nor_sessions_is_refused():
    with pytest.raises(ValidationError, match="task"):
        EvalCase(name="c")


def test_a_single_session_is_refused():
    with pytest.raises(ValidationError, match="at least two"):
        _case(sessions=["only"])


def test_a_blank_session_is_refused():
    with pytest.raises(ValidationError, match="session 2 is blank"):
        _case(sessions=["start", "   "])


def test_sessions_without_a_workspace_are_refused():
    with pytest.raises(ValidationError, match="workspace"):
        EvalCase(name="c", sessions=["a", "b"])


# -- the loader and the CLI ----------------------------------------------------

SESSIONS_YAML = """cases:
  - name: resume
    sessions:
      - Plan the work and do the first step.
      - Continue the work.
    workspace: {}
"""


def test_the_loader_reads_sessions(tmp_path):
    path = tmp_path / "s.eval.yaml"
    path.write_text(SESSIONS_YAML, encoding="utf-8")
    [case] = parse_cases_file(path)
    assert case.sessions == ["Plan the work and do the first step.", "Continue the work."]


def test_task_and_sessions_together_are_an_authoring_error_naming_the_file(tmp_path):
    path = tmp_path / "s.eval.yaml"
    path.write_text(SESSIONS_YAML.replace("    sessions:", "    task: x\n    sessions:"))
    with pytest.raises(CaseParseError, match="s.eval.yaml"):
        parse_cases_file(path)


def test_an_unfilled_session_is_an_authoring_error(tmp_path):
    path = tmp_path / "s.eval.yaml"
    path.write_text(SESSIONS_YAML.replace("Continue the work.", "TODO(skill-lens) next"))
    with pytest.raises(CaseParseError, match="TODO"):
        parse_cases_file(path)


SKILL_MD = """---
name: planner
description: Plans work in files
---
Write the plan to plan.md.
"""


@pytest.mark.parametrize(
    "case_yaml",
    [
        "  - name: both\n    task: x\n    sessions: [a, b]\n    workspace: {}\n",
        "  - name: no-workspace\n    sessions: [a, b]\n",
        "  - name: one\n    sessions: [a]\n    workspace: {}\n",
    ],
)
def test_a_malformed_sessions_case_exits_2(tmp_path, case_yaml):
    skill_dir = tmp_path / "planner"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_MD)
    (skill_dir / "planner.eval.yaml").write_text("cases:\n" + case_yaml)
    result = CliRunner().invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 2, result.output


# -- running the sessions ------------------------------------------------------


def test_a_task_case_makes_one_call_with_the_case_itself(tmp_path):
    runner = _Recorder()
    case = EvalCase(name="one", task="do it")
    result = run_sessions(runner, _skill(tmp_path), case, None, {})
    [(task, seen, workspace)] = runner.calls
    assert seen is case
    assert workspace is None
    assert result.output == "did do it"


def test_each_session_is_one_call_in_order_with_its_own_task(tmp_path):
    runner = _Recorder()
    case = _case(sessions=["first", "second", "third"])
    workspace = create_workspace(case.workspace, label="t")
    try:
        run_sessions(runner, _skill(tmp_path), case, workspace, {})
    finally:
        workspace.cleanup()
    assert [task for task, _, _ in runner.calls] == ["first", "second", "third"]
    assert all(seen.sessions is None for _, seen, _ in runner.calls)
    assert all(seen_workspace is workspace for _, _, seen_workspace in runner.calls)


def test_no_session_runs_after_an_errored_one(tmp_path):
    runner = _Recorder({"second": RunResult(error="provider 500")})
    case = _case(sessions=["first", "second", "third"])
    result = run_sessions(runner, _skill(tmp_path), case, None, {})
    assert [task for task, _, _ in runner.calls] == ["first", "second"]
    assert result.error == "session 2 of 3: provider 500"


def test_the_extra_keywords_reach_every_session(tmp_path):
    seen = []

    class _Scripts:
        name = "scripts"

        def run(self, skill, case, workspace=None, scripts=None):
            seen.append(scripts)
            return RunResult(output="ok")

    run_sessions(_Scripts(), _skill(tmp_path), _case(), None, {"scripts": "runtime"})
    assert seen == ["runtime", "runtime"]


# -- merging the results -------------------------------------------------------


def test_the_output_is_the_last_sessions():
    merged = merge_results([RunResult(output="one"), RunResult(output="two")], ["a", "b"])
    assert merged.output == "two"


def test_tool_calls_are_every_sessions_in_order():
    merged = merge_results(
        [
            RunResult(tool_calls=[ToolCall(name="write_file")]),
            RunResult(tool_calls=[ToolCall(name="read_file"), ToolCall(name="list_files")]),
        ],
        ["a", "b"],
    )
    assert [call.name for call in merged.tool_calls] == ["write_file", "read_file", "list_files"]


def test_hooks_are_every_sessions_in_order():
    # A hook that failed in an earlier session is still part of the case.
    merged = merge_results(
        [
            RunResult(
                hooks=[HookRun(name="PostToolUse:Write", event="PostToolUse", outcome="error")]
            ),
            RunResult(hooks=[HookRun(name="Stop", event="Stop", outcome="success")]),
        ],
        ["a", "b"],
    )
    assert [(hook.name, hook.outcome) for hook in merged.hooks] == [
        ("PostToolUse:Write", "error"),
        ("Stop", "success"),
    ]


def test_the_transcript_marks_where_each_session_starts():
    merged = merge_results(
        [RunResult(transcript=[{"role": "a"}]), RunResult(transcript=[{"role": "b"}])],
        ["first", "second"],
    )
    assert merged.transcript == [
        {"session": 1, "of": 2, "task": "first"},
        {"role": "a"},
        {"session": 2, "of": 2, "task": "second"},
        {"role": "b"},
    ]


def test_spend_and_latency_are_summed():
    merged = merge_results(
        [
            RunResult(input_tokens=10, output_tokens=1, latency_ms=100, cost_usd=0.5),
            RunResult(input_tokens=20, output_tokens=2, latency_ms=200, cost_usd=0.25),
        ],
        ["a", "b"],
    )
    assert (merged.input_tokens, merged.output_tokens) == (30, 3)
    assert merged.latency_ms == 300
    assert merged.cost_usd == 0.75


def test_notes_are_kept_distinct_in_session_order():
    merged = merge_results(
        [
            RunResult(cost_note="no price for m", usage_note=""),
            RunResult(cost_note="no price for m", usage_note="no usage reported"),
            RunResult(cost_note="other"),
        ],
        ["a", "b", "c"],
    )
    assert merged.cost_note == "no price for m; other"
    assert merged.usage_note == "no usage reported"


def test_the_model_is_the_first_one_reported():
    merged = merge_results([RunResult(model=""), RunResult(model="m1")], ["a", "b"])
    assert merged.model == "m1"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((None, None), None),
        ((False, False), False),
        ((False, True), True),
        ((True, None), True),
    ],
)
def test_the_skill_triggered_when_any_session_loaded_it(flags, expected):
    merged = merge_results([RunResult(skill_triggered=flag) for flag in flags], ["a", "b"])
    assert merged.skill_triggered is expected


def test_every_sessions_trigger_decision_is_kept_in_order():
    merged = merge_results(
        [RunResult(skill_triggered=True), RunResult(skill_triggered=False)], ["a", "b"]
    )
    assert merged.skill_triggered_by_session == [True, False]


def test_no_trigger_decisions_are_kept_outside_an_offered_run():
    merged = merge_results([RunResult(), RunResult()], ["a", "b"])
    assert merged.skill_triggered_by_session == []


def test_an_error_names_its_session_and_keeps_the_spend_so_far():
    merged = merge_results(
        [RunResult(output="one", input_tokens=5), RunResult(error="boom", input_tokens=7)],
        ["a", "b", "c"],
    )
    assert merged.error == "session 2 of 3: boom"
    assert merged.input_tokens == 12
    assert merged.output == ""


# -- through the orchestrator --------------------------------------------------


def test_a_later_session_reads_what_an_earlier_one_wrote(tmp_path):
    class _Resumer:
        name = "resumer"

        def run(self, skill, case, workspace=None):
            if case.task == "start":
                workspace.write("plan.md", "step 1 done")
                return RunResult(output="started")
            return RunResult(output=f"resumed after: {workspace.read('plan.md')}")

    case = _case(
        assertions=[
            AssertionSpec(kind="contains", value="resumed after: step 1 done"),
            AssertionSpec(kind="file-produced", file="plan.md"),
        ]
    )
    report = run_evals([_skill(tmp_path)], [_Resumer()], evals_path=_evals(tmp_path, case))
    assert report.passed == 1


def test_every_sessions_writes_reach_the_last_check(tmp_path):
    runner = FakeRunner(
        default=RunResult(output="ok"),
        writes={"start": {"plan.md": "plan"}, "continue": {"progress.md": "phase 2"}},
    )
    case = _case(
        assertions=[
            AssertionSpec(kind="file-produced", file="plan.md"),
            AssertionSpec(kind="contains", value="phase 2", file="progress.md"),
        ]
    )
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.passed == 1


def test_the_workspace_is_deleted_once_after_the_last_session(tmp_path):
    roots = []

    class _Watcher:
        name = "watcher"

        def run(self, skill, case, workspace=None):
            roots.append(workspace.root)
            assert workspace.root.is_dir()
            return RunResult(output="ok")

    report = run_evals([_skill(tmp_path)], [_Watcher()], evals_path=_evals(tmp_path, _case()))
    assert len(set(roots)) == 1
    assert not roots[0].exists()
    assert report.outcomes[0].result.workspace is None


def test_a_kept_workspace_is_the_one_every_session_used(tmp_path):
    report = run_evals(
        [_skill(tmp_path)],
        [FakeRunner(default=RunResult(output="ok"), writes={"start": {"a.md": "x"}})],
        evals_path=_evals(tmp_path, _case()),
        options=RunOptions(keep_workspace=True),
    )
    kept = report.outcomes[0].result.workspace
    try:
        assert (kept / "a.md").read_text() == "x"
    finally:
        shutil.rmtree(kept)


def test_each_arm_runs_every_session_in_its_own_workspace(tmp_path):
    seen: dict[str, set[Path]] = {}
    tasks: dict[str, list[str]] = {}

    class _Arms:
        name = "arms"

        def run(self, skill, case, workspace=None):
            seen.setdefault(skill.variant, set()).add(workspace.root)
            tasks.setdefault(skill.variant, []).append(case.task)
            return RunResult(output="ok")

    run_evals([_skill(tmp_path)], [_Arms()], evals_path=_evals(tmp_path, _case()), baseline="none")
    assert tasks == {"candidate": ["start", "continue"], "baseline": ["start", "continue"]}
    assert len(seen["candidate"]) == 1
    assert len(seen["baseline"]) == 1
    assert seen["candidate"] != seen["baseline"]


def test_repeat_runs_the_whole_sequence_in_a_fresh_workspace(tmp_path):
    runner = _Recorder()
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, _case()), repeat=2)
    assert [task for task, _, _ in runner.calls] == ["start", "continue", "start", "continue"]
    roots = [workspace.root for _, _, workspace in runner.calls]
    assert roots[0] == roots[1]
    assert roots[2] == roots[3]
    assert roots[0] != roots[2]
    assert report.total == 2


def test_trajectory_reads_every_sessions_calls(tmp_path):
    runner = FakeRunner(
        responses={
            "start": RunResult(output="ok", tool_calls=[ToolCall(name="delete_all")]),
            "continue": RunResult(output="ok", tool_calls=[ToolCall(name="read_file")]),
        }
    )
    forbidden = _case(
        name="forbidden",
        tools=[ToolSpec(name="delete_all")],
        trajectory=TrajectorySpec(forbidden=["delete_all"]),
    )
    ordered = _case(
        name="ordered",
        tools=[ToolSpec(name="delete_all")],
        trajectory=TrajectorySpec(order=["delete_all", "read_file"]),
    )
    report = run_evals(
        [_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, forbidden, ordered)
    )
    status = {outcome.case_name: outcome.status for outcome in report.outcomes}
    assert status == {"forbidden": "failed", "ordered": "passed"}


def test_the_budget_reads_the_sum_of_the_sessions(tmp_path):
    runner = FakeRunner(default=RunResult(output="ok", input_tokens=60))
    case = _case(budget=BudgetSpec(max_tokens=100))
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.failed == 1
    assert report.outcomes[0].result.tokens == 120


def test_one_unpriced_session_fails_max_cost_as_not_evaluated(tmp_path):
    runner = FakeRunner(
        responses={
            "start": RunResult(output="ok", cost_usd=0.01),
            "continue": RunResult(output="ok", cost_note="no price data for m"),
        }
    )
    case = _case(budget=BudgetSpec(max_cost_usd=1.0))
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.failed == 1


def test_an_errored_session_errors_the_case_and_keeps_its_spend(tmp_path):
    runner = _Recorder(
        {"start": RunResult(output="ok", input_tokens=9), "continue": RunResult(error="500")}
    )
    case = _case(sessions=["start", "continue", "never"])
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    [outcome] = report.outcomes
    assert outcome.status == "errored"
    assert outcome.result.error == "session 2 of 3: 500"
    assert outcome.result.input_tokens == 9
    assert [task for task, _, _ in runner.calls] == ["start", "continue"]


def _triggers(tmp_path, start, carry_on, expected):
    runner = FakeRunner(
        responses={
            "start": RunResult(output="ok", skill_triggered=start),
            "continue": RunResult(output="ok", skill_triggered=carry_on),
        }
    )
    case = _case(mode="offered", trajectory=TrajectorySpec(skill_triggered=expected))
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    [outcome] = report.outcomes
    [check] = [
        check for score in outcome.scores for check in score.checks if check.id == "skill_triggered"
    ]
    return outcome.status, check.evidence


@pytest.mark.parametrize(
    ("start", "carry_on", "expected", "status"),
    [
        # A negative control fails when any session reached for the skill...
        (True, False, False, "failed"),
        # ...and a positive one when any session did not: a resume skill that
        # fires only in the first session has not resumed anything.
        (True, False, True, "failed"),
        (False, True, True, "failed"),
        (True, True, True, "passed"),
        (False, False, False, "passed"),
    ],
)
def test_skill_triggered_must_hold_in_every_session(tmp_path, start, carry_on, expected, status):
    assert _triggers(tmp_path, start, carry_on, expected)[0] == status


def test_the_trigger_evidence_names_each_session(tmp_path):
    _, evidence = _triggers(tmp_path, True, False, True)
    assert evidence == (
        "session 1: skill was triggered; session 2: skill was not triggered; "
        "expected True in every session"
    )


def test_a_session_with_no_trigger_decision_errors_the_case(tmp_path):
    runner = FakeRunner(
        responses={
            "start": RunResult(output="ok", skill_triggered=True),
            "continue": RunResult(output="ok"),
        }
    )
    case = _case(mode="offered", trajectory=TrajectorySpec(skill_triggered=True))
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.errored == 1


# -- a sessions case handed straight to a runner -------------------------------


def _bundled_runners():
    def never_called(messages, info):
        raise AssertionError("the model must not be called for a sessions case")

    fake_product = Path(__file__).parent / "fake_product.py"
    product = Product(
        name="claude-code",
        argv=(sys.executable, str(fake_product), "-p", "{prompt}"),
        skills_dir=".claude/skills",
        invoke="/{name} {task}",
        parse=parse_claude_code,
        version_command=(sys.executable, str(fake_product), "--version"),
    )
    return [
        FakeRunner(),
        PydanticAIRunner(model=FunctionModel(never_called)),
        LangChainRunner(model=scripted(text("never"))),
        ProductRunner(product),
    ]


@pytest.mark.parametrize("runner", _bundled_runners(), ids=lambda runner: runner.name)
def test_a_runner_handed_a_whole_sessions_case_reports_it_and_never_raises(tmp_path, runner):
    case = _case()
    workspace = create_workspace(case.workspace, label="t")
    try:
        result = runner.run(_skill(tmp_path), case, workspace=workspace)
        assert result.error is not None
        assert "sessions:" in result.error
        assert "skill_lens" not in result.error
        assert workspace.listing() == []
    finally:
        workspace.cleanup()


# -- the judge -----------------------------------------------------------------


def test_the_judge_is_told_every_sessions_task():
    case = _case(sessions=["Plan it.", "Continue it."], judge=JudgeSpec(rubric=["continued"]))
    request = build_request(case, RunResult(output="phase 2 done"))
    assert request.task == (
        "This work ran as 2 separate sessions that shared one workspace. Each session "
        "started fresh; only the files carried over. The response below is the last "
        "session's.\n\nSession 1:\nPlan it.\n\nSession 2:\nContinue it."
    )
    assert request.output == "phase 2 done"


def test_a_one_task_judge_request_is_unchanged():
    case = EvalCase(name="one", task="Do it.", judge=JudgeSpec(rubric=["done"]))
    assert build_request(case, RunResult(output="x")).task == "Do it."


# -- a framework runner --------------------------------------------------------


def test_a_mock_sequence_starts_from_the_top_in_every_session(tmp_path):
    seen = []

    def reply(messages, info: AgentInfo) -> ModelResponse:
        returned = [
            part.content
            for message in messages
            for part in getattr(message, "parts", [])
            if type(part).__name__ == "ToolReturnPart"
        ]
        if returned:
            seen.append(returned[0])
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(tool_name="poll", args={})])

    case = _case(tools=[ToolSpec(name="poll", returns=["one", "two"])])
    workspace = create_workspace(case.workspace, label="t")
    try:
        result = run_sessions(
            PydanticAIRunner(model=FunctionModel(reply)), _skill(tmp_path), case, workspace, {}
        )
    finally:
        workspace.cleanup()
    assert result.errored is False
    assert seen == ["one", "one"]


# -- the token budget across sessions (issue #94) ------------------------------


def test_the_merged_result_keeps_what_retries_cost_and_why_a_session_stopped():
    merged = merge_results(
        [
            RunResult(output="one", retry_cost_usd=0.1),
            RunResult(output="two", retry_cost_usd=0.2, stop_note="pydantic-ai stopped the run"),
        ],
        ["a", "b", "c"],
    )
    assert merged.retry_cost_usd == pytest.approx(0.3)
    assert merged.stop_note == "session 2 of 3: pydantic-ai stopped the run"


def test_no_session_runs_after_one_its_framework_stopped(tmp_path):
    runner = _Recorder({"start": RunResult(output="cut", stop_note="stopped")})
    result = run_sessions(runner, _skill(tmp_path), _case(sessions=["start", "continue"]), None, {})
    # The case is already over its budget; a later session would only spend.
    assert [task for task, _, _ in runner.calls] == ["start"]
    assert result.stop_note == "session 1 of 2: stopped"


def test_each_session_gets_only_the_tokens_the_earlier_ones_left(tmp_path):
    runner = _Recorder({"start": RunResult(input_tokens=50, output_tokens=10)})
    case = _case(sessions=["start", "continue"], budget=BudgetSpec(max_tokens=100))
    run_sessions(runner, _skill(tmp_path), case, None, {})
    budgets = [call_case.budget.max_tokens for _, call_case, _ in runner.calls]
    # `budget:` checks the sessions' sum, so the second session's stop is
    # what the first one left of it, not the whole limit again.
    assert budgets == [100, 40]


def test_a_real_agent_is_stopped_when_the_sessions_together_cross_the_budget(tmp_path):
    def reply(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="done")],
            usage=RequestUsage(input_tokens=50, output_tokens=10),
        )

    runner = PydanticAIRunner(model=FunctionModel(reply))
    case = _case(sessions=["one", "two", "three"], budget=BudgetSpec(max_tokens=100))
    result = run_sessions(runner, _skill(tmp_path), case, None, {})
    # 60, then 60 more against the 40 left: the second session is stopped,
    # and the third never starts.
    assert result.tokens == 120
    assert result.stop_note.startswith("session 2 of 3: ")
    assert result.errored is False
