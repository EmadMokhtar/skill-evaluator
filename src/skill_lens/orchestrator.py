"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from skill_lens.cases.loader import load_cases_for_skill
from skill_lens.evaluators.assertion import AssertionEvaluator
from skill_lens.evaluators.base import Evaluator
from skill_lens.evaluators.budget import BudgetEvaluator
from skill_lens.evaluators.judge import JudgeEvaluator
from skill_lens.evaluators.trajectory import TrajectoryEvaluator
from skill_lens.judges.base import Judge
from skill_lens.judges.fake import FakeJudge
from skill_lens.models import (
    Arm,
    BaselineKind,
    BaselineNote,
    CaseOutcome,
    CaseStatus,
    EvalCase,
    EvalScore,
    RunReport,
    RunResult,
    Skill,
)
from skill_lens.runners.base import Runner
from skill_lens.skills.baseline import BaselineUnavailable, resolve_previous
from skill_lens.workspace import (
    DEFAULT_LIMITS,
    WorkspaceError,
    WorkspaceLimits,
    create_workspace,
)


def _run_one(
    skill: Skill,
    case: EvalCase,
    runner: Runner,
    evaluators: list[Evaluator],
    *,
    arm: Arm = "candidate",
    repeat_index: int = 0,
    report_skill_name: str | None = None,
    keep_workspace: bool = False,
    limits: WorkspaceLimits = DEFAULT_LIMITS,
) -> CaseOutcome:
    """Run a single combination and score it, keeping errored distinct from failed.

    `report_skill_name` is the *candidate's* name. A baseline resolved from git
    keeps its own name and description -- that is what makes an `offered` run
    against the previous version honest -- but both arms must group under one
    heading in the report, and the candidate's name is that heading.

    This function owns the workspace's lifetime. Creation happens here rather
    than inside the runner because deletion must happen *after* scoring, and
    the runner has returned by then. It is also what guarantees every arm and
    every repetition gets a directory of its own.
    """
    name = report_skill_name if report_skill_name is not None else skill.name
    outcome = partial(
        CaseOutcome,
        skill_name=name,
        case_name=case.name,
        runner=runner.name,
        arm=arm,
        repeat_index=repeat_index,
    )

    workspace = None
    if case.workspace is not None:
        try:
            workspace = create_workspace(
                case.workspace,
                label=f"{name}-{case.name}-{arm}-{repeat_index}",
                limits=limits,
            )
        except WorkspaceError as exc:
            # Infra, not signal: a disk or permissions problem says nothing
            # about the skill, so this errors the case rather than failing it.
            return outcome(
                status="errored",
                scores=[],
                result=RunResult(error=f"WorkspaceError: {exc}"),
            )

    try:
        result = runner.run(skill, case, workspace=workspace)
        # Stamped here, not echoed by the runner: an adapter that ignores the
        # parameter then fails loudly on the assertion instead of producing a
        # workspace-less result that looks like a skill problem. Written
        # unconditionally -- including the None case -- so a non-conforming
        # adapter cannot smuggle a path of its own into the report for a case
        # that declared no workspace.
        result = result.model_copy(
            update={"workspace": workspace.root if workspace is not None else None}
        )
        if result.errored:
            scores: list[EvalScore] = []
            status: CaseStatus = "errored"
        else:
            scores = [evaluator.evaluate(case, result) for evaluator in evaluators]
            # An evaluator that blew up (a judge endpoint returning 500,
            # structured output that did not match the rubric) is an infra
            # signal, exactly like a runner that blew up. It must not read as
            # a skill that got worse.
            if any(score.errored for score in scores):
                status = "errored"
            else:
                status = "passed" if all(score.passed for score in scores) else "failed"
    finally:
        # In a finally so an authoring error raised by an evaluator still
        # cleans up before it propagates.
        if workspace is not None and not keep_workspace:
            workspace.cleanup()

    if workspace is not None and not keep_workspace:
        # The directory is gone, so the path must go too: a field pointing at
        # a deleted directory would be a lie in the JSON report.
        result = result.model_copy(update={"workspace": None})

    return outcome(status=status, scores=scores, result=result)


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
    case_filtered: list[str] = field(default_factory=list)
    notes: list[BaselineNote] = field(default_factory=list)


def _plan_work(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None,
    tag: str | None,
    case_filter: str | None,
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
        if case_filter is not None:
            # Case-insensitive substring, like pytest -k: the CI log shows the
            # name, the user copies any distinctive part of it. Applied after
            # --tag so a skill emptied by --tag is recorded under --tag alone.
            needle = case_filter.casefold()
            cases = [c for c in cases if needle in c.name.casefold()]
            if not cases:
                plan.case_filtered.append(skill.name)
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


def _run_item(
    item: _WorkItem,
    evaluators: list[Evaluator],
    keep_workspace: bool,
    limits: WorkspaceLimits,
) -> CaseOutcome:
    return _run_one(
        item.skill,
        item.case,
        item.runner,
        evaluators,
        arm=item.arm,
        repeat_index=item.repeat_index,
        report_skill_name=item.report_skill_name,
        keep_workspace=keep_workspace,
        limits=limits,
    )


def _default_executor(concurrency: int) -> Executor:
    return ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="skill-lens")


def _execute(
    items: list[_WorkItem],
    evaluators: list[Evaluator],
    concurrency: int,
    executor_factory: Callable[[int], Executor] | None,
    keep_workspace: bool = False,
    limits: WorkspaceLimits = DEFAULT_LIMITS,
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
        return [_run_item(item, evaluators, keep_workspace, limits) for item in items]

    executor = (executor_factory or _default_executor)(concurrency)
    try:
        # Inside the try: `submit` itself can raise -- a pool that cannot start
        # another OS thread, a broken pool, a custom factory -- and an executor
        # left un-shut-down keeps its workers alive, so the interpreter's exit
        # handler would finish the work this abort exists to abandon.
        futures = [
            executor.submit(_run_item, item, evaluators, keep_workspace, limits) for item in items
        ]

        def _cancel_queued_after(index: int, finished: Future) -> None:
            # Runs on the worker thread, before it picks up its next item.
            #
            # Only work *after* the failure is cancelled. A lower-index future
            # can still be PENDING -- a worker dequeues an item before it marks
            # the future RUNNING -- and cancelling one would let a higher-index
            # error surface instead of the lowest-index one, which is the
            # determinism this reads futures in submission order to preserve.
            if finished.cancelled() or finished.exception() is None:
                return
            for queued in futures[index + 1 :]:
                queued.cancel()

        for index, future in enumerate(futures):
            future.add_done_callback(partial(_cancel_queued_after, index))

        outcomes: list[CaseOutcome] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except CancelledError:
                # Defensive: our own callback never cancels a future below the
                # failing index, so this should not trigger from our own
                # cancellations. The totality check below is what actually
                # catches an executor that abandons work for its own reasons.
                continue

        if len(outcomes) != len(futures):
            # Only reachable with a custom executor that abandons work on its
            # own: our own callback only ever cancels after a failure, and that
            # failure is always raised above. Returning a short list would let
            # the gate compute a pass rate over a subset of the suite, which is
            # the silent partial run that the zero-cases rule exists to reject.
            raise RuntimeError(
                f"the executor returned {len(outcomes)} of {len(futures)} results; "
                "work was abandoned with no failure to explain it"
            )
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
    case_filter: str | None = None,
    judge: Judge | None = None,
    baseline: BaselineKind | None = None,
    repeat: int = 1,
    concurrency: int = 1,
    executor_factory: Callable[[int], Executor] | None = None,
    keep_workspace: bool = False,
    workspace_limits: WorkspaceLimits | None = None,
) -> RunReport:
    """Run every (skill, case, runner, arm, repetition) and aggregate the results.

    Evaluator exceptions (e.g. ``UnknownAssertionKind``, ``InvalidAssertionValue``
    from `skill_lens.evaluators.assertion`) propagate out of this function by
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

    `case_filter` keeps only cases whose name contains it, case-insensitively;
    a skill it empties is recorded in `case_filtered_skills`, never silently
    dropped.

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

    `keep_workspace` skips deleting each case's temporary directory and leaves
    its path on the `RunResult`, for debugging. `workspace_limits` bounds what
    a case may write; None means the module defaults. Both are per-run
    settings rather than per-case ones -- a cap is a runaway guard, not part
    of what an eval asserts.
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
    plan = _plan_work(skills, runners, evals_path, tag, case_filter, baseline, repeat)
    outcomes = _execute(
        plan.items,
        evaluators,
        concurrency,
        executor_factory,
        keep_workspace,
        workspace_limits or DEFAULT_LIMITS,
    )
    return RunReport(
        outcomes=outcomes,
        skipped_skills=plan.skipped,
        tag_filtered_skills=plan.tag_filtered,
        case_filtered_skills=plan.case_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=plan.notes,
    )
