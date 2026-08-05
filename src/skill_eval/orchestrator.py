"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from skill_eval.cases.loader import load_cases_for_skill
from skill_eval.evaluators.assertion import AssertionEvaluator
from skill_eval.evaluators.base import Evaluator
from skill_eval.evaluators.budget import BudgetEvaluator
from skill_eval.evaluators.judge import JudgeEvaluator
from skill_eval.evaluators.trajectory import TrajectoryEvaluator
from skill_eval.judges.base import Judge
from skill_eval.judges.fake import FakeJudge
from skill_eval.models import (
    Arm,
    BaselineKind,
    BaselineNote,
    CaseOutcome,
    EvalCase,
    RunReport,
    Skill,
)
from skill_eval.runners.base import Runner
from skill_eval.skills.baseline import BaselineUnavailable, resolve_previous


def _run_one(
    skill: Skill,
    case: EvalCase,
    runner: Runner,
    evaluators: list[Evaluator],
    *,
    arm: Arm = "candidate",
    repeat_index: int = 0,
    report_skill_name: str | None = None,
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed.

    `report_skill_name` is the *candidate's* name. A baseline resolved from git
    keeps its own name and description -- that is what makes an `offered` run
    against the previous version honest -- but both arms must group under one
    heading in the report, and the candidate's name is that heading.
    """
    name = report_skill_name if report_skill_name is not None else skill.name
    result = runner.run(skill, case)
    if result.errored:
        return CaseOutcome(
            skill_name=name,
            case_name=case.name,
            runner=runner.name,
            status="errored",
            scores=[],
            result=result,
            arm=arm,
            repeat_index=repeat_index,
        )
    scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
    if any(score.errored for score in scores):
        # An evaluator that blew up (a judge endpoint returning 500, structured
        # output that did not match the rubric) is an infra signal, exactly like
        # a runner that blew up. It must not read as a skill that got worse.
        status = "errored"
    else:
        status = "passed" if all(score.passed for score in scores) else "failed"
    return CaseOutcome(
        skill_name=name,
        case_name=case.name,
        runner=runner.name,
        status=status,
        scores=scores,
        result=result,
        arm=arm,
        repeat_index=repeat_index,
    )


def _baseline_skill(skill: Skill, kind: BaselineKind, notes: list[BaselineNote]) -> Skill | None:
    """The skill the baseline arm runs, or None with a note explaining why not."""
    if kind == "none":
        # Empty description *and* empty instructions is what makes the runner
        # fall back to a neutral preamble, so the skill's name never leaks into
        # a baseline prompt.
        return Skill(
            name=skill.name,
            description="",
            instructions="",
            version="",
            path=skill.path,
            variant="baseline",
        )
    resolved = resolve_previous(skill)
    if isinstance(resolved, BaselineUnavailable):
        notes.append(
            BaselineNote(skill_name=resolved.skill_name, kind="unavailable", reason=resolved.reason)
        )
        return None
    return resolved


def _arms(
    case: EvalCase,
    skill: Skill,
    baseline_skill: Skill | None,
    kind: BaselineKind | None,
    notes: list[BaselineNote],
) -> list[tuple[Arm, Skill]]:
    """Which arms this case runs in."""
    arms: list[tuple[Arm, Skill]] = [("candidate", skill)]
    if baseline_skill is None:
        return arms
    if case.mode == "offered" and kind == "none":
        # There is no skill to offer, so `skill_triggered` would be false by
        # construction. Running it would spend real money to prove a tautology
        # and would report the artifact as "the skill helped 100%".
        notes.append(
            BaselineNote(
                skill_name=skill.name,
                case_name=case.name,
                kind="skipped",
                reason="mode: offered has nothing to offer under --baseline none",
            )
        )
        return arms
    arms.append(("baseline", baseline_skill))
    return arms


@dataclass(frozen=True)
class _WorkItem:
    """One (skill-arm, case, runner, repetition) to run and score.

    `skill` is the arm's skill -- a baseline resolved from git keeps its own
    name -- while `report_skill_name` is the candidate's name, which is the
    heading both arms group under in the report.
    """

    skill: Skill
    case: EvalCase
    runner: Runner
    arm: Arm
    repeat_index: int
    report_skill_name: str


@dataclass
class _Plan:
    """Everything discovery produced, before anything has been run."""

    items: list[_WorkItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    tag_filtered: list[str] = field(default_factory=list)
    notes: list[BaselineNote] = field(default_factory=list)


def _plan_work(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None,
    tag: str | None,
    baseline: BaselineKind | None,
    repeat: int,
) -> _Plan:
    """Discovery, filtering and baseline resolution -- always sequential.

    Baseline resolution shells out to git once per skill; parallelising it
    would multiply subprocess spawns to save nothing. The nesting order here is
    what defines report order, so it must not change.
    """
    plan = _Plan()
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if not cases:
            plan.skipped.append(skill.name)
            continue
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                plan.tag_filtered.append(skill.name)
                continue
        baseline_skill = None if baseline is None else _baseline_skill(skill, baseline, plan.notes)
        for case in cases:
            for arm, arm_skill in _arms(case, skill, baseline_skill, baseline, plan.notes):
                for runner in runners:
                    for index in range(repeat):
                        plan.items.append(
                            _WorkItem(
                                skill=arm_skill,
                                case=case,
                                runner=runner,
                                arm=arm,
                                repeat_index=index,
                                report_skill_name=skill.name,
                            )
                        )
    return plan


def _run_item(item: _WorkItem, evaluators: list[Evaluator]) -> CaseOutcome:
    return _run_one(
        item.skill,
        item.case,
        item.runner,
        evaluators,
        arm=item.arm,
        repeat_index=item.repeat_index,
        report_skill_name=item.report_skill_name,
    )


def _default_executor(concurrency: int) -> Executor:
    return ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="skill-eval")


def _execute(
    items: list[_WorkItem],
    evaluators: list[Evaluator],
    concurrency: int,
    executor_factory: Callable[[int], Executor] | None,
) -> list[CaseOutcome]:
    """Run every work item, reporting them in submission order.

    At `concurrency == 1` no executor is constructed at all. That is not an
    optimisation: it is what keeps the default path single-threaded -- same
    ordering, same exception propagation, and a cassette tier that vcrpy
    (order-sensitive, not thread-safe) can still match.

    Above 1, futures are read in submission order so results never reorder by
    completion time. A failure cancels whatever is still queued rather than
    letting the pool drain: an authoring error must abort the run, and every
    case it would otherwise still run is a paid provider call. Work already in
    flight cannot be un-sent, so the waste is bounded by `concurrency` rather
    than by the size of the suite.

    The executor must run work **in this process**. `_run_item` is handed
    runner and judge instances and closure-backed mock tools, none of which
    pickle, so a process pool would fail at submit. A thread pool is a
    requirement here, not a preference -- which is fine, because the work is
    network-bound and threads release the GIL while waiting on a socket.
    """
    if concurrency == 1:
        return [_run_item(item, evaluators) for item in items]

    executor = (executor_factory or _default_executor)(concurrency)
    try:
        # Inside the try: `submit` itself can raise -- a pool that cannot start
        # another OS thread, a broken pool, a custom factory -- and an executor
        # left un-shut-down keeps its workers alive, so the interpreter's exit
        # handler would finish the work this abort exists to abandon.
        futures = [executor.submit(_run_item, item, evaluators) for item in items]

        def _cancel_queued_on_failure(finished: Future) -> None:
            # Runs on the worker thread, before it picks up its next item.
            if finished.cancelled() or finished.exception() is None:
                return
            for queued in futures:
                queued.cancel()

        for future in futures:
            future.add_done_callback(_cancel_queued_on_failure)

        outcomes: list[CaseOutcome] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except CancelledError:
                # Cancelled by the callback above, so the failure that caused
                # it is on some other future. Keep scanning in submission order
                # until we reach it -- at least one future holds a real
                # exception, or nothing would have been cancelled.
                continue
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown(wait=True)
    return outcomes


def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
    judge: Judge | None = None,
    baseline: BaselineKind | None = None,
    repeat: int = 1,
    concurrency: int = 1,
    executor_factory: Callable[[int], Executor] | None = None,
) -> RunReport:
    """Run every (skill, case, runner, arm, repetition) and aggregate the results.

    Evaluator exceptions (e.g. ``UnknownAssertionKind``, ``InvalidAssertionValue``
    from `skill_eval.evaluators.assertion`) propagate out of this function by
    design: a malformed assertion is an authoring error in the eval YAML, not a
    skill failure, so the run aborts rather than silently reporting a red eval.

    `run_evals` owns the default evaluator composition -- callers (the CLI, in
    particular) must not build their own copy of that list, or the two can
    silently drift apart. `judge` lets a caller swap in a configured judge
    (e.g. `PydanticAIJudge`) without reaching into the default list at all; it
    is only meaningful when `evaluators` is left as None, since an explicit
    `evaluators` list already fully determines scoring. Passing both is
    rejected rather than silently ignoring `judge` -- a caller doing that has
    a contradictory request, not a preference we should guess at.

    `baseline` opts into the second arm; None means today's single-arm run.
    `repeat` samples each arm that many times, each repetition being its own
    outcome. A `BaselineUnavailable` is not an authoring error -- it is a fact
    about the user's checkout -- so it becomes a note on the report rather than
    aborting the run.

    `concurrency` bounds how many work items run at once. It defaults to 1,
    which constructs no executor and runs sequentially -- upgrading must
    never change ordering or spend on its own. Discovery is a separate,
    sequential pass that loads every skill's cases before any case runs, so a
    malformed eval file now aborts before any case runs, whichever skill it
    belongs to. The work is network-bound, so threads (not processes) are the
    right unit; the parameter is typed against `concurrent.futures.Executor`
    via `executor_factory` so a different pool can be swapped in without
    touching call sites. Runners, judges and evaluators must therefore be
    safe to share across threads: no mutable instance state touched by
    run/evaluate/judge.
    """
    if evaluators is not None and judge is not None:
        raise ValueError(
            "run_evals() received both `evaluators` and `judge`; pass an explicit "
            "JudgeEvaluator inside `evaluators` instead of also passing `judge`."
        )
    if repeat < 1:
        raise ValueError(f"repeat must be at least 1, got {repeat}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    evaluators = (
        evaluators
        if evaluators is not None
        else [
            AssertionEvaluator(),
            TrajectoryEvaluator(),
            BudgetEvaluator(),
            # The offline judge by default: M3 must never start spending money
            # on its own. Unscripted it errors rather than passing, so a rubric
            # with no real judge configured is never a vacuous green.
            JudgeEvaluator(judge if judge is not None else FakeJudge()),
        ]
    )
    plan = _plan_work(skills, runners, evals_path, tag, baseline, repeat)
    outcomes = _execute(plan.items, evaluators, concurrency, executor_factory)
    return RunReport(
        outcomes=outcomes,
        skipped_skills=plan.skipped,
        tag_filtered_skills=plan.tag_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=plan.notes,
    )
