"""Build and run the skill x case x runner matrix."""

from __future__ import annotations

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


def run_evals(
    skills: list[Skill],
    runners: list[Runner],
    evals_path: Path | None = None,
    evaluators: list[Evaluator] | None = None,
    tag: str | None = None,
    judge: Judge | None = None,
    baseline: BaselineKind | None = None,
    repeat: int = 1,
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
    """
    if evaluators is not None and judge is not None:
        raise ValueError(
            "run_evals() received both `evaluators` and `judge`; pass an explicit "
            "JudgeEvaluator inside `evaluators` instead of also passing `judge`."
        )
    if repeat < 1:
        raise ValueError(f"repeat must be at least 1, got {repeat}")
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
    outcomes: list[CaseOutcome] = []
    skipped: list[str] = []
    tag_filtered: list[str] = []
    notes: list[BaselineNote] = []
    for skill in skills:
        cases = load_cases_for_skill(skill, evals_path=evals_path)
        if not cases:
            skipped.append(skill.name)
            continue
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
            if not cases:
                tag_filtered.append(skill.name)
                continue
        # Resolved once per skill, never per case or per repetition: it shells
        # out to git.
        baseline_skill = None if baseline is None else _baseline_skill(skill, baseline, notes)
        for case in cases:
            for arm, arm_skill in _arms(case, skill, baseline_skill, baseline, notes):
                for runner in runners:
                    for index in range(repeat):
                        outcomes.append(
                            _run_one(
                                arm_skill,
                                case,
                                runner,
                                evaluators,
                                arm=arm,
                                repeat_index=index,
                                report_skill_name=skill.name,
                            )
                        )
    return RunReport(
        outcomes=outcomes,
        skipped_skills=skipped,
        tag_filtered_skills=tag_filtered,
        baseline_kind=baseline,
        repeat=repeat,
        baseline_notes=notes,
    )
