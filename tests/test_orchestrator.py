import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from skill_eval.cases.loader import CaseParseError
from skill_eval.evaluators.assertion import UnknownAssertionKind
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import (
    CheckResult,
    EvalCase,
    EvalScore,
    JudgeVerdict,
    RunResult,
    Skill,
    ToolCall,
)
from skill_eval.orchestrator import _execute, _WorkItem, run_evals
from skill_eval.runners.fake import FakeRunner
from skill_eval.skills.loader import load_skills

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
    eval file, not a skill failure. The owner decided this should abort the
    whole matrix (propagate out of run_evals) rather than be caught and
    reported as a red eval outcome, so the orchestrator deliberately has no
    try/except around evaluator.evaluate(...). This test locks in that
    behavior; the CLI is expected to turn this exception into a clean exit
    code in a later task.
    """
    yaml_text = (
        "cases:\n  - name: bad kind\n    task: good\n"
        "    assertions:\n      - kind: nonsense\n        value: whatever\n"
    )
    with pytest.raises(UnknownAssertionKind):
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
    with pytest.raises(UnknownAssertionKind):
        run_evals(skills, [FakeRunner()], concurrency=4)


def test_the_surfaced_authoring_error_is_deterministic(tmp_path):
    """Reading futures in submission order means the same error surfaces every
    time, so the message a user sees does not depend on thread scheduling."""
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
        with pytest.raises(UnknownAssertionKind) as caught:
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
        "      - kind: no-such-kind\n        value: x\n"
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

        def run(self, skill, case):
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
        with pytest.raises(UnknownAssertionKind):
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
