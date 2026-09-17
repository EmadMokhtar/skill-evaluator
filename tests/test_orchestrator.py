import subprocess
import sys
import tempfile
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from skill_lens.cases.loader import CaseParseError
from skill_lens.evaluators.assertion import InvalidAssertionValue
from skill_lens.judges.fake import FakeJudge
from skill_lens.models import (
    AssertionSpec,
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeVerdict,
    ProductStatus,
    RunResult,
    ScriptNote,
    Skill,
    ToolCall,
    WorkspaceSpec,
)
from skill_lens.orchestrator import RunOptions, _execute, _WorkItem, run_evals
from skill_lens.runners.fake import FakeRunner
from skill_lens.scripts import ScriptPolicy, ScriptRuntime, ScriptSetupError
from skill_lens.skills.loader import load_skills
from skill_lens.workspace import DEFAULT_LIMITS, Workspace, WorkspaceLimits

CASES_YAML = """cases:
  - name: passes
    task: good
    tags: [smoke]
    assertions:
      - kind: contains
        value: yes
  - name: fails
    task: bad
    assertions:
      - kind: contains
        value: never-there
"""


def _skill_with_cases(tmp_path, name="pdf", yaml_text=CASES_YAML):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{name}.eval.yaml").write_text(yaml_text)
    return Skill(name=name, description="", instructions="", path=skill_dir)


def _runner():
    return FakeRunner(
        responses={
            "good": RunResult(output="yes it worked"),
            "bad": RunResult(output="nope"),
            "explodes": RunResult(error="provider 500"),
        }
    )


def _evals(tmp_path: Path, *cases: EvalCase) -> Path:
    """Write cases to a YAML file and return its path.

    Goes through the real loader so these tests exercise the same validation
    a user's file does.
    """
    import yaml

    path = tmp_path / "generated.eval.yaml"
    payload = {"cases": [case.model_dump(exclude_none=True) for case in cases]}
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_runs_all_cases_and_marks_pass_and_fail(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1


def test_runner_error_is_marked_errored_not_failed(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.errored == 1
    assert report.failed == 0
    assert report.outcomes[0].status == "errored"


def test_skill_with_no_cases_is_reported_as_skipped(tmp_path):
    empty_dir = tmp_path / "bare"
    empty_dir.mkdir()
    skill = Skill(name="bare", description="", instructions="", path=empty_dir)
    report = run_evals([skill], [_runner()])
    assert report.skipped_skills == ["bare"]
    assert report.total == 0


def test_matrix_covers_every_skill_case_runner_combination(tmp_path):
    skills = [_skill_with_cases(tmp_path, "pdf"), _skill_with_cases(tmp_path, "xlsx")]
    runners = [_runner(), FakeRunner(default=RunResult(output="yes"))]
    report = run_evals(skills, runners)
    assert report.total == 8  # 2 skills x 2 cases x 2 runners


def test_outcome_records_skill_case_and_runner_names(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    outcome = report.outcomes[0]
    assert outcome.skill_name == "pdf"
    assert outcome.case_name == "passes"
    assert outcome.runner == "fake"


def test_tag_filter_selects_matching_cases(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], tag="smoke")
    assert report.total == 1
    assert report.outcomes[0].case_name == "passes"


def test_tag_filter_excluding_all_cases_is_tag_filtered_not_skipped(tmp_path):
    """A skill that HAS eval cases, none matching --tag, is not the same as a
    skill with zero eval cases at all. Item 2: distinguish the two so the
    console/gate can report the real cause instead of a misleading "skipped
    (no eval cases)" message.
    """
    yaml_text = "cases:\n  - name: no tags here\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], tag="nonexistent-tag"
    )
    assert report.total == 0
    assert report.skipped_skills == []
    assert report.tag_filtered_skills == ["pdf"]


def test_errored_case_still_records_the_result(tmp_path):
    yaml_text = "cases:\n  - name: boom\n    task: explodes\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].result.error == "provider 500"


def test_evaluator_is_not_run_for_errored_cases(tmp_path):
    yaml_text = (
        "cases:\n  - name: boom\n    task: explodes\n"
        "    assertions:\n      - kind: contains\n        value: never\n"
    )
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])
    assert report.outcomes[0].scores == []


def test_unknown_assertion_kind_aborts_the_run(tmp_path):
    """Characterization test: a malformed assertion aborts run_evals by design.

    An unknown `kind:` in an eval YAML is an authoring error in the user's
    eval file, not a skill failure. Since M6 the case loader rejects it
    during discovery -- before any provider call -- rather than the
    evaluator catching it mid-run; either way the owner decided this should
    abort the whole matrix (propagate out of run_evals) rather than be
    caught and reported as a red eval outcome. This test locks in that
    behavior; the CLI is expected to turn this exception into a clean exit
    code in a later task.
    """
    yaml_text = (
        "cases:\n  - name: bad kind\n    task: good\n"
        "    assertions:\n      - kind: nonsense\n        value: whatever\n"
    )
    with pytest.raises(CaseParseError):
        run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()])


def test_default_evaluators_include_trajectory_and_budget(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    tools:\n"
        "      - name: lookup_order\n"
        "    trajectory:\n"
        "      called: [lookup_order]\n"
        "    budget:\n"
        "      max_tokens: 100\n",
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    runner = FakeRunner(
        default=RunResult(tool_calls=[ToolCall(name="lookup_order")], input_tokens=10)
    )
    report = run_evals(skills, [runner])
    # Task 7 widens the default evaluator list to include the offline judge;
    # this case has no `judge:` block so JudgeEvaluator vacuous-passes and
    # still appends a "judge" score.
    assert [score.evaluator for score in report.outcomes[0].scores] == [
        "assertion",
        "trajectory",
        "budget",
        "judge",
    ]
    assert report.outcomes[0].status == "passed"


class ErroringEvaluator:
    name = "boom"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=False, errored=True, detail="judge died")


class PassingEvaluator:
    name = "fine"

    def evaluate(self, case: EvalCase, result: RunResult) -> EvalScore:
        return EvalScore(evaluator=self.name, passed=True, score=1.0)


def test_an_errored_evaluator_errors_the_case_rather_than_failing_it(tmp_path):
    # A judge endpoint returning 500 must not read as a skill that got worse.
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [FakeRunner()],
        evaluators=[PassingEvaluator(), ErroringEvaluator()],
    )
    assert report.outcomes[0].status == "errored"
    assert report.errored == 1
    assert report.failed == 0


def test_a_merely_failing_evaluator_still_fails_the_case(tmp_path):
    class FailingEvaluator:
        name = "nope"

        def evaluate(self, case, result):
            return EvalScore(evaluator=self.name, passed=False, score=0.0)

    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [FakeRunner()],
        evaluators=[FailingEvaluator()],
    )
    assert report.outcomes[0].status == "failed"


def test_the_default_evaluators_include_a_judge(tmp_path):
    # Default judging is the offline FakeJudge, so this stays free -- and a
    # case with no judge block is a vacuous pass.
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    report = run_evals([_skill_with_cases(tmp_path, yaml_text=yaml_text)], [FakeRunner()])
    assert "judge" in [score.evaluator for score in report.outcomes[0].scores]


def test_an_unjudged_rubric_errors_under_the_default_judge(tmp_path):
    yaml_text = "cases:\n  - name: c\n    task: t\n    judge:\n      rubric:\n        - is polite\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    report = run_evals([skill], [FakeRunner()])
    assert report.outcomes[0].status == "errored"


def test_a_trajectory_violation_fails_the_case(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: s\n---\nbody\n", encoding="utf-8")
    (skill_dir / "s.eval.yaml").write_text(
        "cases:\n"
        "  - name: c\n"
        "    task: t\n"
        "    tools:\n"
        "      - name: issue_refund\n"
        "    trajectory:\n"
        "      forbidden: [issue_refund]\n",
        encoding="utf-8",
    )
    runner = FakeRunner(default=RunResult(tool_calls=[ToolCall(name="issue_refund")]))
    report = run_evals(load_skills(skill_dir), [runner])
    assert report.outcomes[0].status == "failed"
    assert report.outcomes[0].result.errored is False


def test_the_caller_supplied_judge_is_actually_used(tmp_path):
    """`judge=` must reach `JudgeEvaluator`, not be silently discarded.

    An unscripted default `FakeJudge()` always errors a rubric-bearing case
    (see `FakeJudge.NOT_CONFIGURED`). Passing a `FakeJudge` scripted to pass
    the rubric makes the outcome flip to "passed" -- a status a default judge
    could never produce here. That makes the two outcomes unmistakable: if
    `judge` were ignored in favor of a fresh `FakeJudge()`, this would go
    "errored" instead.
    """
    yaml_text = "cases:\n  - name: c\n    task: t\n    judge:\n      rubric:\n        - is polite\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    configured_judge = FakeJudge(
        default=JudgeVerdict(checks=[CheckResult(id="r1", passed=True, evidence="polite tone")])
    )
    report = run_evals([skill], [FakeRunner()], judge=configured_judge)
    assert report.outcomes[0].status == "passed"


def test_passing_both_evaluators_and_judge_raises(tmp_path):
    """`evaluators` and `judge` are mutually exclusive -- pin the guard."""
    yaml_text = "cases:\n  - name: c\n    task: t\n"
    skill = _skill_with_cases(tmp_path, yaml_text=yaml_text)
    with pytest.raises(ValueError) as excinfo:
        run_evals(
            [skill],
            [FakeRunner()],
            evaluators=[PassingEvaluator()],
            judge=FakeJudge(),
        )
    assert "evaluators" in str(excinfo.value)
    assert "judge" in str(excinfo.value)


def _concurrency_skill(tmp_path, count=6):
    """A skill with `count` cases, each trivially passing under FakeRunner."""
    skill_dir = tmp_path / "concurrent"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: concurrent\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    cases = "cases:\n" + "".join(
        f"  - name: case-{i}\n    task: task-{i}\n    assertions:\n"
        f"      - kind: contains\n        value: '[fake]'\n"
        for i in range(count)
    )
    evals = skill_dir / "evals"
    evals.mkdir(exist_ok=True)
    (evals / "concurrent.eval.yaml").write_text(cases, encoding="utf-8")
    return load_skills(skill_dir)


def test_concurrency_produces_the_same_outcomes_in_the_same_order(tmp_path):
    """Order is submission order, never completion order: render_console
    iterates report.outcomes and build_delta groups by insertion order, so
    completion-order results would make output churn between identical runs.
    """
    skills = _concurrency_skill(tmp_path)
    sequential = run_evals(skills, [FakeRunner()])
    parallel = run_evals(skills, [FakeRunner()], concurrency=4)

    assert [(o.skill_name, o.case_name, o.arm, o.repeat_index) for o in parallel.outcomes] == [
        (o.skill_name, o.case_name, o.arm, o.repeat_index) for o in sequential.outcomes
    ]
    assert [o.status for o in parallel.outcomes] == [o.status for o in sequential.outcomes]
    assert parallel.pass_rate == sequential.pass_rate


def test_concurrency_one_never_constructs_an_executor(tmp_path):
    """Not an optimisation: no executor is what keeps the default path
    byte-identical and the order-sensitive cassette tier deterministic."""
    skills = _concurrency_skill(tmp_path, count=2)

    def explode(_workers):
        raise AssertionError("an executor must not be built at concurrency == 1")

    report = run_evals(skills, [FakeRunner()], executor_factory=explode)
    assert report.total == 2


def test_a_custom_executor_factory_is_used_above_one(tmp_path):
    skills = _concurrency_skill(tmp_path, count=2)
    seen: list[int] = []

    def factory(workers):
        seen.append(workers)
        return ThreadPoolExecutor(max_workers=workers)

    report = run_evals(skills, [FakeRunner()], concurrency=3, executor_factory=factory)
    assert seen == [3]
    assert report.total == 2


def test_an_authoring_error_still_aborts_the_run_under_concurrency(tmp_path):
    """A malformed assertion is a mistake in the user's files, not a signal
    about the skill. It must abort, never score as a failed case."""
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    evals = skill_dir / "evals"
    evals.mkdir()
    (evals / "bad.eval.yaml").write_text(
        "cases:\n"
        + "".join(
            f"  - name: case-{i}\n    task: t{i}\n    assertions:\n"
            f"      - kind: no-such-kind\n        value: x\n"
            for i in range(4)
        ),
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    with pytest.raises(CaseParseError):
        run_evals(skills, [FakeRunner()], concurrency=4)


def test_the_surfaced_authoring_error_is_deterministic(tmp_path):
    """Discovery is a separate, sequential pass ahead of execution, so the
    cases in a file are validated in order and the same one always surfaces
    first -- the message a user sees does not depend on thread scheduling,
    because no thread has started yet when this fires."""
    skill_dir = tmp_path / "mixed"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mixed\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    evals = skill_dir / "evals"
    evals.mkdir()
    (evals / "mixed.eval.yaml").write_text(
        "cases:\n"
        "  - name: first\n    task: t1\n    assertions:\n"
        "      - kind: first-bad-kind\n        value: x\n"
        "  - name: second\n    task: t2\n    assertions:\n"
        "      - kind: second-bad-kind\n        value: x\n",
        encoding="utf-8",
    )
    skills = load_skills(skill_dir)
    messages = set()
    for _ in range(5):
        with pytest.raises(CaseParseError) as caught:
            run_evals(skills, [FakeRunner()], concurrency=4)
        messages.add(str(caught.value))
    assert len(messages) == 1
    assert "first-bad-kind" in messages.pop()


def test_concurrency_below_one_is_rejected(tmp_path):
    skills = _concurrency_skill(tmp_path, count=1)
    with pytest.raises(ValueError, match="concurrency must be at least 1"):
        run_evals(skills, [FakeRunner()], concurrency=0)


def test_a_malformed_eval_file_aborts_before_any_case_runs(tmp_path):
    """Discovery is a separate, sequential pass, so every skill's cases are
    loaded before any case is run -- and a malformed eval file therefore costs
    nothing, even when an earlier skill's cases would have run fine."""
    for name, cases in (
        (
            "a",
            "cases:\n  - name: fine\n    task: t\n    assertions:\n"
            "      - kind: contains\n        value: x\n",
        ),
        ("b", "cases:\n  - name: broken\n    task: t\n    nonsense_key: 1\n"),
    ):
        skill_dir = tmp_path / name
        (skill_dir / "evals").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n\nbody\n", encoding="utf-8"
        )
        (skill_dir / "evals" / f"{name}.eval.yaml").write_text(cases, encoding="utf-8")

    ran: list[str] = []

    class _RecordingRunner:
        name = "recording"

        def run(self, skill, case):
            ran.append(case.name)
            return RunResult(output="x")

    with pytest.raises(CaseParseError):
        run_evals(load_skills(tmp_path), [_RecordingRunner()])
    assert ran == []


def test_a_failure_cancels_work_that_is_still_queued(tmp_path):
    """Every case is a paid provider call, so an authoring error has to stop
    the run rather than let the pool drain the queue behind it.

    The first case holds a worker while the second fails, so the assertion is
    about cancellation rather than about which thread won a race: a worker runs
    a future's done callbacks before it picks up its next item, so the cancel
    lands before any queued case can start.

    The second case uses an invalid regex, not an unknown kind: since M6 an
    unknown kind is caught by the case loader during discovery, which runs
    entirely before execution starts, so it could never be the thing that
    fires mid-run with a worker already blocked. A malformed regex is still
    only caught by the evaluator, inside a submitted work item, which is
    exactly the timing this test needs.
    """
    skill_dir = tmp_path / "big"
    (skill_dir / "evals").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: big\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    cases = (
        "cases:\n"
        "  - name: aaa-blocker\n    task: block\n    assertions:\n"
        "      - kind: contains\n        value: x\n"
        "  - name: bbb-bad\n    task: bad\n    assertions:\n"
        "      - kind: regex\n        value: '['\n"
    ) + "".join(
        f"  - name: rest-{i}\n    task: t{i}\n    assertions:\n"
        f"      - kind: contains\n        value: x\n"
        for i in range(10)
    )
    (skill_dir / "evals" / "big.eval.yaml").write_text(cases, encoding="utf-8")

    release = threading.Event()
    lock = threading.Lock()
    seen: list[str] = []

    class _GatedRunner:
        name = "gated"

        def run(self, skill, case, workspace=None):
            with lock:
                seen.append(case.name)
            if case.name == "aaa-blocker":
                release.wait(timeout=10)
            return RunResult(output="x")

    # Liveness backstop, not the mechanism under test -- see the docstring
    # above for why the cancel itself is deterministic. This timer only frees
    # the blocked worker so the run can finish and the failure can surface.
    timer = threading.Timer(1.0, release.set)
    timer.start()
    try:
        with pytest.raises(InvalidAssertionValue):
            run_evals(load_skills(skill_dir), [_GatedRunner()], concurrency=2)
    finally:
        timer.cancel()
        release.set()

    assert "bbb-bad" in seen
    assert len(seen) <= 4, f"queued work was not cancelled; ran {len(seen)}: {seen}"


def test_a_submit_failure_still_shuts_the_executor_down():
    """An executor left running keeps its workers alive, and the interpreter's
    exit handler then joins them -- finishing the work the abort abandoned."""
    shutdowns: list[tuple[bool, bool]] = []

    class _FailingExecutor(ThreadPoolExecutor):
        def __init__(self):
            super().__init__(max_workers=2)
            self._submits = 0

        def submit(self, fn, /, *args, **kwargs):
            self._submits += 1
            if self._submits > 1:
                raise RuntimeError("can't start new thread")
            return super().submit(fn, *args, **kwargs)

        def shutdown(self, wait=True, *, cancel_futures=False):
            shutdowns.append((wait, cancel_futures))
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    items = [
        _WorkItem(
            skill=Skill(name="s", description="d", instructions="i", path=Path("s")),
            case=EvalCase(name=f"c{i}", task="t"),
            runner=FakeRunner(),
            arm="candidate",
            repeat_index=0,
            report_skill_name="s",
        )
        for i in range(3)
    ]
    with pytest.raises(RuntimeError, match="can't start new thread"):
        _execute(items, [], concurrency=2, executor_factory=lambda _n: _FailingExecutor())
    assert shutdowns == [(False, True)]


def test_work_abandoned_without_a_failure_is_an_error_not_a_short_report():
    """A short result list would let the gate score a subset of the suite.

    Our own callback only cancels after a failure, and that failure is raised
    -- but `executor_factory` is public, and a pool that abandons queued work
    for its own reasons must not produce a quietly partial run.
    """

    class _AbandoningExecutor(Executor):
        """Runs the first item, abandons the rest. No threads, so no race."""

        def __init__(self):
            self._ran = 0

        def submit(self, fn, /, *args, **kwargs):
            future: Future = Future()
            if self._ran == 0:
                self._ran += 1
                future.set_result(fn(*args, **kwargs))
            else:
                # A fresh future is PENDING, so this cancel always succeeds --
                # which is the whole point: no worker to race.
                future.cancel()
                future.set_running_or_notify_cancel()
            return future

        def shutdown(self, wait=True, *, cancel_futures=False):
            pass

    items = [
        _WorkItem(
            skill=Skill(name="s", description="d", instructions="i", path=Path("s")),
            case=EvalCase(name=f"c{i}", task="t"),
            runner=FakeRunner(),
            arm="candidate",
            repeat_index=0,
            report_skill_name="s",
        )
        for i in range(4)
    ]
    with pytest.raises(RuntimeError, match="work was abandoned"):
        _execute(items, [], concurrency=2, executor_factory=lambda _n: _AbandoningExecutor())


class _RecordingRunner:
    """Captures the workspace it was handed, and whether the directory existed."""

    name = "recording"

    def __init__(self) -> None:
        self.seen: list[Workspace] = []
        self.seeded: list[str] = []

    def run(self, skill, case, workspace=None):
        if workspace is not None:
            self.seen.append(workspace)
            workspace.write("report.md", f"{skill.variant}")
            # Defensive: most callers of this stub seed nothing, so a missing
            # file must not raise -- only pin what was actually there while
            # the runner had control, for the cases that do seed one.
            try:
                self.seeded.append(workspace.read("in.csv"))
            except OSError:
                pass
        return RunResult(output="done")


def _skill(tmp_path) -> Skill:
    return Skill(name="s", description="d", instructions="i", path=tmp_path)


def _case(**kwargs) -> EvalCase:
    kwargs.setdefault("name", "c")
    kwargs.setdefault("task", "t")
    return EvalCase(**kwargs)


def test_a_case_with_no_workspace_gets_none(tmp_path):
    runner = _RecordingRunner()
    run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, _case()))
    assert runner.seen == []


def test_the_workspace_is_deleted_after_scoring(tmp_path):
    runner = _RecordingRunner()
    case = _case(
        workspace=WorkspaceSpec(),
        assertions=[AssertionSpec(kind="file-produced", file="report.md")],
    )
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    # The assertion passed, which proves the directory still existed while the
    # evaluators ran; it is gone now, which proves cleanup happened after.
    assert report.passed == 1
    assert not runner.seen[0].root.exists()


def test_the_workspace_path_is_cleared_once_the_directory_is_gone(tmp_path):
    # A path pointing at a deleted directory would be a lie in the JSON report.
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.outcomes[0].result is not None
    assert report.outcomes[0].result.workspace is None


def test_keep_workspace_leaves_the_directory_and_the_path(tmp_path):
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals(
        [_skill(tmp_path)],
        [runner],
        evals_path=_evals(tmp_path, case),
        options=RunOptions(keep_workspace=True),
    )
    kept = report.outcomes[0].result.workspace
    try:
        assert kept is not None and Path(kept).is_dir()
    finally:
        # In a finally so a failing assertion above does not leak the
        # directory -- cleanup must run either way.
        if kept is not None:
            Workspace(root=Path(kept)).cleanup()


def test_each_arm_and_repetition_gets_its_own_directory(tmp_path):
    # Two arms sharing one directory would let the baseline read files the
    # candidate wrote -- a silently wrong delta.
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    run_evals(
        [_skill(tmp_path)],
        [runner],
        evals_path=_evals(tmp_path, case),
        baseline="none",
        repeat=2,
    )
    roots = [workspace.root for workspace in runner.seen]
    assert len(roots) == 4
    assert len(set(roots)) == 4


def test_seeded_files_reach_the_runner(tmp_path):
    # _RecordingRunner captures the seeded content itself, inside run(),
    # while it still has the directory -- not by reading it back afterward.
    # That pins the file's presence at the moment the runner actually had
    # control, so this no longer depends on keep_workspace working (a
    # keep_workspace regression can no longer fail this test for an unrelated
    # reason) and needs no cleanup at all: the default keep_workspace=False
    # deletes the directory exactly as every other case does.
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec(files={"in.csv": "a,b\n"}))
    run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert runner.seeded == ["a,b\n"]


def test_configured_limits_reach_the_workspace(tmp_path):
    # A limit read from config and then dropped on the way through would leave
    # the default silently in force.
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    run_evals(
        [_skill(tmp_path)],
        [runner],
        evals_path=_evals(tmp_path, case),
        options=RunOptions(limits=WorkspaceLimits(max_files=7)),
    )
    assert runner.seen[0].limits.max_files == 7


def test_a_seeding_failure_errors_the_case_rather_than_failing_it(tmp_path):
    # Disk and permission problems say nothing about the skill. The loader
    # rejects an escaping path first, so this is unreachable through a YAML
    # file -- it is called directly, which is also the only honest way to
    # reach the branch.
    from skill_lens.orchestrator import _run_one

    outcome = _run_one(
        _skill(tmp_path),
        _case(workspace=WorkspaceSpec(files={"../escape.txt": "x"})),
        _RecordingRunner(),
        [],
    )
    assert outcome.status == "errored"
    assert outcome.result is not None
    assert "workspace" in outcome.result.error.lower()


def test_an_errored_run_still_gets_its_directory_deleted(tmp_path):
    class _Broken(_RecordingRunner):
        def run(self, skill, case, workspace=None):
            if workspace is not None:
                self.seen.append(workspace)
            return RunResult(error="provider exploded")

    runner = _Broken()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.errored == 1
    assert not runner.seen[0].root.exists()


def test_concurrency_does_not_share_directories(tmp_path):
    runner = _RecordingRunner()
    cases = [
        _case(name=f"c{index}", task=f"t{index}", workspace=WorkspaceSpec()) for index in range(6)
    ]
    run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, *cases), concurrency=4)
    roots = [workspace.root for workspace in runner.seen]
    assert len(set(roots)) == 6


def test_an_authoring_error_still_deletes_the_directory(tmp_path):
    # The cleanup lives in a `finally` for exactly this: an evaluator that
    # raises an authoring error must abort the run AND leave no directory
    # behind. Without this test, flattening the finally into straight-line
    # code would pass the whole suite while leaking a directory per case.
    from skill_lens.orchestrator import _run_one

    class _Exploding:
        def evaluate(self, case, result):
            raise InvalidAssertionValue("boom")

    runner = _RecordingRunner()
    with pytest.raises(InvalidAssertionValue):
        _run_one(
            _skill(tmp_path),
            _case(workspace=WorkspaceSpec()),
            runner,
            [_Exploding()],
        )
    assert not runner.seen[0].root.exists()


def test_a_runner_supplied_workspace_is_ignored_for_a_workspace_less_case(tmp_path):
    # The stamp is unconditional now: a runner that returns its own
    # RunResult.workspace must not have that path reach the report for a case
    # that declared no `workspace:` block. Otherwise a non-conforming adapter
    # could smuggle a path of its own past the orchestrator, and the
    # assertion evaluator would use it instead of raising the intended
    # "this run had no workspace" authoring error.
    class _Smuggler:
        name = "smuggler"

        def run(self, skill, case, workspace=None):
            return RunResult(output="x", workspace=Path("/etc"))

    report = run_evals([_skill(tmp_path)], [_Smuggler()], evals_path=_evals(tmp_path, _case()))
    assert report.outcomes[0].result is not None
    assert report.outcomes[0].result.workspace is None


def test_case_filter_selects_by_case_insensitive_substring(tmp_path):
    yaml_text = (
        "cases:\n"
        "  - name: Refuses a refund outside the window\n    task: a\n"
        "  - name: refunds inside the window\n    task: b\n"
        "  - name: greets\n    task: c\n"
    )
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], case_filter="REFUND"
    )
    assert [o.case_name for o in report.outcomes] == [
        "Refuses a refund outside the window",
        "refunds inside the window",
    ]


def test_case_filter_and_tag_filter_both_apply(tmp_path):
    yaml_text = (
        "cases:\n"
        "  - name: refund smoke\n    task: a\n    tags: [smoke]\n"
        "  - name: refund deep\n    task: b\n"
    )
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [_runner()],
        tag="smoke",
        case_filter="refund",
    )
    assert [o.case_name for o in report.outcomes] == ["refund smoke"]


def test_a_case_filter_matching_nothing_is_case_filtered_not_skipped(tmp_path):
    yaml_text = "cases:\n  - name: greets\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)], [_runner()], case_filter="refund"
    )
    assert report.total == 0
    assert report.skipped_skills == []
    assert report.tag_filtered_skills == []
    assert report.case_filtered_skills == ["pdf"]


def test_a_skill_emptied_by_the_tag_filter_is_not_also_case_filtered(tmp_path):
    yaml_text = "cases:\n  - name: refund\n    task: good\n"
    report = run_evals(
        [_skill_with_cases(tmp_path, yaml_text=yaml_text)],
        [_runner()],
        tag="no-such-tag",
        case_filter="refund",
    )
    assert report.tag_filtered_skills == ["pdf"]
    assert report.case_filtered_skills == []


def test_case_filter_is_appended_after_every_pre_existing_parameter():
    # `run_evals` is library API. A caller that passed `judge` positionally
    # before `case_filter` existed must still be binding `judge`, so a new
    # parameter has to sit after every parameter that predates it. `options`
    # (M6 part 2) is the newest, so it is last; `keep_workspace` and
    # `workspace_limits` (M6 part 1) keep the positions they were added in.
    import inspect

    params = list(inspect.signature(run_evals).parameters)
    assert params[-1] == "options"
    assert params[-2] == "case_filter"
    assert params.index("judge") == params.index("tag") + 1
    assert params.index("keep_workspace") == params.index("executor_factory") + 1
    assert params.index("workspace_limits") == params.index("keep_workspace") + 1


def test_the_legacy_keep_workspace_keyword_still_keeps_the_directory(tmp_path):
    # A caller written against M6 part 1 passes `keep_workspace=` directly.
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals(
        [_skill(tmp_path)],
        [runner],
        evals_path=_evals(tmp_path, case),
        keep_workspace=True,
    )
    kept = report.outcomes[0].result.workspace
    try:
        assert kept is not None and Path(kept).is_dir()
    finally:
        if kept is not None:
            Workspace(root=Path(kept)).cleanup()


def test_the_legacy_workspace_limits_keyword_still_reaches_the_workspace(tmp_path):
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    run_evals(
        [_skill(tmp_path)],
        [runner],
        evals_path=_evals(tmp_path, case),
        workspace_limits=WorkspaceLimits(max_files=7),
    )
    assert runner.seen[0].limits.max_files == 7


@pytest.mark.parametrize(
    "legacy",
    [{"keep_workspace": True}, {"workspace_limits": WorkspaceLimits(max_files=7)}],
)
def test_options_together_with_a_legacy_argument_is_rejected(tmp_path, legacy):
    # Mirrors the `evaluators` + `judge` rejection: two sources for one
    # setting is a contradictory request, not a preference to guess at.
    with pytest.raises(ValueError, match="both `options` and the legacy"):
        run_evals(
            [_skill(tmp_path)],
            [_RecordingRunner()],
            evals_path=_evals(tmp_path, _case()),
            options=RunOptions(),
            **legacy,
        )


def _bundled_skill(tmp_path, *scripts: str) -> Skill:
    root = tmp_path / "bundled"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (root / "scripts" / name).write_text("print('x')", encoding="utf-8")
    return Skill(
        name="bundled", description="d", instructions="i", path=root, bundle_root=root.resolve()
    )


class _ScriptAwareRunner(_RecordingRunner):
    """Records the runtime it was handed."""

    def __init__(self) -> None:
        super().__init__()
        self.runtimes: list[ScriptRuntime | None] = []

    def run(self, skill, case, workspace=None, scripts=None):
        self.runtimes.append(scripts)
        return super().run(skill, case, workspace=workspace)


def test_the_legacy_form_never_enables_scripts(tmp_path):
    # The legacy parameters predate scripts, so a caller using them cannot
    # have asked for execution; the runner must see no runtime.
    runner = _ScriptAwareRunner()
    run_evals(
        [_bundled_skill(tmp_path, "count.py")],
        [runner],
        evals_path=_evals(tmp_path, _case()),
        keep_workspace=False,
        workspace_limits=WorkspaceLimits(max_files=7),
    )
    assert runner.runtimes == [None]


def test_run_options_defaults_reproduce_the_old_behaviour(tmp_path):
    runner = _RecordingRunner()
    case = _case(workspace=WorkspaceSpec())
    report = run_evals([_skill(tmp_path)], [runner], evals_path=_evals(tmp_path, case))
    assert report.scripts is None
    assert report.script_notes == []
    assert report.outcomes[0].result.workspace is None
    assert runner.seen[0].limits == DEFAULT_LIMITS


def test_scripts_off_leaves_a_note_per_skill_that_bundles_scripts(tmp_path):
    runner = _RecordingRunner()
    skill = _bundled_skill(tmp_path, "a.py", "b.sh")
    report = run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()))
    assert report.scripts is None
    assert report.script_notes == [ScriptNote(skill_name="bundled", script_count=2)]


def test_scripts_off_notes_nothing_for_a_bundle_without_scripts(tmp_path):
    runner = _RecordingRunner()
    skill = _bundled_skill(tmp_path)
    (tmp_path / "bundled" / "scripts").rmdir()
    (tmp_path / "bundled" / "references").mkdir()
    report = run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()))
    assert report.script_notes == []


def test_scripts_on_runs_preflight_once_and_hands_the_runtime_to_the_runner(tmp_path):
    runner = _ScriptAwareRunner()
    policy = ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)})
    report = run_evals(
        [_bundled_skill(tmp_path, "a.py")],
        [runner],
        evals_path=_evals(tmp_path, _case(workspace=WorkspaceSpec())),
        options=RunOptions(scripts=policy),
    )
    assert report.scripts is not None
    assert report.scripts.sandbox == "none"
    assert report.scripts.detail == 'script_sandbox = "off"'
    assert report.script_notes == []
    (runtime,) = runner.runtimes
    assert runtime is not None and runtime.policy is policy


def test_the_hardening_note_reaches_the_report(tmp_path, monkeypatch):
    # Whatever preflight recorded is what the report says -- on Linux the
    # real note, elsewhere None -- so the console never claims a protection
    # this run did not have.
    import skill_lens.scripts as scripts_module

    monkeypatch.setattr(scripts_module, "harden_process", lambda: "hardened (test)")
    policy = ScriptPolicy(sandbox="off", interpreters={"py": (sys.executable,)})
    report = run_evals(
        [_bundled_skill(tmp_path, "a.py")],
        [_ScriptAwareRunner()],
        evals_path=_evals(tmp_path, _case()),
        options=RunOptions(scripts=policy),
    )
    assert report.scripts is not None
    assert report.scripts.hardening == "hardened (test)"


def test_scripts_off_passes_no_scripts_keyword_so_part_1_runners_keep_working(tmp_path):
    class _PartOneRunner:
        name = "old"

        def run(self, skill, case, workspace=None):
            return RunResult(output="ok")

    report = run_evals([_skill(tmp_path)], [_PartOneRunner()], evals_path=_evals(tmp_path, _case()))
    assert report.outcomes[0].status == "passed"


def test_a_setup_error_aborts_before_any_case_runs(tmp_path):
    runner = _ScriptAwareRunner()
    policy = ScriptPolicy(sandbox="off", interpreters={"py": ("no-such-interpreter-xyz",)})
    with pytest.raises(ScriptSetupError, match="no-such-interpreter-xyz"):
        run_evals(
            [_bundled_skill(tmp_path, "a.py")],
            [runner],
            evals_path=_evals(tmp_path, _case()),
            options=RunOptions(scripts=policy),
        )
    assert runner.runtimes == []


def _git_skill_with_history(tmp_path) -> Skill:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    for version, body in (("1.0.0", "old"), ("1.1.0", "new")):
        (repo / "SKILL.md").write_text(
            f'---\nname: s\nversion: "{version}"\n---\n{body}\n', encoding="utf-8"
        )
        (repo / "scripts").mkdir(exist_ok=True)
        (repo / "scripts" / "a.py").write_text(f"print('{body}')", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"feat: {version}"], cwd=repo, check=True)
    from skill_lens.skills.loader import parse_skill_file

    return parse_skill_file(repo / "SKILL.md")


def test_baseline_bundle_directories_are_deleted_when_the_run_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    runner = _ScriptAwareRunner()
    skill = _git_skill_with_history(tmp_path)
    run_evals(
        [skill],
        [runner],
        evals_path=_evals(tmp_path, _case(workspace=WorkspaceSpec())),
        baseline="previous",
    )
    assert not list(tmp_path.glob("skill-lens-baselines-*"))


def test_baseline_bundle_directories_are_deleted_even_when_a_case_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    skill = _git_skill_with_history(tmp_path)

    class _Exploding:
        name = "assertion"

        def evaluate(self, case, result):
            raise InvalidAssertionValue("boom")

    with pytest.raises(InvalidAssertionValue):
        run_evals(
            [skill],
            [_RecordingRunner()],
            evals_path=_evals(tmp_path, _case()),
            evaluators=[_Exploding()],
            baseline="previous",
        )
    assert not list(tmp_path.glob("skill-lens-baselines-*"))


def test_the_baseline_arm_sees_the_previous_bundle_and_the_candidate_the_current_one(tmp_path):
    runner = _ScriptAwareRunner()
    skill = _git_skill_with_history(tmp_path)
    seen: dict[str, str] = {}

    class _Peeking(_ScriptAwareRunner):
        def run(self, s, case, workspace=None, scripts=None):
            seen[s.variant] = (s.bundle_root / "scripts" / "a.py").read_text(encoding="utf-8")
            return super().run(s, case, workspace=workspace, scripts=scripts)

    runner = _Peeking()
    run_evals([skill], [runner], evals_path=_evals(tmp_path, _case()), baseline="previous")
    assert seen == {"candidate": "print('new')", "baseline": "print('old')"}


def test_baseline_none_arm_gets_no_bundle_even_when_the_candidate_has_one(tmp_path):
    # Keying the bundle tools on path instead of bundle_root would leak the
    # candidate's scripts into the "no skill" arm, since --baseline none's
    # skill shares the candidate's path. bundle_root must stay None instead.
    skill = _bundled_skill(tmp_path, "a.py")
    seen: dict[str, Path | None] = {}

    class _Peeking(_ScriptAwareRunner):
        def run(self, s, case, workspace=None, scripts=None):
            seen[s.variant] = s.bundle_root
            return super().run(s, case, workspace=workspace, scripts=scripts)

    run_evals([skill], [_Peeking()], evals_path=_evals(tmp_path, _case()), baseline="none")
    assert seen["candidate"] == skill.bundle_root
    assert seen["baseline"] is None


class _PreflightRunner(FakeRunner):
    """A FakeRunner that also defines the optional preflight hook."""

    name = "probed"

    def __init__(self, *, raises: Exception | None = None):
        super().__init__(default=RunResult(output="yes"))
        self.calls: list[tuple[list[str], dict[str, list[str]]]] = []
        self._raises = raises

    def preflight(self, skills, cases_by_skill):
        self.calls.append(
            ([s.name for s in skills], {k: [c.name for c in v] for k, v in cases_by_skill.items()})
        )
        if self._raises is not None:
            raise self._raises
        return ProductStatus(name=self.name, executable="/bin/probed", version="1", trust="t")


def test_preflight_runs_once_with_the_cases_that_will_run(tmp_path):
    # The file's CASES_YAML: "passes" carries tags: [smoke], "fails" does not.
    runner = _PreflightRunner()
    report = run_evals([_skill_with_cases(tmp_path)], [runner], tag="smoke", repeat=3)
    assert runner.calls == [(["pdf"], {"pdf": ["passes"]})]  # filtered, and not per repeat
    assert report.products == [
        ProductStatus(name="probed", executable="/bin/probed", version="1", trust="t")
    ]


def test_preflight_sees_only_the_candidate_arm(tmp_path):
    runner = _PreflightRunner()
    run_evals([_skill_with_cases(tmp_path)], [runner], baseline="none")
    (call,) = runner.calls
    assert call == (["pdf"], {"pdf": ["passes", "fails"]})  # one entry per case, not per arm


def test_a_preflight_error_aborts_before_any_case_runs(tmp_path):
    class Boom(Exception):
        pass

    runner = _PreflightRunner(raises=Boom("no product"))
    with pytest.raises(Boom):
        run_evals([_skill_with_cases(tmp_path)], [runner])


def test_a_runner_without_the_hook_is_untouched(tmp_path):
    report = run_evals([_skill_with_cases(tmp_path)], [_runner()])
    assert report.products == []


def test_a_hook_returning_none_adds_no_status(tmp_path):
    class Quiet(FakeRunner):
        name = "quiet"

        def preflight(self, skills, cases_by_skill):
            return None

    report = run_evals([_skill_with_cases(tmp_path)], [Quiet(default=RunResult(output="yes"))])
    assert report.products == []


def test_a_judges_preflight_status_joins_the_products(tmp_path):
    class ProbedJudge(FakeJudge):
        name = "probed-judge"

        def preflight(self):
            return ProductStatus(name="probed-judge", executable="/bin/j", version="2", trust="t")

    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], judge=ProbedJudge())
    assert report.products == [
        ProductStatus(name="probed-judge", executable="/bin/j", version="2", trust="t")
    ]


def test_the_same_product_as_runner_and_judge_is_listed_once(tmp_path):
    status = ProductStatus(name="probed", executable="/bin/probed", version="1", trust="t")

    class SameJudge(FakeJudge):
        name = "probed"

        def preflight(self):
            return status

    report = run_evals([_skill_with_cases(tmp_path)], [_PreflightRunner()], judge=SameJudge())
    assert report.products == [status]


def test_a_judges_hook_returning_none_adds_no_status(tmp_path):
    class QuietJudge(FakeJudge):
        name = "quiet-judge"

        def preflight(self):
            return None

    report = run_evals([_skill_with_cases(tmp_path)], [_runner()], judge=QuietJudge())
    assert report.products == []
